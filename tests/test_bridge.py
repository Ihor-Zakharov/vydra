"""Мост WSL → Windows: содержимое файлов, запрет скриптов, PATH (PowerShell подменён)."""

import pytest

from vydra import bridge, system


@pytest.fixture
def wsl(tmp_path, monkeypatch):
    calls: list[str] = []
    state = {"policy": "RemoteSigned"}
    monkeypatch.setattr(system, "OS", "wsl")
    monkeypatch.setattr(system, "WINDOWS_LIKE", True)
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setattr(bridge, "launcher", lambda: bridge.Path("/home/u/.local/bin/vydra"))
    monkeypatch.setattr(system, "to_windows", lambda p: "C:\\Users\\U\\AppData\\Local\\vydra\\bin")
    monkeypatch.setattr(bridge.shell, "powershell_profiles", lambda: [])

    def fake_powershell(script, timeout=30, sta=False):
        calls.append(script)
        if "Get-ExecutionPolicy" in script:
            return state["policy"]
        if "Set-ItemProperty" in script:
            return "changed"
        return None

    monkeypatch.setattr(system, "powershell", fake_powershell)
    return tmp_path, calls, state


def test_bridge_files_are_ascii_crlf_and_forward_everything(wsl):
    folder, calls, _ = wsl
    messages = bridge.install(folder)
    for name in ("vydra", "выдра"):
        ps1 = (folder / f"{name}.ps1").read_bytes()
        cmd = (folder / f"{name}.cmd").read_bytes()
        ps1.decode("ascii")
        cmd.decode("ascii")
        assert b"\r\n" in ps1 and b"\n" not in ps1.replace(b"\r\n", b"")
        assert b"& wsl.exe -d Ubuntu --cd $cwd -e /home/u/.local/bin/vydra @args" in ps1
        assert b"exit $LASTEXITCODE" in ps1
        assert b'wsl.exe -d Ubuntu --cd "%CD%\\." -e /home/u/.local/bin/vydra %*' in cmd
    assert any("ExpandString" in c for c in calls)  # PATH пишется без порчи %USERPROFILE%
    assert any("PATH" in m for m in messages)


def test_restricted_policy_skips_ps1_and_explains(wsl):
    folder, _, state = wsl
    (folder / "vydra.ps1").write_text("old")
    state["policy"] = "Restricted"
    messages = bridge.install(folder)
    assert not (folder / "vydra.ps1").exists() and (folder / "vydra.cmd").is_file()
    assert any("RemoteSigned" in m for m in messages)


def test_bridge_is_only_for_wsl(monkeypatch):
    monkeypatch.setattr(system, "OS", "linux")
    with pytest.raises(bridge.BridgeError):
        bridge.install()
