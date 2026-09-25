"""Ветки по ОС в vydra/system.py: «показать в папке», «открыть», браузер — какие программы и с чем зовём."""

from pathlib import Path

import pytest

from vydra import system


@pytest.fixture
def spawned(monkeypatch):
    calls = []
    monkeypatch.setattr(system, "_spawn", lambda cmd, hint=None: calls.append(cmd))
    return calls


@pytest.fixture
def media(tmp_path) -> Path:
    path = tmp_path / "папка с пробелами" / "Ролик, (1) & ещё.mp4"
    path.parent.mkdir()
    path.write_bytes(b"x")
    return path


def as_os(monkeypatch, name: str) -> None:
    monkeypatch.setattr(system, "OS", name)
    monkeypatch.setattr(system, "WINDOWS_LIKE", name in ("windows", "wsl"))


def test_wsl_reveal_selects_file_in_explorer(monkeypatch, spawned, media):
    as_os(monkeypatch, "wsl")
    monkeypatch.setattr(system, "to_windows", lambda p: "C:\\Users\\X\\" + p.name)
    system.reveal(media)
    # «/select,» и путь — отдельными аргументами: так Проводник понимает пробелы и запятые в пути
    assert spawned == [["explorer.exe", "/select,", "C:\\Users\\X\\Ролик, (1) & ещё.mp4"]]


def test_wsl_open_and_browser_go_through_explorer(monkeypatch, spawned, media):
    as_os(monkeypatch, "wsl")
    monkeypatch.setattr(system, "to_windows", lambda p: "C:\\x\\" + p.name)
    system.open_path(media)
    system.open_url("http://localhost:8765")
    assert spawned == [["explorer.exe", "C:\\x\\" + media.name], ["explorer.exe", "http://localhost:8765"]]


def test_wsl_without_interop_explains(monkeypatch, spawned, media):
    as_os(monkeypatch, "wsl")
    monkeypatch.setattr(system, "to_windows", lambda p: None)
    with pytest.raises(system.NotSupported, match="interop"):
        system.reveal(media)
    assert spawned == []


def test_mac_reveal_uses_open_r(monkeypatch, spawned, media):
    as_os(monkeypatch, "mac")
    system.reveal(media)
    system.open_path(media)
    assert spawned == [["open", "-R", str(media)], ["open", str(media)]]


def test_linux_reveal_falls_back_to_folder(monkeypatch, spawned, media):
    as_os(monkeypatch, "linux")
    monkeypatch.setattr(system.shutil, "which", lambda name: None)  # нет gdbus
    system.reveal(media)
    assert spawned == [["xdg-open", str(media.parent)]]


def test_missing_file_is_reported_not_opened(monkeypatch, spawned, tmp_path):
    as_os(monkeypatch, "mac")
    with pytest.raises(FileNotFoundError):
        system.reveal(tmp_path / "нет.mp4")
    assert spawned == []


def test_spawn_does_not_wait_for_gui_program(monkeypatch):
    """Проводник/Finder запускаем и не ждём: explorer.exe из WSL живёт, пока открыто окно."""
    seen = {}

    class Popen:
        def __init__(self, cmd, **kw):
            seen.update(kw, cmd=cmd)

    monkeypatch.setattr(system.subprocess, "Popen", Popen)
    as_os(monkeypatch, "mac")
    system._spawn(["open", "x"])
    assert seen["start_new_session"] and seen["stdin"] == system.subprocess.DEVNULL


def test_missing_program_is_not_supported(monkeypatch):
    def popen(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(system.subprocess, "Popen", popen)
    as_os(monkeypatch, "linux")
    with pytest.raises(system.NotSupported, match="xdg-utils"):
        system._spawn(["xdg-open", "x"], hint="Не найден xdg-open — установите пакет xdg-utils")


def test_run_times_out_instead_of_hanging():
    import time

    started = time.monotonic()
    assert system.run(["sleep", "5"], timeout=0.3) is None
    assert time.monotonic() - started < 2


def test_unreachable_windows_drive_is_explained(monkeypatch):
    as_os(monkeypatch, "wsl")
    monkeypatch.setattr(system, "from_windows", lambda text: None)
    with pytest.raises(ValueError, match="Диск Z: не виден из WSL"):
        system.parse_user_path("Z:\\Видео")
    with pytest.raises(ValueError, match="сетевой путь"):
        system.parse_user_path("\\\\nas\\share")


def test_to_windows_is_cached(monkeypatch):
    as_os(monkeypatch, "wsl")
    calls = []
    monkeypatch.setattr(system, "_win_paths", {})
    monkeypatch.setattr(system, "run", lambda cmd, timeout=20: calls.append(cmd) or "C:\\x")
    assert system.to_windows(Path("/mnt/c/x")) == system.to_windows(Path("/mnt/c/x")) == "C:\\x"
    assert len(calls) == 1
