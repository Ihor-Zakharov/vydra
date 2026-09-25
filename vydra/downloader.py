"""Скачивание через yt-dlp: версии без водяных знаков, по возможности сразу H.264.

yt-dlp работает в отдельном процессе (python -m vydra.downloader): обновление yt-dlp
подхватывается без перезапуска сервера, отмена — kill дерева процессов, а падение
извлекателя не роняет сервер. Процессы общаются JSON-строками через stdout.

Надёжность:
- ошибки делятся на временные (сеть, 5xx/429, падение воркера, зависание) и постоянные
  (приватное, удалённое, неподдерживаемое) — повторять имеет смысл только первые;
- сторож убивает воркер, если данные перестали идти (STALL_DOWNLOAD) или сайт молчит
  при извлечении (STALL_EXTRACT);
- недокачанные .part остаются в папке задачи, и следующая попытка докачивает их;
- до начала загрузки проверяется свободное место.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from . import system
from .media import Cancelled

log = logging.getLogger("downloader")

# TikTok отдаёт отдельный формат «download» с пометкой watermarked, YouTube — «Premium» (нужна подписка).
# «?» — пропускать форматы, у которых format_note вообще нет.
CLEAN = "[format_note!*=?watermark][format_note!*=?Premium]"
H264 = "[vcodec~='^(avc|h264)']"
AAC = "[acodec~='^(mp4a|aac)']"
# Формат «audio» у TikTok — трек из библиотеки звуков, а не звук самого ролика
AUDIO = "ba[format_id!=audio]"

QUALITIES = ("max", "1080", "720", "480", "360")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".image"}
INFO_KEYS = (
    "id", "title", "description", "uploader", "channel", "duration", "upload_date", "webpage_url",
    "playlist_index", "n_entries", "playlist_count", "format", "extractor_key",
)  # fmt: skip

STALL_DOWNLOAD = float(os.environ.get("VD_STALL_TIMEOUT", "120"))  # байты не идут дольше — зависло
STALL_EXTRACT = float(os.environ.get("VD_EXTRACT_TIMEOUT", "300"))  # сайт молчит при извлечении
SOCKET_TIMEOUT = 30
WORKER_CMD = [sys.executable, "-m", "vydra.downloader"]  # тесты подменяют на поддельный воркер
SPACE_MARGIN = 64 * 1024 * 1024
# Короткий отрезок длинного ролика качаем куском (ffmpeg по диапазону): 30 с из часового ролика — секунды вместо
# минуты. Длинные куски так не берём: YouTube режет скорость одного соединения ffmpeg (15 мин куска — 7,5 мин),
# а весь ролик yt-dlp качает частями быстро. Не вышло — качаем целиком и режем сами. VD_SECTION_MAX=0 — выключить.
SECTION_MAX = float(os.environ.get("VD_SECTION_MAX", "60"))
SECTION_MIN_VIDEO = 180  # ролики короче качаем целиком: выигрыша нет


class DownloadFailed(Exception):
    """Понятная пользователю ошибка скачивания. transient — есть смысл повторить,
    rate_limited — сайт ограничил запросы (429): пауза перед повтором длиннее."""

    def __init__(self, message: str, transient: bool = False, rate_limited: bool = False):
        super().__init__(message)
        self.transient = transient
        self.rate_limited = rate_limited


class NoSpace(Exception):
    """Не хватает места на диске — повторять бессмысленно."""


PLAYLIST_LIMIT = 50  # больше — только с явным подтверждением
RATE_LIMITED_TEXT = "Сайт временно ограничил запросы — повторю чуть позже."


def format_spec(mode: str, quality: str) -> tuple[str, list[str]]:
    """Строка -f и порядок сортировки для yt-dlp."""
    if mode == "mp3":
        return f"{AUDIO}/b{CLEAN}/ba/b", []
    if quality == "max":
        return f"bv*{CLEAN}+{AUDIO}/b{CLEAN}/bv*+ba/b", []
    # До 1080p почти везде есть H.264 — тогда MP4 собирается без перекодирования.
    # res — по меньшей стороне, поэтому «1080p» верно работает и для вертикальных роликов.
    spec = f"bv*{H264}{CLEAN}+{AUDIO}{AAC}/b{H264}{AAC}{CLEAN}/bv*{CLEAN}+{AUDIO}/b{CLEAN}/bv*+ba/b"
    return spec, [f"res:{quality}"]


@dataclass
class Downloaded:
    path: Path
    info: dict
    thumbnail: Path | None
    section: tuple[float, float] | None = None  # скачан только этот кусок ролика (секунды от начала)
    offset: float = 0.0  # где в файле начинается кусок: ffmpeg без перекодирования берёт и «разгон» до него

    @property
    def watermarked(self) -> bool:
        return "watermark" in (self.info.get("format") or "").lower()


class Reporter:
    """Куда сообщается ход работы (реализуется задачей)."""

    def item(self, info: dict, thumbnail: Path | None) -> None: ...
    def progress(self, percent: float | None, speed: float | None, eta: int | None) -> None: ...
    def stage(self, text: str) -> None: ...
    def question(self, question: dict) -> str:
        """Спросить пользователя; вернуть id варианта (или бросить Cancelled)."""
        return question["default"]

    def note(self, text: str) -> None: ...
    def mode(self, mode: str) -> None: ...
    def clip(self, clip) -> None: ...


# --- классификация ошибок ----------------------------------------------------------------

_PERMANENT = [
    (("unsupported url",), "Эта ссылка не поддерживается — нужна ссылка на конкретное видео."),
    (("confirm you're not a bot", "confirm you’re not a bot", "sign in to confirm your age", "age-restricted",
      "inappropriate for some users"),
     "YouTube просит войти в аккаунт. Добавьте cookies в настройках и повторите."),
    (("login required", "rate-limit reached", "requested content is not available", "log in to", "logged-in",
      "use --cookies"),
     "Сайт просит войти в аккаунт. Добавьте cookies в настройках и повторите."),
    (("private video", "this video is private", "is private"), "Видео приватное — скачать его нельзя."),
    (("video unavailable", "this video is unavailable", "has been removed", "not available in your country",
      "no longer available", "copyright claim", "account has been terminated"),
     "Видео недоступно: удалено или закрыто в вашем регионе."),
    (("http error 404", "404: not found", "http error 410"), "Страница не найдена — проверьте ссылку."),
    (("drm",), "Видео защищено DRM — скачать его нельзя."),
    (("ip address is blocked", "not available in your region", "geo restrict", "geo-restrict"),
     "Сайт не показывает этот ролик с вашего IP-адреса (ограничение по региону) — нужен VPN или cookies."),
    (("live stream recording is not available", "recording is not available"),
     "Запись этой трансляции недоступна — эфир закончился, а запись не сохранили или скрыли."),
    (("live event will begin", "premieres in", "this live event", "is_upcoming", "will begin in"),
     "Трансляция или премьера ещё не началась — попробуйте, когда она начнётся и закончится."),
    (("requested format is not available", "no video formats found"),
     "Нужного формата у ролика нет — попробуйте другое качество или MP3."),
    (("ffmpeg not found", "ffprobe and ffmpeg not found", "ffmpeg is not installed"),
     "Не найден FFmpeg — запустите «vydra doctor --fix» или «Починить» в интерфейсе."),
    (("не хватает места", "no space left"), None),  # сообщение уже понятное
]  # fmt: skip
_TRANSIENT = [
    (("http error 429", "too many requests"), RATE_LIMITED_TEXT),
    (("http error 403", "403: forbidden"),
     "Сайт отказал в доступе (403) — повторю; если не поможет, обновите yt-dlp или добавьте cookies."),
    (("http error 5", "service unavailable", "bad gateway", "gateway time", "internal server error"),
     "Сайт временно не отвечает — повторю чуть позже."),
    (("unable to download webpage", "getaddrinfo", "timed out", "timeout", "connection reset", "network is unreachable",
      "temporary failure in name resolution", "name or service not known", "no route to host", "connection refused",
      "connection aborted", "remote end closed", "incompleteread", "incomplete read", "broken pipe", "eof occurred",
      "ssl", "proxy", "unable to download video data", "did not get any data blocks", "giving up after",
      "unable to connect", "network"),
     "Нет связи с сайтом — проверьте интернет."),
]  # fmt: skip


def classify(message: str) -> tuple[str, bool]:
    """(понятный текст, временная ли ошибка). Неизвестное считаем временным — одна-две попытки не повредят."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", message or "")
    text = re.sub(r"^(ERROR:\s*)?(\[[^\]]+\]\s*)?([\w-]+:\s)?", "", text.strip())
    low = text.lower()
    for needles, human in _PERMANENT:
        if any(n in low for n in needles):
            return (human or text[:300]), False
    for needles, human in _TRANSIENT:
        if any(n in low for n in needles):
            return human, True
    return (text[:300] or "Не удалось скачать"), True


def friendly_error(message: str) -> str:
    return classify(message)[0]


# --- сторона сервера ---------------------------------------------------------------------


def download(
    url: str,
    mode: str,
    quality: str,
    work_dir: Path,
    *,
    ffmpeg: str | None,
    js_runtime: tuple[str, str] | None,
    cookies: Path | None,
    reporter: Reporter,
    cancel: threading.Event,
    library_dir: Path | None = None,
    info_cache: Path | None = None,
    confirm_playlist: bool = False,
    clip: tuple[float, float | None] | None = None,
    allow_section: bool = True,
) -> list[Downloaded]:
    work_dir.mkdir(parents=True, exist_ok=True)
    request = {
        "url": url,
        "mode": mode,
        "quality": quality,
        "work_dir": str(work_dir),
        "ffmpeg": ffmpeg,
        "js_runtime": list(js_runtime) if js_runtime else None,
        "cookies": _private_cookies(cookies, work_dir),
        "library_dir": str(library_dir) if library_dir else None,
        "info_file": str(cached_info(info_cache, url, cookies)) if info_cache and cached_info(info_cache, url, cookies) else None,
        "playlist_limit": None if confirm_playlist else PLAYLIST_LIMIT,
        "clip": list(clip) if clip else None,  # «отрезок за пределами ролика» и загрузка куском
        "allow_section": allow_section,
    }
    reporter.stage("Получаю информацию о видео")
    watch = _Watch()
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as stderr:
        proc = subprocess.Popen(
            list(WORKER_CMD),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            encoding="utf-8",
            errors="replace",
            **system.child_flags(group=True),
        )
        guard = threading.Thread(target=_guard, args=(proc, cancel, watch), name="dl-guard", daemon=True)
        guard.start()
        result: list[Downloaded] | None = None
        error: tuple[str, bool] | None = None
        try:
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(json.dumps(request) + "\n")
            proc.stdin.flush()  # stdin остаётся открытым: по нему уходят ответы на вопросы
            for line in proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                watch.touch()
                kind = msg.get("type")
                if kind == "item":
                    thumb = msg.get("thumbnail")
                    reporter.item(msg["info"], Path(thumb) if thumb else None)
                elif kind == "phase":
                    watch.phase = msg["phase"]
                elif kind == "progress":
                    watch.bytes(msg.get("downloaded"))
                    reporter.progress(msg.get("percent"), msg.get("speed"), msg.get("eta"))
                elif kind == "stage":
                    reporter.stage(msg["text"])
                elif kind == "question":
                    phase, watch.phase = watch.phase, "waiting"  # ждём человека — это не зависание
                    answer = reporter.question(msg["question"])
                    watch.phase = phase
                    watch.touch()
                    watch.last_bytes = time.monotonic()
                    proc.stdin.write(json.dumps({"answer": answer}) + "\n")
                    proc.stdin.flush()
                elif kind == "note":
                    reporter.note(msg["text"])
                elif kind == "mode":
                    reporter.mode(msg["mode"])
                elif kind == "clip":
                    reporter.clip(msg.get("clip"))
                elif kind == "done":
                    result = [
                        Downloaded(Path(i["path"]), i["info"], Path(i["thumbnail"]) if i.get("thumbnail") else None,
                                   tuple(i["section"]) if i.get("section") else None,
                                   float(i.get("offset") or 0.0))  # fmt: skip
                        for i in msg["items"]
                    ]
                elif kind == "error":
                    error = (msg["message"], bool(msg.get("transient")))
                    if msg.get("raw"):
                        log.warning("worker error for %s: %s", url, msg["raw"])
            proc.wait()
        finally:
            system.kill_tree(proc)  # на любой выход — никаких сирот (yt-dlp → ffmpeg)
            watch.finished.set()
            try:
                if proc.stdin:
                    proc.stdin.close()
            except OSError:
                pass
        if cancel.is_set():
            raise Cancelled
        if watch.stalled:
            raise DownloadFailed("Загрузка зависла — сайт перестал отдавать данные", transient=True)
        if result is not None:
            return result
        if error is not None:
            raise DownloadFailed(error[0], transient=error[1], rate_limited=error[0] == RATE_LIMITED_TEXT)
        stderr.seek(0)
        tail = [ln for ln in stderr.read().strip().splitlines() if ln.strip()][-3:]
        log.error("downloader worker died (code %s): %s", proc.returncode, "\n".join(tail))
        text, _ = classify(tail[-1] if tail else "")
        # воркер умер без ответа (OOM, kill, сбой интерпретатора) — это временная проблема
        raise DownloadFailed(text if tail else "Процесс загрузки неожиданно завершился", transient=True)


class _Watch:
    """Состояние для сторожа: когда последний раз что-то приходило и росли ли байты."""

    def __init__(self) -> None:
        self.phase = "extract"  # extract → download → post
        self.last = time.monotonic()
        self.last_bytes = time.monotonic()
        self._bytes: int | None = None
        self.stalled = False
        self.finished = threading.Event()

    def touch(self) -> None:
        self.last = time.monotonic()

    def bytes(self, value) -> None:
        if value is not None and value != self._bytes:
            self._bytes = value
            self.last_bytes = time.monotonic()

    def is_stalled(self) -> bool:
        now = time.monotonic()
        if self.phase == "extract":
            return now - self.last > STALL_EXTRACT
        if self.phase == "download":
            # пока байтов не было — считаем от последнего сообщения, потом — от последнего прироста
            since = self.last if self._bytes is None else self.last_bytes
            return now - since > STALL_DOWNLOAD
        return False  # склейка/постобработка идёт локально, без сети


def _guard(proc: subprocess.Popen, cancel: threading.Event, watch: _Watch) -> None:
    while proc.poll() is None and not watch.finished.is_set():
        if cancel.wait(0.3):
            system.kill_tree(proc)
            return
        if watch.is_stalled():
            log.warning("worker stalled in phase %s — killing", watch.phase)
            watch.stalled = True
            system.kill_tree(proc)
            return


def _private_cookies(cookies: Path | None, work_dir: Path) -> str | None:
    """yt-dlp переписывает cookie-файл в конце; параллельные воркеры его бы испортили — даём каждому копию."""
    if cookies is None:
        return None
    copy = work_dir / "cookies.txt"
    try:
        shutil.copyfile(cookies, copy)
        copy.chmod(0o600)
    except OSError:
        return str(cookies)
    return str(copy)


# --- предпросмотр ------------------------------------------------------------------------

_preview_slots = threading.BoundedSemaphore(3)  # вставили пять ссылок подряд — не больше трёх воркеров
_preview_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_preview_lock = threading.Lock()
PREVIEW_TTL = 300


INFO_TTL = 1800  # полная информация из превью годится для загрузки полчаса (дальше ссылки протухают)


def peek_preview(url: str, cookies: Path | None) -> dict | None:
    """Уже полученное превью ссылки (без обращения к сайту) — например, чтобы узнать размер плейлиста."""
    with _preview_lock:
        cached = _preview_cache.get(f"{url}|{bool(cookies)}")
    if cached and time.monotonic() - cached[0] < PREVIEW_TTL:
        return dict(cached[1])
    return None


def _info_key(url: str, cookies: Path | None) -> str:
    import hashlib

    return hashlib.sha1(f"{url}|{bool(cookies)}".encode()).hexdigest()[:20]


def cached_info(cache_dir: Path | None, url: str, cookies: Path | None) -> Path | None:
    """Файл с полной информацией о ролике из недавнего превью (или None)."""
    if cache_dir is None:
        return None
    path = cache_dir / f"{_info_key(url, cookies)}.json"
    try:
        if time.time() - path.stat().st_mtime < INFO_TTL:
            return path
    except OSError:
        pass
    return None


def prune_info_cache(cache_dir: Path) -> None:
    if not cache_dir.is_dir():
        return
    for path in cache_dir.glob("*.json"):
        try:
            if time.time() - path.stat().st_mtime > INFO_TTL:
                path.unlink()
        except OSError:
            pass


def preview(
    url: str,
    *,
    js_runtime: tuple[str, str] | None,
    cookies: Path | None,
    timeout: float = 90,
    info_cache: Path | None = None,
) -> dict:
    """Сведения о ролике без скачивания: название, длительность, доступные качества.
    Полная информация сохраняется в info_cache — загрузка сразу после превью не извлекает её повторно."""
    key = f"{url}|{bool(cookies)}"
    with _preview_lock:
        cached = _preview_cache.get(key)
        if cached and time.monotonic() - cached[0] < PREVIEW_TTL:
            _preview_cache.move_to_end(key)
            return dict(cached[1])
    if info_cache is not None:
        info_cache.mkdir(parents=True, exist_ok=True)
        prune_info_cache(info_cache)
    request = {
        "info": True,
        "url": url,
        "js_runtime": list(js_runtime) if js_runtime else None,
        "cookies": str(cookies) if cookies else None,
        "readonly_cookies": True,
        "save_info": str(info_cache / f"{_info_key(url, cookies)}.json") if info_cache else None,
    }
    if not _preview_slots.acquire(timeout=timeout):
        raise DownloadFailed("Слишком много ссылок сразу — подождите пару секунд", transient=True)
    try:
        proc = subprocess.run(
            list(WORKER_CMD),
            input=json.dumps(request),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            **system.child_flags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise DownloadFailed("Сайт слишком долго не отвечает — попробуйте ещё раз", transient=True) from exc
    finally:
        _preview_slots.release()
    for line in proc.stdout.splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("type") == "done":
            data = msg["preview"]
            with _preview_lock:
                _preview_cache[key] = (time.monotonic(), dict(data))
                while len(_preview_cache) > 64:
                    _preview_cache.popitem(last=False)
            return data
        if msg.get("type") == "error":
            raise DownloadFailed(msg["message"], transient=bool(msg.get("transient")))
    tail = [ln for ln in proc.stderr.strip().splitlines() if ln.strip()]
    text, transient = classify(tail[-1] if tail else "Не удалось прочитать ссылку")
    raise DownloadFailed(text, transient=transient)


# --- сторона рабочего процесса -----------------------------------------------------------


def _worker() -> None:
    # Протокол идёт в копию stdout, а сам fd 1 перенаправляем в stderr:
    # случайный print из yt-dlp или ffmpeg не сломает JSON-поток
    proto = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    def send(**msg) -> None:
        try:
            proto.write(json.dumps(msg, ensure_ascii=False, default=str) + "\n")
        except (BrokenPipeError, OSError):
            os._exit(3)  # сервер умер — выходим сразу, не качаем в пустоту

    request = json.loads(sys.stdin.readline())
    try:
        if request.get("info"):
            send(type="done", preview=_preview(request))
            return
        items = _run(request, send)
    except (NoSpace, _Permanent) as exc:
        send(type="error", message=str(exc), transient=False)
        return
    except Exception as exc:  # noqa: BLE001 — всё превращаем в понятное сообщение
        text, transient = classify(str(exc))
        send(type="error", message=text, transient=transient, raw=str(exc)[:500])
        return
    send(type="done", items=items)


class _Permanent(Exception):
    """Ошибка, которую повторять бессмысленно (например, плейлист без подтверждения)."""


_TIKTOK_PHOTO = re.compile(r"^(https?://(?:www\.)?tiktok\.com/@[\w.-]*/)photo(/\d+)", re.IGNORECASE)


def extractor_url(url: str) -> str:
    """Ссылка в том виде, который понимает yt-dlp. Фото-пост TikTok (/photo/…) он не узнаёт, а тот же пост
    по /video/… отдаёт — со звуком слайдшоу (картинки yt-dlp не качает)."""
    return _TIKTOK_PHOTO.sub(r"\1video\2", url)


def _common_opts(req: dict, logger) -> dict:
    opts: dict = {
        "quiet": True,
        "no_color": True,
        "noprogress": True,
        "noplaylist": True,
        "logger": logger,
        "socket_timeout": SOCKET_TIMEOUT,
        "extractor_retries": 3,
    }
    if runtime := req.get("js_runtime"):
        opts["js_runtimes"] = {runtime[0]: {"path": runtime[1]}}
    if req.get("cookies"):
        opts["cookiefile"] = req["cookies"]
    return opts


class _Logger:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def debug(self, msg: str) -> None: ...
    def info(self, msg: str) -> None: ...
    def warning(self, msg: str) -> None:
        print(msg, file=sys.stderr)

    def error(self, msg: str) -> None:
        print(msg, file=sys.stderr)
        self.errors.append(msg)


def _preview(req: dict) -> dict:
    from yt_dlp import YoutubeDL

    opts = _common_opts(req, _Logger()) | {"skip_download": True, "extract_flat": "in_playlist"}
    with tempfile.TemporaryDirectory(prefix="vydra-preview-") as tmp:
        if req.get("readonly_cookies") and req.get("cookies"):
            # предпросмотр идёт параллельно с загрузками — общий cookie-файл не переписываем
            opts["cookiefile"] = str(Path(tmp) / "cookies.txt")
            shutil.copyfile(req["cookies"], opts["cookiefile"])
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(extractor_url(req["url"]), download=False)
            if req.get("save_info") and info.get("_type") != "playlist":
                try:
                    Path(req["save_info"]).write_text(json.dumps(ydl.sanitize_info(info)), encoding="utf-8")
                except (OSError, TypeError, ValueError):
                    pass
    if info.get("_type") == "playlist":
        entries = list(info.get("entries") or [])
        first = entries[0] if entries else {}
        thumbs = first.get("thumbnails") or [{}]
        return {
            "url": req["url"], "title": info.get("title"), "uploader": info.get("uploader") or info.get("channel"),
            "duration": None, "thumbnail": first.get("thumbnail") or thumbs[-1].get("url"),
            "platform": None, "heights": [], "has_video": True, "playlist": True, "count": len(entries),
            "preview_url": None,
        }  # fmt: skip
    formats = info.get("formats") or []
    videos = [f for f in formats if f.get("vcodec") not in (None, "none") and f.get("height")]
    heights = sorted({min(f["height"], f.get("width") or f["height"]) for f in videos})
    # Прогрессивный файл (видео+звук одним куском) до 480p — браузер сможет его проиграть для превью
    progressive = [
        f for f in videos
        if f.get("acodec") not in (None, "none") and f.get("protocol") in ("http", "https")
        and f.get("ext") == "mp4" and min(f["height"], f.get("width") or f["height"]) <= 480
        and "watermark" not in (f.get("format_note") or "").lower()
    ]  # fmt: skip
    progressive.sort(key=lambda f: f["height"])
    return {
        "url": info.get("webpage_url") or req["url"],
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "heights": heights,
        "has_video": bool(videos),
        "playlist": False,
        "count": None,
        "preview_url": progressive[-1]["url"] if progressive else None,
        "is_live": bool(info.get("is_live")),  # прямой эфир скачать нельзя, пока он идёт
    }


def _run(req: dict, send) -> list[dict]:
    from yt_dlp import YoutubeDL

    work_dir = Path(req["work_dir"])
    fmt, sort = format_spec(req["mode"], req["quality"])
    seen: set[str] = set()

    def on_progress(d: dict) -> None:
        info = d.get("info_dict") or {}
        key = str(info.get("id"))
        if key not in seen:
            seen.add(key)
            thumb = _find_thumbnail(work_dir, info.get("id"))
            send(type="item", info=_slim(info), thumbnail=str(thumb) if thumb else None)
        if d.get("status") in ("downloading", "finished"):
            send(
                type="progress",
                percent=_overall_percent(d, info),
                speed=d.get("speed"),
                eta=d.get("eta"),
                downloaded=d.get("downloaded_bytes"),
            )

    def on_postprocess(d: dict) -> None:
        if d.get("status") == "started":
            send(type="phase", phase="post")
            if d.get("postprocessor") == "Merger":
                send(type="stage", text="Склейка видео и звука")

    logger = _Logger()
    errors = logger.errors
    opts = _common_opts(req, logger) | {
        "format": fmt,
        "format_sort": sort,
        "paths": {"home": str(work_dir), "temp": str(work_dir)},
        "outtmpl": {"default": "%(id)s.%(ext)s"},
        "merge_output_format": "mkv",  # в mkv влезает любой кодек; итоговый MP4 соберёт media.py
        "writethumbnail": True,
        "progress_hooks": [on_progress],
        "postprocessor_hooks": [on_postprocess],
        "updatetime": False,
        # повторная попытка докачивает .part и не качает заново то, что уже скачано целиком
        "continuedl": True,
        "overwrites": False,
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 5,
        "concurrent_fragment_downloads": 4,
    }
    if req.get("ffmpeg"):
        opts["ffmpeg_location"] = req["ffmpeg"]

    cached = _load_info(req.get("info_file"))
    # Один экземпляр YoutubeDL и на извлечение, и на загрузку: cookies, которые сайт ставит при
    # извлечении (Instagram, TikTok), нужны и CDN — иначе 403
    with YoutubeDL(opts) as ydl:
        if cached is not None:
            try:
                # из кэша превью: 403 тут значит «ссылки протухли», а не «плохой формат» — без подбора замен
                return _download_with(ydl, cached, req, send, work_dir, errors, fallback=False)
            except (NoSpace, _Permanent):
                raise
            except Exception as exc:  # noqa: BLE001 — ссылки из превью протухли (403/410) — извлекаем заново
                print(f"cached info failed, re-extracting: {exc}", file=sys.stderr)
                send(type="phase", phase="extract")
        return _download_with(ydl, None, req, send, work_dir, errors)


def _load_info(path: str | None) -> dict | None:
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _is_playlist(info: dict) -> bool:
    return info.get("_type") in ("playlist", "multi_video")


def _too_many(limit: int) -> _Permanent:
    return _Permanent(f"Это плейлист больше чем из {limit} роликов — подтвердите, что хотите скачать его целиком")


def _download_with(
    ydl, info: dict | None, req: dict, send, work_dir: Path, errors: list[str], fallback: bool = True
) -> list[dict]:
    limit = req.get("playlist_limit")
    if info is None:
        raw = ydl.extract_info(extractor_url(req["url"]), download=False, process=False)
        if _is_playlist(raw):
            # список роликов ленивый: чтобы понять «больше лимита», берём не больше limit+1 штук
            if limit:
                head = list(itertools.islice(iter(raw.get("entries") or []), limit + 1))
                if len(head) > limit:
                    raise _too_many(limit)
                raw["entries"] = head
            info = raw
        else:
            info = ydl.process_ie_result(raw, download=False)
    if _is_playlist(info):
        entries = info.get("entries")
        if limit and isinstance(entries, list) and len(entries) > limit:
            raise _too_many(limit)
    else:
        info = _review(ydl, info, req, send)
        _check_space(info, work_dir, req.get("library_dir"), req["mode"])
    send(type="phase", phase="download")
    section = None if _is_playlist(info) or not req.get("allow_section", True) else section_for(req.get("clip"), info)
    piece = _download_section(ydl, info, req, send, section, work_dir) if section else None
    offsets: dict[str, float] = {}
    if piece is not None:
        info, offsets = piece
    elif fallback and not _is_playlist(info):
        section = None
        info = _download_resilient(ydl, info, req, send)
    else:
        section = None
        info = ydl.process_ie_result(info, download=True)

    entries = [e for e in info.get("entries") or [] if e] if info.get("_type") == "playlist" else [info]
    items = []
    for entry in entries:
        for requested in entry.get("requested_downloads") or []:
            path = Path(requested.get("filepath") or "")
            if path.is_file():
                thumb = _thumbnail_of(entry, work_dir)
                items.append({"path": str(path), "info": _slim(entry), "thumbnail": str(thumb) if thumb else None,
                              "section": list(section) if section else None, "offset": offsets.get(str(path), 0.0)})
    if not items:
        raise RuntimeError(errors[-1] if errors else "По ссылке не нашлось видео")
    return items


# --- отрезок куском ----------------------------------------------------------------------


def section_for(clip, info: dict) -> tuple[float, float] | None:
    """Кусок, который выгодно скачать отдельно, или None — качать ролик целиком."""
    duration = info.get("duration")
    if not clip or clip[1] is None or not duration or info.get("is_live") or SECTION_MAX <= 0:
        return None
    start, end = float(clip[0]), min(float(clip[1]), float(duration))
    length = end - start
    if length <= 0 or length > SECTION_MAX or duration < max(SECTION_MIN_VIDEO, length * 4):
        return None
    return start, end


def _download_section(
    ydl, info: dict, req: dict, send, section: tuple[float, float], work_dir: Path
) -> tuple[dict, dict[str, float]] | None:
    """Скачать только кусок [start, end]: (info, {файл: где в нём начинается кусок}). None — не вышло (всё
    недокачанное убрано), вызывающий качает целиком.

    ffmpeg без перекодирования начинает каждую дорожку раньше куска (видео — с ключевого кадра, звук YouTube —
    с начала своего фрагмента), но заканчивает ровно на конце: кусок — последние (end − start) секунд файла.
    Проверено сверкой звука и кадров с роликом, скачанным целиком (docs/cli/MATRIX.md)."""
    from yt_dlp.utils import download_range_func

    start, end = section
    before = set(work_dir.iterdir())
    send(type="stage", text="Скачиваю отрезок")
    ydl.params["download_ranges"] = download_range_func(None, [(start, end)])
    ydl.params["force_keyframes_at_cuts"] = False  # точный рез сделает media.py при конвертации
    reason = "кусок получился неполным"
    try:
        fresh = {k: v for k, v in info.items() if k != "requested_downloads"}
        result = ydl.process_ie_result(fresh, download=True)
        files = [Path(r.get("filepath") or "") for r in result.get("requested_downloads") or []]
        offsets = {}
        for f in files:
            lead = _duration_of(f, req) - (end - start) if f.is_file() else -1.0
            if not -0.5 <= lead <= 60:  # короче куска или непонятно длиннее — не рискуем
                offsets = {}
                break
            offsets[str(f)] = max(0.0, lead)
        if files and offsets:
            return result, offsets
    except (NoSpace, _Permanent):
        raise
    except Exception as exc:  # noqa: BLE001 — любой сбой куска лечится загрузкой целиком
        reason = str(exc)[:300]
    finally:
        ydl.params.pop("download_ranges", None)
    print(f"section {start}-{end} failed ({reason}); downloading the whole video", file=sys.stderr)
    for path in set(work_dir.iterdir()) - before:  # иначе yt-dlp принял бы кусок за уже скачанный ролик
        if path.is_file() and path.suffix.lower() not in IMAGE_EXTS and path.name != "cookies.txt":
            path.unlink(missing_ok=True)
    send(type="note", text="Отрезок отдельно не скачался — качаю ролик целиком и вырежу сам")
    send(type="phase", phase="download")
    return None


def _duration_of(path: Path, req: dict) -> float:
    """Длительность файла по ffprobe (рядом с ffmpeg); не узнать — -1."""
    ffmpeg = req.get("ffmpeg") or shutil.which("ffmpeg")
    ffprobe = str(Path(ffmpeg).with_name(Path(ffmpeg).name.replace("ffmpeg", "ffprobe"))) if ffmpeg else None
    if not ffprobe or not Path(ffprobe).is_file():
        ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return -1.0  # без ffprobe не проверить, где кусок в файле, — качаем целиком
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60, check=False,
        )  # fmt: skip
        return float(out.stdout.strip() or -1)
    except (OSError, subprocess.SubprocessError, ValueError):
        return -1.0


# --- «не выходит — вот другой вариант» ---------------------------------------------------

# Ошибки конкретного файла-формата (а не сети): имеет смысл взять другой формат того же ролика
FORMAT_PROBLEMS = (
    "http error 403", "403: forbidden", "http error 404", "http error 410", "http error 416",
    "requested format is not available", "did not get any data blocks",
    "drm protected",  # у Vimeo бывает часть форматов под DRM, часть — без
)  # fmt: skip
CANCEL = {"id": "cancel", "label": "Отмена"}


def _has_video(f: dict) -> bool:
    return f.get("vcodec") != "none" and bool(f.get("height") or f.get("vcodec"))


def _chosen(info: dict) -> dict:
    """Что именно выбрано для загрузки: качество, есть ли видео/звук, водяной знак."""
    parts = info.get("requested_formats") or [info]
    video = next((f for f in parts if _has_video(f)), None)
    audio = next((f for f in parts if f.get("acodec") != "none"), None)
    side = 0
    if video and video.get("height"):
        side = min(video["height"], video.get("width") or video["height"])
    return {
        "ids": [f.get("format_id") for f in parts if f.get("format_id")],
        "video": video is not None,
        "audio": audio is not None,
        "side": side,
        "abr": (audio or {}).get("abr") or 0,
        "watermarked": any("watermark" in (f.get("format_note") or "").lower() for f in parts),
    }


def _describe(c: dict) -> str:
    if c["video"]:
        quality = f"{c['side']}p" if c["side"] else "видео"
        return quality if c["audio"] else f"{quality} без звука"
    return f"звук {round(c['abr'])} кбит/с" if c["abr"] else "только звук"


def _worse(alt: dict, old: dict) -> bool:
    return bool(
        (old["video"] and not alt["video"])
        or (old["audio"] and not alt["audio"])
        or (alt["side"] and old["side"] and alt["side"] < old["side"] * 0.9)
        or (alt["watermarked"] and not old["watermarked"])
        or (not old["video"] and alt["abr"] and old["abr"] and alt["abr"] < old["abr"] * 0.7)
    )


def _exclude(spec: str, ids: list[str]) -> str:
    """Та же строка -f, но без уже не сработавших форматов."""
    extra = "".join(f"[format_id!='{i}']" for i in ids if i)
    return "/".join("+".join(atom + extra for atom in alt.split("+")) for alt in spec.split("/"))


def _reselect(ydl, info: dict, spec: str, sort: list[str] | None = None) -> dict:
    ydl.params["format"] = spec
    if sort is not None:
        ydl.params["format_sort"] = sort
    ydl.format_selector = ydl.build_format_selector(spec)
    fresh = {k: v for k, v in info.items() if k not in ("requested_formats", "requested_downloads")}
    return ydl.process_ie_result(fresh, download=False)


def _ask(send, code: str, title: str, message: str, options: list[dict]) -> str:
    """Задать вопрос через протокол и ждать ответ на stdin. Нет ответа или «Отмена» — задача отменяется."""
    default = next(o["id"] for o in options if o.get("primary"))
    send(
        type="question",
        question={
            "id": uuid.uuid4().hex[:8], "code": code, "title": title, "message": message,
            "options": options, "default": default,
        },
    )  # fmt: skip
    line = sys.stdin.readline()
    try:
        answer = (json.loads(line) or {}).get("answer") if line.strip() else None
    except ValueError:
        answer = None
    if answer not in {o["id"] for o in options} or answer == "cancel":
        raise _Permanent("Отменено")
    return answer


def _review(ydl, info: dict, req: dict, send) -> dict:
    """До загрузки: если нужного варианта нет — сказать об этом и предложить замену."""
    from .timecode import format_time

    if info.get("is_live") or info.get("live_status") == "is_live":
        # yt-dlp писал бы эфир, пока тот не кончится (у круглосуточных — никогда): задача висела бы вечно
        raise _Permanent("Это прямой эфир — его можно будет скачать, когда трансляция закончится")
    formats = info.get("formats") or []
    chosen = _chosen(info)
    mode, quality = req["mode"], req["quality"]
    if mode in ("mp4", "both") and not chosen["video"] and not any(_has_video(f) for f in formats):
        _ask(send, "no_video", "У этой ссылки нет видео", "Здесь только звук — могу сохранить его в MP3.",
             [{"id": "mp3", "label": "Скачать MP3", "primary": True}, CANCEL])  # fmt: skip
        req["mode"] = "mp3"
        send(type="mode", mode="mp3")
        info = _reselect(ydl, info, *format_spec("mp3", quality))
        chosen = _chosen(info)
    elif mode == "mp3" and not chosen["audio"] and not any(f.get("acodec") != "none" for f in formats):
        _ask(send, "no_audio", "В ролике нет звука", "MP3 сделать не из чего — могу сохранить само видео.",
             [{"id": "mp4", "label": "Скачать видео (MP4)", "primary": True}, CANCEL])  # fmt: skip
        req["mode"] = "mp4"
        send(type="mode", mode="mp4")
        info = _reselect(ydl, info, *format_spec("mp4", quality))
        chosen = _chosen(info)

    if chosen["watermarked"]:
        _ask(send, "watermarked", "Версии без водяного знака нет",
             "Сайт сейчас отдаёт этот ролик только с водяным знаком.",
             [{"id": "accept", "label": "Скачать с водяным знаком", "primary": True}, CANCEL])  # fmt: skip

    if req["mode"] != "mp3" and quality != "max" and chosen["video"] and 0 < chosen["side"] < int(quality):
        best = max((min(f["height"], f.get("width") or f["height"]) for f in formats
                    if _has_video(f) and f.get("height")), default=0)  # fmt: skip
        if best < int(quality):
            send(type="note", text=f"{quality}p у ролика нет — качаю в лучшем доступном: {chosen['side']}p")
    elif req["mode"] != "mp3" and quality != "max" and chosen["video"] and chosen["side"] > int(quality) * 1.1:
        smallest = min((min(f["height"], f.get("width") or f["height"]) for f in formats
                        if _has_video(f) and f.get("height")), default=0)  # fmt: skip
        if smallest > int(quality):
            send(type="note", text=f"{quality}p у ролика нет, меньше {smallest}p не бывает — сохраняю {chosen['side']}p")

    clip, duration = req.get("clip"), info.get("duration")
    if clip and duration:
        start, end = clip
        if start >= duration:
            _ask(send, "clip_beyond", "Отрезок за пределами ролика",
                 f"Ролик длится {format_time(duration)}, а отрезок начинается с {format_time(start)}.",
                 [{"id": "whole", "label": "Скачать ролик целиком", "primary": True}, CANCEL])  # fmt: skip
            send(type="clip", clip=None)
            req["clip"] = None
        elif end is not None and end > duration + 1:
            send(type="note", text=f"Ролик короче отрезка — сохраню до конца ({format_time(duration)})")
    return info


def _why(exc: Exception) -> str:
    if "drm" in str(exc).lower():
        return "защита DRM"
    code = re.search(r"http error (\d{3})", str(exc).lower())
    return f"ошибка {code.group(1)}" if code else "сайт не отдал файл"


def _download_resilient(ydl, info: dict, req: dict, send) -> dict:
    """Качает выбранный вариант; если сайт не отдаёт именно этот формат — берёт следующий.
    Равноценную замену — молча (с пометкой), заметно худшую — только с согласия."""
    spec, tried = ydl.params["format"], []
    for round_ in range(4):
        try:
            return ydl.process_ie_result(info, download=True)
        except Exception as exc:  # noqa: BLE001 — DownloadError и его родня
            if round_ == 3 or not any(p in str(exc).lower() for p in FORMAT_PROBLEMS):
                raise
            failed = _chosen(info)
            tried += failed["ids"]
            print(f"format {failed['ids']} failed ({exc}); trying another", file=sys.stderr)
            try:
                alt_info = _reselect(ydl, info, _exclude(spec, tried))
            except Exception as none_left:  # noqa: BLE001
                if "drm" in str(exc).lower():
                    raise _Permanent("Видео защищено DRM — скачать его нельзя.") from none_left
                raise _Permanent(f"Сайт не отдаёт этот ролик ({_why(exc)}), других вариантов нет. "
                                 "Попробуйте позже или обновите yt-dlp: выдра обновить") from none_left  # fmt: skip
            alt = _chosen(alt_info)
            if _worse(alt, failed):
                _ask(send, "format_failed", "Этот вариант не скачивается",
                     f"{_describe(failed).capitalize()} скачать не получилось ({_why(exc)}). "
                     f"Есть другой вариант: {_describe(alt)}.",
                     [{"id": "accept", "label": f"Скачать {_describe(alt)}", "primary": True}, CANCEL])  # fmt: skip
            else:
                send(type="note", text=f"{_describe(failed).capitalize()} не скачался ({_why(exc)}) — взял другой источник того же качества")  # noqa: E501
            send(type="phase", phase="download")
            info = alt_info
    return info


def estimate_size(info: dict) -> int:
    """Оценка объёма загрузки по форматам, которые выбрал yt-dlp (0 — неизвестно)."""
    entries = [e for e in info.get("entries") or [] if e] if info.get("_type") == "playlist" else [info]
    total = 0
    for entry in entries:
        for f in entry.get("requested_formats") or [entry]:
            size = f.get("filesize") or f.get("filesize_approx")
            if not size and f.get("tbr") and entry.get("duration"):
                size = f["tbr"] * 1000 / 8 * entry["duration"]
            total += int(size or 0)
    return total


def space_needed(estimate: int, mode: str) -> tuple[int, int]:
    """(нужно во временной папке, нужно в хранилище). Временно: части + склейка + результат."""
    work = int(estimate * 2.2) + SPACE_MARGIN
    library = int(estimate * (1.4 if mode != "mp3" else 0.3)) + SPACE_MARGIN
    return work, library


def check_free(requirements: list[tuple[Path, int]]) -> None:
    """Бросает NoSpace, если на каком-то диске не хватает места (одинаковые диски суммируются)."""
    per_device: dict[int, tuple[Path, int]] = {}
    for path, need in requirements:
        probe = path
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            device = probe.stat().st_dev
        except OSError:
            continue
        prev = per_device.get(device)
        per_device[device] = (probe, need + (prev[1] if prev else 0))
    for probe, need in per_device.values():
        free = shutil.disk_usage(probe).free
        if free < need:
            raise NoSpace(
                f"Не хватает места на диске: нужно около {_human(need)}, свободно {_human(free)} ({probe})"
            )


def _check_space(info: dict, work_dir: Path, library_dir: str | None, mode: str) -> None:
    estimate = estimate_size(info)
    if not estimate:
        return
    work, library = space_needed(estimate, mode)
    requirements = [(work_dir, work)]
    if library_dir:
        requirements.append((Path(library_dir), library))
    check_free(requirements)


def _human(num: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if num < 1024 or unit == "ТБ":
            return f"{num:.1f} {unit}".replace(".", ",")
        num /= 1024
    return str(num)


def _slim(info: dict) -> dict:
    slim = {k: info.get(k) for k in INFO_KEYS}
    if slim.get("description"):
        slim["description"] = slim["description"][:500]
    return slim


def _overall_percent(d: dict, info: dict) -> float | None:
    """Видео и звук качаются по очереди — склеиваем в один общий процент."""
    done = d.get("downloaded_bytes") or 0
    total = d.get("total_bytes") or d.get("total_bytes_estimate")
    part = done / total * 100 if total else None
    if d.get("status") == "finished":
        part = 100.0
    parts = info.get("requested_formats") or []
    if len(parts) < 2:
        return part
    index = next((i for i, f in enumerate(parts) if f.get("format_id") == info.get("format_id")), 0)
    sizes = [f.get("filesize") or f.get("filesize_approx") or 0 for f in parts]
    if all(sizes) and part is not None and d.get("status") != "finished":
        return min(100.0, (sum(sizes[:index]) + done) / sum(sizes) * 100)
    return (index + (part or 0) / 100) / len(parts) * 100


def _thumbnail_of(entry: dict, work_dir: Path) -> Path | None:
    for thumb in reversed(entry.get("thumbnails") or []):
        path = thumb.get("filepath")
        if path and Path(path).is_file():
            return Path(path)
    return _find_thumbnail(work_dir, entry.get("id"))


def _find_thumbnail(work_dir: Path, video_id) -> Path | None:
    if not video_id:
        return None
    escaped = re.sub(r"([*?\[])", r"[\1]", str(video_id))
    for path in work_dir.glob(f"{escaped}.*"):
        if path.suffix.lower() in IMAGE_EXTS:
            return path
    return None


if __name__ == "__main__":
    _worker()
