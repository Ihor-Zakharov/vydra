"""Доустановка инструментов (FFmpeg, Deno), обновление yt-dlp и ярлык на рабочем столе."""

from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from importlib import metadata
from pathlib import Path

from . import system
from .config import EXE, Settings

Progress = Callable[[int, int | None], None]  # скачано байт, всего байт

_FFMPEG = "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/"
_DENO = "https://github.com/denoland/deno/releases/latest/download/"
UA = {"User-Agent": "vydra (+https://github.com)"}


def _arch() -> str:
    machine = platform.machine().lower()
    return "arm64" if machine in ("arm64", "aarch64") else "x64" if machine in ("x86_64", "amd64") else machine


def _os() -> str:
    return "linux" if system.OS == "wsl" else system.OS


def ffmpeg_source() -> list[str] | None:
    """Откуда качать FFmpeg для этой системы (архивы с ffmpeg и ffprobe внутри)."""
    builds = {
        ("linux", "x64"): ["ffmpeg-master-latest-linux64-gpl.tar.xz"],
        ("linux", "arm64"): ["ffmpeg-master-latest-linuxarm64-gpl.tar.xz"],
        ("windows", "x64"): ["ffmpeg-master-latest-win64-gpl.zip"],
        ("windows", "arm64"): ["ffmpeg-master-latest-winarm64-gpl.zip"],
    }
    if files := builds.get((_os(), _arch())):
        return [_FFMPEG + f for f in files]
    if _os() == "mac":  # статические сборки evermeet.cx (x64, на Apple Silicon идут через Rosetta)
        return ["https://evermeet.cx/ffmpeg/getrelease/zip", "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"]
    return None


def deno_source() -> str | None:
    targets = {
        ("linux", "x64"): "x86_64-unknown-linux-gnu",
        ("linux", "arm64"): "aarch64-unknown-linux-gnu",
        ("windows", "x64"): "x86_64-pc-windows-msvc",
        ("mac", "x64"): "x86_64-apple-darwin",
        ("mac", "arm64"): "aarch64-apple-darwin",
    }
    target = targets.get((_os(), _arch()))
    return f"{_DENO}deno-{target}.zip" if target else None


def install_ffmpeg(settings: Settings, progress: Progress | None = None) -> str:
    urls = ffmpeg_source()
    if not urls:
        hint = "brew install ffmpeg" if _os() == "mac" else "установите ffmpeg через пакетный менеджер"
        raise RuntimeError(f"Для этой системы автоустановки нет — {hint}")
    settings.tools_dir.mkdir(parents=True, exist_ok=True)
    wanted = {f"ffmpeg{EXE}", f"ffprobe{EXE}"}
    found: set[str] = set()
    with tempfile.TemporaryDirectory() as tmp:
        for url in urls:
            archive = Path(tmp) / "archive"
            _fetch(url, archive, progress)
            found |= _extract(archive, wanted, settings.tools_dir)
    if found != wanted:
        raise RuntimeError("В архиве не нашлось ffmpeg/ffprobe")
    version = _first_line([str(settings.tools_dir / f"ffmpeg{EXE}"), "-version"])
    return f"FFmpeg установлен: {version or settings.tools_dir}"


def install_deno(settings: Settings, progress: Progress | None = None) -> str:
    url = deno_source()
    if not url:
        raise RuntimeError("Для этой системы автоустановки Deno нет — поставьте Node.js или Deno вручную")
    settings.tools_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "deno.zip"
        _fetch(url, archive, progress)
        _extract(archive, {f"deno{EXE}"}, settings.tools_dir)
    version = _first_line([str(settings.tools_dir / f"deno{EXE}"), "--version"])
    return f"Deno установлен: {version or 'ok'}"


def ytdlp_version() -> str | None:
    try:
        return metadata.version("yt-dlp")
    except metadata.PackageNotFoundError:
        return None


def ytdlp_latest(timeout: float = 5) -> str | None:
    import json

    try:
        with urllib.request.urlopen(
            urllib.request.Request("https://pypi.org/pypi/yt-dlp/json", headers=UA), timeout=timeout
        ) as resp:
            return json.load(resp)["info"]["version"]
    except Exception:  # noqa: BLE001 — нет сети, PyPI недоступен
        return None


def ytdlp_latest_cached(work_dir: Path, max_age: float = 86400, force: bool = False) -> str | None:
    """Последняя версия yt-dlp с PyPI не чаще раза в сутки (кэш в <work>/pypi.json)."""
    import json
    import time

    cache = work_dir / "pypi.json"
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if not force and time.time() - data.get("checked", 0) < max_age:
            return data.get("latest")
    except (OSError, ValueError, AttributeError):
        pass
    latest = ytdlp_latest()
    if latest:
        try:
            from .fsutil import atomic_write

            atomic_write(cache, json.dumps({"checked": time.time(), "latest": latest}))
        except OSError:
            pass
    return latest


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.replace("-", ".").split(".") if p.isdigit())


def update_ytdlp() -> str:
    """Обновляет yt-dlp там, откуда запущена выдра: git-клон (uv.lock) или установленный пакет."""
    uv = os.environ.get("UV") or shutil.which("uv") or _home_uv()
    project = _project_root()
    if project and uv:
        _check([uv, "lock", "--upgrade-package", "yt-dlp", "--upgrade-package", "yt-dlp-ejs"], cwd=project)
        _check([uv, "sync"], cwd=project)
    elif uv:
        _check([uv, "pip", "install", "--python", sys.executable, "-U", "yt-dlp[default,curl-cffi]"])
    else:
        _check([sys.executable, "-m", "pip", "install", "-U", "yt-dlp[default,curl-cffi]"])
    return f"yt-dlp обновлён до {ytdlp_version()}"


# --- ярлык -------------------------------------------------------------------------------

SHORTCUT_NAME = "Выдра"


def shortcut_path() -> Path | None:
    if system.WINDOWS_LIKE:
        desktop = system.desktop_dir()
        return desktop / f"{SHORTCUT_NAME}.lnk" if desktop else None
    if system.OS == "mac":
        return Path.home() / "Desktop" / f"{SHORTCUT_NAME}.command"
    return Path.home() / ".local/share/applications/vydra.desktop"


def create_shortcut() -> str:
    """Ярлык «Выдра», который запускает интерфейс (сервер + браузер)."""
    icon_png = Path(__file__).parent / "static/icons/icon-512.png"
    target = shortcut_path()
    if target is None:
        raise RuntimeError("Не нашёл рабочий стол")

    if system.WINDOWS_LIKE:
        icon = _windows_icon(icon_png)
        if system.OS == "wsl":
            distro = os.environ.get("WSL_DISTRO_NAME", "")
            exe, args = "wsl.exe", f'-d "{distro}" --cd ~ -e "{sys.executable}" -m vydra ui'
        else:
            exe, args = sys.executable, "-m vydra ui"
        win_target = system.to_windows(target)
        script = f"""
$s = (New-Object -ComObject WScript.Shell).CreateShortcut({system.ps_quote(win_target or "")})
$s.TargetPath = {system.ps_quote(exe)}
$s.Arguments = {system.ps_quote(args)}
$s.WindowStyle = 7
$s.Description = 'Выдра — скачать видео без водяных знаков'
{f"$s.IconLocation = {system.ps_quote(icon)}" if icon else ""}
$s.Save()
"""
        system.powershell(script)
        if not target.exists():
            raise RuntimeError("Не удалось создать ярлык")
        return f"Ярлык создан: {system.display_path(target)}"

    if system.OS == "mac":
        target.write_text(f'#!/bin/bash\nexec "{sys.executable}" -m vydra ui\n', encoding="utf-8")
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return f"Ярлык создан: {target}"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "[Desktop Entry]\nType=Application\nName=Выдра\nComment=Скачать видео без водяных знаков\n"
        f'Exec="{sys.executable}" -m vydra ui\nIcon={icon_png}\nTerminal=false\nCategories=AudioVideo;Network;\n',
        encoding="utf-8",
    )
    target.chmod(0o755)
    if desktop := system.desktop_dir():
        copy = desktop / "vydra.desktop"
        shutil.copyfile(target, copy)
        copy.chmod(0o755)
        system.run(["gio", "set", str(copy), "metadata::trusted", "true"])
    return f"Ярлык создан: {target}"


def _windows_icon(png: Path) -> str | None:
    """.ico рядом с ярлыком (в %LOCALAPPDATA%\\vydra), чтобы иконка не зависела от WSL."""
    local = system.powershell("$env:LOCALAPPDATA")
    folder = system.from_windows(local) if local and system.OS == "wsl" else Path(local) if local else None
    ffmpeg = shutil.which("ffmpeg") or Settings.from_env().ffmpeg
    if not folder or not png.is_file() or not ffmpeg:
        return None
    ico = folder / "vydra" / "vydra.ico"
    ico.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(png), "-vf", "scale=256:256", str(ico)],
        capture_output=True,
        check=False,
        **system.child_flags(),
    )
    return system.to_windows(ico) if ico.is_file() else None


# --- мелочи ------------------------------------------------------------------------------


def _fetch(url: str, dest: Path, progress: Progress | None) -> None:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as resp, dest.open("wb") as out:
        total = int(resp.headers.get("Content-Length") or 0) or None
        done = 0
        while chunk := resp.read(1 << 16):
            out.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)


def _extract(archive: Path, wanted: set[str], dest: Path) -> set[str]:
    found: set[str] = set()

    def place(name: str, data: bytes) -> None:
        target = dest / name
        tmp = target.with_name(target.name + ".new")
        tmp.write_bytes(data)
        tmp.chmod(0o755)
        os.replace(tmp, target)
        found.add(name)

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                name = Path(info.filename).name
                if name in wanted and not info.is_dir():
                    place(name, zf.read(info))
    else:
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                name = Path(member.name).name
                if name in wanted and member.isfile() and (f := tf.extractfile(member)):
                    place(name, f.read())
    return found


def _first_line(cmd: list[str]) -> str | None:
    out = system.run(cmd, timeout=15)
    return out.splitlines()[0] if out else None


def _check(cmd: list[str], cwd: Path | None = None) -> None:
    result = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", **system.child_flags()
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-2:]
        raise RuntimeError("Обновление не удалось: " + " / ".join(tail))


def _project_root() -> Path | None:
    """Корень git-клона, если выдра запущена из исходников через uv run."""
    root = Path(__file__).resolve().parent.parent
    if (root / "uv.lock").is_file() and (root / "pyproject.toml").is_file():
        return root
    return None


def _home_uv() -> str | None:
    for candidate in (Path.home() / ".local/bin" / f"uv{EXE}", Path.home() / ".cargo/bin" / f"uv{EXE}"):
        if candidate.is_file():
            return str(candidate)
    return None
