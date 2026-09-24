import shutil
import subprocess
from pathlib import Path

import pytest

from vydra.config import Prefs, Settings
from vydra.library import Library
from vydra.media import Media

def _find_ffmpeg() -> str | None:
    """ffmpeg из PATH, ~/.local/bin или папки, куда его ставит `vydra doctor --fix` (так в CI)."""
    from vydra.config import Settings

    try:
        return Settings.from_env().ffmpeg
    except Exception:  # noqa: BLE001
        return shutil.which("ffmpeg")


_FFMPEG = _find_ffmpeg()
if _FFMPEG:  # чтобы тестовые Settings с пустой папкой инструментов тоже его нашли
    import os

    os.environ["PATH"] = str(Path(_FFMPEG).parent) + os.pathsep + os.environ.get("PATH", "")
needs_ffmpeg = pytest.mark.skipif(_FFMPEG is None, reason="нужен ffmpeg")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        work_dir=tmp_path / "work",
        config_dir=tmp_path / "cfg",
        tools_dir=tmp_path / "tools",
        default_library=tmp_path / "lib",
    )


@pytest.fixture
def library(settings: Settings) -> Library:
    lib = Library(Prefs(settings), Media(settings))
    lib.ensure_layout()
    return lib


@pytest.fixture
def make_clip(tmp_path: Path, settings: Settings):
    """Генерирует тестовый ролик через lavfi: make_clip("a.webm", vcodec="libvpx-vp9", size="320x240")."""

    def make(name: str, *, seconds: float = 3, size: str = "320x240", vcodec: str | None = "libx264",
             acodec: str | None = "aac") -> Path:
        out = tmp_path / name
        cmd = [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        if vcodec:
            cmd += ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration={seconds}"]
        if acodec:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
        if vcodec:
            cmd += ["-c:v", vcodec, "-pix_fmt", "yuv420p"]
            if vcodec == "libvpx-vp9":
                cmd += ["-deadline", "realtime", "-cpu-used", "8"]
        if acodec:
            cmd += ["-c:a", acodec]
        subprocess.run([*cmd, str(out)], check=True)
        return out

    return make


@pytest.fixture(autouse=True)
def no_real_trash(monkeypatch):
    """Тесты не должны складывать файлы в настоящую Корзину пользователя."""
    import vydra.system

    def fake_trash(path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    monkeypatch.setattr(vydra.system, "trash", fake_trash)


def wait_for(predicate, timeout: float = 10.0, interval: float = 0.02) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()
