"""Очередь задач: скачать по ссылке или сконвертировать файл, затем положить в хранилище.

Непрерывность:
- очередь сервера сохраняется в <work>/queue-<порт>.json: после падения, перезапуска или kill -9
  незавершённые задачи продолжаются сами (resumed), история готовых переживает перезапуск;
- у каждой задачи своя постоянная папка <work>/jobs/<id>: .part-файлы докачиваются, а не качаются заново;
  папка удаляется после успеха или окончательной ошибки/отмены;
- временные сбои (сеть, 5xx/429, зависание, падение воркера, битый файл) повторяются с паузой 5 → 15 с,
  всего до трёх попыток; постоянные (приватное, удалено, нет места) — сразу ошибка;
- при остановке сервера активные задачи помечаются прерванными и продолжаются при следующем запуске.
"""

from __future__ import annotations

import errno
import json
import logging
import random
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, fields
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import Settings
from .downloader import (
    Downloaded,
    DownloadFailed,
    NoSpace,
    Reporter,
    check_free,
    classify,
    download,
)
from .fsutil import atomic_write, friendly_os_error
from .library import Library, LibraryUnavailable
from .media import Cancelled, Clip, IntegrityError, MediaError, NoAudio
from .naming import safe_stem, title_for
from .timecode import clip_label

log = logging.getLogger("jobs")

ACTIVE = {"queued", "downloading", "converting", "saving", "waiting"}  # waiting — ждём ответа пользователя
QUESTION_TIMEOUT = 30 * 60  # без ответа полчаса — задачу останавливаем
FINAL = {"done", "error", "cancelled"}
TARGETS = {"mp4": ["mp4"], "mp3": ["mp3"], "both": ["mp4", "mp3"]}
MAX_ATTEMPTS = 3
BACKOFF = (5, 15, 45)  # пауза перед 2-й, 3-й … попыткой
HISTORY = 200  # сколько завершённых задач помнить
HEARTBEAT = 30  # как часто живой процесс отмечается в папках своих задач
STALE_WORK = 3600  # папка задачи без отметки дольше часа и без хозяина — мусор
STALE_UPLOAD = 7 * 86400
PERMANENT_OS_ERRORS = {errno.ENOSPC, errno.EACCES, errno.EPERM, errno.EROFS, errno.ENAMETOOLONG}
HOST_LIMITS = {"tiktok": 1, "instagram": 1}  # эти сайты быстро отвечают 429 на параллельные загрузки
DEFAULT_HOST_LIMIT = 2
HOST_WAIT = 2.0  # сайт занят другой задачей — проверить снова через столько секунд


def canonical_url(url: str | None) -> str | None:
    """Один и тот же ролик по разным ссылкам (youtu.be/x, youtube.com/watch?v=x&t=5, /shorts/x) → один ключ."""
    if not url:
        return None
    parsed = urlparse(url if "://" in url else "https://" + url)
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    path = parsed.path
    if host in ("youtube.com", "music.youtube.com", "youtu.be") or host.endswith(".youtube.com"):
        video = parse_qs(parsed.query).get("v", [None])[0]
        if host == "youtu.be":
            video = path.strip("/").split("/")[0]
        if not video and (m := re.match(r"^/(shorts|live|embed|v)/([\w-]{6,})", path)):
            video = m.group(2)
        if video:
            return f"youtube:{video}"
    if host.endswith("tiktok.com") and (m := re.search(r"/(?:video|photo)/(\d+)", path)):
        return f"tiktok:{m.group(1)}"
    if host.endswith("instagram.com") and (m := re.match(r"^/(?:[\w.]+/)?(?:p|reel|reels|tv)/([\w-]+)", path)):
        return f"instagram:{m.group(1)}"
    return f"{host}{path.rstrip('/')}".lower() or None


def detect_platform(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for domain, name in (
        ("youtube.com", "youtube"),
        ("youtu.be", "youtube"),
        ("tiktok.com", "tiktok"),
        ("instagram.com", "instagram"),
    ):
        if host == domain or host.endswith("." + domain):
            return name
    return "other"


@dataclass
class Job:
    kind: str  # url | file
    source: str  # ссылка или исходное имя файла
    mode: str  # mp4 | mp3 | both
    quality: str = "1080"
    bitrate: int = 192
    clip: Clip | None = None
    folder: str | None = None  # папка хранилища для результата (None — <Платформа>/<Видео|Аудио>)
    confirm_playlist: bool = False  # большой плейлист качаем только с явного согласия
    input_path: Path | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created: float = field(default_factory=time.time)
    status: str = "queued"
    stage: str = "В очереди"
    progress: float | None = 0.0
    speed: float | None = None
    eta: int | None = None
    title: str | None = None
    uploader: str | None = None
    duration: float | None = None
    platform: str = "other"
    index: int = 1
    count: int = 1
    files: list[dict] = field(default_factory=list)
    error: str | None = None
    warning: str | None = None
    thumb: Path | None = None
    finished: float | None = None
    attempt: int = 1
    max_attempts: int = MAX_ATTEMPTS
    retry_at: float | None = None
    resumed: bool = False
    cancel: threading.Event = field(default_factory=threading.Event)
    interrupted: bool = False  # остановка сервера, а не отмена пользователем
    auto_accept: bool = False  # на «не выходит — вот вариант» соглашаться без вопроса
    notes: list[str] = field(default_factory=list)  # что пошло не по плану и что сделали вместо
    decisions: dict = field(default_factory=dict)  # код вопроса → ответ: повторная попытка не переспрашивает
    question: dict | None = None
    answer: str | None = None
    answered: threading.Event = field(default_factory=threading.Event)

    @property
    def active(self) -> bool:
        return self.status in ACTIVE

    def public(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "source": self.source,
            "mode": self.mode,
            "quality": self.quality,
            "bitrate": self.bitrate,
            "clip": list(self.clip) if self.clip else None,
            "folder": self.folder,
            "confirm_playlist": self.confirm_playlist,
            "status": self.status,
            "stage": self.stage,
            "progress": None if self.progress is None else round(self.progress, 1),
            "speed": self.speed,
            "eta": self.eta,
            "title": self.title,
            "uploader": self.uploader,
            "duration": self.duration,
            "platform": self.platform,
            "index": self.index,
            "count": self.count,
            "files": list(self.files),
            "error": self.error,
            "warning": self.warning,
            "thumb": self.thumb is not None,
            "created": self.created,
            "finished": self.finished,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "retry_at": self.retry_at,
            "resumed": self.resumed,
            "question": self.question,
            "auto_accept": self.auto_accept,
            "notes": list(self.notes),
        }

    _TRANSIENT_FIELDS = {"cancel", "interrupted", "speed", "eta", "question", "answer", "answered"}

    def record(self) -> dict:
        data = {f.name: getattr(self, f.name) for f in fields(self) if f.name not in self._TRANSIENT_FIELDS}
        data["clip"] = list(self.clip) if self.clip else None
        data["input_path"] = str(self.input_path) if self.input_path else None
        data["thumb"] = str(self.thumb) if self.thumb else None
        data["files"] = list(self.files)
        return data

    @classmethod
    def from_record(cls, data: dict) -> Job:
        known = {f.name for f in fields(cls)} - cls._TRANSIENT_FIELDS
        kwargs = {k: v for k, v in data.items() if k in known}
        kwargs["clip"] = tuple(data["clip"]) if data.get("clip") else None
        kwargs["input_path"] = Path(data["input_path"]) if data.get("input_path") else None
        kwargs["thumb"] = Path(data["thumb"]) if data.get("thumb") else None
        return cls(**kwargs)


class _JobReporter(Reporter):
    def __init__(self, manager: JobManager, job: Job):
        self.manager = manager
        self.job = job

    def item(self, info: dict, thumbnail: Path | None) -> None:
        job = self.job
        job.status = "downloading"
        job.title = title_for(info)
        job.uploader = info.get("uploader") or info.get("channel")
        job.duration = info.get("duration")
        job.index = info.get("playlist_index") or 1
        job.count = info.get("n_entries") or info.get("playlist_count") or 1
        if thumbnail and job.thumb is None:
            target = self.manager.thumbs_dir / f"{job.id}{thumbnail.suffix}"
            try:
                shutil.copyfile(thumbnail, target)
                job.thumb = target
            except OSError:
                pass
        self.manager.changed()

    def progress(self, percent: float | None, speed: float | None, eta: int | None) -> None:
        job = self.job
        job.status = "downloading"
        job.stage = "Докачиваю" if job.attempt > 1 or job.resumed else "Скачиваю"
        job.progress = percent
        job.speed = speed
        job.eta = eta

    def stage(self, text: str) -> None:
        self.job.stage = text
        self.job.progress = None
        self.job.speed = self.job.eta = None

    def question(self, question: dict) -> str:
        """«Не выходит — вот другой вариант — продолжить?» Блокирует поток задачи до ответа."""
        job = self.job
        options = {o["id"]: o["label"] for o in question["options"]}
        remembered = job.decisions.get(question["code"])
        if remembered in options:
            return remembered
        if job.auto_accept:
            answer = question["default"]
            job.notes.append(f"{question['title']} — {options[answer].lower()} (автоматически)")
            job.decisions[question["code"]] = answer
            self.manager.changed()
            return answer
        job.answer = None
        job.answered.clear()
        stage = job.stage
        job.status, job.stage, job.progress, job.question = "waiting", question["title"], None, question
        job.speed = job.eta = None
        self.manager.changed()
        log.info("job %s asks %s: %s", job.id, question["code"], question["message"])
        deadline = time.monotonic() + QUESTION_TIMEOUT
        try:
            while not job.answered.wait(0.5):
                if job.cancel.is_set():
                    raise Cancelled
                if time.monotonic() > deadline:
                    job.cancel.set()
                    raise Cancelled
        finally:
            job.question = None
        answer = job.answer or "cancel"
        if answer == "cancel":
            job.cancel.set()
            raise Cancelled
        job.decisions[question["code"]] = answer
        job.notes.append(f"{question['title']} — {options[answer].lower()}")
        job.status, job.stage = "downloading", stage
        self.manager.changed()
        return answer

    def note(self, text: str) -> None:
        if text not in self.job.notes:
            self.job.notes.append(text)
            self.manager.changed()

    def mode(self, mode: str) -> None:
        self.job.mode = mode
        self.manager.changed()

    def clip(self, clip) -> None:
        self.job.clip = tuple(clip) if clip else None
        self.manager.changed()


class JobManager:
    def __init__(
        self,
        settings: Settings,
        library: Library,
        max_parallel: int | None = None,
        persist: bool = False,
    ):
        self.settings = settings
        self.library = library
        self.media = library.media
        self.jobs_root = settings.work_dir / "jobs"
        self.thumbs_dir = settings.work_dir / "thumbs"
        self.uploads_dir = settings.work_dir / "uploads"
        for directory in (self.jobs_root, self.thumbs_dir, self.uploads_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.store = settings.work_dir / f"queue-{settings.port}.json" if persist else None
        self.version = 0  # растёт при каждом изменении состояния задач (для SSE)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._running: set[str] = set()
        self._timers: dict[str, threading.Timer] = {}
        self._stopping = threading.Event()
        self._dirty = threading.Event()
        self._hosts: dict[str, int] = {}  # сколько загрузок идёт с каждого сайта
        self._pieces: set[str] = set()  # задачи, чья папка хранит кусок ролика (а не ролик целиком)
        self.info_cache = settings.work_dir / "info"  # полная информация из превью для быстрого старта
        workers = max_parallel or settings.max_parallel
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")

        resumable = self._load()
        clean_work_dir(settings.work_dir, protected=self.protected_ids())
        threading.Thread(target=self._heartbeat, name="jobs-heartbeat", daemon=True).start()
        if self.store:
            threading.Thread(target=self._saver, name="jobs-saver", daemon=True).start()
        for job in resumable:
            self._schedule(job, max(0.0, (job.retry_at or 0) - time.time()))

    # --- публичное API -------------------------------------------------------------------

    def submit(self, job: Job) -> Job:
        if job.kind == "url":
            job.platform = detect_platform(job.source)
        else:
            job.platform = "file"
            job.title = job.title or Path(job.source).stem
        with self._lock:
            self._jobs[job.id] = job
        self.changed()
        self._pool.submit(self._run, job)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def all(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created, reverse=True)

    def list(self) -> list[dict]:
        return [j.public() for j in self.all()]

    def protected_ids(self) -> set[str]:
        """Задачи, чьи папки трогать нельзя: свои незавершённые + чужие из файлов очередей."""
        with self._lock:
            own = {j.id for j in self._jobs.values() if j.active}
        return own | queued_ids_on_disk(self.settings.work_dir)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or not job.active:
                return False
            job.cancel.set()
            timer = self._timers.pop(job_id, None)
            if timer:
                timer.cancel()
            if job.id not in self._running:  # ждёт в очереди или паузы перед повтором
                self._finish(job, "cancelled")
        return True

    def find_existing(self, url: str, mode: str, clip) -> list[dict] | None:
        """Файлы этого же ролика (та же ссылка, формат и отрезок), которые уже лежат в хранилище."""
        key = canonical_url(url)
        if not key:
            return None
        wanted = {"mp4": {"video"}, "mp3": {"audio"}, "both": {"video", "audio"}}[mode]
        clip_list = list(clip) if clip else None
        found = self.library.find(
            lambda i: i.type in wanted and canonical_url(i.source) == key and (i.clip or None) == clip_list
        )
        by_type: dict[str, object] = {}
        for item in sorted(found, key=lambda i: i.added, reverse=True):
            by_type.setdefault(item.type, item)
        if set(by_type) != wanted:
            return None
        return [
            {"id": i.id, "name": i.name, "path": i.path, "folder": i.folder,
             "type": "mp4" if i.type == "video" else "mp3", "size": i.size, "part": 1}
            for i in by_type.values()
        ]  # fmt: skip

    def add_existing(self, job: Job, files: list[dict]) -> Job:
        """Задача, которая сразу «готова»: такой файл уже есть в хранилище."""
        job.platform = detect_platform(job.source) if job.kind == "url" else "file"
        item = self.library.get(files[0]["id"])
        job.title = item.title if item else job.title
        job.uploader = item.uploader if item else None
        job.files = files
        job.status, job.stage, job.progress = "done", "Готово", 100.0
        job.warning = "Уже есть в хранилище — повторно не скачивал"
        job.finished = time.time()
        with self._lock:
            self._jobs[job.id] = job
        self.changed()
        return job

    def retry(self, job_id: str) -> Job:
        """Ручной повтор ошибки или отменённой задачи: те же настройки, счётчик попыток с нуля."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status not in ("error", "cancelled"):
                raise ValueError("Повторить можно только задачу с ошибкой или отменённую")
            if job.kind == "file" and not (job.input_path and job.input_path.is_file()):
                raise ValueError("Исходный файл уже удалён — загрузите его снова")
            job.cancel = threading.Event()
            job.interrupted = False
            job.attempt, job.retry_at, job.error, job.warning = 1, None, None, None
            job.status, job.stage, job.progress, job.finished = "queued", "В очереди", 0.0, None
            job.speed = job.eta = None
        self.changed()
        self._pool.submit(self._run, job)
        return job

    def answer(self, job_id: str, option: str) -> Job:
        """Ответ на вопрос задачи. KeyError — нет задачи, ValueError — она ни о чём не спрашивает."""
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        question = job.question
        if job.status != "waiting" or not question:
            raise ValueError("Задача ни о чём не спрашивает")
        if option not in {o["id"] for o in question["options"]}:
            raise ValueError("Такого варианта нет")
        job.answer = option
        job.answered.set()
        return job

    def clear_finished(self) -> None:
        with self._lock:
            for job_id in [k for k, j in self._jobs.items() if not j.active]:
                self._forget(self._jobs.pop(job_id))
        self.changed()

    def changed(self) -> None:
        self.version += 1
        self._dirty.set()

    def shutdown(self, timeout: float = 10.0) -> None:
        """Мягкая остановка: активные задачи прерываются, но остаются в очереди до следующего запуска."""
        self._stopping.set()
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            for job in self._jobs.values():
                if job.active:
                    job.interrupted = True
                    job.cancel.set()
        self._pool.shutdown(wait=False, cancel_futures=True)
        deadline = time.monotonic() + timeout
        while self._running and time.monotonic() < deadline:
            time.sleep(0.05)
        with self._lock:
            for job in self._jobs.values():
                if job.active and self.store is None:
                    self._finish(job, "cancelled")  # консоль: продолжать некому — прибираем сразу
                elif job.active:
                    job.status, job.stage, job.progress = "queued", "Прервано — продолжу при следующем запуске", None
                    job.speed = job.eta = None
        self._save()

    def new_upload_dir(self) -> Path:
        path = self.uploads_dir / uuid.uuid4().hex[:12]
        path.mkdir(parents=True)
        return path

    # --- сохранение очереди --------------------------------------------------------------

    def _load(self) -> list[Job]:
        if self.store is None or not self.store.is_file():
            return []
        try:
            records = json.loads(self.store.read_text(encoding="utf-8")).get("jobs", [])
        except (OSError, ValueError) as exc:
            log.warning("очередь %s не читается (%s) — начинаю с пустой", self.store, exc)
            return []
        resumable = []
        for record in records:
            try:
                job = Job.from_record(record)
            except (TypeError, ValueError):
                continue
            if job.thumb and not job.thumb.is_file():
                job.thumb = None
            if job.status in ACTIVE:
                job.resumed = True
                job.status, job.progress = "queued", 0.0
                job.stage = "Продолжаю после перезапуска"
                resumable.append(job)
            self._jobs[job.id] = job
        log.info("очередь восстановлена: %d задач, продолжаю %d", len(self._jobs), len(resumable))
        return resumable

    def _save(self) -> None:
        if self.store is None:
            return
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created)
            finished = [j for j in jobs if not j.active]
            for old in finished[:-HISTORY]:  # история не растёт бесконечно
                self._forget(self._jobs.pop(old.id))
            records = [j.record() for j in sorted(self._jobs.values(), key=lambda j: j.created)]
        try:
            atomic_write(self.store, json.dumps({"version": 1, "jobs": records}, ensure_ascii=False, default=str))
        except OSError as exc:
            log.warning("не удалось сохранить очередь: %s", exc)

    def _saver(self) -> None:
        while not self._stopping.is_set():
            if self._dirty.wait(1.0):
                self._dirty.clear()
                self._save()
                time.sleep(0.3)  # не чаще ~3 раз в секунду

    def _heartbeat(self) -> None:
        while not self._stopping.wait(HEARTBEAT):
            with self._lock:
                ids = [j.id for j in self._jobs.values() if j.active]
            for job_id in ids:
                alive = self.jobs_root / job_id / ".alive"
                if alive.parent.is_dir():
                    try:
                        alive.touch()
                    except OSError:
                        pass

    def _forget(self, job: Job) -> None:
        if job.thumb:
            job.thumb.unlink(missing_ok=True)
        if job.input_path is not None and job.input_path.parent.parent == self.uploads_dir:
            shutil.rmtree(job.input_path.parent, ignore_errors=True)

    # --- выполнение ----------------------------------------------------------------------

    def _schedule(self, job: Job, delay: float) -> None:
        if delay <= 0:
            self._pool.submit(self._run, job)
            return

        def fire() -> None:
            with self._lock:
                self._timers.pop(job.id, None)
            if job.status == "queued" and not job.cancel.is_set() and not self._stopping.is_set():
                self._pool.submit(self._run, job)

        timer = threading.Timer(delay, fire)
        timer.daemon = True
        with self._lock:
            self._timers[job.id] = timer
        timer.start()

    def _run(self, job: Job) -> None:
        with self._lock:
            if job.cancel.is_set() or self._stopping.is_set() or job.status != "queued":
                return
            self._running.add(job.id)
        work = self.jobs_root / job.id
        try:
            work.mkdir(parents=True, exist_ok=True)
            (work / ".alive").touch()
            job.retry_at = None
            job.error = None
            self.changed()
            log.info(
                "job %s start kind=%s platform=%s mode=%s attempt=%d/%d resumed=%s",
                job.id, job.kind, job.platform, job.mode, job.attempt, job.max_attempts, job.resumed,
            )  # fmt: skip
            if job.kind == "url":
                self._run_url(job, work)
            else:
                self._run_file(job, work)
            self._finish(job, "done")
        except _HostBusy:
            job.status, job.stage, job.progress = "queued", "Жду очереди к сайту", None
            self._schedule(job, HOST_WAIT)
        except Cancelled:
            self._interrupted_or_cancelled(job)
        except Exception as exc:  # noqa: BLE001 — любая ошибка должна дойти до интерфейса
            if job.cancel.is_set():
                self._interrupted_or_cancelled(job)
            else:
                message, transient = _explain(exc)
                log.warning("job %s failed attempt=%d transient=%s: %s", job.id, job.attempt, transient, message)
                if isinstance(exc, IntegrityError) or job.id in self._pieces:
                    # битый файл — с нуля; кусок ролика — тоже: yt-dlp принял бы его за уже скачанный ролик целиком
                    _wipe(work)
                if transient and job.attempt < job.max_attempts and not self._stopping.is_set():
                    self._retry_later(job, message, rate_limited=getattr(exc, "rate_limited", False))
                else:
                    self._finish(job, "error", message)
        finally:
            job.speed = job.eta = None
            with self._lock:
                self._running.discard(job.id)
                self._pieces.discard(job.id)
            self.changed()

    def _interrupted_or_cancelled(self, job: Job) -> None:
        # продолжить после перезапуска может только сервер (очередь на диске); у консоли прерванная
        # задача — это отмена: её папку с недокачанным убираем сразу, а не через час
        if job.interrupted and self.store is not None:
            job.status, job.stage, job.progress = "queued", "Прервано — продолжу при следующем запуске", None
        else:
            self._finish(job, "cancelled")

    def _retry_later(self, job: Job, reason: str, rate_limited: bool = False) -> None:
        delay = BACKOFF[min(job.attempt - 1, len(BACKOFF) - 1)] * (3 if rate_limited else 1)
        delay = round(delay + random.uniform(0, delay * 0.3))  # разброс: задачи не бьют в сайт одновременно
        job.attempt += 1
        job.retry_at = time.time() + delay
        job.status, job.progress = "queued", None
        job.stage = f"{reason.rstrip('.')} — повтор через {delay} с (попытка {job.attempt} из {job.max_attempts})"
        log.info("job %s: временная ошибка (%s), повтор через %s с", job.id, reason, delay)
        self._schedule(job, delay)

    def _finish(self, job: Job, status: str, error: str | None = None) -> None:
        job.status = status
        job.stage = {"done": "Готово", "error": "Ошибка", "cancelled": "Отменено"}[status]
        job.progress = 100.0 if status == "done" else job.progress
        job.error = error
        job.retry_at = None
        job.finished = time.time()
        log.info("job %s %s files=%d error=%s", job.id, status, len(job.files), error)
        _wipe(self.jobs_root / job.id)
        # исходник своего файла храним до успеха: без него не получится «Повторить» (у консоли повтора нет)
        if (job.input_path is not None and job.input_path.parent.parent == self.uploads_dir
                and (status == "done" or self.store is None)):
            shutil.rmtree(job.input_path.parent, ignore_errors=True)
        self.changed()

    def _run_url(self, job: Job, work: Path) -> None:
        host = job.platform if job.platform != "other" else (urlparse(job.source).hostname or "other")
        limit = HOST_LIMITS.get(host, DEFAULT_HOST_LIMIT)
        with self._lock:
            if self._hosts.get(host, 0) >= limit:
                raise _HostBusy
            self._hosts[host] = self._hosts.get(host, 0) + 1
        try:
            job.status = "downloading"
            items = download(
                job.source,
                job.mode,
                job.quality,
                work,
                ffmpeg=self.settings.ffmpeg,
                js_runtime=self.settings.js_runtime,
                cookies=self.settings.cookies_file,
                reporter=_JobReporter(self, job),
                cancel=job.cancel,
                library_dir=self.library.root,
                info_cache=self.info_cache,
                confirm_playlist=job.confirm_playlist,
                clip=job.clip,
                allow_section=job.attempt == 1,  # повтор после сбоя — надёжный путь: ролик целиком
            )
        finally:
            with self._lock:
                self._hosts[host] = max(0, self._hosts.get(host, 1) - 1)
        if any(item.section for item in items):
            self._pieces.add(job.id)
        if any(item.watermarked for item in items):
            job.warning = "Версии без водяного знака не нашлось — сохранена версия с ним"
        for n, item in enumerate(items, 1):
            job.index, job.count = n, len(items)
            self._check_source(item)
            self._convert_item(job, item, work, n)

    def _check_source(self, item: Downloaded) -> None:
        """Скачанный файл читается и не обрезан (иначе — повтор с нуля)."""
        try:
            probe = self.media.probe(item.path)
        except MediaError as exc:
            raise IntegrityError("Скачанный файл повреждён") from exc
        expected = item.section[1] - item.section[0] if item.section else item.info.get("duration")
        if expected and probe.duration and probe.duration < expected * 0.9 - 2:
            raise IntegrityError(f"Скачалось {probe.duration:.0f} с из {expected:.0f} с — файл неполный")

    def _convert_item(self, job: Job, item: Downloaded, work: Path, n: int) -> None:
        info = item.info
        title = title_for(info)
        if job.count == 1:
            job.title = title
        date = info.get("upload_date") or ""
        meta = {
            "title": title.strip()[:200],
            "artist": info.get("uploader") or info.get("channel") or "",
            "comment": info.get("webpage_url") or job.source,
            "date": date[:4],
        }
        cover = self.media.make_cover(item.thumbnail, work / f"cover-{n}.jpg") if item.thumbnail else None
        stem = safe_stem(title, fallback=f"{job.platform}-{info.get('id') or n}")
        # Отрезок вырезаем сами после скачивания: диапазоны yt-dlp через ffmpeg для YouTube
        # обрываются (googlevideo рвёт длинные запросы), а так рез точный до кадра на любом сайте
        clip = job.clip
        if clip:
            stem = f"{stem} ({clip_label(clip, '–').replace(':', '.')})"
        source = info.get("webpage_url") or job.source
        cut = clip
        if clip and item.section:  # скачан только кусок: время отрезка — в координатах файла
            shift = item.section[0] - item.offset
            end = item.section[1] if clip[1] is None else min(clip[1], item.section[1])
            cut = (max(0.0, clip[0] - shift), end - shift)
        self._convert(job, item.path, stem, meta, cover, work, clip=cut, source=source, part=n, saved_clip=clip)

    def _run_file(self, job: Job, work: Path) -> None:
        if job.input_path is None or not job.input_path.is_file():
            raise MediaError("Исходный файл пропал — загрузите его снова")
        size = job.input_path.stat().st_size
        try:
            check_free([(work, int(size * 1.5) + 64 * 1024 * 1024), (self.library.root, int(size * 1.5))])
        except NoSpace as exc:
            raise DownloadFailed(str(exc)) from exc
        stem = safe_stem(Path(job.source).stem, fallback="converted")
        if job.clip:
            stem = f"{stem} ({clip_label(job.clip, '–').replace(':', '.')})"
        self._convert(job, job.input_path, stem, {}, None, work, clip=job.clip, source=None, part=1)

    def _convert(
        self,
        job: Job,
        src: Path,
        stem: str,
        meta: dict,
        cover: Path | None,
        work: Path,
        *,
        clip: Clip | None,
        source: str | None,
        part: int,
        saved_clip: Clip | None = None,  # отрезок в координатах ролика — для хранилища (clip — в координатах файла)
    ) -> None:
        for ext in TARGETS[job.mode]:
            if job.cancel.is_set():
                raise Cancelled
            # уже сохранено прошлой попыткой (упали между MP4 и MP3) — второй копии не делаем
            if any(f.get("type") == ext and f.get("part", 1) == part and self._still_there(f) for f in job.files):
                continue
            action = "Вырезаю отрезок в" if clip else "Конвертирую в"
            job.status, job.stage, job.progress = "converting", f"{action} {ext.upper()}", 0.0
            self.changed()

            def on_progress(pct: float) -> None:
                job.progress = pct

            tmp = work / f"out-{part}.{ext}"
            tmp.unlink(missing_ok=True)
            if ext == "mp4":
                self.media.to_mp4(src, tmp, meta, on_progress, job.cancel, clip=clip)
            else:
                try:
                    self.media.to_mp3(src, tmp, job.bitrate, meta, cover, on_progress, job.cancel, clip=clip)
                except NoAudio:
                    if job.mode != "both" or not any(f.get("part", 1) == part for f in job.files):
                        raise
                    # просили «оба», а звука в ролике нет: видео уже сохранено — это не ошибка задачи
                    job.warning = "В ролике нет звука — сохранено только видео, MP3 сделать не из чего"
                    self.changed()
                    continue
            if job.cancel.is_set():
                raise Cancelled

            job.status, job.stage, job.progress = "saving", "Кладу в хранилище", None
            self.changed()
            folder = job.folder
            if folder and not self.library.folder_exists(folder):
                job.warning = f"Папки «{folder}» больше нет — файл сохранён в папку по умолчанию"
                folder = None
            item = self.library.add(
                tmp,
                platform=job.platform,
                stem=stem,
                ext=ext,
                title=meta.get("title") or stem,
                source=source,
                uploader=meta.get("artist") or None,
                cover=cover,
                folder=folder,
                clip=saved_clip or clip or job.clip,
            )
            job.files.append(
                {
                    "id": item.id, "name": Path(item.path).name, "path": item.path, "folder": item.folder,
                    "type": ext, "size": item.size, "part": part,
                }
            )  # fmt: skip
            self.changed()

    def _still_there(self, file: dict) -> bool:
        item = self.library.get(file.get("id", ""))
        return item is not None and self.library.abs_path(item).is_file()


class _HostBusy(Exception):
    """С этого сайта уже качается столько, сколько можно — подождать без траты попытки."""


def _explain(exc: Exception) -> tuple[str, bool]:
    """(понятный текст, стоит ли повторять)."""
    if isinstance(exc, DownloadFailed):
        return str(exc), exc.transient
    if isinstance(exc, IntegrityError):
        return str(exc), True
    if isinstance(exc, LibraryUnavailable):
        return "Папка хранилища недоступна — подключите диск или выберите другую папку в настройках", False
    if isinstance(exc, MediaError | ValueError | NoSpace):
        return str(exc), False
    if isinstance(exc, OSError):
        return friendly_os_error(exc), exc.errno not in PERMANENT_OS_ERRORS
    log.exception("неожиданная ошибка задачи", exc_info=exc)
    return classify(str(exc))


def _wipe(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def queued_ids_on_disk(work_dir: Path) -> set[str]:
    """id незавершённых задач из всех файлов очередей (любой порт) — их данные нужны для продолжения."""
    ids: set[str] = set()
    for store in work_dir.glob("queue-*.json"):
        try:
            for record in json.loads(store.read_text(encoding="utf-8")).get("jobs", []):
                if record.get("status") in ACTIVE:
                    ids.add(record.get("id"))
        except (OSError, ValueError, AttributeError):
            continue
    return ids


def clean_work_dir(work_dir: Path, protected: set[str], force: bool = False) -> int:
    """Убирает брошенные папки задач и загрузок. Никогда не трогает защищённые задачи и папки
    со свежей отметкой .alive (их прямо сейчас обрабатывает живой процесс — сервер или консоль).
    force=True — не ждать часа (кнопка «Очистить» у доктора). Возвращает освобождённые байты."""
    now = time.time()
    freed = 0
    jobs_root = work_dir / "jobs"
    if jobs_root.is_dir():
        for folder in jobs_root.iterdir():
            if folder.name in protected:
                continue
            alive = folder / ".alive"
            try:
                beat = alive.stat().st_mtime if alive.exists() else folder.stat().st_mtime
            except OSError:
                continue
            if now - beat < (HEARTBEAT * 4 if force else STALE_WORK):
                continue
            freed += dir_size(folder)
            _wipe(folder)
    uploads = work_dir / "uploads"
    if uploads.is_dir():
        referenced = _uploads_in_queues(work_dir)
        for folder in uploads.iterdir():
            try:
                age = now - folder.stat().st_mtime
            except OSError:
                continue
            if folder.name in referenced and age < STALE_UPLOAD:
                continue
            if age < (HEARTBEAT * 4 if force else STALE_WORK):
                continue  # только что загружен, задача ещё не создана
            freed += dir_size(folder)
            _wipe(folder)
    legacy = work_dir / "sessions"  # раскладка первых версий
    if legacy.is_dir():
        freed += dir_size(legacy)
        _wipe(legacy)
    return freed


def _uploads_in_queues(work_dir: Path) -> set[str]:
    names: set[str] = set()
    for store in work_dir.glob("queue-*.json"):
        try:
            for record in json.loads(store.read_text(encoding="utf-8")).get("jobs", []):
                if record.get("input_path"):
                    names.add(Path(record["input_path"]).parent.name)
        except (OSError, ValueError, AttributeError):
            continue
    return names


def dir_size(path: Path) -> int:
    total = 0
    if path.is_dir():
        for file in path.rglob("*"):
            try:
                if file.is_file():
                    total += file.stat().st_size
            except OSError:
                pass
    return total
