import time

import pytest
from fastapi.testclient import TestClient

from conftest import needs_ffmpeg
from vydra.main import create_app


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), base_url="http://localhost") as c:
        yield c


def test_health_and_info(client):
    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/api/info").json()["library"]["is_default"] is True


def test_foreign_host_and_origin_are_rejected(client):
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 403
    assert client.post("/api/jobs/clear", headers={"origin": "https://evil.example"}).status_code == 403


def test_bad_links_and_clips(client):
    assert client.post("/api/jobs", json={"urls": ["не ссылка"]}).status_code == 422
    r = client.post("/api/jobs", json={"urls": ["https://youtu.be/x"], "start": "2:00", "end": "1:00"})
    assert r.status_code == 422 and "позже" in r.json()["detail"]


def test_library_files_cannot_escape_root(client):
    assert client.get("/lib/../../etc/passwd").status_code == 404
    assert client.get("/lib/%2e%2e/%2e%2e/etc/passwd").status_code == 404


def test_change_and_reset_library_folder(client, tmp_path):
    r = client.post("/api/settings/library", json={"path": str(tmp_path / "Другая")})
    assert r.status_code == 200 and r.json()["library"]["is_default"] is False
    assert client.post("/api/settings/library", json={"path": "relative/path"}).status_code == 400
    assert client.post("/api/settings/library", json={"reset": True}).json()["library"]["is_default"] is True


def test_cookies_upload_is_validated(client):
    r = client.post("/api/settings/cookies", files={"file": ("c.txt", b"not cookies")})
    assert r.status_code == 400
    good = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc\n"
    assert client.post("/api/settings/cookies", files={"file": ("c.txt", good)}).json()["cookies"]["present"]
    assert client.delete("/api/settings/cookies").json()["cookies"]["present"] is False


@needs_ffmpeg
def test_convert_upload_lands_in_library(client, make_clip):
    data = make_clip("мой ролик.webm", vcodec="libvpx-vp9", acodec="libopus", seconds=4).read_bytes()
    jobs = client.post("/api/convert", files={"files": ("мой ролик.webm", data)},
                       data={"mode": "both", "bitrate": "128", "start": "1", "end": "3"}).json()
    job_id = jobs[0]["id"]
    for _ in range(100):
        job = next(j for j in client.get("/api/jobs").json() if j["id"] == job_id)
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert job["status"] == "done", job["error"]
    assert [f["path"] for f in job["files"]] == ["Мои файлы/Видео/мой ролик (0.01–0.03).mp4",
                                                "Мои файлы/Аудио/мой ролик (0.01–0.03).mp3"]
    items = client.get("/api/library").json()["items"]
    assert {i["type"] for i in items} == {"video", "audio"}
    video = next(i for i in items if i["type"] == "video")
    assert client.get("/lib/" + video["path"], headers={"Range": "bytes=0-9"}).status_code == 206
