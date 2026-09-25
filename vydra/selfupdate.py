"""Самообновление: `vydra update` ставит последнюю версию выдры из того же репозитория, что и установщик.

Откуда брать (первое, что есть): `--repo` → переменная VYDRA_REPO → источник, записанный при установке
(<config>/install.json) → источник из квитанции uv (uv-receipt.toml) → GitHub main.

Что стоит сейчас, помнит <config>/install.json: источник, коммит, дата коммита, версия. Его пишут установщик
(`vydra update --record`) и само обновление. Что есть в источнике — узнаём у GitHub API (коммит ветки) или у git
локальной копии. Совпадает — ставить нечего; узнать не удалось — переустанавливаем и честно об этом говорим.

Ставит uv (`uv tool install --force`, как install.sh). Перед этим окружение выдры копируется в резерв: если uv
упал или новая версия не запускается, резерв возвращается на место — рабочая установка не ломается.
Вся сеть — с таймаутами.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_REPO = "https://github.com/Ihor-Zakharov/vydra/archive/refs/heads/main.zip"
RECORD = "install.json"
NET_TIMEOUT = 10
INSTALL_TIMEOUT = 900  # uv может скачать Python и все зависимости
CHECK_TIMEOUT = 120
UA = {"User-Agent": "vydra-update", "Accept": "application/vnd.github+json"}
_GITHUB_ZIP = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/archive/refs/heads/(.+?)\.(?:zip|tar\.gz)$", re.I)


class UpdateError(Exception):
    """Обновить не вышло; установленная выдра осталась прежней. hint — что сделать."""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


@dataclass
class Revision:
    source: str  # путь к локальной копии или URL архива
    commit: str | None = None
    date: str | None = None  # ISO 8601
    version: str | None = None
    dirty: bool = False  # в локальной копии есть незакоммиченные правки

    def label(self) -> str:
        parts = [self.version or "?"]
        if self.commit:
            parts.append(self.commit[:7] + (" + правки" if self.dirty else ""))
        if day := _day(self.date):
            parts.append(f"от {day}")
        return " · ".join(parts)

    def same_as(self, other: Revision) -> bool:
        """Та же версия того же источника (незакоммиченные правки — никогда не «та же»)."""
        return bool(
            self.commit and other.commit and self.commit == other.commit and not other.dirty and not self.dirty
            and normalize(self.source) == normalize(other.source)
        )  # fmt: skip


def _day(iso: str | None) -> str | None:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", iso or "")
    return f"{m[3]}.{m[2]}.{m[1]}" if m else None


def is_url(source: str) -> bool:
    return bool(re.match(r"^(?:https?|git\+https?|git\+ssh|file)://", source, re.I))


def normalize(source: str) -> str:
    source = source.strip()
    return source if is_url(source) else str(Path(source).expanduser().resolve())


def describe(source: str) -> str:
    if m := _GITHUB_ZIP.match(source):
        return f"GitHub {m[1]}/{m[2]} (ветка {m[3]})"
    return source if is_url(source) else f"локальная копия {source}"


# --- где стоит выдра ------------------------------------------------------------------------


def installed_env() -> Path | None:
    """Окружение uv tool, из которого запущена выдра (установщик ставит именно так), или None."""
    prefix = Path(sys.prefix)
    return prefix if (prefix / "uv-receipt.toml").is_file() else None


def _receipt(env: Path) -> dict:
    try:
        return tomllib.loads((env / "uv-receipt.toml").read_text(encoding="utf-8")).get("tool", {})
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def receipt_source(env: Path) -> str | None:
    for req in _receipt(env).get("requirements") or []:
        if req.get("name") == "vydra":
            return req.get("directory") or req.get("path") or req.get("url")
    return None


def read_record(config_dir: Path) -> Revision | None:
    try:
        data = json.loads((config_dir / RECORD).read_text(encoding="utf-8"))
        return Revision(**{k: data.get(k) for k in ("source", "commit", "date", "version")}, dirty=bool(data.get("dirty")))
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def write_record(config_dir: Path, rev: Revision) -> None:
    from .fsutil import atomic_write

    data = asdict(rev) | {"installed": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    try:
        atomic_write(config_dir / RECORD, json.dumps(data, ensure_ascii=False, indent=2))
    except OSError:
        pass  # без записи следующее обновление просто переустановит


def choose_source(arg: str | None, config_dir: Path, env: Path | None) -> tuple[str, str]:
    """(источник, откуда он взялся). Записанная раньше локальная папка, которой больше нет, пропускается."""
    if arg:
        source = normalize(arg)
        if not is_url(source) and not (Path(source) / "pyproject.toml").is_file():
            raise UpdateError(f"В «{source}» нет исходников выдры (pyproject.toml)", "Укажите папку с копией репозитория")
        return source, "указан ключом --repo"
    if env_repo := os.environ.get("VYDRA_REPO"):
        return normalize(env_repo), "из переменной VYDRA_REPO"
    candidates = [((read_record(config_dir) or Revision("")).source, "откуда ставили")]
    if env is not None:
        candidates.append((receipt_source(env) or "", "откуда ставили (по uv)"))
    for source, why in candidates:
        if source and (is_url(source) or (Path(source) / "pyproject.toml").is_file()):
            return normalize(source), why
    return DEFAULT_REPO, "репозиторий по умолчанию"


# --- что есть в источнике -------------------------------------------------------------------


def inspect(source: str) -> Revision:
    """Последняя версия в источнике. commit=None — узнать не удалось (нет сети, не git, чужой адрес)."""
    if not is_url(source):
        return _inspect_local(Path(source))
    if m := _GITHUB_ZIP.match(source):
        return _inspect_github(source, m[1], m[2], m[3])
    return Revision(source)


def _git(folder: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(folder), *args], stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=NET_TIMEOUT, check=False)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _inspect_local(folder: Path) -> Revision:
    rev = Revision(str(folder), version=_pyproject_version((folder / "pyproject.toml").read_text(encoding="utf-8"))
                   if (folder / "pyproject.toml").is_file() else None)  # fmt: skip
    rev.commit = _git(folder, "rev-parse", "HEAD")
    if rev.commit:
        rev.date = _git(folder, "log", "-1", "--format=%cI")
        rev.dirty = bool(_git(folder, "status", "--porcelain", "--untracked-files=no"))
    return rev


def _get(url: str) -> bytes | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=NET_TIMEOUT) as resp:
            return resp.read(2 * 1024 * 1024)
    except Exception:  # noqa: BLE001 — нет сети, лимит API, 404: «узнать не удалось»
        return None


def _inspect_github(source: str, owner: str, repo: str, branch: str) -> Revision:
    rev = Revision(source)
    raw = _get(f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}")
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        data = {}
    rev.commit = data.get("sha") if isinstance(data, dict) else None
    if rev.commit:
        rev.date = ((data.get("commit") or {}).get("committer") or {}).get("date")
        pyproject = _get(f"https://raw.githubusercontent.com/{owner}/{repo}/{rev.commit}/pyproject.toml")
        rev.version = _pyproject_version(pyproject.decode("utf-8", "replace")) if pyproject else None
    return rev


def _pyproject_version(text: str) -> str | None:
    try:
        return tomllib.loads(text).get("project", {}).get("version")
    except tomllib.TOMLDecodeError:
        return None


# --- установка ------------------------------------------------------------------------------


def uv_binary() -> str | None:
    if found := os.environ.get("UV") or shutil.which("uv"):
        return found
    for candidate in (Path.home() / ".local/bin/uv", Path.home() / ".cargo/bin/uv"):
        if candidate.is_file():
            return str(candidate)
    return None


def install(source: str, env: Path, backup: Path, run=subprocess.run) -> str:
    """Поставить выдру из source на место env. Возвращает новую версию. UpdateError — не вышло, а прежняя
    установка восстановлена из резервной копии (uv сам не трогает её при ошибке сборки или разрешения, но
    на обрыв посреди установки полагаться не стоит)."""
    uv = uv_binary()
    if uv is None:
        raise UpdateError("Не найден uv — через него выдра и ставится", "Запустите установщик ещё раз (install.sh)")
    shutil.rmtree(backup, ignore_errors=True)
    backup.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(env, backup, symlinks=True)
    except OSError as exc:
        raise UpdateError(f"Не удалось сделать резервную копию перед обновлением: {exc.strerror or exc}",
                          "Освободите место на диске и повторите") from exc  # fmt: skip
    python = f"{sys.version_info.major}.{sys.version_info.minor}"
    cmd = [uv, "tool", "install", "--force", "--reinstall-package", "vydra", "--python", python, source]
    try:
        try:
            result = run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=INSTALL_TIMEOUT, check=False)  # fmt: skip
        except subprocess.TimeoutExpired as exc:
            _restore(env, backup)
            raise UpdateError(f"Установка не закончилась за {INSTALL_TIMEOUT // 60} мин", _NET_HINT) from exc
        except OSError as exc:
            _restore(env, backup)
            raise UpdateError(f"uv не запустился: {exc.strerror or exc}") from exc
        if result.returncode != 0:
            _restore(env, backup)
            raise UpdateError(*_explain_uv(result.stderr or result.stdout or "", source))
        version = _check(env, run)
        if version is None:
            _restore(env, backup)
            raise UpdateError("Новая версия поставилась, но не запускается — вернул прежнюю",
                              "Сообщите об этом; пока работает прежняя версия")  # fmt: skip
        return version
    finally:
        shutil.rmtree(backup, ignore_errors=True)


_NET_HINT = "Проверьте интернет и повторите: выдра обновить. Установленная версия не изменилась"


def _explain_uv(output: str, source: str) -> tuple[str, str | None]:
    tail = " / ".join(line.strip() for line in output.strip().splitlines()[-3:] if line.strip())[:400]
    low = output.lower()
    if any(s in low for s in ("dns", "failed to fetch", "failed to download", "connect", "timed out", "network",
                              "resolve host", "error sending request")):  # fmt: skip
        return f"Нет связи с {describe(source)} — новая версия не скачалась", _NET_HINT
    if "404" in low or "not found" in low:
        return f"Источник не найден: {source}", "Проверьте адрес или путь (--repo)"
    return f"Новая версия не установилась: {tail or 'uv завершился с ошибкой'}", "Установленная версия не изменилась"


def _check(env: Path, run) -> str | None:
    """Новая выдра запускается? Версия или None."""
    python = env / "bin" / "python"
    try:
        out = run([str(python), "-c", "import vydra, vydra.cli; print(vydra.__version__)"], stdin=subprocess.DEVNULL,
                  capture_output=True, text=True, timeout=CHECK_TIMEOUT, check=False)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def _restore(env: Path, backup: Path) -> None:
    shutil.rmtree(env, ignore_errors=True)
    shutil.copytree(backup, env, symlinks=True)
    bin_dir = env / "bin"
    for entry in _receipt(env).get("entrypoints") or []:  # команды в ~/.local/bin — ссылки в окружение
        link = Path(entry.get("install-path") or "")
        if link.name and not link.exists() and (bin_dir / entry.get("name", "")).exists():
            try:
                link.unlink(missing_ok=True)
                link.symlink_to(bin_dir / entry["name"])
            except OSError:
                pass
