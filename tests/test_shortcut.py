"""Ярлык Windows: кириллица в имени и описании (WScript.Shell её портит) — регрессия."""

import json
import re
from pathlib import Path

import pytest

import vydra.system as system
import vydra.tools as tools


@pytest.fixture
def windows_desktop(tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr(system, "OS", "wsl")
    monkeypatch.setattr(system, "WINDOWS_LIKE", True)
    monkeypatch.setattr(system, "desktop_dir", lambda: desktop)
    monkeypatch.setattr(
        system, "to_windows", lambda p: "C:\\Users\\Ihor\\Desktop" + ("" if Path(p) == desktop else "\\" + Path(p).name)
    )
    monkeypatch.setattr(tools, "_windows_icon", lambda png: None)
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    return desktop


def fake_powershell(desktop: Path, scripts: list[str], tamper: bool = False):
    def run(script: str, timeout: float = 30, sta: bool = False):
        scripts.append(script)
        if "WScript.Shell" in script:
            name = re.search(r"(vydra-shortcut-\w+\.lnk)", script).group(1)
            (desktop / name).write_bytes(b"new link")
            return None
        args = re.search(r"\$link\.Arguments = '(.*)'", script).group(1).replace("''", "'")
        desc = re.search(r"\$link\.Description = '(.*)'", script).group(1)
        return json.dumps({"path": "C:\\Windows\\System32\\wsl.exe", "args": args, "desc": "?????" if tamper else desc})

    return run


def test_cyrillic_shortcut_goes_through_ascii_temp_and_shell_application(windows_desktop, monkeypatch):
    scripts: list[str] = []
    monkeypatch.setattr(system, "powershell", fake_powershell(windows_desktop, scripts))
    tools.create_shortcut()
    assert (windows_desktop / "Выдра.lnk").read_bytes() == b"new link"
    assert not list(windows_desktop.glob("vydra-shortcut-*"))
    create, fill = scripts
    assert "WScript.Shell" in create and not re.search("[а-яА-ЯёЁ]", create)  # WScript не видит кириллицу
    assert "Shell.Application" in fill and tools.SHORTCUT_DESCRIPTION in fill
    assert "-d Ubuntu --cd ~ -e" in fill and '"Ubuntu"' not in fill


def test_failed_verification_keeps_the_old_shortcut(windows_desktop, monkeypatch):
    (windows_desktop / "Выдра.lnk").write_bytes(b"working old link")
    monkeypatch.setattr(system, "powershell", fake_powershell(windows_desktop, [], tamper=True))
    with pytest.raises(RuntimeError, match="проверка"):
        tools.create_shortcut()
    assert (windows_desktop / "Выдра.lnk").read_bytes() == b"working old link"
    assert not list(windows_desktop.glob("vydra-shortcut-*"))
