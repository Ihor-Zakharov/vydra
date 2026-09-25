"""Настоящие загрузки (ролики NASA — общественное достояние). Запуск: uv run pytest -m live"""

import time

import pytest
from fastapi.testclient import TestClient

from vydra.main import create_app

pytestmark = pytest.mark.live

URLS = {
    "youtube": "https://www.youtube.com/shorts/fUrlyCjL8JA",
    "tiktok": "https://www.tiktok.com/@nasa/video/7686148096895569165",
    "instagram": "https://www.instagram.com/reel/DW-IhhKjdrC/",
}


@pytest.mark.parametrize("platform", URLS)
def test_real_download(settings, platform):
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        job = client.post("/api/jobs", json={"urls": [URLS[platform]], "mode": "both", "quality": "720"}).json()[0]
        for _ in range(600):
            job = next(j for j in client.get("/api/jobs").json() if j["id"] == job["id"])
            if job["status"] in ("done", "error"):
                break
            time.sleep(0.5)
        assert job["status"] == "done", job["error"]
        assert job["warning"] is None  # версия без водяного знака нашлась
        assert sorted(f["type"] for f in job["files"]) == ["mp3", "mp4"]


def test_real_short_clip_of_long_video_is_downloaded_as_a_piece(settings):
    """10 с из часовой лекции: кусок, а не весь ролик (и длина результата точная)."""
    from vydra.media import Media

    url = "https://www.youtube.com/watch?v=eQcmzGIKrzg"
    started = time.monotonic()
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        body = {"urls": [url], "mode": "mp4", "quality": "360", "start": "1:05:00", "end": "1:05:10"}
        job = client.post("/api/jobs", json=body).json()[0]
        for _ in range(600):
            job = next(j for j in client.get("/api/jobs").json() if j["id"] == job["id"])
            if job["status"] in ("done", "error"):
                break
            time.sleep(0.5)
        assert job["status"] == "done", job["error"]
        path = client.app.state.library.root / job["files"][0]["path"]
        assert abs(Media(settings).probe(path).duration - 10) < 1
    assert time.monotonic() - started < 60
