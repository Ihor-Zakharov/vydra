"""«Не выходит — вот другой вариант — продолжить?»: выбор замены, протокол вопросов, задачи в ожидании ответа."""

import io
import json
import sys
import threading

import pytest
from yt_dlp import YoutubeDL

from conftest import needs_ffmpeg, wait_for
from test_reliability import URL, fake_download
from vydra import downloader, jobs
from vydra.downloader import _chosen, _describe, _exclude, _review, _worse, format_spec
from vydra.jobs import FINAL, Job, JobManager
from vydra.media import Cancelled


def fmt(fid, *, v="none", a="none", h=None, w=None, note=None, abr=None, ext="mp4"):
    return {"format_id": fid, "url": f"https://example.com/{fid}", "ext": ext, "vcodec": v, "acodec": a,
            "height": h, "width": w, "format_note": note, "abr": abr, "tbr": 1000, "protocol": "https"}


def processed(formats, mode="mp4", quality="1080", duration=60):
    spec, sort = format_spec(mode, quality)
    ydl = YoutubeDL({"format": spec, "format_sort": sort, "quiet": True, "no_warnings": True, "simulate": True})
    info = ydl.process_ie_result(
        {"id": "x", "title": "t", "extractor": "test", "extractor_key": "Test", "duration": duration,
         "webpage_url": "https://example.com/v", "formats": formats},
        download=False,
    )
    return ydl, info


class Asked:
    """Подменяет stdin воркера ответами и собирает отправленные сообщения."""

    def __init__(self, monkeypatch, *answers):
        monkeypatch.setattr(sys, "stdin", io.StringIO("".join(json.dumps({"answer": a}) + "\n" for a in answers)))
        self.sent = []

    def __call__(self, **msg):
        self.sent.append(msg)

    def kinds(self, kind):
        return [m for m in self.sent if m["type"] == kind]


# --- чистые функции --------------------------------------------------------------------------


def test_exclude_adds_filter_to_every_atom():
    assert _exclude("bv*[x]+ba/b", ["251", "137-drc"]) == (
        "bv*[x][format_id!='251'][format_id!='137-drc']+ba[format_id!='251'][format_id!='137-drc']"
        "/b[format_id!='251'][format_id!='137-drc']"
    )


def test_worse_detects_real_downgrades_only():
    hd = {"ids": [], "video": True, "audio": True, "side": 1080, "abr": 128, "watermarked": False}
    assert not _worse(dict(hd), hd)
    assert _worse(dict(hd, side=480), hd)
    assert _worse(dict(hd, audio=False), hd)
    assert _worse(dict(hd, watermarked=True), hd)
    assert _describe(dict(hd, audio=False)) == "1080p без звука"
    assert _describe({"video": False, "audio": True, "side": 0, "abr": 129.4}) == "звук 129 кбит/с"


# --- проверка до загрузки --------------------------------------------------------------------


def test_watermarked_only_asks_before_download(monkeypatch):
    ydl, info = processed([fmt("download", v="h264", a="aac", note="watermarked", h=1280, w=720)])
    send = Asked(monkeypatch, "accept")
    _review(ydl, info, {"mode": "mp4", "quality": "1080"}, send)
    assert send.kinds("question")[0]["question"]["code"] == "watermarked"


def test_audio_only_link_offers_mp3(monkeypatch):
    ydl, info = processed([fmt("a1", a="opus", abr=160, ext="webm")])
    send, req = Asked(monkeypatch, "mp3"), {"mode": "mp4", "quality": "1080"}
    info = _review(ydl, info, req, send)
    assert send.kinds("question")[0]["question"]["code"] == "no_video"
    assert req["mode"] == "mp3" and send.kinds("mode") == [{"type": "mode", "mode": "mp3"}]
    assert _chosen(info)["audio"]


def test_missing_quality_is_a_note_not_a_question(monkeypatch):
    ydl, info = processed([fmt("v720", v="avc1", a="mp4a", h=1280, w=720)])
    send = Asked(monkeypatch)
    _review(ydl, info, {"mode": "mp4", "quality": "1080"}, send)
    assert not send.kinds("question")
    assert "1080p у ролика нет" in send.kinds("note")[0]["text"]


def test_clip_after_the_end_offers_whole_video(monkeypatch):
    ydl, info = processed([fmt("v", v="avc1", a="mp4a", h=720, w=1280)], duration=30)
    send = Asked(monkeypatch, "whole")
    _review(ydl, info, {"mode": "mp4", "quality": "720", "clip": [45, 60]}, send)
    assert send.kinds("question")[0]["question"]["code"] == "clip_beyond"
    assert send.kinds("clip") == [{"type": "clip", "clip": None}]


def test_cancel_answer_stops_the_worker(monkeypatch):
    ydl, info = processed([fmt("download", v="h264", a="aac", note="watermarked", h=1280, w=720)])
    with pytest.raises(downloader._Permanent):
        _review(ydl, info, {"mode": "mp4", "quality": "1080"}, Asked(monkeypatch, "cancel"))


# --- протокол с настоящим процессом ----------------------------------------------------------

ASKING_WORKER = """
import json, sys
req = json.loads(sys.stdin.readline())
def send(**m):
    print(json.dumps(m, ensure_ascii=False), flush=True)
send(type="question", question={"id": "q", "code": "watermarked", "title": "Версии без водяного знака нет",
     "message": "…", "options": [{"id": "accept", "label": "Скачать с водяным знаком", "primary": True},
     {"id": "cancel", "label": "Отмена"}], "default": "accept"})
answer = json.loads(sys.stdin.readline())["answer"]
send(type="note", text="ответ: " + answer)
send(type="error", message="готово", transient=False)
"""


class Recorder(downloader.Reporter):
    def __init__(self, answer):
        self.answer, self.asked, self.notes = answer, [], []

    def question(self, question):
        self.asked.append(question)
        if self.answer == "cancel":
            raise Cancelled
        return self.answer

    def note(self, text):
        self.notes.append(text)


def run_asking(tmp_path, monkeypatch, answer):
    script = tmp_path / "asking.py"
    script.write_text(ASKING_WORKER)
    monkeypatch.setattr(downloader, "WORKER_CMD", [sys.executable, str(script)])
    reporter = Recorder(answer)
    try:
        downloader.download(URL, "mp4", "1080", tmp_path / "w", ffmpeg=None, js_runtime=None, cookies=None,
                            reporter=reporter, cancel=threading.Event())
    except downloader.DownloadFailed:
        pass
    return reporter


def test_answer_travels_back_to_the_worker(tmp_path, monkeypatch):
    reporter = run_asking(tmp_path, monkeypatch, "accept")
    assert reporter.asked[0]["code"] == "watermarked"
    assert reporter.notes == ["ответ: accept"]


def test_cancel_from_the_question_cancels_the_download(tmp_path, monkeypatch):
    with pytest.raises(Cancelled):
        run_asking(tmp_path, monkeypatch, "cancel")


# --- задача ждёт ответа ----------------------------------------------------------------------

QUESTION = {
    "id": "q1", "code": "format_failed", "title": "Этот вариант не скачивается", "message": "Есть 480p",
    "options": [{"id": "accept", "label": "Скачать 480p", "primary": True}, {"id": "cancel", "label": "Отмена"}],
    "default": "accept",
}  # fmt: skip


def asks(work_dir, kw):
    assert kw["reporter"].question(dict(QUESTION)) == "accept"


@needs_ffmpeg
def test_job_waits_for_answer_then_finishes(settings, library, make_clip, monkeypatch):
    monkeypatch.setattr(jobs, "download", fake_download(make_clip("s.mp4"), [asks]))
    manager = JobManager(settings, library)
    job = manager.submit(Job(kind="url", source=URL, mode="mp4"))
    assert wait_for(lambda: job.status == "waiting")
    assert job.public()["question"]["code"] == "format_failed"
    with pytest.raises(ValueError):
        manager.answer(job.id, "nonsense")
    manager.answer(job.id, "accept")
    assert wait_for(lambda: job.status in FINAL)
    assert job.status == "done", job.error
    assert job.question is None and any("скачать 480p" in n for n in job.notes)
    assert job.decisions == {"format_failed": "accept"}
    manager.shutdown()


def test_answering_cancel_cancels_the_job(settings, library, monkeypatch):
    def cancel_step(work_dir, kw):
        kw["reporter"].question(dict(QUESTION))

    monkeypatch.setattr(jobs, "download", fake_download(None, [cancel_step]))
    manager = JobManager(settings, library)
    job = manager.submit(Job(kind="url", source=URL, mode="mp4"))
    assert wait_for(lambda: job.status == "waiting")
    manager.answer(job.id, "cancel")
    assert wait_for(lambda: job.status in FINAL)
    assert job.status == "cancelled"
    manager.shutdown()


@needs_ffmpeg
def test_auto_accept_never_waits(settings, library, make_clip, monkeypatch):
    monkeypatch.setattr(jobs, "download", fake_download(make_clip("s.mp4"), [asks]))
    manager = JobManager(settings, library)
    job = manager.submit(Job(kind="url", source=URL, mode="mp4", auto_accept=True))
    assert wait_for(lambda: job.status in FINAL)
    assert job.status == "done" and "(автоматически)" in job.notes[0]
    manager.shutdown()


@needs_ffmpeg
def test_retry_does_not_ask_the_same_question_twice(settings, library, make_clip, monkeypatch):
    from vydra.downloader import DownloadFailed

    def asks_then_fails(work_dir, kw):
        kw["reporter"].question(dict(QUESTION))
        raise DownloadFailed("Нет связи с сайтом", transient=True)

    monkeypatch.setattr(jobs, "BACKOFF", (0.05, 0.05, 0.05))
    monkeypatch.setattr(jobs, "download", fake_download(make_clip("s.mp4"), [asks_then_fails, asks]))
    manager = JobManager(settings, library)
    job = manager.submit(Job(kind="url", source=URL, mode="mp4"))
    assert wait_for(lambda: job.status == "waiting")
    manager.answer(job.id, "accept")
    assert wait_for(lambda: job.status in FINAL)  # вторая попытка ответила сама — из job.decisions
    assert job.status == "done" and job.attempt == 2
    manager.shutdown()


def test_answer_endpoint_takes_json_body(settings, library, monkeypatch):
    from fastapi.testclient import TestClient

    from vydra.main import create_app

    def waits(work_dir, kw):
        kw["reporter"].question(dict(QUESTION))

    monkeypatch.setattr(jobs, "download", fake_download(None, [waits]))
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        assert client.post("/api/jobs/nope/answer", json={"option": "accept"}).status_code == 404
        job_id = client.post("/api/jobs", json={"urls": [URL]}).json()[0]["id"]
        assert wait_for(lambda: client.get("/api/jobs").json()[0]["status"] == "waiting")
        assert client.post(f"/api/jobs/{job_id}/answer", json={"option": "bogus"}).status_code == 409
        assert client.post(f"/api/jobs/{job_id}/answer", json={"option": "cancel"}).status_code == 200
        assert wait_for(lambda: client.get("/api/jobs").json()[0]["status"] == "cancelled")
