"""Запущенные серверы выдры: найти, остановить, перезапустить (`vydra stop`, `vydra restart`, install.sh).

Каждый `vydra ui` держит файловую блокировку <work>/ui-<порт>.lock, пока работает, и пишет рядом
<work>/ui-<порт>.pid (pid, версия, время старта). Так сервер находится без сети: в WSL с mirrored-сетью
и брандмауэром connect() к закрытому порту висит минутами, а блокировка отвечает мгновенно.

Остановка — SIGTERM (uvicorn сохраняет очередь и выходит), через 10 с — SIGKILL.
Перезапуск — SIGUSR1: `vydra ui` мягко останавливает сервер и заново запускает себя (exec) в том же
окне терминала — уже новой версией. Серверы версий без перезапуска (нет .pid) останавливаются и
поднимаются заново в фоне; их журнал — <work>/ui-<порт>.log.
"""

from __future__ import annotations

import errno
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .fsutil import FileLock, atomic_write

RESTART_SIGNAL = getattr(signal, "SIGUSR1", None)  # на нативной Windows перезапуска на месте нет
STOP_TIMEOUT = 10.0


def port_state(port: int) -> str:
    """free | busy | denied. Пробуем занять порт сами, как это сделает uvicorn (с SO_REUSEADDR).

    Не connect(): в WSL с networkingMode=mirrored и включённым брандмауэром подключение
    к закрытому порту не получает отказа и висит минутами — `vydra ui` молча не запускался."""
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OverflowError:
            return "denied"
        except OSError as exc:
            return "denied" if exc.errno in (errno.EACCES, errno.EPERM) else "busy"
        return "free"


def alive(port: int, timeout: float = 1.0) -> bool:
    """Отвечает ли на порту выдра. Свободный порт не спрашиваем: в WSL запрос к нему висит до таймаута."""
    if port_state(port) == "free":
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as resp:
            return b'"ok"' in resp.read()
    except Exception:  # noqa: BLE001
        return False


def wait_alive(port: int, timeout: float) -> bool:
    return _wait(lambda: alive(port), timeout)


def lock_path(work_dir: Path, port: int) -> Path:
    return work_dir / f"ui-{port}.lock"


def pid_path(work_dir: Path, port: int) -> Path:
    return work_dir / f"ui-{port}.pid"


def log_path(work_dir: Path, port: int) -> Path:
    return work_dir / f"ui-{port}.log"


@dataclass
class Server:
    port: int
    pid: int | None  # None — сервер есть (блокировка занята), но процесс не нашёлся
    version: str | None = None
    started: float | None = None
    restartable: bool = False  # умеет перезапускаться на месте по SIGUSR1

    def public(self) -> dict:
        return {"port": self.port, "pid": self.pid, "version": self.version, "url": f"http://localhost:{self.port}"}


def write_pid(work_dir: Path, port: int) -> None:
    record = {
        "pid": os.getpid(),
        "port": port,
        "version": __version__,
        "started": time.time(),
        "restart": RESTART_SIGNAL is not None,
    }
    try:
        atomic_write(pid_path(work_dir, port), json.dumps(record))
    except OSError:
        pass  # без .pid сервер всё равно найдётся по блокировке


def clear_pid(work_dir: Path, port: int) -> None:
    path = pid_path(work_dir, port)
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError, AttributeError):
        pass


def lock_held(path: Path) -> bool:
    """Держит ли блокировку живой процесс. Проверка мгновенная и без сети."""
    try:
        fh = path.open("rb")
    except OSError:
        return False
    with fh:
        if os.name == "nt":
            lock = FileLock(path, timeout=0)
            lock.__enter__()
            held = not lock.acquired
            lock.__exit__(None, None, None)
            return held
        import fcntl

        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except OSError:
            return False
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return False


def pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # процесс есть, но чужой
    except OSError:
        return False
    return True


def _read_pid(work_dir: Path, port: int) -> dict:
    try:
        data = json.loads(pid_path(work_dir, port).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def holders(path: Path) -> set[int]:
    """PID процессов, у которых файл открыт (кроме нас самих): /proc на Linux и в WSL, lsof на macOS."""
    target = str(path.resolve())
    me = os.getpid()
    found: set[int] = set()
    proc = Path("/proc")
    if proc.is_dir():
        for entry in proc.iterdir():
            if not entry.name.isdigit() or int(entry.name) == me:
                continue
            try:
                fds = list((entry / "fd").iterdir())
            except OSError:
                continue
            for fd in fds:
                try:
                    if os.readlink(fd) == target:
                        found.add(int(entry.name))
                        break
                except OSError:
                    continue
        return found
    try:  # macOS: /proc нет — спрашиваем lsof (с таймаутом)
        out = subprocess.run(["lsof", "-t", "--", target], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                             timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return found
    return {int(p) for p in out.stdout.split() if p.isdigit() and int(p) != me}


def holder_pid(path: Path) -> int | None:
    """PID процесса, который держит файл блокировки (для серверов старых версий без .pid)."""
    found = holders(path)
    return min(found) if found else None


def running(work_dir: Path, port: int | None = None) -> list[Server]:
    """Серверы, запущенные с этой папкой временных файлов (т. е. тем же пользователем и настройками)."""
    found: list[Server] = []
    for lock in sorted(work_dir.glob("ui-*.lock")):
        m = re.fullmatch(r"ui-(\d+)\.lock", lock.name)
        if not m or (port is not None and int(m.group(1)) != port):
            continue
        number = int(m.group(1))
        if not lock_held(lock):
            pid_path(work_dir, number).unlink(missing_ok=True)  # сервер умер, не прибравшись
            continue
        record = _read_pid(work_dir, number)
        pid = record.get("pid") if pid_alive(record.get("pid")) else None
        owners = holders(lock)
        if pid is not None and owners and pid not in owners:
            pid = None  # .pid от прошлого сервера, а номер уже занял чужой процесс — такой не трогаем
        restartable = bool(pid and record.get("restart"))
        if pid is None:
            pid, record = (min(owners) if owners else None), {}
        found.append(Server(number, pid, record.get("version"), record.get("started"), restartable))
    return found


def _wait(predicate, timeout: float, step: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


def stop(work_dir: Path, server: Server, timeout: float = STOP_TIMEOUT) -> bool:
    """Мягко остановить (SIGTERM), а если не ушёл за timeout — убить. True — порт освободился."""
    lock = lock_path(work_dir, server.port)
    if server.pid is None:
        return not lock_held(lock)
    try:
        os.kill(server.pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    if _wait(lambda: not lock_held(lock) or not pid_alive(server.pid), timeout):
        return True
    try:
        os.kill(server.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except OSError:
        pass
    return _wait(lambda: not lock_held(lock), 3)


def restart(work_dir: Path, server: Server, timeout: float = 20.0) -> bool:
    """Перезапустить сервер новой версией. True — снова работает на том же порту."""
    if server.restartable and RESTART_SIGNAL is not None and server.pid:
        before = server.started or 0
        try:
            os.kill(server.pid, RESTART_SIGNAL)
        except OSError:
            return False

        def restarted() -> bool:
            record = _read_pid(work_dir, server.port)
            return (record.get("started") or 0) > before and alive(server.port)

        def settled() -> bool:  # перезапустился — или сигнал застал его на старте/остановке, и он просто вышел
            return restarted() or not pid_alive(server.pid)

        _wait(settled, timeout, 0.2)
        if restarted():
            return True
        if pid_alive(server.pid) or lock_held(lock_path(work_dir, server.port)):
            return False
        proc = spawn(work_dir, server.port)  # процесса больше нет — поднимаем новый в фоне
        return _wait(lambda: proc.poll() is None and alive(server.port), timeout, 0.2)
    if not stop(work_dir, server):
        return False
    proc = spawn(work_dir, server.port)
    return _wait(lambda: proc.poll() is None and alive(server.port), timeout, 0.2)


def spawn(work_dir: Path, port: int) -> subprocess.Popen:
    """Запустить `vydra ui` в фоне, без браузера; вывод — в <work>/ui-<порт>.log."""
    work_dir.mkdir(parents=True, exist_ok=True)
    log = log_path(work_dir, port).open("ab")
    try:
        return subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "vydra", "ui", "--no-browser", "--port", str(port)],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, "COLUMNS": "100"},
        )
    finally:
        log.close()
