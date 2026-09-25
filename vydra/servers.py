"""Запущенные серверы выдры: найти, остановить, перезапустить (`vydra stop`, `vydra restart`, install.sh).

Каждый `vydra ui` держит файловую блокировку <work>/ui-<порт>.lock, пока работает, и пишет рядом
<work>/ui-<порт>.pid (pid, версия, время старта). Так сервер находится без сети: в WSL с mirrored-сетью
и брандмауэром connect() к закрытому порту висит минутами, а блокировка отвечает мгновенно.

Остановка — SIGTERM (uvicorn сохраняет очередь и выходит), через 10 с — SIGKILL. На Windows сигналов нет
(os.kill там — это TerminateProcess без уборки, а os.kill(pid, 0) шлёт Ctrl+C), поэтому `vydra stop` кладёт
рядом <work>/ui-<порт>.stop: сервер видит его и мягко выходит; не вышел — taskkill всего дерева процессов.
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
WINDOWS = os.name == "nt"
STOP_BY_FILE = WINDOWS  # мягкая остановка через файл-просьбу, а не сигнал
STOP_POLL = 0.5


def port_state(port: int) -> str:
    """free | busy | denied. Пробуем занять порт сами, как это сделает uvicorn.

    Не connect(): в WSL с networkingMode=mirrored и включённым брандмауэром подключение
    к закрытому порту не получает отказа и висит минутами — `vydra ui` молча не запускался.
    На Windows — без SO_REUSEADDR: с ним занятый порт отвечает WSAEACCES, и выдра звала «администратора»."""
    with socket.socket() as sock:
        if not WINDOWS:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OverflowError:
            return "denied"
        except OSError as exc:
            return bind_error_state(exc, WINDOWS)
        return "free"


def bind_error_state(exc: OSError, windows: bool) -> str:
    """Почему не занять порт. Windows не делит порты на «системные»: WSAEACCES там — порт держит программа
    с эксклюзивным доступом или его зарезервировала система (Hyper-V, WSL, Docker) — это «занят», берём соседний."""
    if exc.errno in (errno.EACCES, errno.EPERM) and not windows:
        return "denied"
    return "busy"


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


def stop_request_path(work_dir: Path, port: int) -> Path:
    return work_dir / f"ui-{port}.stop"


def request_stop(work_dir: Path, port: int) -> bool:
    """Попросить сервер на этом порту мягко остановиться (его наблюдатель увидит файл)."""
    try:
        stop_request_path(work_dir, port).write_text(str(os.getpid()), encoding="utf-8")
        return True
    except OSError:
        return False


def watch_stop_request(work_dir: Path, port: int, on_stop, done, poll: float = STOP_POLL) -> None:
    """Для сервера: ждёт файл-просьбу об остановке (Windows: `vydra stop`), убирает его и зовёт on_stop().
    done — threading.Event: сервер остановился сам, наблюдать больше не нужно."""
    path = stop_request_path(work_dir, port)
    path.unlink(missing_ok=True)  # просьба от прошлого сервера на этом порту — не нам
    while not done.wait(poll):
        if path.exists():
            path.unlink(missing_ok=True)
            on_stop()
            return


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
    if WINDOWS:  # os.kill(pid, 0) на Windows — это Ctrl+C (CTRL_C_EVENT == 0), а не проверка
        from .system import process_alive

        return process_alive(pid)
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
    if WINDOWS:  # ни /proc, ни lsof; сервер находится по .pid
        return found
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
    if STOP_BY_FILE:
        return _stop_by_file(work_dir, server, timeout)
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


def _stop_by_file(work_dir: Path, server: Server, timeout: float) -> bool:
    """Windows: просьба файлом (сервер сохраняет очередь и выходит), не вышел — taskkill всего дерева."""
    lock = lock_path(work_dir, server.port)
    request_stop(work_dir, server.port)
    try:
        if _wait(lambda: not lock_held(lock) or (server.pid is not None and not pid_alive(server.pid)), timeout):
            return True
        if server.pid is None:
            return False
        kill_process_tree(server.pid)
        return _wait(lambda: not lock_held(lock), 3)
    finally:
        stop_request_path(work_dir, server.port).unlink(missing_ok=True)


def kill_process_tree(pid: int) -> None:
    """Убить процесс с потомками (yt-dlp → ffmpeg): на Windows — taskkill /T, иначе SIGKILL."""
    if WINDOWS:
        from .system import child_flags

        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], stdin=subprocess.DEVNULL, capture_output=True,
                           timeout=15, check=False, **child_flags())  # fmt: skip
        except (OSError, subprocess.SubprocessError):
            pass
        return
    try:
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except OSError:
        pass


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
    from .system import child_flags

    work_dir.mkdir(parents=True, exist_ok=True)
    log = log_path(work_dir, port).open("ab")
    try:
        # своя сессия (POSIX) / своя скрытая консоль (Windows): сервер переживёт закрытие окна, из которого запущен
        return subprocess.Popen(  # noqa: S603
            [sys.executable, "-I", "-m", "vydra", "ui", "--no-browser", "--port", str(port)],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "COLUMNS": "100", "PYTHONIOENCODING": "utf-8"},
            **child_flags(group=True),
        )
    finally:
        log.close()
