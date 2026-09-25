"""Непрерывность: повторы, сохранение очереди, продолжение после сбоя, сторож зависаний."""

import json
import shutil
import sys
import threading
import time

import pytest

from conftest import needs_ffmpeg, wait_for
from vydra import downloader, jobs
from vydra.downloader import Downloaded, DownloadFailed, classify
from vydra.jobs import FINAL, Job, JobManager, canonical_url, clean_work_dir
from vydra.media import Cancelled

URL = "https://www.youtube.com/watch?v=abcdefghijk"


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(jobs, "BACKOFF", (0.05, 0.05, 0.05))
    monkeypatch.setattr(jobs, "HOST_WAIT", 0.05)


def fake_download(clip_path, plan):
    """plan — список: исключение (бросить) или None (успех); вызовы записываются в plan.calls."""
    calls = []

    def fake(url, mode, quality, work_dir, **kw):
        calls.append(work_dir)
        step = plan[min(len(calls) - 1, len(plan) - 1)]
        if isinstance(step, Exception):
            raise step
        if callable(step):
            step(work_dir, kw)
        dst = work_dir / "src.mp4"
        shutil.copyfile(clip_path, dst)
        return [Downloaded(dst, {"id": "abcdefghijk", "title": "Ролик", "duration": 3, "webpage_url": url}, None)]

    fake.calls = calls
    return fake


@needs_ffmpeg
def test_transient_errors_are_retried_until_success(settings, library, make_clip, monkeypatch, fast):
    fake = fake_download(make_clip("s.mp4"), [DownloadFailed("Нет связи с сайтом", transient=True)] * 2 + [None])
    monkeypatch.setattr(jobs, "download", fake)
    manager = JobManager(settings, library)
    job = manager.submit(Job(kind="url", source=URL, mode="mp4"))
    assert wait_for(lambda: job.status in FINAL)
    assert job.status == "done", job.error
    assert job.attempt == 3 and len(fake.calls) == 3
    assert not (manager.jobs_root / job.id).exists()  # папка задачи убрана после успеха
    manager.shutdown()


def test_permanent_error_is_not_retried(settings, library, make_clip, monkeypatch, fast):
    fake = fake_download(None, [DownloadFailed("Видео приватное — скачать его нельзя.", transient=False)])
    monkeypatch.setattr(jobs, "download", fake)
    manager = JobManager(settings, library)
    job = manager.submit(Job(kind="url", source=URL, mode="mp4"))
    assert wait_for(lambda: job.status in FINAL)
    assert (job.status, job.attempt, len(fake.calls)) == ("error", 1, 1)
    assert "приватное" in job.error
    manager.shutdown()


def test_retries_are_limited(settings, library, monkeypatch, fast):
    fake = fake_download(None, [DownloadFailed("Нет связи с сайтом", transient=True)])
    monkeypatch.setattr(jobs, "download", fake)
    manager = JobManager(settings, library)
    job = manager.submit(Job(kind="url", source=URL, mode="mp4"))
    assert wait_for(lambda: job.status in FINAL)
    assert job.status == "error" and len(fake.calls) == jobs.MAX_ATTEMPTS
    manager.shutdown()


def test_cancel_while_waiting_for_retry(settings, library, monkeypatch):
    monkeypatch.setattr(jobs, "BACKOFF", (30, 30, 30))
    monkeypatch.setattr(jobs, "download", fake_download(None, [DownloadFailed("Нет связи", transient=True)]))
    manager = JobManager(settings, library)
    job = manager.submit(Job(kind="url", source=URL, mode="mp4"))
    assert wait_for(lambda: job.retry_at is not None)
    assert job.status == "queued" and "попытка 2 из 3" in job.stage
    assert manager.cancel(job.id)
    assert job.status == "cancelled" and job.retry_at is None
    manager.shutdown()


@needs_ffmpeg
def test_manual_retry(settings, library, make_clip, monkeypatch, fast):
    fake = fake_download(make_clip("s.mp4"), [DownloadFailed("приватное", transient=False), None])
    monkeypatch.setattr(jobs, "download", fake)
    manager = JobManager(settings, library)
    job = manager.submit(Job(kind="url", source=URL, mode="mp4"))
    assert wait_for(lambda: job.status in FINAL) and job.status == "error"
    manager.retry(job.id)
    assert wait_for(lambda: job.status in FINAL) and job.status == "done"
    with pytest.raises(ValueError):
        manager.retry(job.id)
    with pytest.raises(KeyError):
        manager.retry("nope")
    manager.shutdown()


@needs_ffmpeg
def test_queue_survives_kill_and_resumes_partial_data(settings, library, make_clip, monkeypatch):
    """Имитация kill -9: в файле очереди задача «downloading», в её папке — недокачанный кусок."""
    job = Job(kind="url", source=URL, mode="mp4", status="downloading", stage="Скачиваю", attempt=1)
    work = settings.work_dir / "jobs" / job.id
    work.mkdir(parents=True)
    (work / "abcdefghijk.f137.mp4.part").write_bytes(b"x" * 1000)
    settings.work_dir.joinpath(f"queue-{settings.port}.json").write_text(
        json.dumps({"version": 1, "jobs": [job.record()]}), encoding="utf-8"
    )
    seen_partial = []
    fake = fake_download(make_clip("s.mp4"), [lambda w, kw: seen_partial.append((w / "abcdefghijk.f137.mp4.part").exists())])
    monkeypatch.setattr(jobs, "download", fake)
    manager = JobManager(settings, library, persist=True)
    restored = manager.get(job.id)
    assert restored is not None and restored.resumed
    assert wait_for(lambda: restored.status in FINAL)
    assert restored.status == "done" and seen_partial == [True]  # докачка в той же папке
    manager.shutdown()
    history = json.loads(settings.work_dir.joinpath(f"queue-{settings.port}.json").read_text())["jobs"]
    assert history[0]["status"] == "done"  # история переживает перезапуск


def test_graceful_shutdown_keeps_active_jobs_for_next_start(settings, library, monkeypatch):
    started = threading.Event()

    def blocking(url, mode, quality, work_dir, cancel, **kw):
        started.set()
        cancel.wait(10)
        raise Cancelled

    monkeypatch.setattr(jobs, "download", blocking)
    manager = JobManager(settings, library, persist=True)
    job = manager.submit(Job(kind="url", source=URL, mode="mp4"))
    assert started.wait(5)
    manager.shutdown()
    assert job.status == "queued" and "Прервано" in job.stage
    stored = json.loads(settings.work_dir.joinpath(f"queue-{settings.port}.json").read_text())["jobs"]
    assert stored[0]["status"] == "queued"
    monkeypatch.setattr(jobs, "download", fake_download(None, [DownloadFailed("x", transient=False)]))
    again = JobManager(settings, library, persist=True)
    assert again.get(job.id).resumed
    again.shutdown()


def test_per_host_limit(settings, library, monkeypatch, fast):
    running, peak, release = [], [0], threading.Event()

    def slow(url, mode, quality, work_dir, **kw):
        running.append(1)
        peak[0] = max(peak[0], len(running))
        release.wait(5)
        running.pop()
        raise DownloadFailed("стоп", transient=False)

    monkeypatch.setattr(jobs, "download", slow)
    manager = JobManager(settings, library, max_parallel=3)
    first = manager.submit(Job(kind="url", source="https://www.tiktok.com/@a/video/1", mode="mp4"))
    second = manager.submit(Job(kind="url", source="https://www.tiktok.com/@a/video/2", mode="mp4"))
    assert wait_for(lambda: "Жду очереди к сайту" in (first.stage + second.stage), timeout=3)
    release.set()
    assert wait_for(lambda: first.status in FINAL and second.status in FINAL)
    assert peak[0] == 1  # с TikTok — строго по одной
    manager.shutdown()


@needs_ffmpeg
def test_duplicate_guard_finds_same_video_by_any_link(settings, library, make_clip):
    library.add(make_clip("a.mp4"), platform="youtube", stem="Ролик", ext="mp4", title="Ролик",
                source="https://www.youtube.com/watch?v=abcdefghijk")
    manager = JobManager(settings, library)
    assert manager.find_existing("https://youtu.be/abcdefghijk?si=share", "mp4", None)
    assert manager.find_existing("https://youtu.be/abcdefghijk", "both", None) is None  # MP3 ещё нет
    assert manager.find_existing("https://youtu.be/abcdefghijk", "mp4", (10.0, 20.0)) is None  # другой отрезок
    manager.shutdown()


def test_canonical_url():
    assert canonical_url("https://youtu.be/abcdefghijk?t=3") == canonical_url("youtube.com/shorts/abcdefghijk")
    assert canonical_url("https://www.tiktok.com/@nasa/video/123?lang=ru") == "tiktok:123"
    assert canonical_url("https://www.instagram.com/nasa/reel/Abc_1/") == "instagram:Abc_1"


@pytest.mark.parametrize(
    ("message", "transient"),
    [
        ("ERROR: [youtube] x: Private video. Sign in if you've been granted access", False),
        ("ERROR: Unsupported URL: https://example.com", False),
        ("ERROR: [youtube] x: Video unavailable", False),
        ("ERROR: unable to download video data: HTTP Error 429: Too Many Requests", True),
        ("ERROR: [download] Got error: ('Connection aborted.', ConnectionResetError(104))", True),
        ("HTTP Error 503: Service Unavailable", True),
        ("Не хватает места на диске: нужно 2 ГБ", False),
        # найдено матрицей загрузок: раньше считались временными и повторялись трижды впустую
        ("ERROR: [TikTok] 7139980461132074283: Your IP address is blocked from accessing this post", False),
        ("ERROR: [youtube] jfKfPfyJRdk: This live stream recording is not available.", False),
        ("какая-то новая ошибка", True),
    ],
)
def test_error_classification(message, transient):
    assert classify(message)[1] is transient


# --- поддельный воркер: сторож зависаний, падение, отмена ----------------------------------

FAKE_WORKER = """
import json, sys, time
sys.stdin.readline()  # stdin остаётся открытым: по нему приходят ответы на вопросы
mode = {mode!r}
def send(**m):
    print(json.dumps(m), flush=True)
if mode == "stall":
    send(type="phase", phase="download")
    send(type="progress", percent=1, downloaded=100)
    time.sleep(60)
elif mode == "crash":
    sys.exit(3)
elif mode == "permanent":
    send(type="error", message="Видео приватное — скачать его нельзя.", transient=False)
elif mode == "slow":
    time.sleep(60)
"""


def run_fake(tmp_path, monkeypatch, mode, cancel=None, stall=0.6):
    script = tmp_path / f"worker_{mode}.py"
    script.write_text(FAKE_WORKER.format(mode=mode))
    monkeypatch.setattr(downloader, "WORKER_CMD", [sys.executable, str(script)])
    monkeypatch.setattr(downloader, "STALL_DOWNLOAD", stall)
    started = time.monotonic()
    try:
        downloader.download(URL, "mp4", "1080", tmp_path / "w", ffmpeg=None, js_runtime=None, cookies=None,
                            reporter=downloader.Reporter(), cancel=cancel or threading.Event())
    finally:
        elapsed = time.monotonic() - started
    return elapsed


def test_stalled_worker_is_killed_and_marked_transient(tmp_path, monkeypatch):
    with pytest.raises(DownloadFailed) as info:
        elapsed = run_fake(tmp_path, monkeypatch, "stall")
    assert info.value.transient and "зависла" in str(info.value)


def test_crashed_worker_is_transient(tmp_path, monkeypatch):
    with pytest.raises(DownloadFailed) as info:
        run_fake(tmp_path, monkeypatch, "crash")
    assert info.value.transient


def test_worker_permanent_error_is_passed_through(tmp_path, monkeypatch):
    with pytest.raises(DownloadFailed) as info:
        run_fake(tmp_path, monkeypatch, "permanent")
    assert not info.value.transient and "приватное" in str(info.value)


def test_cancel_kills_worker_quickly(tmp_path, monkeypatch):
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(Cancelled):
        run_fake(tmp_path, monkeypatch, "slow", cancel=cancel, stall=60)
    assert time.monotonic() - started < 5


def test_clean_work_dir_never_touches_live_or_queued_jobs(settings):
    jobs_root = settings.work_dir / "jobs"
    live, queued, dead = (jobs_root / n for n in ("live", "queued", "dead"))
    for folder in (live, queued, dead):
        folder.mkdir(parents=True)
        (folder / "data.part").write_bytes(b"x" * 10)
    (live / ".alive").touch()
    old = time.time() - 7200
    import os

    for folder in (queued, dead):
        os.utime(folder, (old, old))
    freed = clean_work_dir(settings.work_dir, protected={"queued"}, force=True)
    assert live.exists() and queued.exists() and not dead.exists()
    assert freed == 10


def test_console_shutdown_cleans_interrupted_jobs(settings, library, monkeypatch):
    """У консоли (очередь не сохраняется) прерванную задачу никто не продолжит — её папка убирается сразу."""
    started = threading.Event()

    def blocking(url, mode, quality, work_dir, cancel, **kw):
        (work_dir / "video.part").write_bytes(b"x" * 1024)
        started.set()
        cancel.wait(10)
        raise Cancelled

    monkeypatch.setattr(jobs, "download", blocking)
    manager = JobManager(settings, library)  # как в консоли: persist=False
    running = manager.submit(Job(kind="url", source=URL, mode="mp4"))
    assert started.wait(5)
    manager.shutdown()
    assert running.status == "cancelled"
    assert not any((settings.work_dir / "jobs").iterdir())


def test_console_library_starts_no_background_ffmpeg(settings):
    """Консоль живёт секунды: фоновые постеры (ffmpeg) пережили бы её сиротами."""
    from vydra.config import Prefs
    from vydra.library import Library
    from vydra.media import Media

    def enrichers():
        return sum(1 for t in threading.enumerate() if t.name == "library-enrich")

    before = enrichers()
    Library(Prefs(settings), Media(settings), enrich=False)
    assert enrichers() == before
    from vydra import cli

    assert "enrich=False" in open(cli.__file__, encoding="utf-8").read()


@pytest.mark.parametrize("persist", [False, True])
def test_failed_conversion_keeps_upload_only_for_the_server(settings, library, persist):
    """Сервер хранит исходник для «Повторить»; консоль повторять не умеет — копию убирает (найдено Ctrl+C-тестом)."""
    manager = JobManager(settings, library, persist=persist)
    upload = manager.new_upload_dir() / "не-видео.mp4"
    upload.write_text("это не видео")
    job = manager.submit(Job(kind="file", source=upload.name, mode="mp3", input_path=upload))
    assert wait_for(lambda: job.status in FINAL)
    manager.shutdown()
    assert job.status == "error"
    assert upload.parent.exists() is persist


def test_tiktok_photo_post_goes_to_ytdlp_as_video():
    """yt-dlp не узнаёт /photo/ — была «ссылка не поддерживается»; тот же пост по /video/ отдаёт звук слайдшоу."""
    photo = "https://www.tiktok.com/@nasa/photo/7250000000000000000?lang=ru"
    assert downloader.extractor_url(photo) == "https://www.tiktok.com/@nasa/video/7250000000000000000?lang=ru"
    assert downloader.extractor_url(URL) == URL
    assert canonical_url(photo) == canonical_url(photo.replace("/photo/", "/video/"))


# --- отрезок куском ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("clip", "duration", "expected"),
    [
        ((3900, 3930), 4060, (3900.0, 3930.0)),  # 30 с из часа — куском
        ((3900, 5000), 4060, None),  # 160 с — длинный кусок YouTube отдаёт медленно: целиком
        ((10, 40), 60, None),  # ролик короткий — выигрыша нет
        ((3900, None), 4060, None),  # «до конца» — целиком
        (None, 4060, None),
        ((4050, 4100), 4060, (4050.0, 4060.0)),  # конец за длиной ролика — до конца ролика
    ],
)
def test_section_decision(clip, duration, expected):
    assert downloader.section_for(clip, {"duration": duration}) == expected


def test_live_or_disabled_never_uses_sections(monkeypatch):
    assert downloader.section_for((10, 20), {"duration": 4000, "is_live": True}) is None
    monkeypatch.setattr(downloader, "SECTION_MAX", 0)
    assert downloader.section_for((10, 20), {"duration": 4000}) is None


@needs_ffmpeg
def test_downloaded_section_is_cut_in_its_own_time(settings, library, make_clip, monkeypatch):
    """Воркер скачал только кусок 1:05:00–1:05:05 часового ролика, а ffmpeg добавил перед ним 3 с «разгона»:
    не «файл неполный», вырезаются последние 5 с файла, а в хранилище — отрезок в координатах ролика."""
    piece = make_clip("piece.mp4", seconds=8)

    def fake(url, mode, quality, work_dir, **kw):
        dst = work_dir / "src.mp4"
        shutil.copyfile(piece, dst)
        info = {"id": "abcdefghijk", "title": "Лекция", "duration": 4060, "webpage_url": url}
        return [Downloaded(dst, info, None, section=(3900.0, 3905.0), offset=3.0)]

    monkeypatch.setattr(jobs, "download", fake)
    manager = JobManager(settings, library)
    job = manager.submit(Job(kind="url", source=URL, mode="mp4", clip=(3900.0, 3905.0)))
    assert wait_for(lambda: job.status in FINAL, timeout=30)
    manager.shutdown()
    assert job.status == "done", job.error
    item = library.get(job.files[0]["id"])
    assert item.clip == [3900.0, 3905.0] and 4 <= item.duration <= 6
    assert "(1.05.00–1.05.05)" in job.files[0]["name"]
    assert manager.find_existing(URL, "mp4", (3900.0, 3905.0))
