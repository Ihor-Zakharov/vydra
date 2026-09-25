"""`vydra stop` / `vydra restart` / `vydra ui`: сервер находится по блокировке, без сети, и останавливается."""

import json
import os
import socket
import subprocess
import sys
import textwrap
import time

import pytest
from typer.testing import CliRunner

from vydra import servers, system
from vydra.cli import app

runner = CliRunner()

# «Сервер»: держит блокировку ui-<порт>.lock, по желанию пишет .pid, на SIGTERM выходит,
# на SIGUSR1 «перезапускается» — переписывает .pid с новым временем старта
FAKE = textwrap.dedent(
    """
    import fcntl, json, os, signal, sys, time
    work, port, write_pid = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
    fh = open(os.path.join(work, f"ui-{port}.lock"), "a+b")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    def pid(started):
        with open(os.path.join(work, f"ui-{port}.pid"), "w") as f:
            json.dump({"pid": os.getpid(), "port": int(port), "version": "1.0.0", "started": started,
                       "restart": True}, f)
    if write_pid:
        pid(time.time())
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGUSR1, lambda *_: pid(time.time() + 1))
    print("ready", flush=True)
    while True:
        time.sleep(0.1)
    """
)


@pytest.fixture
def fake_server(tmp_path):
    procs = []

    def start(port: int, write_pid: bool = True) -> subprocess.Popen:
        proc = subprocess.Popen(
            [sys.executable, "-c", FAKE, str(tmp_path), str(port), "1" if write_pid else "0"],
            stdout=subprocess.PIPE,
            text=True,
        )
        assert proc.stdout.readline().strip() == "ready"
        procs.append(proc)
        return proc

    yield start
    for proc in procs:
        proc.kill()
        proc.wait()


def test_stale_lock_is_not_a_server(tmp_path):
    (tmp_path / "ui-8765.lock").write_bytes(b"")
    (tmp_path / "ui-8765.pid").write_text(json.dumps({"pid": 999999, "port": 8765}))
    assert servers.running(tmp_path) == []
    assert not (tmp_path / "ui-8765.pid").exists()  # мусор от упавшего сервера убран


def test_running_and_stop(tmp_path, fake_server):
    proc = fake_server(8801)
    found = servers.running(tmp_path)
    assert [(s.port, s.pid, s.restartable) for s in found] == [(8801, proc.pid, True)]
    started = time.monotonic()
    assert servers.stop(tmp_path, found[0])
    assert time.monotonic() - started < 3
    assert proc.wait(timeout=5) == 0
    assert servers.running(tmp_path) == []


def test_old_server_without_pid_file_is_found_by_lock_holder(tmp_path, fake_server):
    if not os.path.isdir("/proc") and not system.OS == "mac":
        pytest.skip("нужен /proc или lsof")
    proc = fake_server(8802, write_pid=False)
    [server] = servers.running(tmp_path, 8802)
    assert server.pid == proc.pid and not server.restartable


def test_port_filter(tmp_path, fake_server):
    fake_server(8803)
    assert servers.running(tmp_path, 8804) == []
    assert [s.port for s in servers.running(tmp_path, 8803)] == [8803]


def test_restart_in_place_signals_the_server(tmp_path, fake_server, monkeypatch):
    fake_server(8805)
    monkeypatch.setattr(servers, "alive", lambda port, timeout=1.0: True)
    [server] = servers.running(tmp_path)
    assert servers.restart(tmp_path, server, timeout=5)


def test_stop_command_when_nothing_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("VD_WORK_DIR", str(tmp_path))
    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 0
    assert "не запущена" in result.output


def test_stop_command_stops_server(tmp_path, monkeypatch, fake_server):
    monkeypatch.setenv("VD_WORK_DIR", str(tmp_path))
    proc = fake_server(8806)
    result = runner.invoke(app, ["стоп"])
    assert result.exit_code == 0, result.output
    assert "Остановлена выдра на порту 8806" in result.output
    assert proc.wait(timeout=5) == 0


def test_port_state():
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        port = srv.getsockname()[1]
        assert servers.port_state(port) == "busy"
    assert servers.port_state(port) == "free"
    if os.geteuid() != 0:
        assert servers.port_state(1) == "denied"


def test_ui_on_privileged_port_explains_quickly(tmp_path, monkeypatch):
    if os.geteuid() == 0:
        pytest.skip("root может занять любой порт")
    monkeypatch.setenv("VD_WORK_DIR", str(tmp_path))
    started = time.monotonic()
    result = runner.invoke(app, ["ui", "--port", "80", "--no-browser"])
    assert result.exit_code == 1
    assert "прав администратора" in result.output
    assert time.monotonic() - started < 3


def test_ui_rejects_impossible_port():
    result = runner.invoke(app, ["ui", "--port", "70000"])
    assert result.exit_code == 2


def test_ui_waits_for_a_starting_server_then_explains(tmp_path, monkeypatch, fake_server):
    """Блокировку держит другой `vydra ui`, а порт не отвечает — не висим, а объясняем, что делать."""
    monkeypatch.setenv("VD_WORK_DIR", str(tmp_path))
    monkeypatch.setattr(servers, "wait_alive", lambda port, timeout: False)
    fake_server(8807)
    result = runner.invoke(app, ["ui", "--port", "8807", "--no-browser"])
    assert result.exit_code == 1
    assert "не отвечает" in result.output and "stop" in result.output


def test_wsl_downloads_dir_is_cached(tmp_path, monkeypatch):
    target = tmp_path / "Downloads"
    target.mkdir()
    calls = []
    monkeypatch.setenv("VD_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setattr(system, "powershell", lambda *a, **k: calls.append(a) or "C:\\Users\\X\\Downloads")
    monkeypatch.setattr(system, "from_windows", lambda text: target)
    assert system._wsl_downloads() == target
    assert system._wsl_downloads() == target  # второй раз — из кэша, без PowerShell
    assert len(calls) == 1


def test_restart_quiet_when_nothing_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("VD_WORK_DIR", str(tmp_path))
    result = runner.invoke(app, ["restart", "--quiet"])
    assert result.exit_code == 0 and result.output.strip() == ""


def test_slow_library_does_not_delay_server_start(settings, monkeypatch):
    """Хранилище на медленном диске (/mnt/c, сетевой диск): сервер всё равно отвечает за секунды."""
    from fastapi.testclient import TestClient

    from vydra import main
    from vydra.library import Library

    release = __import__("threading").Event()
    monkeypatch.setattr(main, "LAYOUT_WAIT", 0.3)
    monkeypatch.setattr(Library, "ensure_layout", lambda self: release.wait(10))
    started = time.monotonic()
    with TestClient(main.create_app(settings, watch=False), base_url="http://localhost") as client:
        assert client.get("/api/health").status_code == 200
        assert time.monotonic() - started < 3
        release.set()
