"""API проводника, SSE, повтор задач, папка назначения, дубликаты, плейлисты, отчёт."""

import io
import threading
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from conftest import needs_ffmpeg, wait_for
from vydra import downloader, jobs
from vydra.downloader import DownloadFailed
from vydra.main import create_app


@pytest.fixture
def client(settings, monkeypatch):
    monkeypatch.setattr(jobs, "download", lambda *a, **k: (_ for _ in ()).throw(DownloadFailed("нет", transient=False)))
    with TestClient(create_app(settings, watch=False), base_url="http://localhost") as c:
        yield c


def test_fs_listing_and_folder_lifecycle(client):
    root = client.get("/api/fs").json()
    assert [f["name"] for f in root["folders"]][:5] == ["YouTube", "TikTok", "Instagram", "Другие сайты", "Мои файлы"]
    r = client.post("/api/fs/folder", json={"parent": "TikTok", "name": "Танцы"})
    assert r.status_code == 200 and r.json()["path"] == "TikTok/Танцы"
    assert client.post("/api/fs/folder", json={"parent": "TikTok", "name": "танцы"}).status_code == 409
    assert client.post("/api/fs/folder", json={"parent": "TikTok", "name": "a:b"}).status_code == 400
    assert client.post("/api/fs/folder", json={"parent": "../..", "name": "x"}).status_code == 400
    assert client.post("/api/fs/rename", json={"path": "TikTok/Танцы", "name": "Танцы 2026"}).json() == {
        "path": "TikTok/Танцы 2026"
    }
    assert client.post("/api/fs/rename", json={"path": "TikTok", "name": "x"}).status_code == 403
    assert client.post("/api/fs/move", json={"paths": ["TikTok/Танцы 2026"], "to": ""}).json()["moved"][0]["to"] == (
        "Танцы 2026"
    )
    assert client.delete("/api/fs", params={"path": "YouTube/Видео"}).status_code == 403
    assert client.delete("/api/fs", params={"path": "Танцы 2026"}).status_code == 200
    assert client.delete("/api/fs", params={"path": "Танцы 2026"}).status_code == 404
    tree = client.get("/api/fs/tree").json()
    assert {c["name"] for c in tree["tree"]["children"]} >= {"YouTube", "TikTok"}


def test_sse_on_real_server_and_shutdown_with_open_tab(settings, monkeypatch):
    """Настоящий uvicorn: события приходят потоком, а остановка не зависает из-за открытой вкладки."""
    import socket

    import httpx
    import uvicorn

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = create_app(settings, watch=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                           timeout_graceful_shutdown=2))  # fmt: skip
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    assert wait_for(lambda: server.started, timeout=10)
    base = f"http://127.0.0.1:{port}"
    events: list[str] = []
    with httpx.stream("GET", f"{base}/api/events", headers={"host": f"localhost:{port}"}, timeout=10) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())
            if {"library", "jobs"} <= set(events):
                break
    assert {"library", "jobs"} <= set(events)
    rev = httpx.get(f"{base}/api/library/rev", headers={"host": "localhost"}).json()["rev"]
    httpx.post(f"{base}/api/fs/folder", json={"parent": "", "name": "Новая"}, headers={"host": "localhost"})
    assert httpx.get(f"{base}/api/library/rev", headers={"host": "localhost"}).json()["rev"] > rev

    hanging = threading.Thread(
        target=lambda: httpx.get(f"{base}/api/events", headers={"host": "localhost"}, timeout=30), daemon=True
    )
    hanging.start()  # вкладка с открытым SSE
    time.sleep(0.5)
    started = time.monotonic()
    server.should_exit = True
    thread.join(15)
    assert not thread.is_alive() and time.monotonic() - started < 10


def test_retry_endpoint(client):
    job = client.post("/api/jobs", json={"urls": ["https://youtu.be/abcdefghijk"]}).json()[0]
    assert wait_for(lambda: client.get("/api/jobs").json()[0]["status"] == "error")
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 200
    assert client.post("/api/jobs/nope/retry").status_code == 404
    assert wait_for(lambda: client.get("/api/jobs").json()[0]["status"] == "error")
    listed = client.get("/api/jobs").json()[0]
    assert {"attempt", "max_attempts", "retry_at", "resumed"} <= set(listed)


def test_folder_param_is_validated(client):
    r = client.post("/api/jobs", json={"urls": ["https://youtu.be/abcdefghijk"], "folder": "Нет/Такой"})
    assert r.status_code == 422
    r = client.post("/api/jobs", json={"urls": ["https://youtu.be/abcdefghijk"], "folder": "../etc"})
    assert r.status_code == 422


@needs_ffmpeg
def test_convert_into_custom_folder(client, make_clip):
    client.post("/api/fs/folder", json={"parent": "", "name": "Проекты"})
    data = make_clip("мой.mp4").read_bytes()
    job = client.post("/api/convert", files={"files": ("мой.mp4", data)}, data={"mode": "mp3", "folder": "Проекты"}).json()[0]
    assert wait_for(lambda: client.get("/api/jobs").json()[0]["status"] == "done", timeout=20)
    files = client.get("/api/jobs").json()[0]["files"]
    assert files[0]["path"] == "Проекты/мой.mp3" and files[0]["folder"] == "Проекты"
    listing = client.get("/api/fs", params={"path": "Проекты"}).json()
    assert [f["name"] for f in listing["files"]] == ["мой.mp3"]


@needs_ffmpeg
def test_duplicate_guard(client, make_clip):
    app_library = client.app.state.library
    app_library.add(make_clip("a.mp4"), platform="youtube", stem="Ролик", ext="mp4", title="Ролик",
                    source="https://www.youtube.com/watch?v=abcdefghijk")
    job = client.post("/api/jobs", json={"urls": ["https://youtu.be/abcdefghijk?si=1"], "mode": "mp4"}).json()[0]
    assert job["status"] == "done" and "Уже есть" in job["warning"] and job["files"]
    forced = client.post("/api/jobs", json={"urls": ["https://youtu.be/abcdefghijk"], "force": True}).json()[0]
    assert forced["status"] == "queued"


def test_big_playlist_needs_confirmation(client, settings):
    url = "https://www.youtube.com/playlist?list=PL123"
    with downloader._preview_lock:
        downloader._preview_cache[f"{url}|False"] = (time.monotonic(), {"playlist": True, "count": 120})
    r = client.post("/api/jobs", json={"urls": [url]})
    assert r.status_code == 422 and "120" in r.json()["detail"]
    assert client.post("/api/jobs", json={"urls": [url], "confirm_playlist": True}).status_code == 200


def test_report_never_contains_cookies(client):
    secret = b"SUPERSECRETCOOKIE123"
    cookies = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\t" + secret + b"\n"
    client.post("/api/settings/cookies", files={"file": ("c.txt", cookies)})
    r = client.get("/api/doctor/report")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(r.content))
    assert {"versions.json", "doctor.json"} <= set(archive.namelist())
    assert all(secret not in archive.read(n) for n in archive.namelist())
