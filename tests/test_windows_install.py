"""install.ps1 и выдра на настоящей Windows — из WSL, в изолированном «чистом» профиле.

Запуск: uv run pytest -m winlive -s   (нужен WSL с interop и powershell.exe; минут 5–10, качает ~300 МБ).

Изоляция: временные USERPROFILE / LOCALAPPDATA / APPDATA / TEMP с кириллицей, пробелом и апострофом в имени
(«Иг'орь О'Брайен»), свои папки uv, PATH только из System32 и WindowsPowerShell, powershell -NoProfile.
Установщик выполняется так же, как у пользователя, — текстом через iex (как `irm … | iex`), источник пакета —
копия этого дерева (VYDRA_REPO). Реальная выдра пользователя, его PATH, профиль PowerShell и рабочий стол не
трогаются: UV_NO_MODIFY_PATH, VD_DESKTOP_DIR, свой порт; в конце временные папки удаляются.

Сценарий «b» — «всё уже стоит, но других версий»: в PATH заглушки python.exe из WindowsApps, чужие ffmpeg.cmd
и старый node.cmd, в профиле — старый uv 0.4.30, задан PYTHONHOME; выдра должна поставить своё и работать.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.request
import zipfile
from pathlib import Path

import pytest

from vydra import system

pytestmark = [
    pytest.mark.winlive,
    pytest.mark.skipif(system.OS != "wsl" or not system.powershell_exe(), reason="нужен WSL с powershell.exe"),
]

ROOT = Path(__file__).parent.parent
PROFILE = "Иг'орь О'Брайен"
PORT = 8797
SHORT = "https://www.youtube.com/shorts/fUrlyCjL8JA"
LONG = "https://www.youtube.com/watch?v=eQcmzGIKrzg"

# Только ASCII: Windows PowerShell 5.1 читает файлы без BOM в ANSI. Пути приходят переменными окружения.
DRIVER = r"""param([string]$Step)
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$iso = $env:VW_ISO
$prof = $env:VW_PROFILE
$env:USERPROFILE = $prof
$env:HOMEPATH = $prof.Substring(2)
$env:LOCALAPPDATA = "$prof\AppData\Local"
$env:APPDATA = "$prof\AppData\Roaming"
$env:TEMP = "$prof\AppData\Local\Temp"
$env:TMP = $env:TEMP
$env:WIN_PD_OVERRIDE_LOCAL_APPDATA = $env:LOCALAPPDATA
$env:WIN_PD_OVERRIDE_APPDATA = $env:APPDATA
$env:WIN_PD_OVERRIDE_DOWNLOADS = "$prof\Downloads"
$env:UV_CACHE_DIR = "$iso\uv-cache"
$env:UV_TOOL_DIR = "$prof\AppData\Roaming\uv\tools"
$env:UV_TOOL_BIN_DIR = "$prof\.local\bin"
$env:UV_INSTALL_DIR = "$prof\.local\bin"
$env:UV_PYTHON_INSTALL_DIR = "$prof\AppData\Roaming\uv\python"
$env:UV_PYTHON_BIN_DIR = "$prof\AppData\Roaming\uv\python-bin"
$env:UV_NO_MODIFY_PATH = '1'
$env:VD_DESKTOP_DIR = "$prof\Desktop"
$env:VYDRA_NO_LAUNCH = '1'
$env:VYDRA_REPO = "$iso\src"
$env:Path = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem;$env:SystemRoot\System32\WindowsPowerShell\v1.0"
if ($env:VW_EXTRA_PATH) { $env:Path = "$env:VW_EXTRA_PATH;$env:Path" }
$bin = "$prof\.local\bin"
if ($Step -eq 'profile') {
    [Console]::Out.Write($PROFILE.CurrentUserAllHosts + '|' + [Environment]::GetFolderPath('Desktop'))
    exit 0
}
if ($Step -eq 'install') {
    if ($env:VW_PYTHONHOME) { $env:PYTHONHOME = $env:VW_PYTHONHOME }
    [IO.File]::ReadAllText("$iso\src\install.ps1") | iex
    exit $global:VydraInstallExit
}
if ($Step -eq 'ui') {
    Start-Process -FilePath "$bin\vydra.exe" -ArgumentList $args -WindowStyle Hidden
    exit 0
}
& "$bin\vydra.exe" @args
exit $LASTEXITCODE
"""

FAKE_CMD = {  # «чужие» программы в PATH: обёртки .cmd, как у scoop/choco/nvm-windows
    "ffmpeg.cmd": "@echo ffmpeg version 3.4.2 Copyright (c) 2000-2018 the FFmpeg developers\r\n",
    "ffprobe.cmd": "@echo ffprobe version 3.4.2\r\n",
    "node.cmd": "@echo v18.19.0\r\n",
    "yt-dlp.cmd": "@echo 2021.12.01\r\n",
}


class Win:
    def __init__(self, base: Path, base_win: str, name: str, extra_path: str = ""):
        self.iso, self.iso_win = base, base_win
        self.prof = base / name / PROFILE
        self.prof_win = f"{base_win}\\{name}\\{PROFILE}"
        self.extra_path = extra_path
        self.pythonhome = ""
        for sub in ("AppData/Local/Temp", "AppData/Roaming", ".local/bin", "Downloads", "Desktop", "Documents"):
            (self.prof / sub).mkdir(parents=True, exist_ok=True)

    def run(self, step: str, *args: str, timeout: float = 900) -> subprocess.CompletedProcess:
        env_names = "VW_ISO:VW_PROFILE:VW_EXTRA_PATH:VW_PYTHONHOME"
        import os

        env = {**os.environ, "VW_ISO": self.iso_win, "VW_PROFILE": self.prof_win, "VW_EXTRA_PATH": self.extra_path,
               "VW_PYTHONHOME": self.pythonhome, "WSLENV": env_names}  # fmt: skip
        cmd = [system.powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
               f"{self.iso_win}\\driver.ps1", step, *args]  # fmt: skip
        started = time.monotonic()
        out = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, env=env,
                             cwd="/mnt/c", check=False)  # fmt: skip
        text = (out.stdout + out.stderr).decode("utf-8", errors="replace")
        print(f"\n$ vydra-winlive {step} {' '.join(args)}  → код {out.returncode}, {time.monotonic() - started:.1f} с\n{text}")
        return subprocess.CompletedProcess(cmd, out.returncode, text, "")

    @property
    def library(self) -> Path:
        return self.prof / "Downloads" / "VideoDownloader"


@pytest.fixture(scope="module")
def base():
    temp = system.powershell("[Console]::Out.Write([IO.Path]::GetTempPath())")
    assert temp, "не удалось узнать %TEMP% Windows"
    base_win = temp.rstrip("\\") + "\\vydra-winlive"
    base = system.from_windows(base_win)
    assert base is not None
    shutil.rmtree(base, ignore_errors=True)
    (base / "src").mkdir(parents=True)
    for name in ("pyproject.toml", "README.md", "uv.lock", "install.ps1"):
        shutil.copy2(ROOT / name, base / "src" / name)
    shutil.copytree(ROOT / "vydra", base / "src" / "vydra", ignore=shutil.ignore_patterns("__pycache__"))
    (base / "driver.ps1").write_bytes(DRIVER.replace("\n", "\r\n").encode("ascii"))
    yield base, base_win
    shutil.rmtree(base, ignore_errors=True)


def _isolated(win: Win):
    """Рабочий стол в этой среде — внутри временной папки. Профиль PowerShell Windows берёт из реестра («Документы»),
    поэтому его содержимое запоминаем: после проверки оно должно остаться прежним (иначе вернём и упадём)."""
    out = win.run("profile").stdout.strip().splitlines()[-1]
    profile, _, desktop = out.partition("|")
    assert not desktop or desktop.lower().startswith(win.iso_win.lower()), f"не изолировано: {desktop}"
    if not profile or profile.lower().startswith(win.iso_win.lower()):
        return lambda: None
    real = system.from_windows(profile)
    before = real.read_bytes() if real and real.is_file() else None

    def verify() -> None:
        after = real.read_bytes() if real.is_file() else None
        if after != before:
            if before is None:
                real.unlink()
            else:
                real.write_bytes(before)
            pytest.fail(f"установщик изменил настоящий профиль PowerShell {profile} (возвращён как был)")

    return verify


def _check_run(win: Win) -> None:
    """doctor → MP4 → MP3 с отрезком → ui + /api → stop."""
    doctor = win.run("run", "doctor")
    assert doctor.returncode == 0, "doctor нашёл ошибки"
    assert "ffmpeg.cmd" not in doctor.stdout and "3.4.2" not in doctor.stdout  # взял свой FFmpeg, не обёртку

    mp4 = win.run("run", "d", SHORT, "-q", "360", "--yes")
    assert mp4.returncode == 0
    mp3 = win.run("run", "d", LONG, "-f", "mp3", "--from", "0:10", "--to", "0:25", "--yes")
    assert mp3.returncode == 0
    assert "целиком" not in mp3.stdout and "whole video" not in mp3.stdout  # отрезок скачан куском
    files = sorted(p.name for p in win.library.rglob("*") if p.suffix in (".mp4", ".mp3"))
    assert any(f.endswith(".mp4") for f in files) and any(f.endswith(".mp3") for f in files), files
    assert not [p for p in win.library.rglob("*") if p.suffix in (".part", ".ytdl")]

    assert win.run("ui", "ui", "--no-browser", "--port", str(PORT)).returncode == 0
    deadline, health = time.monotonic() + 30, None
    while time.monotonic() < deadline and health is None:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=3) as resp:
                health = json.load(resp)
        except OSError:
            time.sleep(0.5)
    print("GET /api/health →", health)
    assert health and health.get("ok")
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/info", timeout=5) as resp:
        info = json.load(resp)
    assert info["os"] == "windows" and "~" not in info["library"]["path"]
    stop = win.run("run", "stop")
    assert stop.returncode == 0 and str(PORT) in stop.stdout
    time.sleep(1)
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=3)
        still = True
    except OSError:
        still = False
    assert not still, "сервер не остановился"


def test_clean_windows(base):
    """(а) Чистая Windows: нет Python, uv, ffmpeg, deno, git; установка, повторная установка при запущенной выдре."""
    root, root_win = base
    win = Win(root, root_win, "a")
    verify = _isolated(win)
    install = win.run("install")
    verify()
    assert install.returncode == 0, "установщик упал"
    _check_run(win)

    # повторный запуск установщика, пока работает веб-интерфейс: остановить, обновить, запустить снова
    assert win.run("ui", "ui", "--no-browser", "--port", str(PORT)).returncode == 0
    time.sleep(5)
    again = win.run("install")
    verify()
    assert again.returncode == 0, "повторная установка при запущенной выдре упала"
    assert "8797" in again.stdout
    time.sleep(5)
    assert win.run("run", "stop").returncode == 0


def test_windows_with_foreign_versions(base):
    """(б) Всё уже стоит, но других версий: заглушка python из WindowsApps, чужие ffmpeg/node/yt-dlp (.cmd),
    старый uv 0.4.30 в ~/.local/bin, PYTHONHOME во время установки."""
    root, root_win = base
    fake = root / "b-foreign"
    fake.mkdir(exist_ok=True)
    for name, body in FAKE_CMD.items():
        (fake / name).write_bytes(body.encode("ascii"))
    windowsapps = system.powershell("[Console]::Out.Write($env:LOCALAPPDATA + '\\Microsoft\\WindowsApps')") or ""
    win = Win(root, root_win, "b", extra_path=f"{root_win}\\b-foreign;{windowsapps}")
    win.pythonhome = "C:\\Python27"
    old_uv = "https://github.com/astral-sh/uv/releases/download/0.4.30/uv-x86_64-pc-windows-msvc.zip"
    archive = root / "old-uv.zip"
    urllib.request.urlretrieve(old_uv, archive)  # noqa: S310
    with zipfile.ZipFile(archive) as zf:
        (win.prof / ".local/bin/uv.exe").write_bytes(zf.read("uv.exe"))
    verify = _isolated(win)
    install = win.run("install")
    verify()
    assert install.returncode == 0, "установщик упал"
    assert "PYTHONHOME" in install.stdout  # предупредил
    assert (win.prof / "Desktop" / "Выдра.lnk").is_file(), "ярлык на рабочем столе с кириллицей в пути не создан"
    assert "WinError" not in install.stdout and "???" not in install.stdout
    _check_run(win)
