"""Всё, что зависит от ОС: «Загрузки», открыть/показать файл, выбрать папку, Корзина, ярлык.

Поддерживаются Windows, WSL (Windows-сторона через interop), macOS и Linux.
"""

from __future__ import annotations

import base64
import errno
import os
import platform
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

from platformdirs import user_downloads_dir


def _detect() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    if "microsoft" in platform.uname().release.lower() and shutil.which("powershell.exe"):
        return "wsl"
    return "linux"


OS = _detect()
WINDOWS_LIKE = OS in ("windows", "wsl")


class NotSupported(Exception):
    pass


def child_flags(group: bool = False) -> dict:
    """Параметры Popen для фоновых процессов: на Windows без окна консоли,
    group=True — своя группа процессов, чтобы убивать дерево целиком (yt-dlp → ffmpeg)."""
    if OS == "windows":
        flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        if group:
            flags |= subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        return {"creationflags": flags}
    return {"start_new_session": True} if group else {}


def kill_tree(proc: subprocess.Popen) -> None:
    """Убить процесс вместе с потомками; тихо, если он уже завершился."""
    if proc.poll() is not None:
        return
    try:
        if OS == "windows":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=15,
                check=False,
                **child_flags(),
            )
        else:
            import signal

            try:
                os.killpg(proc.pid, signal.SIGKILL)  # группа создана start_new_session
            except (ProcessLookupError, PermissionError):
                proc.kill()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
        proc.wait(timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


# --- запуск внешних программ -------------------------------------------------------------


def run(cmd: list[str], timeout: float = 20) -> str | None:
    """stdout программы или None, если её нет или она упала."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            check=False,
            # cmd.exe из WSL ругается на UNC-путь \\wsl.localhost\... в роли текущей папки
            cwd="/mnt/c" if OS == "wsl" and Path("/mnt/c").is_dir() else None,
            **child_flags(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = result.stdout.decode("utf-8", errors="replace").strip()
    return out if result.returncode == 0 else None


def powershell(script: str, timeout: float = 30, sta: bool = False) -> str | None:
    """PowerShell-скрипт через -EncodedCommand: никаких проблем с кавычками и кириллицей."""
    exe = shutil.which("powershell.exe") or shutil.which("powershell")
    if not exe:
        return None
    script = "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n" + script
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    args = [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    if sta:
        args.append("-STA")
    return run([*args, "-EncodedCommand", encoded], timeout=timeout)


def ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


# --- пути --------------------------------------------------------------------------------


def to_windows(path: Path) -> str | None:
    """Путь в том виде, в каком его увидит Windows (для WSL — C:\\…), иначе None."""
    if OS == "windows":
        return str(path)
    if OS != "wsl":
        return None
    return run(["wslpath", "-w", str(path)], timeout=5)


def from_windows(text: str) -> Path | None:
    if OS == "windows":
        return Path(text)
    if OS != "wsl":
        return None
    out = run(["wslpath", "-u", text], timeout=5)
    return Path(out) if out else None


def display_path(path: Path) -> str:
    return to_windows(path) or str(path)


def downloads_dir() -> Path:
    if OS == "wsl":
        script = "(New-Object -ComObject Shell.Application).NameSpace('shell:Downloads').Self.Path"
        converted = from_windows(powershell(script) or "")
        if converted and converted.is_dir():
            return converted
    try:
        return Path(user_downloads_dir())
    except Exception:  # noqa: BLE001 — платформенные сюрпризы
        return Path.home() / "Downloads"


def desktop_dir() -> Path | None:
    if WINDOWS_LIKE:
        path = powershell("[Environment]::GetFolderPath('Desktop')")
        return from_windows(path) if path else None
    out = run(["xdg-user-dir", "DESKTOP"]) if OS == "linux" else None
    path = Path(out) if out else Path.home() / "Desktop"
    return path if path.is_dir() else None


def parse_user_path(text: str) -> Path:
    """Путь из поля ввода: «D:\\Кино», «~/Movies», «/mnt/d/…» — в путь для этой ОС."""
    raw = text.strip().strip('"').strip("'")
    if not raw:
        raise ValueError("Укажите путь к папке")
    looks_windows = (len(raw) >= 2 and raw[1] == ":" and raw[0].isalpha()) or raw.startswith("\\\\")
    if looks_windows and OS == "wsl":
        converted = from_windows(raw)
        if converted is None:
            raise ValueError("Не удалось преобразовать путь Windows")
        return converted
    path = Path(raw).expanduser()
    if not path.is_absolute():
        example = "D:\\Видео" if WINDOWS_LIKE else "~/Movies"
        raise ValueError(f"Нужен полный путь, например {example}")
    return path


# --- действия ----------------------------------------------------------------------------


def open_path(path: Path) -> None:
    """Открыть файл программой по умолчанию или папку в файловом менеджере."""
    _must_exist(path)
    if OS == "windows":
        try:
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
        except OSError as exc:  # нет программы для этого типа файла
            raise NotSupported(f"Windows не знает, чем открыть «{path.name}»: {exc.strerror or exc}") from exc
    elif OS == "wsl":
        _spawn(["explorer.exe", _win(path)])
    elif OS == "mac":
        _spawn(["open", str(path)])
    else:
        _spawn(["xdg-open", str(path)], hint="Не найден xdg-open — установите пакет xdg-utils")


def reveal(path: Path) -> None:
    """Показать файл или папку выделенными в Проводнике / Finder / файловом менеджере."""
    _must_exist(path)
    if OS in ("windows", "wsl"):
        # «/select,» и путь — отдельными аргументами: так Проводник понимает пути с пробелами
        _spawn(["explorer.exe" if OS == "wsl" else "explorer", "/select,", _win(path)])
    elif OS == "mac":
        _spawn(["open", "-R", str(path)])
    else:
        uri = path.resolve().as_uri()
        shown = run(
            ["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
             "--object-path", "/org/freedesktop/FileManager1",
             "--method", "org.freedesktop.FileManager1.ShowItems", f"['{uri}']", ""],
            timeout=5,
        ) if shutil.which("gdbus") else None  # fmt: skip
        if shown is None:
            _spawn(["xdg-open", str(path.parent)], hint="Не найден xdg-open — установите пакет xdg-utils")


def _must_exist(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(errno.ENOENT, "Файл не найден — возможно, его удалили или переместили", str(path))


def open_url(url: str) -> None:
    if OS == "wsl":
        _spawn(["explorer.exe", url])  # браузер по умолчанию на стороне Windows
    else:
        webbrowser.open(url)


def pick_folder(initial: Path | None) -> Path | None:
    """Системный диалог выбора папки. None — пользователь нажал «Отмена»."""
    if WINDOWS_LIKE:
        start = to_windows(initial) if initial else ""
        script = f"""
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form -Property @{{TopMost=$true; ShowInTaskbar=$false}}
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Папка для хранилища выдры'
$dialog.ShowNewFolderButton = $true
$dialog.SelectedPath = {ps_quote(start or "")}
if ($dialog.ShowDialog($owner) -eq 'OK') {{ [Console]::Out.Write($dialog.SelectedPath) }}
"""
        out = powershell(script, timeout=600, sta=True)
        return (from_windows(out) if OS == "wsl" else Path(out)) if out else None
    if OS == "mac":
        out = run(["osascript", "-e", 'POSIX path of (choose folder with prompt "Папка для хранилища выдры")'], 600)
        return Path(out) if out else None
    for cmd in (
        ["zenity", "--file-selection", "--directory", "--title=Папка для хранилища"],
        ["kdialog", "--getexistingdirectory", str(initial or Path.home())],
    ):
        if shutil.which(cmd[0]):
            out = run(cmd, timeout=600)
            return Path(out) if out else None
    raise NotSupported("Системный диалог недоступен — впишите путь вручную")


def trash(path: Path) -> None:
    """В Корзину, а не насовсем: случайный клик можно отменить."""
    win = to_windows(path) if OS == "wsl" else None
    if win and len(win) > 2 and win[1] == ":":
        method = "DeleteDirectory" if path.is_dir() else "DeleteFile"
        script = (
            "Add-Type -AssemblyName Microsoft.VisualBasic\n"
            f"[Microsoft.VisualBasic.FileIO.FileSystem]::{method}({ps_quote(win)},"
            "'OnlyErrorDialogs','SendToRecycleBin')"
        )
        powershell(script, timeout=120)
        if not path.exists():
            return
    from send2trash import send2trash

    send2trash(str(path))


def hide(path: Path) -> None:
    """Скрыть служебную папку (на macOS/Linux её и так скрывает точка в имени)."""
    if OS == "windows":
        import ctypes

        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x2)  # type: ignore[attr-defined]
    elif OS == "wsl" and (win := to_windows(path)):
        run(["cmd.exe", "/c", "attrib", "+h", win], timeout=10)


def _win(path: Path) -> str:
    win = to_windows(path)
    if not win:
        raise NotSupported("Не удалось получить путь Windows — проверьте, что в WSL включён interop")
    return win


def _spawn(cmd: list[str], hint: str | None = None) -> None:
    """Запустить GUI-программу и не ждать её (без окна консоли на Windows)."""
    kwargs = child_flags() if OS == "windows" else {"start_new_session": True}
    try:
        subprocess.Popen(  # noqa: S603
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd="/mnt/c" if OS == "wsl" and Path("/mnt/c").is_dir() else None,
            **kwargs,
        )
    except OSError as exc:
        if isinstance(exc, FileNotFoundError):
            raise NotSupported(hint or f"Не найдена программа {cmd[0]}") from exc
        raise NotSupported(f"Не удалось запустить {cmd[0]}: {exc.strerror or exc}") from exc
