"""Надёжные файловые операции: атомарная запись, replace с повторами, межпроцессная блокировка."""

from __future__ import annotations

import errno
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("fsutil")


def replace(src: Path, dst: Path, attempts: int = 20) -> None:
    """os.replace, который переживает короткую блокировку файла в Windows (антивирус, индексатор, браузер)."""
    for n in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if n == attempts - 1:
                raise
            time.sleep(0.05 * (n + 1))


def atomic_write(path: Path, text: str, backup: bool = False) -> None:
    """Пишет во временный файл рядом и подменяет: читатель видит либо старую, либо новую версию целиком.
    backup=True — перед подменой кладёт копию прежней версии в <имя>.bak."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if backup and path.is_file():
            bak = path.with_name(path.name + ".bak")
            bak_tmp = bak.with_name(f"{bak.name}.{os.getpid()}.tmp")
            try:
                bak_tmp.write_bytes(path.read_bytes())
                replace(bak_tmp, bak)
            except OSError:
                bak_tmp.unlink(missing_ok=True)
        replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def friendly_os_error(exc: OSError) -> str:
    code = exc.errno
    if code == errno.ENOSPC:
        return "Не хватает места на диске хранилища"
    if code in (errno.EACCES, errno.EPERM, errno.EROFS):
        return "Нет прав на запись в папку хранилища — выберите другую в настройках"
    if code in (errno.ENOENT, errno.ENODEV, errno.ENXIO, errno.EIO):
        return "Папка хранилища недоступна — диск отключён или папку удалили"
    if code == errno.ENAMETOOLONG:
        return "Слишком длинное имя файла"
    return f"Ошибка диска: {exc.strerror or exc}"


class FileLock:
    """Эксклюзивная блокировка между процессами (сервер и консоль правят один индекс).

    Реентерабельна внутри процесса только в паре с внешним RLock — вызывающий должен
    сам не брать её повторно. Если ФС не умеет блокировки, работаем без неё (best effort)."""

    def __init__(self, path: Path, timeout: float = 15.0):
        self.path = path
        self.timeout = timeout
        self._fh = None
        self._locked = False

    @property
    def acquired(self) -> bool:
        return self._locked

    def __enter__(self) -> FileLock:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a+b")
        except OSError as exc:
            log.warning("lock file unavailable (%s): %s", self.path, exc)
            return self
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._try_lock()
                self._locked = True
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    if self.timeout:
                        log.warning("lock %s is busy for %.0fs — continuing without it", self.path, self.timeout)
                    return self
                time.sleep(0.05)
            except OSError as exc:  # ФС без поддержки блокировок
                log.debug("locking unsupported on %s: %s", self.path, exc)
                return self

    def _try_lock(self) -> None:
        assert self._fh is not None
        if os.name == "nt":
            import msvcrt

            self._fh.seek(0)
            try:
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EDEADLK):
                    raise BlockingIOError from exc
                raise
        else:
            import fcntl

            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def __exit__(self, *exc) -> None:
        if self._fh is None:
            return
        try:
            if self._locked:
                if os.name == "nt":
                    import msvcrt

                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self._fh.close()
            self._fh = None
            self._locked = False
