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


def test_stale_pid_of_unrelated_process_is_never_signalled(tmp_path, fake_server):
    """В .pid — номер процесса, который уже не сервер (номер переиспользован): его не трогаем, берём владельца блокировки."""
    if not os.path.isdir("/proc"):
        pytest.skip("нужен /proc")
    stranger = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        server = fake_server(8808, write_pid=False)
        (tmp_path / "ui-8808.pid").write_text(json.dumps({"pid": stranger.pid, "port": 8808, "restart": True}))
        [found] = servers.running(tmp_path, 8808)
        assert found.pid == server.pid and not found.restartable
    finally:
        stranger.kill()
        stranger.wait()


def test_port_filter(tmp_path, fake_server):
    fake_server(8803)
    assert servers.running(tmp_path, 8804) == []
    assert [s.port for s in servers.running(tmp_path, 8803)] == [8803]


def test_restart_in_place_signals_the_server(tmp_path, fake_server, monkeypatch):
    fake_server(8805)
    monkeypatch.setattr(servers, "alive", lambda port, timeout=1.0: True)
    [server] = servers.running(tmp_path)
    assert servers.restart(tmp_path, server, timeout=5)


def test_restart_of_a_server_that_died_on_the_signal_starts_a_new_one(tmp_path, monkeypatch):
    """SIGUSR1 застал сервер на остановке, и тот просто вышел: restart поднимает новый, а не ждёт 20 с (ревью)."""
    dying = FAKE.replace("lambda *_: pid(time.time() + 1)", "lambda *_: sys.exit(0)")
    proc = subprocess.Popen([sys.executable, "-c", dying, str(tmp_path), "8809", "1"], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "ready"
    import threading

    threading.Thread(target=proc.wait, daemon=True).start()  # как оболочка терминала: забирает вышедший процесс
    spawned = []

    class Fresh:
        def poll(self):
            return None

    monkeypatch.setattr(servers, "spawn", lambda work, port: spawned.append(port) or Fresh())
    monkeypatch.setattr(servers, "alive", lambda port, timeout=1.0: bool(spawned))
    [server] = servers.running(tmp_path)
    started = time.monotonic()
    try:
        assert servers.restart(tmp_path, server, timeout=10)
    finally:
        proc.kill()
        proc.wait()
    assert spawned == [8809] and time.monotonic() - started < 5


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


def test_slow_windows_interop_does_not_block_other_requests(settings, library, monkeypatch):
    """Проводник/PowerShell из WSL отвечают секундами — остальной API (и SSE) в это время работает."""
    import threading

    from fastapi.testclient import TestClient

    from vydra import main

    media = library.root / "YouTube" / "Видео" / "clip.mp4"
    media.write_bytes(b"x")
    library.scan(force=True)
    item = library.items()[0]
    monkeypatch.setattr(system, "reveal", lambda path: time.sleep(2))
    with TestClient(main.create_app(settings, watch=False), base_url="http://localhost") as client:
        slow = threading.Thread(target=lambda: client.post(f"/api/library/{item['id']}/reveal"))
        slow.start()
        time.sleep(0.2)
        started = time.monotonic()
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/jobs").status_code == 200
        assert time.monotonic() - started < 1
        slow.join()


@pytest.mark.skipif(servers.RESTART_SIGNAL is None, reason="нужен SIGUSR1")
def test_real_ui_restarts_in_place_and_stops_on_hangup(tmp_path):
    """Настоящий `vydra ui`: перезапуск (SIGUSR1) — тот же процесс, новая версия; закрыли окно (SIGHUP) — мягкая остановка."""
    import signal as sig

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, VD_WORK_DIR=str(tmp_path / "work"), VD_LIBRARY_DIR=str(tmp_path / "lib"),
               VD_CONFIG_DIR=str(tmp_path / "cfg"), COLUMNS="100")  # fmt: skip
    proc = subprocess.Popen([sys.executable, "-m", "vydra", "ui", "--no-browser", "--port", str(port)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)  # fmt: skip
    work = tmp_path / "work"
    try:
        assert servers.wait_alive(port, 20)
        [server] = servers.running(work)
        assert server.pid == proc.pid and server.restartable
        assert servers.restart(work, server, timeout=20)
        assert servers.running(work)[0].pid == proc.pid  # exec: тот же процесс, то же окно
        proc.send_signal(sig.SIGHUP)
        assert proc.wait(timeout=15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    output = proc.stdout.read()
    assert "обновлена и перезапущена" in output and "Выдра остановлена" in output
    assert servers.running(work) == [] and not servers.pid_path(work, port).exists()
