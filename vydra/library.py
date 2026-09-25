"""Офлайн-хранилище и «проводник» по нему.

<папка>/
  YouTube/  TikTok/  Instagram/  Другие сайты/  Мои файлы/     отделы по соцсетям (системные папки)
    Видео/  Аудио/                                           тоже системные; внутри — любые свои папки
  .vydra/             скрытая служебная папка: индекс и постеры

Индекс (.vydra/library.json) лежит в самой папке, поэтому хранилище переносимо. Изменения, сделанные
в Проводнике/Finder, подхватываются наблюдателем (опрос mtime папок: на /mnt/c из WSL inotify не видит
действий Windows-программ). Правки индекса — под файловой блокировкой: сервер и консоль работают вместе.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import queue
import shutil
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from . import system
from .config import Prefs
from .fsutil import FileLock, atomic_write
from .media import Media, MediaError
from .naming import save_unique

log = logging.getLogger("library")

SERVICE = ".vydra"
LEGACY_CINEMA = "Кинотеатр.html"  # офлайн-кинотеатр первых версий — больше не создаётся
LEGACY_SERVICE_FILES = ("cinema.css", "cinema.js", "library.js")
LAYOUT_VERSION = 2
TYPE_DIRS = {"video": "Видео", "audio": "Аудио"}
PLATFORM_DIRS = {
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "other": "Другие сайты",
    "file": "Мои файлы",
}
FOLDER_PLATFORM = {v.casefold(): k for k, v in PLATFORM_DIRS.items()}
SYSTEM_FOLDERS = {p.casefold() for p in PLATFORM_DIRS.values()} | {
    f"{p}/{t}".casefold() for p in PLATFORM_DIRS.values() for t in TYPE_DIRS.values()
}
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".opus", ".ogg", ".wav", ".flac"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS
FULL_RESCAN = 1800.0  # раз в полчаса наблюдатель сверяет хранилище целиком
ENRICH_FLUSH = 10.0  # результаты фоновой обработки пишутся в индекс не чаще, чем раз в столько секунд
STALE_PARTIAL = 600  # .part и пустые заготовки старше 10 минут без изменений — мусор после сбоя

_FORBIDDEN = set('<>:"/\\|?*')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


@dataclass
class Item:
    id: str
    path: str  # относительный POSIX-путь от корня хранилища
    type: str  # video | audio
    title: str
    platform: str  # метаданные: откуда скачано (не меняется при перемещении файла)
    size: int
    added: float
    source: str | None = None
    uploader: str | None = None
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    poster: str | None = None  # .vydra/posters/<id>.jpg
    clip: list | None = None  # [начало, конец|None], если это отрезок
    probed: bool = False  # метаданные файла уже прочитаны (ссылку на оригинал и название второй раз не ищем)

    @classmethod
    def from_dict(cls, data: dict) -> Item:
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in fields})

    @property
    def folder(self) -> str:
        return self.path.rpartition("/")[0]

    @property
    def name(self) -> str:
        return self.path.rpartition("/")[2]

    def public(self) -> dict:
        # вручную, а не asdict: тот копирует вглубь и на 50 000 файлов занимает секунды
        data = {name: getattr(self, name) for name in _ITEM_FIELDS}
        if self.clip is not None:
            data["clip"] = list(self.clip)
        data["folder"], data["name"] = self.folder, self.name
        return data


_ITEM_FIELDS = tuple(Item.__dataclass_fields__)
SORTS = ("added", "name", "size", "duration")


def kind_of(path: Path | str) -> str:
    return "video" if Path(path).suffix.lower() in VIDEO_EXTS else "audio"


class LibraryUnavailable(OSError):
    """Папка хранилища недоступна: диск отключён или родительскую папку удалили."""

    def __init__(self, root: Path):
        super().__init__(errno.ENOENT, f"Папка хранилища недоступна: {system.display_path(root)}")


class FsError(Exception):
    """Ошибка проводника; status — HTTP-код для API."""

    status = 400


class InvalidName(FsError):
    status = 400


class NotFound(FsError):
    status = 404


class Protected(FsError):
    status = 403


class Conflict(FsError):
    status = 409


# --- проверка имён и путей --------------------------------------------------------------


def validate_name(name: str) -> str:
    """Имя файла/папки, допустимое сразу в Windows, macOS и Linux."""
    name = unicodedata.normalize("NFC", (name or "").strip())
    if not name:
        raise InvalidName("Введите название")
    if name in (".", "..") or name.startswith("."):
        raise InvalidName("Название не может начинаться с точки")
    bad = sorted({ch for ch in name if ch in _FORBIDDEN})
    if bad:
        raise InvalidName("Нельзя использовать символы " + " ".join(bad))
    if any(unicodedata.category(ch) == "Cc" for ch in name):
        raise InvalidName("В названии есть управляющие символы")
    if name.endswith((".", " ")):
        raise InvalidName("Название не может заканчиваться точкой или пробелом")
    if name.split(".")[0].upper().strip() in _RESERVED:
        raise InvalidName(f"«{name}» — зарезервированное имя в Windows")
    if len(name) > 120 or len(name.encode("utf-8")) > 200:
        raise InvalidName("Слишком длинное название (до 120 символов)")
    return name


def validate_rel(rel: str | None) -> str:
    """Относительный путь внутри хранилища: «» — корень; без .., без скрытых и служебных папок."""
    raw = (rel or "").strip()
    if "\\" in raw or "\x00" in raw:
        raise InvalidName("Недопустимый путь")
    raw = raw.strip("/")
    if not raw:
        return ""
    parts = raw.split("/")
    for part in parts:
        if part in ("", ".", "..") or part.startswith("."):
            raise InvalidName("Недопустимый путь")
    return "/".join(unicodedata.normalize("NFC", p) for p in parts)


def is_system(rel: str) -> bool:
    return rel == "" or rel.casefold() in SYSTEM_FOLDERS


def _platform_from_path(rel: str) -> str:
    """Платформа файла, про который ничего не известно: по отделу верхнего уровня, иначе «Мои файлы»."""
    parts = PurePosixPath(rel).parts
    if len(parts) >= 2:
        return FOLDER_PLATFORM.get(parts[0].casefold(), "file")
    return "file"


class Library:
    def __init__(self, prefs: Prefs, media: Media, enrich: bool = True):
        """enrich=False — без фоновых постеров и длительностей (консоль: она живёт секунды, а ffmpeg,
        запущенный фоном, пережил бы её сиротой; постеры доделает сервер при следующем запуске)."""
        self.prefs = prefs
        self.media = media
        self._lock = threading.RLock()
        self._depth = 0  # вложенность транзакций: файловая блокировка берётся только снаружи
        self._dirty = False
        self._items: dict[str, Item] = {}
        self._loaded_root: Path | None = None
        self._index_mtime: int | None = None
        self._last_scan = 0.0
        self._poster_tried: set[str] = set()
        self._queue: queue.Queue[str] = queue.Queue()
        self._queued: set[str] = set()  # уже в очереди фоновой обработки — второй раз не ставим
        self._attempted: set[str] = set()  # уже обработаны в этом процессе (или ffprobe не смог) — не повторяем
        self._enrich_dirty = False  # результаты фоновой обработки ждут записи в индекс (пачкой)
        self._mutations = 0  # растёт при любом изменении индекса в памяти — ключ кэша представлений
        self._cache: _View | None = None
        self.rev = int(time.time() * 1000)  # растёт при любом изменении хранилища (SSE, кинотеатр)
        self._watching = threading.Event()
        self._idle = threading.Event()  # снят, пока хранилище переезжает в другую папку
        self._idle.set()
        self._moving = threading.Lock()  # два переноса сразу — нельзя
        if enrich:
            threading.Thread(target=self._enrich_loop, name="library-enrich", daemon=True).start()

    # --- пути ----------------------------------------------------------------------------

    @property
    def root(self) -> Path:
        return self.prefs.library_dir

    @property
    def service_dir(self) -> Path:
        return self.root / SERVICE

    def abs_path(self, item: Item) -> Path:
        return self.root / item.path

    def available(self) -> bool:
        """Папка есть — или её можно создать. Отключённый диск (папка вне домашней, и нет даже
        её родителя) — нет: иначе создали бы пустое хранилище вместо подключаемого."""
        root = self.root
        if root.is_dir() or root.parent.is_dir():
            return True
        home = Path.home()
        try:
            return home.is_dir() and root.resolve().is_relative_to(home.resolve())
        except OSError:
            return False

    def resolve(self, rel: str) -> Path | None:
        """Файл внутри хранилища по относительному пути (для /lib/…); выход за корень — None."""
        root = self.root.resolve()
        path = (root / rel).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return None
        return path

    def _dir(self, rel: str | None) -> Path:
        """Папка проводника: внутри хранилища и существует."""
        rel = validate_rel(rel)
        root = self.root
        if not root.is_dir():
            raise NotFound("Папка хранилища недоступна")
        path = root / rel if rel else root
        if not path.resolve().is_relative_to(root.resolve()):
            raise InvalidName("Недопустимый путь")
        if not path.is_dir():
            raise NotFound("Папка не найдена — возможно, её переименовали или удалили")
        return path

    def folder_exists(self, rel: str | None) -> bool:
        try:
            self._dir(rel or "")
            return True
        except FsError:
            return False

    def _bump(self) -> None:
        self.rev = max(self.rev + 1, int(time.time() * 1000))
        self._mutations += 1

    # --- транзакции ----------------------------------------------------------------------

    @contextmanager
    def _txn(self):
        """Чтение-изменение-запись индекса под замком потоков и под файловой блокировкой между
        процессами (сервер и консоль). Индекс перечитывается, если его переписал другой процесс."""
        with self._lock:
            outer = self._depth == 0
            lock = FileLock(self.root / SERVICE / ".lock") if outer and self.root.is_dir() else None
            if lock:
                lock.__enter__()
            self._depth += 1
            try:
                self._ensure_loaded()
                yield
                if outer and self._dirty:
                    self._write_index()
            finally:
                self._depth -= 1
                if lock:
                    lock.__exit__(None, None, None)

    # --- структура папки -----------------------------------------------------------------

    def ensure_layout(self) -> None:
        root = self.root
        if not self.available():
            raise LibraryUnavailable(root)
        fresh_service = not (root / SERVICE).exists()
        root.mkdir(parents=True, exist_ok=True)
        (root / SERVICE / "posters").mkdir(parents=True, exist_ok=True)
        if fresh_service:
            self._hide(root / SERVICE)
        self._migrate_layout()
        for platform_dir in PLATFORM_DIRS.values():
            for type_dir in TYPE_DIRS.values():
                (root / platform_dir / type_dir).mkdir(parents=True, exist_ok=True)
        self._remove_legacy_cinema()
        self.sweep_partials()
        with self._txn():
            if not (root / SERVICE / "library.json").is_file():
                self._dirty = True  # пустой индекс — чтобы доктор видел, что хранилище создано
        self._bump()

    def _remove_legacy_cinema(self) -> None:
        """Убирает офлайн-кинотеатр, который создавали первые версии. Удаляется только наш
        сгенерированный файл (узнаём по ссылкам на .vydra/cinema.js и .vydra/library.js) —
        одноимённый файл пользователя не трогаем. Служебные файлы в .vydra — целиком наши."""
        root = self.root
        page = root / LEGACY_CINEMA
        try:
            if page.is_file() and page.stat().st_size < 256 * 1024:
                text = page.read_text(encoding="utf-8", errors="replace")
                if ".vydra/cinema.js" in text and ".vydra/library.js" in text:
                    page.unlink()
        except OSError as exc:
            log.warning("не удалось убрать старый кинотеатр: %s", exc)
        service = root / SERVICE
        for name in LEGACY_SERVICE_FILES:
            try:
                (service / name).unlink(missing_ok=True)
            except OSError:
                pass
        shutil.rmtree(service / "fonts", ignore_errors=True)

    def _hide(self, path: Path) -> None:
        try:
            system.hide(path)
        except Exception:  # noqa: BLE001 — не скрылась, и ладно
            pass

    def _migrate_layout(self) -> None:
        """Видео/<П>/… → <П>/Видео/…, Аудио/<П>/… → <П>/Аудио/…

        Безопасно: только переименования внутри одного тома, ни один файл не удаляется, пути в индексе
        и id сохраняются. Идемпотентно и переживает прерывание: метка версии пишется в самом конце,
        при повторном запуске доделывается остаток, а индекс чинится для уже перенесённых файлов."""
        root = self.root
        marker = root / SERVICE / "layout.json"
        try:
            version = json.loads(marker.read_text(encoding="utf-8")).get("version", 1)
        except (OSError, ValueError, AttributeError):
            version = 1
        if version >= LAYOUT_VERSION:
            return
        old_roots = [root / t for t in TYPE_DIRS.values() if (root / t).is_dir()]
        if old_roots:
            log.info("перенос хранилища на новую раскладку (отделы по платформам)")
            with self._txn():
                by_path = {i.path: i for i in self._items.values()}
                for old_root in old_roots:
                    for dirpath, dirnames, filenames in os.walk(old_root):
                        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                        for name in filenames:
                            src = Path(dirpath) / name
                            old_rel = src.relative_to(root).as_posix()
                            new_rel = _migrated_path(old_rel)
                            if new_rel is None:
                                continue
                            dst = _unique_variant(root / new_rel)
                            try:
                                dst.parent.mkdir(parents=True, exist_ok=True)
                                os.rename(src, dst)
                            except OSError as exc:
                                log.warning("не удалось перенести %s: %s", src, exc)
                                continue
                            if old_rel in by_path:
                                by_path[old_rel].path = dst.relative_to(root).as_posix()
                                self._dirty = True
                # прерванный прошлый запуск: файл уже перенесён, а индекс помнит старый путь
                for item in self._items.values():
                    if (root / item.path).exists():
                        continue
                    new_rel = _migrated_path(item.path)
                    if new_rel and (root / new_rel).exists():
                        item.path = new_rel
                        self._dirty = True
                for old_root in old_roots:
                    _remove_empty_dirs(old_root)
        atomic_write(marker, json.dumps({"version": LAYOUT_VERSION}))
        self._bump()

    def sweep_partials(self, max_age: float = STALE_PARTIAL) -> int:
        """Убирает хвосты оборванных копирований: *.part и пустые заготовки имён без свежего .part."""
        root = self.root
        if not root.is_dir():
            return 0
        now = time.time()
        removed = 0
        exts = tuple(MEDIA_EXTS)
        stack = [str(root)]
        while stack:  # на строках и scandir: хранилище бывает на 50 000 файлов
            try:
                entries = list(os.scandir(stack.pop()))
            except OSError:
                continue
            names = {e.name for e in entries}
            for entry in entries:
                name = entry.name
                if name.startswith("."):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                        continue
                    stale_part = name.endswith(".part")
                    if not stale_part and not name.lower().endswith(exts):
                        continue
                    st = entry.stat()
                    if now - st.st_mtime < max_age:
                        continue
                    stale_placeholder = (
                        not stale_part and st.st_size == 0
                        and not (name + ".part" in names and _fresh(Path(entry.path + ".part"), max_age))
                    )  # fmt: skip
                    if stale_part or stale_placeholder:
                        os.unlink(entry.path)
                        removed += 1
                except OSError:
                    continue
        return removed

    def set_root(self, path: Path) -> None:
        path = path.expanduser()
        if path.exists() and not path.is_dir():
            raise ValueError("По этому пути лежит файл, а нужна папка")
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / f".vydra-write-test-{uuid.uuid4().hex[:6]}"
            probe.write_bytes(b"ok")
            probe.unlink()
        except OSError as exc:
            raise ValueError(f"В эту папку нельзя записывать: {exc.strerror or exc}") from exc
        with self._lock:
            previous = self.root
            self.prefs.set_library_dir(path)
            if not _same_path(previous, path):
                self.prefs.set_previous_library_dir(previous)  # «перенести уже скачанное?» — откуда брать
            self._loaded_root = None
            self._poster_tried.clear()
        self.ensure_layout()
        self.scan(force=True)

    def move_root(
        self, new: Path, progress: Callable[[int, int, str], None] | None = None, source: Path | None = None
    ) -> dict:
        """Переносит хранилище в другую папку и переключается на неё.

        Переносится то, что принадлежит хранилищу: отделы платформ, папки с медиафайлами (и пустые
        папки, созданные в проводнике), медиафайлы из корня и служебная .vydra (индекс, постеры).
        Посторонние файлы и папки без медиа остаются на месте — это важно, если хранилищем был,
        например, весь «Загрузки». На одном диске — мгновенное переименование, между дисками —
        копия во временный .part, сверка размера, переименование и только потом удаление исходника.
        Совпадения имён — « (2)». Прерванный перенос продолжается повторным вызовом: журнал
        переименований пишется в <новая папка>/.vydra/move-journal.txt по мере работы.

        source — перенести не текущее хранилище, а прежнее (интерфейс сначала меняет папку, а потом
        спрашивает «перенести уже скачанное?»): содержимое source сливается в new, которая уже текущая."""
        if self.prefs.settings.fixed_library:
            raise ValueError("Папка задана переменной окружения VD_LIBRARY_DIR — перенос выключен")
        old = (source or self.root).expanduser()
        new = new.expanduser()
        if not old.is_dir():
            raise LibraryUnavailable(old)
        if _same_path(old, new):
            raise ValueError("Это и есть текущая папка хранилища" if source is None else "Переносить нечего: это та же папка")
        if _inside(new, old):
            raise ValueError("Новая папка находится внутри текущего хранилища — выберите другую")
        try:
            new.mkdir(parents=True, exist_ok=True)
            probe = new / f".vydra-write-test-{uuid.uuid4().hex[:6]}"
            probe.write_bytes(b"ok")
            probe.unlink()
        except OSError as exc:
            raise ValueError(f"В эту папку нельзя записывать: {exc.strerror or exc}") from exc

        if not self._moving.acquire(blocking=False):
            raise ValueError("Перенос уже идёт — дождитесь его окончания")
        self._idle.clear()
        try:
            with self._lock:
                if source is None:
                    self._ensure_loaded()
                    old_items = [Item.from_dict(asdict(i)) for i in self._items.values()]
                else:  # индекс прежней папки — со своими id, постерами и отрезками
                    old_items = _read_index(old / SERVICE / "library.json") or []
            journal_path = new / SERVICE / "move-journal.txt"
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            entries = _library_entries(old, old_items)
            total = sum(_tree_size(e) for e in entries)
            state = {"done": 0}
            renamed: dict[str, str] = _read_journal(journal_path)

            def report(name: str) -> None:
                if progress:
                    progress(state["done"], total, name)

            with journal_path.open("a", encoding="utf-8") as journal:

                def moved_file(src: Path, dst: Path, size: int) -> None:
                    state["done"] += size
                    old_rel, new_rel = src.relative_to(old).as_posix(), dst.relative_to(new).as_posix()
                    if old_rel != new_rel:
                        renamed[old_rel] = new_rel
                        journal.write(f"{old_rel}\t{new_rel}\n")
                        journal.flush()
                    report(new_rel)

                for entry in entries:
                    _move_entry(entry, new / entry.name, moved_file, report)
                _move_posters(old / SERVICE / "posters", new / SERVICE / "posters")

            with self._lock:
                self.prefs.set_library_dir(new)
                self._loaded_root = None
                self._poster_tried.clear()
                self._ensure_loaded()  # индекс новой папки (если там уже было хранилище)
                for item in old_items:
                    item.path = renamed.get(item.path, item.path)
                    if item.poster and not (new / item.poster).is_file():
                        item.poster = None
                    if item.id not in self._items:
                        self._items[item.id] = item
                self._dirty = True
            self.ensure_layout()
            _cleanup_old_root(old)
            journal_path.unlink(missing_ok=True)
            self.prefs.set_previous_library_dir(None)
        finally:
            self._idle.set()
            self._moving.release()
        changes = self.scan(force=True)
        self._bump()
        return {"bytes": state["done"], "total": total, "renamed": len(renamed), "added": changes.get("added", 0)}

    # --- индекс --------------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Перечитывает индекс при смене папки или если его переписал другой процесс.
        Битый library.json восстанавливается из library.json.bak, а не начинается с нуля."""
        root = self.root
        index = root / SERVICE / "library.json"
        try:
            mtime = index.stat().st_mtime_ns
        except OSError:
            mtime = None
        if self._loaded_root == root and mtime == self._index_mtime:
            return
        items = _read_index(index)
        if items is None:
            items = _read_index(index.with_name("library.json.bak"))
            if items is not None:
                log.warning("library.json повреждён — восстановлен из library.json.bak")
                self._dirty = True
        reload = self._loaded_root is not None
        self._items = {i.id: i for i in items or []}
        self._index_mtime = mtime
        self._loaded_root = root
        if reload:
            self._bump()

    def _write_index(self) -> None:
        if not self.root.is_dir():
            raise LibraryUnavailable(self.root)
        service = self.root / SERVICE
        service.mkdir(parents=True, exist_ok=True)
        self._mutations += 1
        view = self._view()
        items = view.ordered
        payload = {
            "version": 1,
            "generated": time.time(),
            "root": system.display_path(self.root),
            "items": items,
        }
        text = json.dumps(payload, ensure_ascii=False)
        atomic_write(service / "library.json", text, backup=True)
        try:
            self._index_mtime = (service / "library.json").stat().st_mtime_ns
        except OSError:
            self._index_mtime = None
        self._dirty = False
        self._bump()
        view.key, view.encoded = (self.rev, id(self._items), self._mutations), None  # то же содержимое — не строим заново

    def items(self) -> list[dict]:
        """Все файлы, новые сверху (копия списка; словари общие — не менять)."""
        self._refresh()
        return list(self._view().ordered)

    def _refresh(self) -> None:
        """Сверить индекс с диском перед чтением — если за папкой не следит наблюдатель (консоль).
        У сервера наблюдатель пересканирует сам при любом изменении: полный обход 50 000 файлов
        на каждый запрос (как было) занимал десятки секунд."""
        if not self._watching.is_set():
            self.scan()

    def _view(self) -> _View:
        with self._lock:
            self._ensure_loaded()
            key = (self.rev, id(self._items), self._mutations)
            if self._cache is None or self._cache.key != key:
                self._cache = _View(key, list(self._items.values()))
            return self._cache

    def query(
        self,
        *,
        q: str | None = None,
        kind: str | None = None,
        folder: str | None = None,
        sort: str = "added",
        order: str | None = None,
        offset: int = 0,
        limit: int | None = None,
        refresh: bool = True,
    ) -> dict:
        """Постранично: поиск по названию и имени файла (все слова), тип, папка (только прямые файлы),
        сортировка added | name | size | duration (без длительности — в конце). По умолчанию новые сверху,
        по имени — от А до Я."""
        if refresh:
            self._refresh()
        view = self._view()
        items = view.sorted(sort, order)
        if folder is not None:
            key = validate_rel(folder).casefold()
            items = [i for i in items if i["folder"].casefold() == key]
        if kind:
            items = [i for i in items if i["type"] == kind]
        if q and (words := q.casefold().split()):
            hay = view.haystack
            items = [i for i in items if all(w in hay[i["id"]] for w in words)]
        total = len(items)
        offset = max(0, offset)
        page = items[offset : offset + limit] if limit else items[offset:]
        end = offset + len(page)
        return {"items": page, "total": total, "offset": offset, "limit": limit,
                "next_offset": end if end < total else None}  # fmt: skip

    def full_json(self) -> bytes:
        """Ответ /api/library без параметров (как раньше: весь список) — собирается один раз на ревизию."""
        self._refresh()
        view = self._view()
        if view.encoded is None:
            payload = {"root": system.display_path(self.root), "stats": view.stats, "items": view.ordered,
                       "rev": self.rev}  # fmt: skip
            view.encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        return view.encoded

    def get(self, item_id: str) -> Item | None:
        with self._lock:
            self._ensure_loaded()
            return self._items.get(item_id)

    def find(self, match: Callable[[Item], bool]) -> list[Item]:
        """Элементы индекса, подходящие под условие и реально лежащие на диске."""
        with self._lock:
            self._ensure_loaded()
            return [i for i in self._items.values() if match(i) and self.abs_path(i).is_file()]

    def stats(self) -> dict:
        return dict(self._view().stats)

    # --- синхронизация с диском ----------------------------------------------------------

    def scan(self, force: bool = False, dirs: set[str] | None = None) -> dict:
        """Сверяет индекс с папкой. Возвращает, что поменялось (для health-check).

        dirs — сверить только эти папки (их прямые файлы): наблюдатель знает, где что поменялось, и не обходит
        всё хранилище (на /mnt/c 10 000 файлов обходятся ~35 с). Недоступную папку (отключённый диск) не трогаем:
        иначе индекс решил бы, что все файлы удалены."""
        if not self._idle.is_set():
            return {}  # идёт переезд: исчезающие файлы — не повод чистить индекс
        with self._lock:
            if not force and time.monotonic() - self._last_scan < 3:
                return {}
            self._last_scan = time.monotonic()
            root = self.root
        if not root.is_dir():
            return {}
        # долгий обход диска — без блокировки: чтение API в это время не ждёт
        if dirs is None:
            on_disk = _walk_media(root)
        else:
            with self._lock:
                known = {i.path for i in self._items.values() if i.folder in dirs}
            on_disk = _media_in(root, dirs, known)
        with self._lock:
            if self.root != root:
                return {}
            with self._txn():
                by_path = {i.path: i for i in self._items.values()}
                scope = self._items.values() if dirs is None else [i for i in self._items.values() if i.folder in dirs]
                gone = [i for i in scope if i.path not in on_disk]
                fresh = [rel for rel in on_disk if rel not in by_path]
                moved = _match_moves(gone, fresh, on_disk)
                for item, rel in moved:
                    item.path = rel
                    fresh.remove(rel)
                    gone.remove(item)
                for item in gone:
                    self._drop(item)
                for rel in fresh:
                    st = on_disk[rel]
                    item = Item(
                        id=uuid.uuid4().hex[:12],
                        path=rel,
                        type=kind_of(rel),
                        title=Path(rel).stem,
                        platform=_platform_from_path(rel),
                        size=st.st_size,
                        added=st.st_mtime,
                    )
                    self._items[item.id] = item
                    self._enqueue(item.id)
                by_path = {i.path: i for i in self._items.values()}
                for rel, st in on_disk.items():
                    item = by_path.get(rel)
                    if item is None:
                        continue
                    if st is not None and item.size != st.st_size:
                        item.size = st.st_size
                        self._dirty = True
                        self._enqueue(item.id, again=True)
                    elif (item.duration is None or (item.poster is None and item.id not in self._poster_tried)
                          or (item.source is None and not item.probed)):  # fmt: skip
                        self._enqueue(item.id)
                if gone or fresh or moved:
                    self._dirty = True
            return {"added": len(fresh), "removed": len(gone), "moved": len(moved)}

    def _drop(self, item: Item) -> None:
        self._items.pop(item.id, None)
        if item.poster:
            (self.root / item.poster).unlink(missing_ok=True)
        self._dirty = True

    def start_watching(self, interval: float = 1.0) -> None:
        """Фоновый наблюдатель: изменения в Проводнике/Finder видны в интерфейсе за ~1–2 с."""
        if self._watching.is_set():
            return
        self._watching.set()
        threading.Thread(target=self._watch_loop, args=(interval,), name="library-watch", daemon=True).start()

    def stop_watching(self) -> None:
        self._watching.clear()

    def _watch_loop(self, interval: float) -> None:
        previous: dict | None = None
        hot: dict[str, float] = {}
        dirs: dict = {}  # кэш списка подпапок: перечитываем только папки, у которых сменился mtime
        full_at = time.monotonic() + FULL_RESCAN
        while self._watching.is_set():
            started = time.monotonic()
            try:
                snapshot = _snapshot(self.root, hot, dirs)
            except Exception:  # noqa: BLE001 — наблюдатель не должен умирать
                log.exception("watch snapshot")
                snapshot = None
            if previous is not None and snapshot != previous:
                changed = _changed_dirs(previous, snapshot or {})
                now = time.monotonic()
                for rel in changed:
                    hot[rel] = now  # за файлами этой папки последим подольше: копирование идёт
                try:
                    if snapshot is None or not previous:
                        self.scan(force=True)  # хранилище пропало или появилось — полностью
                    elif changed:
                        self.scan(force=True, dirs=changed)  # только изменившиеся папки
                except Exception:  # noqa: BLE001
                    log.exception("watch rescan")
                self._bump()
            elif snapshot is not None and time.monotonic() > full_at:
                full_at = time.monotonic() + FULL_RESCAN  # изредка — полная сверка (правка файла на месте и т. п.)
                try:
                    if self.scan(force=True).get("added") or self._dirty:
                        self._bump()
                except Exception:  # noqa: BLE001
                    log.exception("watch full rescan")
            for rel, since in list(hot.items()):
                if time.monotonic() - since > 30:
                    hot.pop(rel, None)
            previous = snapshot if snapshot is not None else {}
            # на медленном диске (/mnt/c) такт длится секунды — не крутимся впустую: пауза не меньше двух тактов
            time.sleep(max(interval, (time.monotonic() - started) * 2))

    # --- добавление и удаление -----------------------------------------------------------

    def add(
        self,
        tmp: Path,
        *,
        platform: str,
        stem: str,
        ext: str,
        title: str,
        source: str | None = None,
        uploader: str | None = None,
        cover: Path | None = None,
        folder: str | None = None,
        clip: list | tuple | None = None,
    ) -> Item:
        self._idle.wait(3600)  # хранилище переезжает — сохраним, когда закончит
        if not self.available():
            raise LibraryUnavailable(self.root)
        kind = kind_of(f"x.{ext}")
        platform = platform if platform in PLATFORM_DIRS else "other"
        if folder:
            target_dir = self._dir(folder)
        else:
            target_dir = self.root / PLATFORM_DIRS[platform] / TYPE_DIRS[kind]
        try:
            probe = self.media.probe(tmp)
        except MediaError:
            probe = None
        final = save_unique(tmp, target_dir, stem, ext)
        rel = final.relative_to(self.root).as_posix()
        with self._txn():
            existing = next((i for i in self._items.values() if i.path == rel), None)
            item = existing or Item(id=uuid.uuid4().hex[:12], path=rel, type=kind, title=title,
                                    platform=platform, size=0, added=time.time())  # fmt: skip
            item.title, item.platform, item.source, item.uploader = title, platform, source, uploader
            item.clip = list(clip) if clip else None
            item.probed = True  # ссылка и название известны из самой загрузки
            item.size = final.stat().st_size
            if probe is not None:
                item.duration = probe.duration
                if probe.video:
                    item.width, item.height = probe.video.get("width"), probe.video.get("height")
            if cover and cover.is_file():
                poster = self.root / SERVICE / "posters" / f"{item.id}.jpg"
                try:
                    poster.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(cover, poster)
                    item.poster = poster.relative_to(self.root).as_posix()
                except OSError:
                    pass  # без постера — сделает фоновая обработка
            self._items[item.id] = item
            self._dirty = True
        if not item.poster:
            self._enqueue(item.id, again=True)
        return item

    def delete(self, item_id: str) -> None:
        with self._txn():
            item = self._items.get(item_id)
            if item is None:
                raise KeyError(item_id)
            path = self.abs_path(item)
            if path.exists():
                system.trash(path)
            self._drop(item)

    # --- проводник -----------------------------------------------------------------------

    def _folder_info(self, rel: str, path: Path, agg: dict[str, list[int]]) -> dict:
        count, size = agg.get(rel.casefold(), [0, 0])
        parts = PurePosixPath(rel).parts
        platform = FOLDER_PLATFORM.get(parts[0].casefold()) if len(parts) == 1 else None
        try:
            modified = path.stat().st_mtime
        except OSError:
            modified = None
        return {
            "name": path.name if rel else self.root.name,
            "path": rel,
            "platform": platform,
            "system": is_system(rel),
            "count": count,
            "size": size,
            "modified": modified,
        }

    def list_dir(self, rel: str | None, page: dict | None = None) -> dict:
        """Содержимое папки. page — параметры постраничной выдачи файлов (q, kind, sort, order, offset, limit);
        без них — все файлы папки, новые сверху (как раньше)."""
        rel = validate_rel(rel)
        self._refresh()
        view = self._view()
        with self._lock:
            path = self._dir(rel)
            agg = view.aggregate
            folders = []
            for entry in _subdirs(path):
                child = f"{rel}/{entry.name}" if rel else entry.name
                folders.append(self._folder_info(child, Path(entry.path), agg))
            order = {name.casefold(): n for n, name in enumerate(PLATFORM_DIRS.values())}
            order |= {name.casefold(): n for n, name in enumerate(TYPE_DIRS.values())}
            folders.sort(key=lambda f: (not f["system"], order.get(f["name"].casefold(), 99), f["name"].casefold()))
            paged = None
            if page is not None:
                paged = self.query(folder=rel, refresh=False, **page)
                files = paged["items"]
            else:
                files = list(view.by_folder.get(rel.casefold(), ()))
            crumbs = [{"name": self.root.name, "path": ""}]
            parts = rel.split("/") if rel else []
            for n in range(len(parts)):
                crumbs.append({"name": parts[n], "path": "/".join(parts[: n + 1])})
            result = {
                "path": rel,
                "name": crumbs[-1]["name"],
                "breadcrumbs": crumbs,
                "folders": folders,
                "files": files,
                "rev": self.rev,
            }
            if paged is not None:
                result |= {"files_total": paged["total"], "offset": paged["offset"], "limit": paged["limit"],
                           "next_offset": paged["next_offset"]}  # fmt: skip
            return result

    def tree(self) -> dict:
        with self._lock:
            root = self.root
            if not root.is_dir():
                raise NotFound("Папка хранилища недоступна")
            agg = self._view().aggregate

            def build(rel: str, path: Path, depth: int) -> dict:
                node = self._folder_info(rel, path, agg)
                children = []
                if depth < 32:
                    for entry in _subdirs(path):
                        child = f"{rel}/{entry.name}" if rel else entry.name
                        children.append(build(child, Path(entry.path), depth + 1))
                children.sort(key=lambda c: (not c["system"], c["name"].casefold()))
                node["children"] = children
                return node

            return {"rev": self.rev, "tree": build("", root, 0)}

    def make_folder(self, parent: str | None, name: str) -> dict:
        name = validate_name(name)
        with self._txn():
            parent_path = self._dir(parent)
            if name.casefold() in _names(parent_path):
                raise Conflict(f"«{name}» уже есть в этой папке")
            target = parent_path / name
            try:
                target.mkdir()
            except FileExistsError as exc:
                raise Conflict(f"«{name}» уже есть в этой папке") from exc
            rel = target.relative_to(self.root).as_posix()
            self._bump()
            return self._folder_info(rel, target, self._view().aggregate)

    def rename(self, rel: str, name: str) -> str:
        rel = validate_rel(rel)
        name = validate_name(name)
        if not rel:
            raise Protected("Корень хранилища переименовать нельзя")
        if is_system(rel):
            raise Protected("Это системная папка выдры — её нельзя переименовать или удалить")
        with self._txn():
            src = self.root / rel
            if not src.exists():
                raise NotFound("Файл или папка не найдены")
            is_dir = src.is_dir()
            if not is_dir and Path(name).suffix.lower() != src.suffix.lower():
                name = validate_name(name + src.suffix)  # расширение сохраняем: иначе файл не откроется
            if name == src.name:
                return rel
            if name.casefold() in _names(src.parent) - {src.name.casefold()}:
                raise Conflict(f"«{name}» уже есть в этой папке")
            dst = src.with_name(name)
            if name.casefold() == src.name.casefold():  # смена только регистра — через временное имя
                tmp = src.with_name(f".vydra-rename-{uuid.uuid4().hex[:6]}")
                os.rename(src, tmp)
                os.rename(tmp, dst)
            else:
                os.rename(src, dst)
            new_rel = dst.relative_to(self.root).as_posix()
            self._repath(rel, new_rel, is_dir=is_dir)
            if not is_dir:
                for item in self._items.values():
                    if item.path == new_rel and item.title == Path(rel).stem:
                        item.title = dst.stem
            self._bump()
            return new_rel

    def move(self, rels: list[str], to: str | None) -> list[dict]:
        with self._txn():
            dest = self._dir(to)
            dest_rel = validate_rel(to)
            sources = []
            for raw in rels:  # сначала проверяем всё, потом двигаем — чтобы не застрять на полпути
                rel = validate_rel(raw)
                if not rel:
                    raise Protected("Корень хранилища переместить нельзя")
                if is_system(rel):
                    raise Protected(f"«{rel}» — системная папка выдры, её нельзя перемещать")
                src = self.root / rel
                if not src.exists():
                    raise NotFound(f"«{rel}» не найден")
                if src.is_dir() and (dest_rel.casefold() + "/").startswith(rel.casefold() + "/"):
                    raise Conflict("Нельзя переместить папку внутрь самой себя")
                sources.append((rel, src))
            moved = []
            for rel, src in sources:
                if src.parent.resolve() == dest.resolve():
                    moved.append({"from": rel, "to": rel})
                    continue
                is_dir = src.is_dir()
                target = _unique_variant(dest / src.name, names=_names(dest), is_dir=is_dir)
                shutil.move(str(src), str(target))
                new_rel = target.relative_to(self.root).as_posix()
                self._repath(rel, new_rel, is_dir=is_dir)
                moved.append({"from": rel, "to": new_rel})
            self._bump()
            return moved

    def remove(self, rel: str, recursive: bool = False) -> None:
        rel = validate_rel(rel)
        if not rel:
            raise Protected("Корень хранилища удалить нельзя")
        if is_system(rel):
            raise Protected("Это системная папка выдры — её нельзя переименовать или удалить")
        with self._txn():
            path = self.root / rel
            if not path.exists():
                raise NotFound("Файл или папка не найдены")
            if path.is_dir() and not recursive and any(path.iterdir()):
                raise Conflict("Папка не пустая — подтвердите удаление вместе с содержимым")
            system.trash(path)
            prefix = rel.casefold() + "/"
            for item in list(self._items.values()):
                p = item.path.casefold()
                if p == rel.casefold() or p.startswith(prefix):
                    self._drop(item)
            self._bump()

    def _repath(self, old: str, new: str, is_dir: bool) -> None:
        """После переименования/перемещения: пути в индексе за файлами, id не меняются."""
        old_cf = old.casefold()
        for item in self._items.values():
            path_cf = item.path.casefold()
            if not is_dir and path_cf == old_cf:
                item.path = new
            elif is_dir and path_cf.startswith(old_cf + "/"):
                item.path = new + item.path[len(old):]
            else:
                continue
            self._dirty = True

    # --- метаданные и постеры в фоне ------------------------------------------------------

    def _enqueue(self, item_id: str, again: bool = False) -> None:
        """В очередь фоновой обработки — один раз. again — файл изменился, обработать заново."""
        with self._lock:
            if again:
                self._attempted.discard(item_id)
            if item_id in self._queued or item_id in self._attempted:
                return
            self._queued.add(item_id)
        self._queue.put(item_id)

    def _enrich_loop(self) -> None:
        last_flush = time.monotonic()
        while True:
            try:
                item_id = self._queue.get(timeout=2)
            except queue.Empty:
                item_id = None
            if item_id is not None:
                with self._lock:
                    self._queued.discard(item_id)
                    self._attempted.add(item_id)
                try:
                    self._enrich(item_id)
                except Exception:  # noqa: BLE001 — фоновый поток не должен падать
                    log.exception("enrich %s", item_id)
            # индекс пишем пачкой: раз в ENRICH_FLUSH секунд или когда очередь опустела (раньше — после
            # каждого файла: на 50 000 файлов это 50 000 перезаписей индекса в 25 МБ)
            if self._enrich_dirty and (self._queue.empty() or time.monotonic() - last_flush > ENRICH_FLUSH):
                self._flush_enrichment()
                last_flush = time.monotonic()

    def _flush_enrichment(self) -> None:
        try:
            with self._lock:
                if not self.root.is_dir():
                    return
                with self._txn():
                    if self._enrich_dirty:
                        self._dirty = True
                    self._enrich_dirty = False
        except OSError as exc:
            log.warning("не удалось сохранить длительности и постеры: %s", exc)

    def _enrich(self, item_id: str) -> None:
        with self._lock:
            item = self._items.get(item_id)
            if item is None:
                return
            path, root = self.abs_path(item), self.root
            want_poster = item.poster is None and item_id not in self._poster_tried
            if want_poster:
                self._poster_tried.add(item_id)
        if not path.is_file():
            return
        try:
            probe = self.media.probe(path)
        except MediaError:
            return
        poster_rel = None
        if want_poster:
            poster = root / SERVICE / "posters" / f"{item_id}.jpg"
            try:
                poster.parent.mkdir(parents=True, exist_ok=True)
                if self.media.make_poster(path, poster, probe):
                    poster_rel = poster.relative_to(root).as_posix()
            except (OSError, MediaError):
                pass
        with self._lock:
            if self.root != root:
                return
            current = self._items.get(item_id)
            if current is None:
                return
            current.duration = probe.duration
            if probe.video:
                current.width, current.height = probe.video.get("width"), probe.video.get("height")
            if poster_rel:
                current.poster = poster_rel
            # файл найден сканированием (не скачан этим сервером): ссылку на оригинал и название берём из тегов,
            # которые выдра пишет при сохранении (comment — ссылка, title — название)
            tags = probe.tags or {}
            if current.source is None and _is_url(tags.get("comment") or tags.get("purl") or ""):
                current.source = (tags.get("comment") or tags.get("purl")).strip()
            if tags.get("title") and current.title == Path(current.path).stem:
                current.title = str(tags["title"]).strip()[:300]
            current.probed = True
            self._enrich_dirty = True
            self._mutations += 1

    # --- для health-check ----------------------------------------------------------------

    def audit(self) -> dict:
        with self._lock:
            self._ensure_loaded()
            items = list(self._items.values())
            root = self.root
        missing = sum(1 for i in items if not (root / i.path).is_file())
        no_poster = sum(1 for i in items if i.type == "video" and not (i.poster and (root / i.poster).is_file()))
        index_ok = _read_index(root / SERVICE / "library.json") is not None
        return {"items": len(items), "missing": missing, "no_poster": no_poster, "index": index_ok}

    def rebuild(self) -> dict:
        """Полная пересборка: структура папок, пересканирование, заново все постеры."""
        with self._lock:
            self._poster_tried.clear()
            self._loaded_root = None  # перечитать индекс (с восстановлением из .bak)
            self._ensure_loaded()
            for item in self._items.values():
                if item.poster and not (self.root / item.poster).is_file():
                    item.poster = None
                    self._dirty = True
        self.ensure_layout()
        return self.scan(force=True)


class _View:
    """Снимок индекса для чтения: словари файлов, итоги, сортировки, строки поиска, агрегаты по папкам.
    Строится один раз на ревизию индекса — повторные запросы к 50 000 файлов не пересчитывают всё заново."""

    def __init__(self, key: tuple, items: list[Item]):
        self.key = key
        self.ordered = [i.public() for i in sorted(items, key=lambda i: i.added, reverse=True)]
        self.encoded: bytes | None = None
        self._sorted: dict[tuple[str, str], list[dict]] = {}
        self._haystack: dict[str, str] | None = None
        self._aggregate: dict[str, list[int]] | None = None
        self._by_folder: dict[str, list[dict]] | None = None
        videos = [i for i in self.ordered if i["type"] == "video"]
        video_size = sum(i["size"] for i in videos)
        total = sum(i["size"] for i in self.ordered)
        self.stats = {
            "count": len(self.ordered),
            "videos": len(videos),
            "audios": len(self.ordered) - len(videos),
            "size": total,
            "size_by_type": {"video": video_size, "audio": total - video_size},
        }

    def sorted(self, sort: str = "added", order: str | None = None) -> list[dict]:
        if sort not in SORTS:
            raise ValueError(f"Сортировка: {', '.join(SORTS)}")
        order = order or ("asc" if sort == "name" else "desc")
        if (sort, order) == ("added", "desc"):
            return self.ordered
        if (sort, order) not in self._sorted:
            if sort == "name":
                key = lambda i: ((i["title"] or i["name"]).casefold(), i["name"].casefold())  # noqa: E731
                items = sorted(self.ordered, key=key, reverse=order == "desc")
            elif sort == "duration":  # без длительности — всегда в конце
                known = [i for i in self.ordered if i["duration"] is not None]
                unknown = [i for i in self.ordered if i["duration"] is None]
                items = sorted(known, key=lambda i: i["duration"], reverse=order == "desc") + unknown
            else:
                items = sorted(self.ordered, key=lambda i: i[sort], reverse=order == "desc")
            self._sorted[(sort, order)] = items
        return self._sorted[(sort, order)]

    @property
    def haystack(self) -> dict[str, str]:
        if self._haystack is None:
            self._haystack = {i["id"]: f"{i['title'] or ''} {i['name']}".casefold() for i in self.ordered}
        return self._haystack

    @property
    def by_folder(self) -> dict[str, list[dict]]:
        if self._by_folder is None:
            self._by_folder = {}
            for i in self.ordered:
                self._by_folder.setdefault(i["folder"].casefold(), []).append(i)
        return self._by_folder

    @property
    def aggregate(self) -> dict[str, list[int]]:
        """Сколько медиафайлов и байт в каждой папке — рекурсивно (по индексу, без обхода диска)."""
        if self._aggregate is None:
            agg: dict[str, list[int]] = {}
            for i in self.ordered:
                parts = i["path"].split("/")[:-1]
                for depth in range(len(parts) + 1):
                    entry = agg.setdefault("/".join(parts[:depth]).casefold(), [0, 0])
                    entry[0] += 1
                    entry[1] += i["size"]
            self._aggregate = agg
        return self._aggregate


def _is_url(text: str) -> bool:
    return text.strip().lower().startswith(("http://", "https://")) and " " not in text.strip()


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve() or (a.exists() and b.exists() and os.path.samefile(a, b))
    except OSError:
        return False


def _inside(child: Path, parent: Path) -> bool:
    try:
        return child.resolve().is_relative_to(parent.resolve())
    except OSError:
        return False


def _has_media(path: Path) -> bool:
    for _dirpath, _dirnames, filenames in os.walk(path):
        if any(Path(n).suffix.lower() in MEDIA_EXTS for n in filenames):
            return True
    return False


def _library_entries(root: Path, items: list[Item]) -> list[Path]:
    """Что из корня принадлежит хранилищу и переезжает вместе с ним."""
    indexed_top = {PurePosixPath(i.path).parts[0].casefold() for i in items if PurePosixPath(i.path).parts}
    platform_dirs = {p.casefold() for p in PLATFORM_DIRS.values()}
    entries = []
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        name = entry.name
        if name.startswith(".") or name == LEGACY_CINEMA:
            continue
        if entry.is_dir() and not entry.is_symlink():
            empty = not any(entry.iterdir())
            if name.casefold() in platform_dirs or name.casefold() in indexed_top or empty or _has_media(entry):
                entries.append(entry)
        elif entry.is_file() and entry.suffix.lower() in MEDIA_EXTS:
            entries.append(entry)
    return entries


def _tree_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                pass
    return total


def _move_entry(src: Path, dst: Path, moved_file, report) -> None:
    """Файл или папку src → dst со слиянием папок и « (2)» для совпавших файлов."""
    if src.is_dir():
        if not dst.exists():
            try:
                size = _tree_size(src)
                os.rename(src, dst)  # тот же диск — мгновенно, целиком
                for dirpath, _dirnames, filenames in os.walk(dst):
                    for name in filenames:
                        moved = Path(dirpath) / name
                        moved_file(src / moved.relative_to(dst), moved, moved.stat().st_size)
                return
            except OSError:
                pass  # другой диск или занято — по одному файлу
            if dst.exists() and not dst.is_dir():
                dst = _unique_variant(dst, is_dir=True)
        elif not dst.is_dir():
            dst = _unique_variant(dst, is_dir=True)
        dst.mkdir(parents=True, exist_ok=True)
        for child in sorted(src.iterdir(), key=lambda e: e.name):
            _move_entry(child, dst / child.name, moved_file, report)
        try:
            src.rmdir()
        except OSError:
            pass  # остался файл, который не удалось перенести, — папку не трогаем
        return
    size = src.stat().st_size
    if dst.is_file() and _same_content(src, dst):
        # прошлый перенос оборвался между «скопировал» и «удалил исходник»: копия уже на месте — не дублируем
        try:
            src.unlink()
        except OSError as exc:
            log.warning("не удалось удалить исходник %s: %s", src, exc)
        moved_file(src, dst, size)
        return
    target = dst if not dst.exists() else _unique_variant(dst, is_dir=False)
    try:
        os.rename(src, target)
    except OSError:
        part = target.with_name(target.name + ".part")
        shutil.copyfile(src, part)
        if part.stat().st_size != size:
            part.unlink(missing_ok=True)
            raise OSError(errno.EIO, f"Копия «{src.name}» получилась неполной — исходник не тронут")
        os.replace(part, target)
        try:
            src.unlink()
        except OSError as exc:  # исходник занят — копия уже есть, ничего не потеряно
            log.warning("не удалось удалить исходник %s после копирования: %s", src, exc)
    moved_file(src, target, size)


def _same_content(a: Path, b: Path, sample: int = 1 << 16) -> bool:
    """Одинаковые ли файлы: размер + начало и конец (полная сверка гигабайтных видео на /mnt/c — слишком долго)."""
    try:
        size = a.stat().st_size
        if size != b.stat().st_size:
            return False
        with a.open("rb") as fa, b.open("rb") as fb:
            if fa.read(sample) != fb.read(sample):
                return False
            if size > sample:
                fa.seek(max(0, size - sample))
                fb.seek(max(0, size - sample))
                return fa.read(sample) == fb.read(sample)
            return True
    except OSError:
        return False


def _move_posters(src: Path, dst: Path) -> None:
    if not src.is_dir():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for poster in src.iterdir():
        target = dst / poster.name
        if target.exists():
            continue
        try:
            os.rename(poster, target)
        except OSError:
            try:
                shutil.copyfile(poster, target)
            except OSError:
                pass


def _read_journal(path: Path) -> dict[str, str]:
    renamed: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            old, sep, new = line.partition("\t")
            if sep:
                renamed[old] = new
    except OSError:
        pass
    return renamed


def _cleanup_old_root(old: Path) -> None:
    """После переезда: служебные файлы старой папки и пустые отделы. Чужие файлы не трогаем."""
    shutil.rmtree(old / SERVICE, ignore_errors=True)
    for platform_dir in PLATFORM_DIRS.values():
        for type_dir in TYPE_DIRS.values():
            try:
                (old / platform_dir / type_dir).rmdir()
            except OSError:
                pass
        try:
            (old / platform_dir).rmdir()
        except OSError:
            pass
    try:
        old.rmdir()  # только если опустела
    except OSError:
        pass


def _read_index(path: Path) -> list[Item] | None:
    """Элементы индекса; None — файла нет или он повреждён."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [Item.from_dict(raw) for raw in data.get("items", [])]
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _walk_media(root: Path) -> dict[str, os.stat_result]:
    """Все медиафайлы хранилища: относительный путь → stat. На строках и scandir, без Path на каждый файл
    (на 50 000 файлов Path.relative_to и .suffix занимали ~4 с из 5)."""
    found: dict[str, os.stat_result] = {}
    exts = tuple(MEDIA_EXTS)
    stack = [(str(root), "")]
    while stack:
        path, rel = stack.pop()
        try:
            entries = list(os.scandir(path))
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if name.startswith("."):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append((entry.path, f"{rel}/{name}" if rel else name))
                elif name.lower().endswith(exts):
                    st = entry.stat()
                    if st.st_size > 0:  # 0 байт — имя зарезервировано, файл ещё копируется
                        found[f"{rel}/{name}" if rel else name] = st
            except OSError:
                continue
    return found


def _media_in(root: Path, dirs: set[str], known: set[str] | frozenset = frozenset()) -> dict[str, os.stat_result | None]:
    """Медиафайлы прямо в этих папках (без вложенных): для сверки только того, что поменялось.
    Уже известные файлы не stat-им (None): на /mnt/c stat стоит ~3 мс, а в папке бывают тысячи файлов."""
    found: dict[str, os.stat_result | None] = {}
    exts = tuple(MEDIA_EXTS)
    for rel in dirs:
        try:
            entries = list(os.scandir(root / rel if rel else root))
        except OSError:
            continue  # папку удалили — её файлы уйдут из индекса
        for entry in entries:
            name = entry.name
            if name.startswith(".") or not name.lower().endswith(exts):
                continue
            child = f"{rel}/{name}" if rel else name
            if child in known:
                found[child] = None
                continue
            try:
                if entry.is_file(follow_symlinks=False):
                    st = entry.stat()
                    if st.st_size > 0:
                        found[child] = st
            except OSError:
                continue
    return found


def _changed_dirs(previous: dict, snapshot: dict) -> set[str]:
    """Папки, чьё содержимое поменялось: сменился mtime, папка появилась или исчезла, изменился файл в ней."""
    changed = set()
    for key in previous.keys() | snapshot.keys():
        if previous.get(key) == snapshot.get(key):
            continue
        if key.startswith("d:"):
            changed.add(key[2:])
        elif key.startswith("f:"):
            changed.add(key[2:].rpartition("/")[0])
    return changed


def _snapshot(root: Path, hot: dict[str, float], dirs: dict | None = None) -> dict[str, tuple] | None:
    """Дешёвый слепок: mtime всех папок (добавление/удаление/переименование меняет mtime родителя)
    + размеры файлов в «горячих» папках, где недавно что-то менялось (копирование ещё идёт).

    dirs — кэш «папка → (mtime, подпапки)» между вызовами: содержимое папки перечитывается, только если её
    mtime сменился. Иначе раз в секунду перебирались бы все 50 000 файлов (а на /mnt/c это медленно)."""
    if not root.is_dir():
        return None
    cache = dirs if dirs is not None else {}
    if cache.get("") and cache[""][2] != str(root):  # хранилище сменилось — кэш не годится
        cache.clear()
    snap: dict[str, tuple] = {}
    seen: set[str] = set()
    stack = [(str(root), "")]
    while stack:
        path, rel = stack.pop()
        try:
            mtime = os.stat(path).st_mtime_ns
        except OSError:
            continue
        seen.add(rel)
        snap[f"d:{rel}"] = (mtime,)
        cached = cache.get(rel)
        if cached is None or cached[0] != mtime or rel in hot:
            try:
                entries = [e for e in os.scandir(path) if not e.name.startswith(".")]
            except OSError:
                continue
            subdirs = []
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        subdirs.append(entry.name)
                    elif rel in hot:
                        est = entry.stat()
                        child = f"{rel}/{entry.name}" if rel else entry.name
                        snap[f"f:{child}"] = (est.st_size, est.st_mtime_ns)
                except OSError:
                    continue
            cached = (mtime, subdirs, str(root) if not rel else "")
            cache[rel] = cached
        for name in cached[1]:
            stack.append((os.path.join(path, name), f"{rel}/{name}" if rel else name))
    for gone in [k for k in cache if k not in seen]:
        del cache[gone]
    try:
        snap["index"] = (os.stat(root / SERVICE / "library.json").st_mtime_ns,)
    except OSError:
        pass
    return snap


def _fresh(path: Path, max_age: float) -> bool:
    """Файл есть и менялся недавно (например, .part, в который прямо сейчас копируют)."""
    try:
        return time.time() - path.stat().st_mtime < max_age
    except OSError:
        return False


def _subdirs(path: Path) -> list[os.DirEntry]:
    try:
        return [e for e in os.scandir(path) if e.is_dir(follow_symlinks=False) and not e.name.startswith(".")]
    except OSError:
        return []


def _names(path: Path) -> set[str]:
    try:
        return {e.name.casefold() for e in os.scandir(path)}
    except OSError:
        return set()


def _unique_variant(target: Path, names: set[str] | None = None, is_dir: bool | None = None) -> Path:
    """target или «имя (2)», «имя (3)»… — без учёта регистра, как в Windows."""
    names = names if names is not None else _names(target.parent)
    if target.name.casefold() not in names and not target.exists():
        return target
    folder = target.is_dir() if is_dir is None else is_dir
    stem, suffix = (target.name, "") if folder else (target.stem, target.suffix)
    n = 2
    while True:
        candidate = target.with_name(f"{stem} ({n}){suffix}")
        if candidate.name.casefold() not in names and not candidate.exists():
            return candidate
        n += 1


def _migrated_path(rel: str) -> str | None:
    """Путь в старой раскладке (Видео/<П>/…) → в новой (<П>/Видео/…); None — это не старая раскладка."""
    parts = PurePosixPath(rel).parts
    if len(parts) < 2 or parts[0] not in TYPE_DIRS.values():
        return None
    type_dir, rest = parts[0], parts[1:]
    if len(rest) >= 2 and rest[0].casefold() in FOLDER_PLATFORM:
        platform_dir = PLATFORM_DIRS[FOLDER_PLATFORM[rest[0].casefold()]]
        rest = rest[1:]
    else:
        platform_dir = PLATFORM_DIRS["file"]
    return "/".join((platform_dir, type_dir, *rest))


def _remove_empty_dirs(top: Path) -> None:
    for dirpath, _dirnames, _filenames in sorted(os.walk(top), key=lambda w: -len(w[0])):
        try:
            os.rmdir(dirpath)  # удаляется только пустая папка
        except OSError:
            pass


def _match_moves(gone: list[Item], fresh: list[str], on_disk: dict) -> list[tuple[Item, str]]:
    """Файл переложили в другую папку руками — узнаём его по имени и размеру и сохраняем id
    (по id кинотеатр помнит, где вы остановились)."""
    moves = []
    for item in gone:
        name = Path(item.path).name
        candidates = [rel for rel in fresh if Path(rel).name == name and on_disk[rel].st_size == item.size]
        if len(candidates) == 1 and all(m[1] != candidates[0] for m in moves):
            moves.append((item, candidates[0]))
    return moves
