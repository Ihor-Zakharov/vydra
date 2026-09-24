"""Имена файлов, которые понимает Windows, и сохранение без перезаписи."""

from __future__ import annotations

import errno
import os
import re
import shutil
import threading
import unicodedata
from pathlib import Path

MAX_STEM = 100
MAX_STEM_BYTES = 180
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_REPLACE = str.maketrans({":": " -", "/": "-", "\\": "-", "|": "-", '"': "'", "<": "(", ">": ")", "?": "", "*": ""})
_GENERIC_TITLE = re.compile(r"^(video|reel|post) by \S+$", re.IGNORECASE)
_save_lock = threading.Lock()


def safe_stem(text: str, fallback: str = "video") -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = re.sub(r"#\S+", "", text)  # хэштеги из подписей TikTok/Instagram
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")  # управляющие, \n
    text = text.translate(_REPLACE)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_STEM:
        text = text[:MAX_STEM].rsplit(" ", 1)[0] if " " in text[:MAX_STEM] else text[:MAX_STEM]
    while len(text.encode("utf-8")) > MAX_STEM_BYTES:  # эмодзи — по 4 байта, а имя в ext4/APFS — до 255 байт
        text = text[:-1]
    text = text.rstrip(" .-")
    if not text:
        return fallback
    if text.split(".")[0].upper() in _RESERVED:
        text = f"_{text}"
    return text


def title_for(info: dict) -> str:
    """У Instagram заголовок «Video by nasa» — полезнее первая строка подписи."""
    title = (info.get("title") or "").strip()
    description = (info.get("description") or "").strip()
    # TikTok: yt-dlp обрезает подпись до «…» — берём её целиком
    if title.endswith(("...", "…")) and description.startswith(title.rstrip(".…")[:40]):
        return description.splitlines()[0]
    if (not title or _GENERIC_TITLE.match(title)) and description:
        first_line = description.splitlines()[0]
        if safe_stem(first_line, ""):
            return first_line
    return title or info.get("id") or "video"


MAX_PATH = 240  # Windows: 260 минус запас на « (99)» и .part
MAX_NAME_BYTES = 240  # ext4/APFS: 255 байт на имя; кириллица — 2 байта, эмодзи — 4


def _windows_len(directory: Path) -> int | None:
    """Длина пути в том виде, как его увидит Windows (None — не Windows-диск)."""
    raw = str(directory)
    if os.name == "nt":
        return len(raw)
    if re.match(r"^/mnt/[a-zA-Z]/", raw + "/"):
        return len(raw) - 4  # /mnt/c → C:
    return None


def fit_stem(stem: str, directory: Path, ext: str) -> str:
    """Укорачивает имя, чтобы полный путь влез в MAX_PATH Windows и имя — в 255 байт."""
    suffix = f" (99).{ext}.part"
    win = _windows_len(directory)
    while stem:
        name = stem + suffix
        too_long = len(name.encode("utf-8")) > MAX_NAME_BYTES + len(".part")
        if win is not None:
            too_long = too_long or win + 1 + len(name) > MAX_PATH + len(".part")
        if not too_long:
            break
        cut = stem[:-1]
        stem = cut.rsplit(" ", 1)[0] if " " in cut[-12:] else cut
        stem = stem.rstrip(" .-")
    return stem or "video"


class CopyError(OSError):
    """Копия получилась не того размера — диск сбоит или место кончилось на ходу."""


def save_unique(src: Path, directory: Path, stem: str, ext: str) -> Path:
    """Копирует src в directory/<stem>.<ext>, при совпадении добавляет « (2)», « (3)»…

    Имя резервируется пустым файлом (сканер хранилища пропускает файлы в 0 байт), данные пишутся
    в <имя>.part и атомарно подменяются. Совпадения ищутся без учёта регистра — как в Windows."""
    from .fsutil import replace

    directory.mkdir(parents=True, exist_ok=True)
    stem = fit_stem(stem, directory, ext)
    size = src.stat().st_size
    free = shutil.disk_usage(directory).free
    if free < size + 16 * 1024 * 1024:
        raise OSError(errno.ENOSPC, "Не хватает места на диске хранилища")
    with _save_lock:  # под замком только резерв имени, само копирование — снаружи
        taken = {entry.name.casefold() for entry in directory.iterdir()}
        n = 1
        while True:
            name = f"{stem}.{ext}" if n == 1 else f"{stem} ({n}).{ext}"
            target = directory / name
            if name.casefold() in taken or f"{name}.part".casefold() in taken:
                n += 1
                continue
            try:
                target.touch(exist_ok=False)
                break
            except FileExistsError:
                n += 1
    # копируем во временное имя и переименовываем: сканер хранилища не увидит недокопированный файл
    part = target.with_name(target.name + ".part")
    try:
        shutil.copyfile(src, part)
        if part.stat().st_size != size:
            raise CopyError(errno.EIO, "Копия файла получилась неполной")
        replace(part, target)
    except BaseException:
        part.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    src.unlink(missing_ok=True)
    return target
