"""Целостность: проверка результата, имена без коллизий, нехватка места, межпроцессная блокировка."""

import errno
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from conftest import needs_ffmpeg
from vydra import naming
from vydra.fsutil import FileLock, atomic_write
from vydra.media import IntegrityError, Media
from vydra.naming import fit_stem, save_unique


@needs_ffmpeg
def test_validate_rejects_wrong_duration_and_truncated_files(settings, make_clip, tmp_path):
    media = Media(settings)
    good = make_clip("good.mp4", seconds=4)
    media.validate(good, "mp4", 4.0)
    with pytest.raises(IntegrityError, match="неполный"):
        media.validate(good, "mp4", 60.0)
    with pytest.raises(IntegrityError):
        media.validate(good, "mp3", 4.0)  # в MP4 нет mp3-дорожки
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(good.read_bytes()[:2000])
    with pytest.raises(IntegrityError):
        media.validate(broken, "mp4", 4.0)


def test_save_unique_is_case_insensitive(tmp_path):
    folder = tmp_path / "out"
    folder.mkdir()
    (folder / "Title.mp4").write_bytes(b"old")
    src = tmp_path / "new.mp4"
    src.write_bytes(b"new")
    target = save_unique(src, folder, "title", "mp4")
    assert target.name == "title (2).mp4"
    assert (folder / "Title.mp4").read_bytes() == b"old"


def test_save_unique_refuses_when_disk_is_full(tmp_path, monkeypatch):
    src = tmp_path / "big.mp4"
    src.write_bytes(b"x" * 1000)
    monkeypatch.setattr(naming.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    with pytest.raises(OSError) as info:
        save_unique(src, tmp_path / "out", "big", "mp4")
    assert info.value.errno == errno.ENOSPC
    assert not list((tmp_path / "out").glob("*"))  # ни заготовок, ни .part


def test_fit_stem_limits_bytes_and_windows_path():
    stem = fit_stem("🚀" * 200, Path("/home/x"), "mp4")
    assert len((stem + " (99).mp4").encode()) <= 245
    deep = Path("/mnt/c/Users/Ihor/" + "очень длинная папка/" * 8)
    stem = fit_stem("ролик " * 50, deep, "mp4")
    assert len(str(deep)) - 4 + 1 + len(stem + " (99).mp4") <= 240


def test_atomic_write_keeps_backup(tmp_path):
    path = tmp_path / "index.json"
    atomic_write(path, "v1")
    atomic_write(path, "v2", backup=True)
    assert path.read_text() == "v2" and path.with_name("index.json.bak").read_text() == "v1"
    assert not list(tmp_path.glob("*.tmp"))


def test_file_lock_is_exclusive_between_processes(tmp_path):
    lock_path = tmp_path / "x.lock"
    holder = subprocess.Popen(
        [sys.executable, "-c", f"import sys, time; sys.path.insert(0, {str(Path.cwd())!r});"
         f"from vydra.fsutil import FileLock; lock = FileLock(__import__('pathlib').Path({str(lock_path)!r})).__enter__();"
         "print('locked', flush=True); time.sleep(1.5)"],
        stdout=subprocess.PIPE, text=True,
    )  # fmt: skip
    assert holder.stdout.readline().strip() == "locked"
    busy = FileLock(lock_path, timeout=0).__enter__()
    assert not busy.acquired
    busy.__exit__()
    holder.wait(10)
    free = FileLock(lock_path, timeout=0).__enter__()
    assert free.acquired
    free.__exit__()
