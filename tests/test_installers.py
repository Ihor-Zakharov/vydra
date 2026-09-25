"""Установщики: install.ps1 — только ASCII и разбирается PowerShell; install.sh — корректный sh."""

import shutil
import subprocess
from pathlib import Path

import pytest

from vydra import system

ROOT = Path(__file__).parent.parent


def test_install_ps1_is_ascii_without_bom():
    data = (ROOT / "install.ps1").read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")  # BOM ломает `irm | iex` в PowerShell 5.1
    data.decode("ascii")


@pytest.mark.skipif(not shutil.which("sh"), reason="нет sh")
def test_install_sh_syntax():
    assert subprocess.run(["sh", "-n", str(ROOT / "install.sh")]).returncode == 0


@pytest.mark.skipif(not (shutil.which("powershell.exe") or shutil.which("pwsh")), reason="нет PowerShell")
def test_install_ps1_parses():
    exe = shutil.which("pwsh") or "powershell.exe"
    path = ROOT / "install.ps1"
    target = system.to_windows(path) if exe.endswith(".exe") and system.OS == "wsl" else str(path)
    script = (
        "$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{target}',[ref]$null,[ref]$e); if ($e) {{ $e | % {{ $_.Message }}; exit 1 }}"
    )
    out = subprocess.run([exe, "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=120,
                         cwd="/mnt/c" if system.OS == "wsl" else None)  # fmt: skip
    assert out.returncode == 0, out.stdout + out.stderr


def test_install_sh_restarts_running_ui_after_update():
    """Повторный install.sh = обновление: работающий `vydra ui` перезапускается новой версией."""
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "vydra restart --quiet" in text
    assert text.index("uv tool install") < text.index("vydra restart --quiet")
    assert "VYDRA_NO_WINDOWS" in text  # проверка с временным HOME не трогает мост в Windows
