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
SPACE_MARGIN = 64 * 1024 * 1024


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

    @property
    def watermarked(self) -> bool:
        return "watermark" in (self.info.get("format") or "").lower()


class Reporter:
    """Куда сообщается ход работы (реализуется задачей)."""

    def item(self, info: dict, thumbnail: Path | None) -> None: ...
    def progress(self, percent: float | None, speed: float | None, eta: int | None) -> None: ...
    def stage(self, text: str) -> None: ...


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
    (("requested format is not available", "no video formats found"),
     "Нужного формата у ролика нет — попробуйте другое качество или MP3."),
    (("ffmpeg not found", "ffprobe and ffmpeg not found", "ffmpeg is not installed"),
     "Не найден FFmpeg — запустите «vydra doctor --fix» или «Починить» в интерфейсе."),
    (("не хватает места", "no space left"), None),  # сообщение уже понятное
]  # fmt: skip
_TRANSIENT = [
    (("http error 429", "too many requests"), RATE_LIMITED_TEXT),
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
    }
    reporter.stage("Получаю информацию о видео")
    watch = _Watch()
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as stderr:
        proc = subprocess.Popen(
            [sys.executable, "-m", "vydra.downloader"],
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
            proc.stdin.write(json.dumps(request))
            proc.stdin.close()
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
                elif kind == "done":
                    result = [
                        Downloaded(Path(i["path"]), i["info"], Path(i["thumbnail"]) if i.get("thumbnail") else None)
                        for i in msg["items"]
                    ]
                elif kind == "error":
                    error = (msg["message"], bool(msg.get("transient")))
            proc.wait()
        finally:
            system.kill_tree(proc)  # на любой выход — никаких сирот (yt-dlp → ffmpeg)
            watch.finished.set()
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
            [sys.executable, "-m", "vydra.downloader"],
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

    request = json.loads(sys.stdin.read())
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
        send(type="error", message=text, transient=transient)
        return
    send(type="done", items=items)


class _Permanent(Exception):
    """Ошибка, которую повторять бессмысленно (например, плейлист без подтверждения)."""


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
            info = ydl.extract_info(req["url"], download=False)
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

    info = _load_info(req.get("info_file"))
    with YoutubeDL(opts) as ydl:
        if info is not None:
            try:
                return _download_with(ydl, info, req, send, work_dir, errors)
            except (NoSpace, _Permanent):
                raise
            except Exception as exc:  # noqa: BLE001 — ссылки из превью протухли (403/410) — извлекаем заново
                print(f"cached info failed, re-extracting: {exc}", file=sys.stderr)
                send(type="phase", phase="extract")
        info = _extract(req, logger)
        return _download_with(ydl, info, req, send, work_dir, errors)


def _load_info(path: str | None) -> dict | None:
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _extract(req: dict, logger) -> dict:
    """Плейлисты извлекаются «плоско» (только список): так можно сосчитать ролики, не извлекая каждый."""
    from yt_dlp import YoutubeDL

    with YoutubeDL(_common_opts(req, logger) | {"extract_flat": "in_playlist"}) as flat:
        return flat.extract_info(req["url"], download=False)


def _download_with(ydl, info: dict, req: dict, send, work_dir: Path, errors: list[str]) -> list[dict]:
    if info.get("_type") == "playlist":
        entries = list(info.get("entries") or [])
        info["entries"] = entries
        limit = req.get("playlist_limit")
        if limit and len(entries) > limit:
            raise _Permanent(
                f"Это плейлист из {len(entries)} роликов — подтвердите, что хотите скачать его целиком"
            )
    _check_space(info, work_dir, req.get("library_dir"), req["mode"])
    send(type="phase", phase="download")
    info = ydl.process_ie_result(info, download=True)

    entries = [e for e in info.get("entries") or [] if e] if info.get("_type") == "playlist" else [info]
    items = []
    for entry in entries:
        for requested in entry.get("requested_downloads") or []:
            path = Path(requested.get("filepath") or "")
            if path.is_file():
                thumb = _thumbnail_of(entry, work_dir)
                items.append({"path": str(path), "info": _slim(entry), "thumbnail": str(thumb) if thumb else None})
    if not items:
        raise RuntimeError(errors[-1] if errors else "По ссылке не нашлось видео")
    return items


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
