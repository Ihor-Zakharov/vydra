"""Самодиагностика и починка: `vydra doctor [--fix]` и панель «Состояние системы»."""

from __future__ import annotations

import shutil
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from . import system, tools
from .config import Settings
from .jobs import clean_work_dir, dir_size, queued_ids_on_disk
from .library import Library

MIN_FREE = 2 * 1024**3
TEMP_WARN = 500 * 1024**2
SITES = {"YouTube": "https://www.youtube.com", "TikTok": "https://www.tiktok.com", "Instagram": "https://www.instagram.com"}


@dataclass
class Check:
    id: str
    title: str
    status: str  # ok | warn | fail
    detail: str
    fix: str | None = None  # подпись кнопки «починить»
    hint: str | None = None

    def public(self) -> dict:
        return asdict(self)


@dataclass
class FixResult:
    id: str
    ok: bool
    message: str

    def public(self) -> dict:
        return asdict(self)


class Doctor:
    def __init__(self, settings: Settings, library: Library, protected=None):
        self.settings = settings
        self.library = library
        # id задач, чьи временные данные трогать нельзя (сервер передаёт свои живые задачи)
        self.protected = protected or (lambda: queued_ids_on_disk(settings.work_dir))

    # --- проверки ------------------------------------------------------------------------

    def run(self, network: bool = True) -> list[Check]:
        checks = [
            self._ffmpeg,
            self._js,
            self._ytdlp,
            self._storage,
            self._library,
            self._temp,
            self._cookies,
            self._shortcut,
        ]
        if network:
            checks.insert(3, self._network)
        with ThreadPoolExecutor(max_workers=len(checks)) as pool:
            return list(pool.map(_safe, checks))

    def _ffmpeg(self) -> Check:
        ffmpeg, ffprobe = self.settings.ffmpeg, self.settings.ffprobe
        fix = "Скачать FFmpeg" if tools.ffmpeg_source() else None
        hint = None if fix else "macOS: brew install ffmpeg · Linux: sudo apt install ffmpeg"
        if not ffmpeg or not ffprobe:
            missing = " и ".join(n for n, p in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)) if not p)
            return Check("ffmpeg", "FFmpeg", "fail", f"Не найден {missing} — без него нет MP4/MP3", fix, hint)
        version = system.run([ffmpeg, "-version"], timeout=15)
        if not version:
            return Check("ffmpeg", "FFmpeg", "fail", f"{ffmpeg} не запускается", fix, hint)
        first = version.splitlines()[0].replace("ffmpeg version ", "")
        return Check("ffmpeg", "FFmpeg", "ok", f"{first.split(' Copyright')[0]}")

    def _js(self) -> Check:
        runtime = self.settings.js_runtime
        fix = "Установить Deno" if tools.deno_source() else None
        if not runtime:
            return Check(
                "js", "JS-движок для YouTube", "warn",
                "Не найден ни Node.js, ни Deno — YouTube может отдавать не все качества", fix,
            )  # fmt: skip
        name, path = runtime
        version = system.run([path, "--version"], timeout=15)
        if not version:
            return Check("js", "JS-движок для YouTube", "warn", f"{name} не запускается", fix)
        return Check("js", "JS-движок для YouTube", "ok", f"{name} {version.splitlines()[0]}")

    def _ytdlp(self) -> Check:
        current = tools.ytdlp_version()
        if not current:
            return Check("ytdlp", "yt-dlp", "fail", "yt-dlp не установлен", "Установить yt-dlp")
        latest = tools.ytdlp_latest_cached(self.settings.work_dir)
        if latest and tools.version_tuple(latest) > tools.version_tuple(current):
            return Check(
                "ytdlp", "yt-dlp", "warn", f"Версия {current}, вышла {latest} — сайты часто ломают старые версии",
                "Обновить yt-dlp",
            )  # fmt: skip
        suffix = " (последняя)" if latest else " (не удалось проверить обновления)"
        return Check("ytdlp", "yt-dlp", "ok", f"Версия {current}{suffix}")

    def _network(self) -> Check:
        def reachable(url: str) -> bool:
            try:
                req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=6):
                    return True
            except urllib.error.HTTPError:
                return True  # ответил — значит доступен
            except Exception:  # noqa: BLE001
                return False

        with ThreadPoolExecutor(max_workers=len(SITES)) as pool:
            results = dict(zip(SITES, pool.map(reachable, SITES.values()), strict=True))
        down = [name for name, ok in results.items() if not ok]
        if not down:
            return Check("network", "Доступ к сайтам", "ok", "YouTube, TikTok и Instagram отвечают")
        status = "fail" if len(down) == len(SITES) else "warn"
        return Check(
            "network", "Доступ к сайтам", status, "Не отвечает: " + ", ".join(down),
            hint="Проверьте интернет, VPN или прокси",
        )  # fmt: skip

    def _storage(self) -> Check:
        root = self.library.root
        where = system.display_path(root)
        if not root.is_dir():
            return Check("storage", "Папка хранилища", "fail", f"Папки нет: {where}", "Создать папку")
        try:
            probe = root / ".vydra-write-test"
            probe.write_bytes(b"ok")
            probe.unlink()
        except OSError:
            return Check(
                "storage", "Папка хранилища", "fail", f"Нет прав на запись: {where}",
                hint="Выберите другую папку в настройках",
            )  # fmt: skip
        free = shutil.disk_usage(root).free
        if free < MIN_FREE:
            return Check(
                "storage", "Папка хранилища", "warn", f"{where} — свободно всего {_size(free)}",
                hint="Освободите место или выберите папку на другом диске",
            )  # fmt: skip
        return Check("storage", "Папка хранилища", "ok", f"{where} · свободно {_size(free)}")

    def _library(self) -> Check:
        if not self.library.root.is_dir():
            if not self.library.available():
                return Check(
                    "library", "Хранилище и кинотеатр", "fail", "Папка хранилища недоступна — диск отключён?",
                    hint="Подключите диск или выберите другую папку в настройках",
                )  # fmt: skip
            return Check("library", "Хранилище и кинотеатр", "warn", "Папка хранилища не создана", "Пересобрать")
        self.library.scan(force=True)
        audit = self.library.audit()
        problems = []
        if not audit.get("index", True):
            problems.append("индекс повреждён (восстановится из копии)")
        if not audit["cinema"]:
            problems.append("нет файлов кинотеатра")
        if audit["missing"]:
            problems.append(f"пропало файлов: {audit['missing']}")
        if audit["no_poster"]:
            problems.append(f"без постера: {audit['no_poster']}")
        if problems:
            return Check("library", "Хранилище и кинотеатр", "warn", "; ".join(problems).capitalize(), "Пересобрать")
        return Check("library", "Хранилище и кинотеатр", "ok", f"Файлов: {audit['items']}, кинотеатр на месте")

    def _temp(self) -> Check:
        work = self.settings.work_dir
        size = sum(dir_size(work / d) for d in ("jobs", "uploads", "sessions", "info"))
        if size > TEMP_WARN:
            return Check("temp", "Временные файлы", "warn", f"Занято {_size(size)}", "Очистить")
        return Check("temp", "Временные файлы", "ok", f"Занято {_size(size)}")

    def _cookies(self) -> Check:
        path = self.settings.cookies_file
        if path is None:
            return Check("cookies", "Cookies для входа", "ok", "Не заданы — нужны, только если сайт просит войти")
        lines = [ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        entries = [ln for ln in lines if not ln.startswith("#") and len(ln.split("\t")) >= 7]
        if not entries:
            return Check(
                "cookies", "Cookies для входа", "warn", "Файл не похож на cookies.txt (формат Netscape)",
                "Удалить cookies", "Экспортируйте cookies расширением «Get cookies.txt LOCALLY»",
            )  # fmt: skip
        return Check("cookies", "Cookies для входа", "ok", f"Подключены: {len(entries)} записей")

    def _shortcut(self) -> Check:
        path = tools.shortcut_path()
        if path is None:
            return Check("shortcut", "Ярлык «Выдра»", "ok", "Рабочий стол не найден — запуск командой vydra ui")
        if path.exists():
            return Check("shortcut", "Ярлык «Выдра»", "ok", system.display_path(path))
        return Check("shortcut", "Ярлык «Выдра»", "warn", "Ярлыка для запуска нет", "Создать ярлык")

    # --- починка -------------------------------------------------------------------------

    def fix(self, check_id: str, progress: tools.Progress | None = None) -> FixResult:
        try:
            message = self._fix(check_id, progress)
            return FixResult(check_id, True, message)
        except Exception as exc:  # noqa: BLE001 — починка не должна ронять сервер
            return FixResult(check_id, False, str(exc) or exc.__class__.__name__)

    def _fix(self, check_id: str, progress: tools.Progress | None) -> str:
        if check_id == "ffmpeg":
            return tools.install_ffmpeg(self.settings, progress)
        if check_id == "js":
            return tools.install_deno(self.settings, progress)
        if check_id == "ytdlp":
            return tools.update_ytdlp()
        if check_id in ("storage", "library"):
            changes = self.library.rebuild()
            return f"Хранилище пересобрано (новых файлов: {changes.get('added', 0)}, убрано: {changes.get('removed', 0)})"
        if check_id == "temp":
            freed = clean_work_dir(self.settings.work_dir, protected=self.protected(), force=True)
            return f"Освобождено {_size(freed)}"
        if check_id == "cookies":
            (self.settings.config_dir / "cookies.txt").unlink(missing_ok=True)
            return "Cookies удалены"
        if check_id == "shortcut":
            return tools.create_shortcut()
        raise ValueError(f"Для «{check_id}» починки нет")

    def fix_all(self, checks: list[Check] | None = None) -> list[FixResult]:
        checks = checks if checks is not None else self.run()
        return [self.fix(c.id) for c in checks if c.status != "ok" and c.fix]


def summary(checks: list[Check]) -> dict:
    return {s: sum(1 for c in checks if c.status == s) for s in ("ok", "warn", "fail")}


def _safe(check) -> Check:
    try:
        return check()
    except Exception as exc:  # noqa: BLE001 — упавшая проверка — это тоже результат
        name = check.__name__.strip("_")
        return Check(name, name, "fail", f"Проверка упала: {exc}")


def _size(num: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if num < 1024 or unit == "ТБ":
            return f"{num:.0f} {unit}" if unit in ("Б", "КБ") else f"{num:.1f} {unit}".replace(".", ",")
        num /= 1024
    return f"{num} Б"
