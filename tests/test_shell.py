"""Умные ссылки и автодополнение: эталон на Python и настоящие оболочки (bash, zsh, fish, PowerShell)."""

import shutil
import subprocess

import pytest

from vydra import shell, system
from vydra.shell import quote_links

YT = "https://www.youtube.com/watch?v=UwullClrOuw&t=8140s"
CASES = [
    (f"vydra d {YT} -f mp3", f"vydra d '{YT}' -f mp3"),
    (f"выдра скачать {YT} -ф мп3", f"выдра скачать '{YT}' -ф мп3"),
    (f"  выдра {YT}", f"  выдра '{YT}'"),
    (f"vydra d '{YT}' -f mp3", f"vydra d '{YT}' -f mp3"),  # уже в кавычках
    (f'vydra d "{YT}"', f'vydra d "{YT}"'),
    (f"vydra d {YT} https://youtu.be/x?a=1;b=2", f"vydra d '{YT}' 'https://youtu.be/x?a=1;b=2'"),
    ("vydra d https://en.wikipedia.org/wiki/A_(b)#c", "vydra d 'https://en.wikipedia.org/wiki/A_(b)#c'"),
    ("vydra d youtu.be/abc?t=5&x=1", "vydra d 'youtu.be/abc?t=5&x=1'"),  # голый домен со спецсимволом
    ("vydra d youtu.be/abc", "vydra d youtu.be/abc"),  # без спецсимволов — не трогаем
    ("vydra d https://x.com/a\\&b", "vydra d https://x.com/a\\&b"),  # экранировано вручную
    (f"curl {YT}", f"curl {YT}"),  # чужая команда
    (f"echo vydra {YT}", f"echo vydra {YT}"),
    (f"~/.local/bin/vydra d {YT}", f"~/.local/bin/vydra d '{YT}'"),
    (f"vydra d {YT} --папка 'TikTok/Мои танцы'", f"vydra d '{YT}' --папка 'TikTok/Мои танцы'"),
    (f"vydra d {YT}\t-f mp3", f"vydra d '{YT}'\t-f mp3"),
    ("vydra", "vydra"),
    ("vydra d 'незакрытая https://a.b/?x&y", "vydra d 'незакрытая https://a.b/?x&y"),
]


@pytest.mark.parametrize(("line", "expected"), CASES)
def test_reference(line, expected):
    assert quote_links(line) == expected


def test_reference_powershell_uses_backtick_escape():
    assert quote_links(f"vydra d {YT}", "powershell") == f"vydra d '{YT}'"
    assert quote_links("vydra d https://x.com/a`&b", "powershell") == "vydra d https://x.com/a`&b"
    assert quote_links(f"vydra.ps1 d {YT}", "powershell") == f"vydra.ps1 d '{YT}'"


def _run_shell(program: list[str], script: str, lines: list[str]) -> list[str]:
    out = subprocess.run([*program], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.split("\x1f")[:-1]


@pytest.mark.skipif(not shutil.which("bash"), reason="нет bash")
def test_bash_matches_reference(tmp_path):
    lib = tmp_path / "vydra.bash"
    lib.write_text(shell.script("bash"), encoding="utf-8")
    driver = f"source {lib}\n"
    for line, _ in CASES:
        literal = line.replace("'", "'\\''")
        driver += f"READLINE_LINE='{literal}'; __vydra_readline_hook; printf '%s\\x1f' \"$READLINE_LINE\"\n"
    got = _run_shell(["bash", "--norc", "--noprofile"], driver, [])
    assert got == [expected for _, expected in CASES]


@pytest.mark.skipif(not shutil.which("zsh"), reason="нет zsh")
def test_zsh_matches_reference(tmp_path):
    lib = tmp_path / "vydra.zsh"
    lib.write_text(shell.script("zsh"), encoding="utf-8")
    driver = f"compdef() {{ :; }}\nsource {lib}\n"
    for line, _ in CASES:
        literal = line.replace("'", "'\\''")
        driver += f"__vydra_quote_line '{literal}'; printf '%s\\x1f' \"$REPLY\"\n"
    got = _run_shell(["zsh", "-f"], driver, [])
    assert got == [expected for _, expected in CASES]


@pytest.mark.skipif(not shutil.which("fish"), reason="нет fish")
def test_fish_matches_reference(tmp_path):
    lib = tmp_path / "vydra.fish"
    lib.write_text(shell.script("fish"), encoding="utf-8")
    driver = f"source {lib}\n"
    for line, _ in CASES:
        literal = line.replace("\\", "\\\\").replace("'", "\\'")
        driver += f"printf '%s\\x1f' (__vydra_quote_line '{literal}')\n"
    got = _run_shell(["fish", "--no-config"], driver, [])
    assert got == [expected for _, expected in CASES]


def _powershell() -> str | None:
    for exe in ("pwsh", "pwsh.exe", "powershell.exe", "powershell"):
        if shutil.which(exe):
            return exe
    return None


@pytest.mark.skipif(_powershell() is None, reason="нет PowerShell")
def test_powershell_matches_reference(tmp_path):
    ps_cases = [(line, quote_links(line, "powershell")) for line, _ in CASES if "\\&" not in line]
    body = shell.script("powershell")
    lines = [f"$lines{n} = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{_b64(line)}'))"
             for n, (line, _) in enumerate(ps_cases)]  # fmt: skip
    prints = [f"[Console]::Out.Write((__VydraQuoteLine $lines{n}) + [char]31)" for n in range(len(ps_cases))]
    script = "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n" + body + "\n" + "\n".join(lines + prints)
    path = tmp_path / "t.ps1"
    path.write_bytes(script.encode("utf-8-sig"))
    exe = _powershell()
    target = system.to_windows(path) if exe.endswith(".exe") and system.OS == "wsl" else str(path)
    if target is None:
        pytest.skip("путь Windows недоступен")
    out = subprocess.run([exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", target],
                         capture_output=True, timeout=120, cwd="/mnt/c" if system.OS == "wsl" else None)  # fmt: skip
    got = out.stdout.decode("utf-8", errors="replace").split("\x1f")[:-1]
    assert got == [expected for _, expected in ps_cases], out.stderr.decode("utf-8", errors="replace")


def _b64(text: str) -> str:
    import base64

    return base64.b64encode(text.encode("utf-8")).decode()


def test_powershell_script_is_ascii_only():
    shell.script("powershell").encode("ascii")


def test_install_is_idempotent_and_uninstallable(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(shell.Path, "home", lambda: tmp_path)
    rc = tmp_path / ".bashrc"
    rc.write_text("# мой bashrc\nexport A=1\n", encoding="utf-8")
    legacy = tmp_path / ".bash_completions" / "vydra.sh"
    legacy.parent.mkdir()
    legacy.write_text("old")
    rc.write_text(rc.read_text() + f"source '{legacy}'\n", encoding="utf-8")
    cfg = tmp_path / "cfg"
    shell.install(cfg, ["bash"])
    shell.install(cfg, ["bash"])
    text = rc.read_text(encoding="utf-8")
    assert text.count(shell.BEGIN) == 1 and "export A=1" in text and ".bash_completions" not in text
    assert shell.installed(cfg)["bash"]
    shell.uninstall(cfg)
    assert shell.BEGIN not in rc.read_text(encoding="utf-8") and "export A=1" in rc.read_text(encoding="utf-8")


def test_powershell_profile_block_keeps_utf16_encoding(tmp_path):
    profile = tmp_path / "profile.ps1"
    profile.write_bytes(b"\xff\xfe" + "Write-Host 'привет'\r\n".encode("utf-16-le"))
    target = shell.Target("powershell", tmp_path / "vydra.ps1", profile)
    shell._write_block(profile, shell.block(target), powershell=True)
    raw = profile.read_bytes()
    assert raw.startswith(b"\xff\xfe")
    text = raw[2:].decode("utf-16-le")
    assert "привет" in text and shell.BEGIN in text and "\r\n" in text


@pytest.mark.skipif(not shutil.which("bash") or system.OS == "windows", reason="нужен bash и pty")
def test_bash_enter_key_really_quotes_links(tmp_path):
    """Настоящий интерактивный bash в псевдотерминале: набираем ссылку с & и жмём Enter."""
    import os
    import pty
    import select
    import time

    rc = tmp_path / "rc.bash"
    rc.write_text(
        "PS1='$ '\n"
        "vydra() { printf 'ARGS:'; printf '%s|' \"$@\"; echo; }\n"
        f"source {tmp_path / 'vydra.bash'}\n",
        encoding="utf-8",
    )
    (tmp_path / "vydra.bash").write_text(shell.script("bash"), encoding="utf-8")
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe("bash", ["bash", "--rcfile", str(rc), "-i"], {**os.environ, "TERM": "dumb", "INPUTRC": "/dev/null"})
    output = b""

    def read_for(seconds: float) -> None:
        nonlocal output
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            ready, _, _ = select.select([fd], [], [], 0.1)
            if ready:
                try:
                    output += os.read(fd, 4096)
                except OSError:
                    return

    try:
        read_for(1.0)
        os.write(fd, f"vydra d {YT} -f mp3\r".encode())
        read_for(1.5)
        os.write(fd, b"exit\r")
        read_for(0.5)
    finally:
        os.close(fd)
        os.waitpid(pid, 0)
    text = output.decode("utf-8", errors="replace")
    assert f"ARGS:d|{YT}|-f|mp3|" in text, text
