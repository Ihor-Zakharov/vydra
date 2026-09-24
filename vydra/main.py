"""HTTP API и раздача интерфейса. Запуск: `vydra ui` (или uvicorn --factory vydra.main:create_app).

Всё долгое (yt-dlp, ffmpeg, обход диска, PowerShell) работает в потоках: обычные `def`-обработчики
FastAPI выполняет в пуле потоков, а в `async`-обработчиках тяжёлое уносится в run_in_threadpool.
Цикл событий никогда не блокируется — интерфейс отвечает даже во время больших загрузок.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from . import __version__, diagnostics, system, tools
from .config import Prefs, Settings
from .downloader import PLAYLIST_LIMIT, DownloadFailed, peek_preview, preview
from .fsutil import friendly_os_error
from .health import Doctor, summary
from .jobs import Job, JobManager, detect_platform
from .library import FsError, Library, validate_rel
from .media import Media
from .naming import safe_stem
from .timecode import parse_clip

STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
MAX_COOKIES = 5 * 1024 * 1024
SSE_HEARTBEAT = 15

log = logging.getLogger("vydra")

Mode = Literal["mp4", "mp3", "both"]
Quality = Literal["max", "1080", "720", "480", "360"]
Bitrate = Literal[128, 192, 256, 320]


def normalize_url(raw: str) -> str:
    url = raw.strip()
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or "." not in (parts.hostname or "") or len(url) > 4096:
        raise ValueError(f"Это не похоже на ссылку: {raw[:80]}")
    return url


class DownloadRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=50)
    mode: Mode = "mp4"
    quality: Quality = "1080"
    bitrate: Bitrate = 192
    start: str | None = None
    end: str | None = None
    folder: str | None = None  # папка хранилища для результата
    force: bool = False  # качать, даже если такой файл уже есть
    confirm_playlist: bool = False  # согласие на плейлист больше PLAYLIST_LIMIT роликов

    @field_validator("urls")
    @classmethod
    def _urls(cls, urls: list[str]) -> list[str]:
        result = [normalize_url(u) for u in urls if u.strip()]
        if not result:
            raise ValueError("Вставьте ссылку на видео")
        return result


class PreviewRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def _url(cls, url: str) -> str:
        return normalize_url(url)


class LibraryPathRequest(BaseModel):
    path: str | None = None
    reset: bool = False


class FixRequest(BaseModel):
    id: str


class FolderRequest(BaseModel):
    parent: str = ""
    name: str


class RenameRequest(BaseModel):
    path: str
    name: str


class MoveRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=500)
    to: str = ""


def create_app(settings: Settings | None = None, watch: bool = True) -> FastAPI:
    settings = settings or Settings.from_env()
    diagnostics.setup_logging(settings)
    prefs = Prefs(settings)
    library = Library(prefs, Media(settings))
    manager = JobManager(settings, library, persist=True)
    doctor = Doctor(settings, library, protected=manager.protected_ids)
    stopping = threading.Event()

    def prepare_library() -> None:
        try:
            library.ensure_layout()
            library.scan(force=True)
        except OSError as exc:  # папка недоступна — покажет «Состояние системы»
            log.warning("хранилище недоступно: %s", exc)
        if watch:
            library.start_watching()

    def freshness() -> None:
        """Раз в сутки (не блокируя ничего) узнаём, не вышел ли новый yt-dlp, — доктор покажет."""
        while not stopping.wait(5):
            try:
                tools.ytdlp_latest_cached(settings.work_dir)
            except Exception:  # noqa: BLE001
                pass
            if stopping.wait(6 * 3600):
                return

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        threading.Thread(target=prepare_library, name="library-init", daemon=True).start()
        threading.Thread(target=freshness, name="ytdlp-freshness", daemon=True).start()
        log.info("выдра %s запущена, хранилище: %s", __version__, library.root)
        yield
        stopping.set()
        library.stop_watching()
        await run_in_threadpool(manager.shutdown)
        log.info("выдра остановлена")

    app = FastAPI(title="выдра", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings, app.state.library, app.state.manager = settings, library, manager

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Сервер пишет файлы на диск, поэтому чужие сайты (CSRF, DNS rebinding) отсекаем по Host/Origin
        host = urlsplit("//" + request.headers.get("host", "")).hostname
        origin = request.headers.get("origin")
        if host not in LOCAL_HOSTS or (origin and origin != "null" and urlsplit(origin).hostname not in LOCAL_HOSTS):
            return JSONResponse({"detail": "Доступ только с этого компьютера"}, status_code=403)
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.exception_handler(FsError)
    async def fs_error(_: Request, exc: FsError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(OSError)
    async def os_error(_: Request, exc: OSError):
        log.warning("OSError в API: %s", exc)
        return JSONResponse({"detail": friendly_os_error(exc)}, status_code=500)

    # --- интерфейс -----------------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        return FileResponse(
            STATIC / "sw.js",
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
        )

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/lib/{rel:path}", include_in_schema=False)
    def library_file(rel: str, download: bool = False):
        path = library.resolve(rel)
        if path is None:
            raise HTTPException(404, "Файл не найден")
        disposition = "attachment" if download else "inline"
        headers = {"Cache-Control": "no-cache"} if path.suffix in (".js", ".html", ".json") else None
        return FileResponse(path, filename=path.name, content_disposition_type=disposition, headers=headers)

    # --- общее ---------------------------------------------------------------------------

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__}

    def library_info() -> dict:
        root = library.root
        return {
            "path": system.display_path(root),
            "default": system.display_path(settings.default_library),
            "is_default": root == settings.default_library,
            "fixed": settings.fixed_library is not None,
        }

    def settings_payload() -> dict:
        cookies = settings.cookies_file
        return {
            "library": library_info(),
            "cookies": {"present": cookies is not None, "updated": cookies.stat().st_mtime if cookies else None},
        }

    @app.get("/api/info")
    def info():
        return {
            "version": __version__,
            "ytdlp": tools.ytdlp_version(),
            "os": system.OS,
            "library": library_info(),
            "cookies": settings.cookies_file is not None,
            "can_pick_folder": system.OS in ("windows", "wsl", "mac") or shutil.which("zenity") is not None,
            "playlist_limit": PLAYLIST_LIMIT,
        }

    @app.get("/api/events")
    async def events():
        """Server-Sent Events: `library` при любом изменении хранилища, `jobs` — при смене состояния задач."""

        async def stream():
            last_rev = last_jobs = None
            beat = time.monotonic()
            yield "retry: 2000\n\n"
            while not stopping.is_set():
                rev, jobs_version = library.rev, manager.version
                if rev != last_rev:
                    last_rev = rev
                    yield f"event: library\ndata: {json.dumps({'rev': rev})}\n\n"
                if jobs_version != last_jobs:
                    last_jobs = jobs_version
                    yield "event: jobs\ndata: {}\n\n"
                if time.monotonic() - beat > SSE_HEARTBEAT:
                    beat = time.monotonic()
                    yield ": ping\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # --- задачи --------------------------------------------------------------------------

    @app.post("/api/preview")
    def make_preview(req: PreviewRequest):
        try:
            data = preview(
                req.url, js_runtime=settings.js_runtime, cookies=settings.cookies_file, info_cache=manager.info_cache
            )
        except DownloadFailed as exc:
            raise HTTPException(422, str(exc)) from exc
        data["platform"] = detect_platform(data.get("url") or req.url)
        return data

    def check_folder(folder: str | None) -> str | None:
        if not folder:
            return None
        try:
            rel = validate_rel(folder)
        except FsError as exc:
            raise HTTPException(422, str(exc)) from exc
        if rel and not library.folder_exists(rel):
            raise HTTPException(422, f"Папка «{rel}» не найдена в хранилище")
        return rel or None

    @app.post("/api/jobs")
    def create_jobs(req: DownloadRequest):
        try:
            clip = parse_clip(req.start, req.end)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        folder = check_folder(req.folder)
        if not req.confirm_playlist:
            for url in req.urls:
                seen = peek_preview(url, settings.cookies_file)
                if seen and seen.get("playlist") and (seen.get("count") or 0) > PLAYLIST_LIMIT:
                    raise HTTPException(
                        422,
                        f"Это плейлист из {seen['count']} роликов — подтвердите загрузку целиком "
                        f"(confirm_playlist)",
                    )
        jobs = []
        for url in req.urls:
            job = Job(
                kind="url", source=url, mode=req.mode, quality=req.quality, bitrate=req.bitrate, clip=clip,
                folder=folder, confirm_playlist=req.confirm_playlist,
            )  # fmt: skip
            existing = None if req.force else manager.find_existing(url, req.mode, clip)
            jobs.append(manager.add_existing(job, existing) if existing else manager.submit(job))
        return [j.public() for j in jobs]

    @app.post("/api/convert")
    async def convert(
        files: list[UploadFile] = File(...),
        mode: Mode = Form("mp4"),
        bitrate: int = Form(192),  # из формы приходит строкой — Literal[int] её не примет
        start: str | None = Form(None),
        end: str | None = Form(None),
        folder: str | None = Form(None),
    ):
        if bitrate not in (128, 192, 256, 320):
            raise HTTPException(422, "Битрейт MP3: 128, 192, 256 или 320")
        try:
            clip = parse_clip(start, end)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        folder_rel = await run_in_threadpool(check_folder, folder)
        created = []
        for upload in files:
            name = Path(upload.filename or "file").name
            upload_dir = await run_in_threadpool(manager.new_upload_dir)
            dest = upload_dir / (safe_stem(Path(name).stem, "file") + Path(name).suffix.lower()[:10])
            try:
                # копирование на диск — в потоке: большой файл не должен останавливать сервер
                await run_in_threadpool(_save_upload, upload, dest)
            except OSError as exc:
                shutil.rmtree(upload_dir, ignore_errors=True)
                raise HTTPException(507, friendly_os_error(exc)) from exc
            if dest.stat().st_size == 0:
                shutil.rmtree(upload_dir, ignore_errors=True)
                raise HTTPException(422, f"Файл «{name}» пустой")
            job = Job(kind="file", source=name, mode=mode, bitrate=bitrate, clip=clip, input_path=dest,
                      folder=folder_rel)  # fmt: skip
            created.append(manager.submit(job).public())
        return created

    @app.get("/api/jobs")
    def list_jobs():
        return manager.list()

    @app.post("/api/jobs/clear")
    def clear_jobs():
        manager.clear_finished()
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        if not manager.cancel(job_id):
            raise HTTPException(404, "Задача не найдена или уже завершена")
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: str):
        try:
            return manager.retry(job_id).public()
        except KeyError as exc:
            raise HTTPException(404, "Задача не найдена") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/jobs/{job_id}/thumbnail")
    def job_thumbnail(job_id: str):
        job = manager.get(job_id)
        if job is None or job.thumb is None or not job.thumb.is_file():
            raise HTTPException(404)
        return FileResponse(job.thumb, headers={"Cache-Control": "max-age=3600"})

    # --- хранилище -----------------------------------------------------------------------

    @app.get("/api/library")
    def list_library():
        items = library.items()
        return {
            "root": system.display_path(library.root),
            "stats": library.stats(),
            "items": items,
            "rev": library.rev,
        }

    @app.get("/api/library/rev")
    def library_rev():
        return {"rev": library.rev}

    def library_item(item_id: str):
        item = library.get(item_id)
        if item is None or not library.abs_path(item).is_file():
            raise HTTPException(404, "Файл не найден — возможно, его удалили или переместили")
        return item

    def os_action(action, path: Path) -> dict:
        try:
            action(path)
        except system.NotSupported as exc:
            raise HTTPException(501, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/library/{item_id}/open")
    def open_item(item_id: str):
        return os_action(system.open_path, library.abs_path(library_item(item_id)))

    @app.post("/api/library/{item_id}/reveal")
    def reveal_item(item_id: str):
        return os_action(system.reveal, library.abs_path(library_item(item_id)))

    @app.delete("/api/library/{item_id}")
    def delete_item(item_id: str):
        library_item(item_id)
        try:
            library.delete(item_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"Не удалось удалить: {exc}") from exc
        return {"ok": True}

    @app.post("/api/library/rescan")
    def rescan():
        return library.scan(force=True)

    @app.post("/api/folder/open")
    def open_folder():
        if not library.available():
            raise HTTPException(409, "Папка хранилища недоступна — диск отключён?")
        library.root.mkdir(parents=True, exist_ok=True)
        return os_action(system.open_path, library.root)

    # --- проводник -----------------------------------------------------------------------

    @app.get("/api/fs")
    def fs_list(path: str = ""):
        return library.list_dir(path)

    @app.get("/api/fs/tree")
    def fs_tree():
        return library.tree()

    @app.post("/api/fs/folder")
    def fs_mkdir(req: FolderRequest):
        return library.make_folder(req.parent, req.name)

    @app.post("/api/fs/rename")
    def fs_rename(req: RenameRequest):
        return {"path": library.rename(req.path, req.name)}

    @app.post("/api/fs/move")
    def fs_move(req: MoveRequest):
        return {"moved": library.move(req.paths, req.to)}

    @app.delete("/api/fs")
    def fs_delete(path: str = Query(...), recursive: bool = False):
        library.remove(path, recursive=recursive)
        return {"ok": True}

    # --- настройки -----------------------------------------------------------------------

    @app.get("/api/settings")
    def get_settings():
        return settings_payload()

    @app.post("/api/settings/library")
    def set_library(req: LibraryPathRequest):
        if settings.fixed_library:
            raise HTTPException(409, "Папка задана переменной окружения VD_LIBRARY_DIR")
        try:
            path = settings.default_library if req.reset else system.parse_user_path(req.path or "")
            library.set_root(path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return settings_payload()

    @app.post("/api/settings/library/pick")
    def pick_library():
        if settings.fixed_library:
            raise HTTPException(409, "Папка задана переменной окружения VD_LIBRARY_DIR")
        try:
            path = system.pick_folder(library.root)
        except system.NotSupported as exc:
            raise HTTPException(501, str(exc)) from exc
        if path is None:
            return {"cancelled": True}
        try:
            library.set_root(path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return settings_payload()

    @app.post("/api/settings/cookies")
    async def upload_cookies(file: UploadFile = File(...)):
        data = await file.read(MAX_COOKIES + 1)
        if len(data) > MAX_COOKIES:
            raise HTTPException(413, "Файл слишком большой для cookies.txt")
        if b"\t" not in data:
            raise HTTPException(400, "Это не cookies.txt — нужен формат Netscape (расширение «Get cookies.txt LOCALLY»)")
        target = settings.config_dir / "cookies.txt"

        def save() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name("cookies.txt.tmp")
            tmp.write_bytes(data)
            tmp.chmod(0o600)
            tmp.replace(target)

        await run_in_threadpool(save)
        return settings_payload()

    @app.delete("/api/settings/cookies")
    def delete_cookies():
        (settings.config_dir / "cookies.txt").unlink(missing_ok=True)
        return settings_payload()

    # --- здоровье ------------------------------------------------------------------------

    @app.get("/api/doctor")
    def run_doctor():
        checks = doctor.run()
        return {"checks": [c.public() for c in checks], "summary": summary(checks)}

    @app.post("/api/doctor/fix")
    def fix(req: FixRequest):
        results = doctor.fix_all() if req.id == "all" else [doctor.fix(req.id)]
        checks = doctor.run()
        return {"results": [r.public() for r in results], "checks": [c.public() for c in checks]}

    @app.get("/api/doctor/report")
    def report():
        checks = [c.public() for c in doctor.run()]
        extra = {"stats": library.stats(), "audit": library.audit(), "root": system.display_path(library.root)}
        data = diagnostics.build_report(settings, checks, extra)
        name = time.strftime("vydra-report-%Y%m%d-%H%M.zip")
        return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    return app


def _save_upload(upload: UploadFile, dest: Path) -> None:
    upload.file.seek(0)
    with dest.open("wb") as out:
        shutil.copyfileobj(upload.file, out, 1 << 20)
