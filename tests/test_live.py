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
