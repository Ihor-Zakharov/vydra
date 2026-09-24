import shutil
import subprocess
from pathlib import Path

import pytest

from vydra.config import Prefs, Settings
from vydra.library import Library
from vydra.media import Media

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") and not Path.home().joinpath(".local/bin/ffmpeg").exists(),
                                  reason="нужен ffmpeg")


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
