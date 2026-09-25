"""`vydra update`: откуда обновляться, есть ли что обновлять, установка с откатом — без сети и без uv."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vydra import cli, selfupdate
from vydra.selfupdate import Revision, UpdateError

runner = CliRunner()
GH = selfupdate.DEFAULT_REPO


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.delenv("VYDRA_REPO", raising=False)
    return tmp_path / "cfg"


def fake_env(tmp_path: Path, requirement: dict) -> Path:
    env = tmp_path / "tools" / "vydra"
    (env / "bin").mkdir(parents=True)
    (env / "bin" / "vydra").write_text("#!/bin/sh\n")
    req = ", ".join(f'{k} = "{v}"' for k, v in requirement.items())
    (env / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{{ {req} }}]\npython = "3.13"\n'
        f'entrypoints = [{{ name = "vydra", install-path = "{tmp_path / "bin" / "vydra"}", from = "vydra" }}]\n'
    )
    return env


def git_repo(path: Path, version: str = "1.0.0") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "pyproject.toml").write_text(f'[project]\nname = "vydra"\nversion = "{version}"\n')
    for args in (["init", "-q"], ["add", "."], ["-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    return path


# --- откуда ---------------------------------------------------------------------------------


def test_source_priority(tmp_path, cfg, monkeypatch):
    local = git_repo(tmp_path / "src")
    env = fake_env(tmp_path, {"name": "vydra", "directory": str(local)})
    assert selfupdate.choose_source(None, cfg, None) == (GH, "репозиторий по умолчанию")
    assert selfupdate.choose_source(None, cfg, env)[0] == str(local)  # по квитанции uv
    selfupdate.write_record(cfg, Revision("https://example.com/vydra.zip"))
    assert selfupdate.choose_source(None, cfg, env)[0] == "https://example.com/vydra.zip"  # записано установщиком
    monkeypatch.setenv("VYDRA_REPO", str(local))
    assert selfupdate.choose_source(None, cfg, env) == (str(local), "из переменной VYDRA_REPO")
    assert selfupdate.choose_source(GH, cfg, env) == (GH, "указан ключом --repo")


def test_vanished_local_copy_falls_back_to_github(tmp_path, cfg):
    """Ставили из рабочей копии, а её удалили — обновляемся с GitHub, а не падаем."""
    selfupdate.write_record(cfg, Revision(str(tmp_path / "удалённая")))
    env = fake_env(tmp_path, {"name": "vydra", "directory": str(tmp_path / "тоже-нет")})
    assert selfupdate.choose_source(None, cfg, env)[0] == GH


def test_explicit_folder_without_sources_is_an_error(tmp_path, cfg):
    with pytest.raises(UpdateError, match="нет исходников"):
        selfupdate.choose_source(str(tmp_path), cfg, None)


# --- что нового -----------------------------------------------------------------------------


@pytest.mark.skipif(not shutil.which("git"), reason="нужен git")
def test_local_revision_and_dirty_copy(tmp_path):
    repo = git_repo(tmp_path / "src", "1.2.0")
    rev = selfupdate.inspect(str(repo))
    assert rev.version == "1.2.0" and len(rev.commit) == 40 and rev.date and not rev.dirty
    assert rev.same_as(Revision(str(repo), commit=rev.commit))
    (repo / "pyproject.toml").write_text('[project]\nname = "vydra"\nversion = "1.2.1"\n')
    dirty = selfupdate.inspect(str(repo))
    assert dirty.dirty and "+ правки" in dirty.label() and not Revision(str(repo), commit=rev.commit).same_as(dirty)


def test_github_revision(monkeypatch):
    answers = {
        "https://api.github.com/repos/Ihor-Zakharov/vydra/commits/main":
            json.dumps({"sha": "a" * 40, "commit": {"committer": {"date": "2026-09-25T04:40:00Z"}}}).encode(),
        f"https://raw.githubusercontent.com/Ihor-Zakharov/vydra/{'a' * 40}/pyproject.toml":
            b'[project]\nname = "vydra"\nversion = "1.1.0"\n',
    }  # fmt: skip
    monkeypatch.setattr(selfupdate, "_get", answers.get)
    rev = selfupdate.inspect(GH)
    assert rev.label() == "1.1.0 · aaaaaaa · от 25.09.2026"


def test_no_network_means_unknown_not_crash(monkeypatch):
    def offline(req, timeout):
        assert timeout <= 15  # вся сеть — с таймаутом
        raise OSError("нет сети")

    monkeypatch.setattr(selfupdate.urllib.request, "urlopen", offline)
    assert selfupdate.inspect(GH).commit is None
    assert selfupdate.inspect("https://example.com/vydra.zip").commit is None


# --- установка с откатом --------------------------------------------------------------------


class Uv:
    """Подмена subprocess.run: uv tool install и проверка новой версии."""

    def __init__(self, env: Path, install_rc=0, breaks=False, version="1.1.0"):
        self.env, self.install_rc, self.breaks, self.version, self.calls = env, install_rc, breaks, version, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if "tool" in cmd:
            if self.install_rc == 0 or self.breaks:
                (self.env / "bin" / "vydra").write_text("новая")
            else:
                shutil.rmtree(self.env / "bin")  # «uv упал посреди установки»
            return subprocess.CompletedProcess(cmd, self.install_rc, "", "error: Failed to fetch: dns error")
        ok = not self.breaks
        return subprocess.CompletedProcess(cmd, 0 if ok else 1, self.version + "\n" if ok else "", "")


def test_install_success(tmp_path, monkeypatch):
    monkeypatch.setattr(selfupdate, "uv_binary", lambda: "/x/uv")
    env = fake_env(tmp_path, {"name": "vydra", "url": GH})
    uv = Uv(env)
    assert selfupdate.install(GH, env, tmp_path / "backup", run=uv) == "1.1.0"
    assert uv.calls[0][:6] == ["/x/uv", "tool", "install", "--force", "--reinstall-package", "vydra"]
    assert uv.calls[0][-1] == GH and not (tmp_path / "backup").exists()


@pytest.mark.parametrize("uv_kind", ["network", "broken-after-install"])
def test_failed_install_restores_working_version(tmp_path, monkeypatch, uv_kind):
    monkeypatch.setattr(selfupdate, "uv_binary", lambda: "/x/uv")
    env = fake_env(tmp_path, {"name": "vydra", "url": GH})
    (tmp_path / "bin").mkdir()
    uv = Uv(env, install_rc=2, breaks=False) if uv_kind == "network" else Uv(env, breaks=True)
    with pytest.raises(UpdateError) as info:
        selfupdate.install(GH, env, tmp_path / "backup", run=uv)
    assert (env / "bin" / "vydra").read_text() == "#!/bin/sh\n"  # прежняя установка на месте
    assert (tmp_path / "bin" / "vydra").is_symlink()  # команда в ~/.local/bin снова ведёт в окружение
    assert not (tmp_path / "backup").exists()
    if uv_kind == "network":
        assert "Нет связи" in str(info.value) and "не изменилась" in info.value.hint


def test_no_uv_is_explained(tmp_path, monkeypatch):
    monkeypatch.setattr(selfupdate, "uv_binary", lambda: None)
    with pytest.raises(UpdateError, match="uv"):
        selfupdate.install(GH, fake_env(tmp_path, {"name": "vydra"}), tmp_path / "b")


# --- команда --------------------------------------------------------------------------------


@pytest.fixture
def cli_env(tmp_path, monkeypatch, cfg):
    monkeypatch.setenv("VD_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("VD_WORK_DIR", str(tmp_path / "work"))
    ytdlp = []
    monkeypatch.setattr(cli, "_update_ytdlp", lambda check=False: ytdlp.append(check))
    return ytdlp


def test_update_when_already_latest_only_checks_ytdlp(tmp_path, monkeypatch, cli_env, cfg):
    env = fake_env(tmp_path, {"name": "vydra", "url": GH})
    monkeypatch.setattr(selfupdate, "installed_env", lambda: env)
    selfupdate.write_record(cfg, Revision(GH, commit="a" * 40))
    monkeypatch.setattr(selfupdate, "inspect", lambda source: Revision(GH, commit="a" * 40, version="1.0.0"))
    monkeypatch.setattr(selfupdate, "install", lambda *a, **k: pytest.fail("ставить нечего"))
    result = runner.invoke(cli.app, ["update"])
    assert result.exit_code == 0, result.output
    assert "последняя версия" in result.output and cli_env == [False]


def test_update_installs_new_version_then_restarts_ui(tmp_path, monkeypatch, cli_env, cfg):
    env = fake_env(tmp_path, {"name": "vydra", "url": GH})
    monkeypatch.setattr(selfupdate, "installed_env", lambda: env)
    selfupdate.write_record(cfg, Revision(GH, commit="a" * 40, date="2026-09-20T00:00:00Z"))
    monkeypatch.setattr(selfupdate, "inspect", lambda s: Revision(GH, commit="b" * 40, date="2026-09-25T00:00:00Z"))
    monkeypatch.setattr(selfupdate, "install", lambda source, env, backup: "1.1.0")
    steps = []
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: steps.append(cmd[4:]) or subprocess.CompletedProcess(cmd, 0))
    result = runner.invoke(cli.app, ["обновить"])
    assert result.exit_code == 0, result.output
    assert "aaaaaaa" in result.output and "1.1.0 · bbbbbbb · от 25.09.2026" in result.output
    assert ["update", "--only-ytdlp", "--no-banner"] in steps and ["restart", "--quiet"] in steps
    assert selfupdate.read_record(cfg).commit == "b" * 40 and selfupdate.read_record(cfg).version == "1.1.0"


def test_unknown_latest_reinstalls_and_says_so(tmp_path, monkeypatch, cli_env, cfg):
    env = fake_env(tmp_path, {"name": "vydra", "url": GH})
    monkeypatch.setattr(selfupdate, "installed_env", lambda: env)
    monkeypatch.setattr(selfupdate, "inspect", lambda s: Revision(s))
    installed = []
    monkeypatch.setattr(selfupdate, "install", lambda source, env, backup: installed.append(source) or "1.0.0")
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0))
    result = runner.invoke(cli.app, ["update"])
    assert result.exit_code == 0 and installed == [GH]
    assert "переустанавливаю" in result.output and "коммит неизвестен" in result.output


def test_failed_update_is_an_error_with_hint(tmp_path, monkeypatch, cli_env):
    env = fake_env(tmp_path, {"name": "vydra", "url": GH})
    monkeypatch.setattr(selfupdate, "installed_env", lambda: env)
    monkeypatch.setattr(selfupdate, "inspect", lambda s: Revision(s))

    def broken(*a, **k):
        raise UpdateError("Нет связи с GitHub", selfupdate._NET_HINT)

    monkeypatch.setattr(selfupdate, "install", broken)
    result = runner.invoke(cli.app, ["update"])
    assert result.exit_code == 1 and "Нет связи" in result.output and "не изменилась" in result.output


def test_update_from_sources_checkout_updates_only_ytdlp(monkeypatch, cli_env):
    monkeypatch.setattr(selfupdate, "installed_env", lambda: None)
    result = runner.invoke(cli.app, ["update"])
    assert result.exit_code == 0 and "git pull" in result.output and cli_env == [False]


def test_only_ytdlp_and_check(monkeypatch, cli_env, tmp_path):
    assert runner.invoke(cli.app, ["update", "--только-ytdlp"]).exit_code == 0
    env = fake_env(tmp_path, {"name": "vydra", "url": GH})
    monkeypatch.setattr(selfupdate, "installed_env", lambda: env)
    monkeypatch.setattr(selfupdate, "inspect", lambda s: Revision(s, commit="c" * 40))
    monkeypatch.setattr(selfupdate, "install", lambda *a, **k: pytest.fail("--check ничего не ставит"))
    result = runner.invoke(cli.app, ["update", "--check"])
    assert result.exit_code == 0 and "выдра обновить" in result.output
    assert cli_env == [False, True]


def test_record_from_installer(tmp_path, monkeypatch, cfg):
    monkeypatch.setenv("VD_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(selfupdate, "inspect", lambda s: Revision(s, commit="d" * 40))
    assert runner.invoke(cli.app, ["update", "--record", GH]).exit_code == 0
    rec = selfupdate.read_record(cfg)
    assert rec.source == GH and rec.commit == "d" * 40 and rec.version


def test_installer_records_the_install():
    text = (Path(__file__).parent.parent / "install.sh").read_text(encoding="utf-8")
    assert 'vydra update --record "$REPO"' in text


def test_real_uv_tool_env_is_detected(tmp_path, monkeypatch):
    env = fake_env(tmp_path, {"name": "vydra"})
    monkeypatch.setattr(sys, "prefix", str(env))
    assert selfupdate.installed_env() == env


def test_own_processes_never_import_vydra_from_the_current_folder(tmp_path, monkeypatch):
    """`python -m vydra` ищет пакет сначала в текущей папке: из клона репозитория перезапуск и воркер загрузок
    поднимали чужую (старую) версию — найдено проверкой самообновления (/api/health отвечал 1.0.0 после 1.0.1)."""
    from vydra import downloader, servers, tools

    assert downloader.WORKER_CMD[1] == "-I"
    assert tools.shortcut_command()[1].endswith("-I -m vydra ui")
    fake = tmp_path / "vydra"
    fake.mkdir()
    (fake / "__init__.py").write_text('__version__ = "чужая"\n')
    out = subprocess.run([sys.executable, "-I", "-c", "import vydra; print(vydra.__version__)"], cwd=tmp_path,
                         capture_output=True, text=True, timeout=30)  # fmt: skip
    assert out.stdout.strip() != "чужая"
    seen = {}

    class Popen:
        def __init__(self, cmd, **kw):
            seen["cmd"] = cmd

    monkeypatch.setattr(servers.subprocess, "Popen", Popen)
    servers.spawn(tmp_path, 8810)
    assert seen["cmd"][1:4] == ["-I", "-m", "vydra"]
