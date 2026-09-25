"""Ссылки из буфера обмена: «скопировал в браузере → выдра скачать -ф мп3».

Так не нужно вставлять ссылку в командную строку, где «&» в адресе YouTube ломает команду
(bash уводит её в фон, PowerShell вообще не принимает строку).
"""

from __future__ import annotations

import re
import shutil

from . import system

_LINK = re.compile(
    r"(?:https?://[^\s<>\"'«»]+"
    r"|(?<![\w/.@])(?:(?:www|m|vm|vt|music)\.)?(?:youtube\.com|youtu\.be|tiktok\.com|instagram\.com)/[^\s<>\"'«»]+)",
    re.IGNORECASE,
)
_TRAILING = ".,;:!?)]}»›\"'"


TIMEOUT = 8.0  # PowerShell на холодной Windows стартует секунды; дольше — считаем, что буфер недоступен


class Unavailable(Exception):
    """Буфер обмена прочитать не удалось (нет программы, она упала или не ответила) — это не «пусто»."""


def read_strict() -> str:
    """Текст из буфера обмена ("" — пуст). Unavailable — прочитать не удалось, с объяснением почему."""
    if system.WINDOWS_LIKE:
        if not (shutil.which("powershell.exe") or shutil.which("powershell")):
            raise Unavailable("не найден powershell.exe — в WSL выключен interop с Windows?")
        # PowerShell отдаёт Unicode как есть (system.powershell переключает вывод на UTF-8)
        out = system.powershell("Get-Clipboard -Raw", timeout=TIMEOUT)
        if out is None:
            raise Unavailable(f"PowerShell не ответил за {TIMEOUT:.0f} с или завершился с ошибкой")
        return out
    if system.OS == "mac":
        out = system.run(["pbpaste"], timeout=5)
        if out is None:
            raise Unavailable("pbpaste не ответил")
        return out
    tried = False
    for cmd in (["wl-paste", "--no-newline"], ["xclip", "-o", "-selection", "clipboard"], ["xsel", "-ob"]):
        if shutil.which(cmd[0]):
            tried = True
            out = system.run(cmd, timeout=5)
            if out:
                return out
    if not tried:
        raise Unavailable(unavailable_hint())
    return ""


def read() -> str | None:
    """Текст из буфера обмена или None (буфер пуст или недоступен)."""
    try:
        return read_strict() or None
    except Unavailable:
        return None


def extract_links(text: str | None, limit: int = 50) -> list[str]:
    """Все ссылки из текста — без повторов, без хвостовой пунктуации."""
    links: list[str] = []
    for match in _LINK.finditer(text or ""):
        link = match.group(0).rstrip(_TRAILING)
        if link.count("(") < link.count(")"):  # «(https://…)» — скобка не часть адреса
            link = link.rstrip(")")
        if link and link not in links:
            links.append(link)
        if len(links) >= limit:
            break
    return links


def links() -> list[str]:
    return extract_links(read())


def unavailable_hint() -> str:
    if system.OS == "linux":
        return "Для буфера обмена поставьте wl-clipboard (Wayland) или xclip (X11)"
    return "Скопируйте ссылку ещё раз"
