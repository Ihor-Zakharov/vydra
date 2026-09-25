"""Настройки: где хранилище, временные файлы, свои инструменты (ffmpeg, deno) и prefs.json."""

from __future__ import annotations

import json
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir, user_data_dir

from . import system

APP = "vydra"
FOLDER_NAME = "VideoDownloader"
EXE = ".exe" if system.OS == "windows" else ""


@dataclass(frozen=True)
class Settings:
    work_dir: Path  # временные файлы (в WSL — на ext4, чтобы не гонять ffmpeg по /mnt/c)
    config_dir: Path  # prefs.json (выбранная папка) и cookies.txt
    tools_dir: Path  # ffmpeg/ffprobe/deno, которые доустановил health-fix
    default_library: Path  # «Загрузки»/VideoDownloader
    fixed_library: Path | None = None  # VD_LIBRARY_DIR: папка задана снаружи и из интерфейса не меняется
    port: int = 8765
    max_parallel: int = 2

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ.get
        fixed = env("VD_LIBRARY_DIR")
        return cls(
            work_dir=Path(env("VD_WORK_DIR") or user_cache_dir(APP, appauthor=False)),
            config_dir=Path(env("VD_CONFIG_DIR") or user_config_dir(APP, appauthor=False)),
            tools_dir=Path(env("VD_TOOLS_DIR") or Path(user_data_dir(APP, appauthor=False)) / "bin"),
            default_library=system.downloads_dir() / FOLDER_NAME,
            fixed_library=Path(fixed) if fixed else None,
            port=int(env("VD_PORT", "8765")),
            max_parallel=int(env("VD_PARALLEL", "2")),
        )

    # Бинарники ищем при каждом обращении: health-fix может доустановить их на лету.

    def binary(self, name: str) -> str | None:
        own = self.tools_dir / f"{name}{EXE}"
        if own.is_file() and os.access(own, os.X_OK):
            return str(own)
        if found := shutil.which(name):
            return found
        for directory in (Path.home() / ".local/bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
            candidate = directory / f"{name}{EXE}"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        return None

    @property
    def ffmpeg(self) -> str | None:
        return self.binary("ffmpeg")

    @property
    def ffprobe(self) -> str | None:
        return self.binary("ffprobe")

    @property
    def js_runtime(self) -> tuple[str, str] | None:
        """(имя, путь) JS-движка: без него YouTube отдаёт не все форматы."""
        if node := os.environ.get("VD_NODE") or self.binary("node") or _nvm_node():
            return ("node", node)
        if deno := self.binary("deno"):
            return ("deno", deno)
        return None

    @property
    def cookies_file(self) -> Path | None:
        path = self.config_dir / "cookies.txt"
        return path if path.is_file() and path.stat().st_size > 0 else None


class Prefs:
    """Настройки, которые меняются из интерфейса (prefs.json)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.config_dir / "prefs.json"
        self._lock = threading.Lock()

    def _read(self) -> dict:
        # root хранилища спрашивают тысячи раз подряд (на каждый файл) — перечитываем, только если файл сменился
        try:
            st = self.path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            return {}
        cached = getattr(self, "_cached", None)
        if cached is not None and cached[0] == stamp:
            return dict(cached[1])
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        data = data if isinstance(data, dict) else {}
        self._cached = (stamp, data)
        return dict(data)

    def _write(self, data: dict) -> None:
        from .fsutil import atomic_write

        atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=2))

    @property
    def library_dir(self) -> Path:
        if self.settings.fixed_library:
            return self.settings.fixed_library
        custom = self._read().get("library_dir")
        return Path(custom) if custom else self.settings.default_library

    @property
    def console_art(self) -> bool | None:
        """Заставка интерактивной консоли: True/False — выбрано, None — по умолчанию (включена)."""
        value = self._read().get("console_art")
        return value if isinstance(value, bool) else None

    def set_console_art(self, on: bool) -> None:
        with self._lock:
            data = self._read()
            data["console_art"] = on
            self._write(data)

    @property
    def previous_library_dir(self) -> Path | None:
        """Прежняя папка хранилища — откуда переносить «уже скачанное» после смены папки."""
        value = self._read().get("previous_library_dir")
        return Path(value) if value else None

    def set_previous_library_dir(self, path: Path | None) -> None:
        with self._lock:
            data = self._read()
            if path is None:
                data.pop("previous_library_dir", None)
            else:
                data["previous_library_dir"] = str(path)
            self._write(data)

    def set_library_dir(self, path: Path | None) -> None:
        with self._lock:
            data = self._read()
            if path is None or path == self.settings.default_library:
                data.pop("library_dir", None)
            else:
                data["library_dir"] = str(path)
            self._write(data)


def _nvm_node() -> str | None:
    """node из nvm: при запуске ярлыком nvm не подключён к PATH."""

    def version(path: Path) -> tuple[int, ...]:
        return tuple(int(p) for p in path.parent.parent.name.lstrip("v").split(".") if p.isdigit())

    candidates = sorted((Path.home() / ".nvm/versions/node").glob("v*/bin/node"), key=version)
    return str(candidates[-1]) if candidates else None
