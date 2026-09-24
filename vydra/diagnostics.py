"""Журнал и отчёт для диагностики.

Журнал: <кэш>/logs/vydra.log, ротация 5 × 2 МБ. Из строк вычищаются query-параметры ссылок
(в подписанных адресах CDN бывают токены), cookies в журнал не пишутся вообще.
Отчёт (`vydra doctor --report`, GET /api/doctor/report) — zip с версиями, результатами доктора,
журналами и очередью задач; cookies и содержимое cookies.txt туда не попадают никогда.
"""

from __future__ import annotations

import io
import json
import logging
import logging.handlers
import platform
import re
import sys
import time
import zipfile
from pathlib import Path

from . import __version__, system, tools
from .config import Settings

LOG_NAME = "vydra.log"
_URL_QUERY = re.compile(r"(https?://[^\s?#\"'<>]+)\?[^\s\"'<>]*")
_SECRETS = re.compile(r"(?i)(cookie|authorization|token|signature|sig|key)=([^\s&\"']+)")


def redact(text: str) -> str:
    text = _URL_QUERY.sub(r"\1?…", text)
    return _SECRETS.sub(r"\1=…", text)


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def log_dir(settings: Settings) -> Path:
    return settings.work_dir / "logs"


def setup_logging(settings: Settings, level: int = logging.INFO) -> Path | None:
    """Файловый журнал с ротацией (идемпотентно). Возвращает путь к журналу или None."""
    root = logging.getLogger()
    root.setLevel(level)
    path = log_dir(settings) / LOG_NAME
    for handler in root.handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler) and Path(handler.baseFilename) == path:
            return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
    except OSError:
        return None
    handler.setFormatter(_RedactingFormatter("%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"))
    root.addHandler(handler)
    return path


def build_report(settings: Settings, checks: list[dict], extra: dict | None = None) -> bytes:
    """Zip-отчёт для поддержки. Никаких cookies; ссылки — без подписанных параметров."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        versions = {
            "vydra": __version__,
            "yt-dlp": tools.ytdlp_version(),
            "python": sys.version,
            "platform": platform.platform(),
            "os": system.OS,
            "ffmpeg": (system.run([settings.ffmpeg, "-version"]) or "").splitlines()[:1] if settings.ffmpeg else None,
            "js_runtime": list(settings.js_runtime) if settings.js_runtime else None,
            "cookies": settings.cookies_file is not None,  # только факт наличия
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        zf.writestr("versions.json", json.dumps(versions, ensure_ascii=False, indent=2))
        zf.writestr("doctor.json", json.dumps(checks, ensure_ascii=False, indent=2))
        if extra:
            zf.writestr("library.json", json.dumps(extra, ensure_ascii=False, indent=2))
        for log in sorted(log_dir(settings).glob(f"{LOG_NAME}*")):
            try:
                zf.writestr(f"logs/{log.name}", redact(log.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass
        for queue in sorted(settings.work_dir.glob("queue-*.json")):
            try:
                zf.writestr(queue.name, redact(queue.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass
        prefs = settings.config_dir / "prefs.json"
        if prefs.is_file():
            zf.writestr("prefs.json", prefs.read_text(encoding="utf-8", errors="replace"))
    return buffer.getvalue()
