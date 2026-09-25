"""Починки для нативной Windows, которые проверяются без Windows (живой прогон — test_windows_install.py)."""

import errno
import os
import stat
import threading
from pathlib import Path

from vydra import config, downloader, servers, shell
from vydra.config import Settings

ROOT = Path(__file__).parent.parent


def test_busy_port_on_windows_is_busy_not_admin_only():
    """Windows отвечает WSAEACCES на занятый/зарезервированный порт: это «занят» (берём соседний), а не «нужен админ»."""
    denied = OSError(errno.EACCES, "forbidden")
    assert servers.bind_error_state(denied, windows=True) == "busy"
    assert servers.bind_error_state(denied, windows=False) == "denied"
    assert servers.bind_error_state(OSError(errno.EADDRINUSE, "in use"), windows=True) == "busy"


def test_stop_request_file_stops_the_server(tmp_path):
    """Windows: `vydra stop` кладёт ui-<порт>.stop, сервер видит его и останавливается мягко."""
    hit, done = threading.Event(), threading.Event()
    (tmp_path / "ui-8801.stop").write_text("old")  # просьба прошлому серверу — не нам
    watcher = threading.Thread(target=servers.watch_stop_request, args=(tmp_path, 8801, hit.set, done, 0.02))
    watcher.start()
    assert not hit.wait(0.2)
    assert servers.request_stop(tmp_path, 8801)
    assert hit.wait(2)
    watcher.join(2)
    assert not (tmp_path / "ui-8801.stop").exists()


def test_windows_stop_uses_request_then_taskkill(tmp_path, monkeypatch):
    monkeypatch.setattr(servers, "STOP_BY_FILE", True)
    monkeypatch.setattr(servers, "lock_held", lambda path: True)  # сервер не уходит сам
    monkeypatch.setattr(servers, "pid_alive", lambda pid: True)
    killed = []
    monkeypatch.setattr(servers, "kill_process_tree", killed.append)
    assert not servers.stop(tmp_path, servers.Server(8802, 4242), timeout=0.2)
    assert killed == [4242]
    assert not (tmp_path / "ui-8802.stop").exists()  # просьбу за собой убрали


def test_worker_puts_own_tools_first_in_path(monkeypatch):
    """yt-dlp ищет ffmpeg для загрузки куском только в PATH — своя копия выдры должна быть там первой."""
    monkeypatch.setenv("PATH", "/usr/bin")
    downloader.prefer_own_tools({"ffmpeg": "/opt/vydra/bin/ffmpeg", "js_runtime": ["deno", "/opt/vydra/bin/deno"]})
    assert os.environ["PATH"].split(os.pathsep) == ["/opt/vydra/bin", "/usr/bin"]


def _script(folder: Path, name: str, body: str) -> Path:
    path = folder / name
    path.write_text(f"#!/bin/sh\necho '{body}'\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_old_node_in_path_is_skipped_for_deno(tmp_path, monkeypatch):
    """Node.js 18/20 из PATH yt-dlp не берёт — выдра не должна на нём останавливаться."""
    bin_dir, tools = tmp_path / "bin", tmp_path / "tools"
    bin_dir.mkdir()
    tools.mkdir()
    _script(bin_dir, "node", "v18.19.0")
    deno = _script(tools, "deno", "deno 2.9.7 (stable, release, x86_64-unknown-linux-gnu)")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("VD_NODE", raising=False)
    monkeypatch.setattr(config, "_nvm_node", lambda: None)
    s = Settings(work_dir=tmp_path, config_dir=tmp_path, tools_dir=tools, default_library=tmp_path)
    assert s.js_runtime == ("deno", str(deno))
    deno.unlink()
    assert s.js_runtime is None
    assert s.stale_js_runtime()[2] == "18.19.0"  # доктор скажет, что node старый


def test_powershell_profile_only_where_scripts_may_run():
    """Restricted (по умолчанию в Windows PowerShell 5.1): строка в profile.ps1 = красная ошибка в каждом окне."""
    assert not shell.scripts_allowed("Restricted")
    assert not shell.scripts_allowed("AllSigned")
    assert not shell.scripts_allowed("")
    assert shell.scripts_allowed("RemoteSigned") and shell.scripts_allowed("Bypass")


def test_install_ps1_survives_windows_powershell_51():
    """Все вызовы программ — через обёртки с ErrorActionPreference=Continue (иначе stderr uv роняет 5.1);
    под irm | iex нет `exit` (закрыл бы окно); запущенная выдра останавливается до uv tool install."""
    text = (ROOT / "install.ps1").read_text(encoding="ascii")
    body = text[text.index("function Install-Vydra {"):]
    assert "*> $null" not in body and "2>&1" not in body
    import re

    code = "\n".join(line.split("#")[0] for line in body.splitlines())
    assert re.findall(r"\bexit\b", code) == ["exit"]  # единственный — под if ($VydraSelf), то есть при запуске файлом
    assert "if ($VydraSelf) { exit" in code
    assert body.index("Stop-Vydra $uv") < body.index("$install = @('tool', 'install'")
    assert "SecurityProtocol" in text and "UV_PYTHON_PREFERENCE = 'only-managed'" in text
    assert "update', '--record'" in text
