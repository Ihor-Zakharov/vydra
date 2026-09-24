"""Таймкоды отрезков: «1:30», «01:02:03», «90», «1.5», «1m30s», «1м30с»."""

from __future__ import annotations

import re

_UNITS = re.compile(r"(\d+(?:[.,]\d+)?)\s*(мин|сек|h|ч|m|м|s|с)", re.IGNORECASE)  # длинные раньше коротких
_SECONDS = {"h": 3600, "ч": 3600, "m": 60, "м": 60, "мин": 60, "s": 1, "с": 1, "сек": 1}


def parse_time(text: str | None) -> float | None:
    """Секунды или None для пустой строки. ValueError — с понятным текстом."""
    raw = (text or "").strip().lower().replace(",", ".")
    if not raw or raw in ("конец", "end", "-"):
        return None
    if re.fullmatch(r"\d+(\.\d+)?", raw):
        return float(raw)
    if re.fullmatch(r"\d+(:\d{1,2}){1,2}(\.\d+)?", raw):
        total = 0.0
        for part in raw.split(":"):
            total = total * 60 + float(part)
        return total
    matches = _UNITS.findall(raw)
    if matches and _UNITS.sub("", raw).strip() == "":
        return sum(float(num) * _SECONDS[unit] for num, unit in matches)
    raise ValueError(f"Не понимаю время «{text}». Примеры: 1:30, 1:02:03, 90, 1м30с")


def parse_clip(start: str | None, end: str | None) -> tuple[float, float | None] | None:
    """(начало, конец|None) или None, если отрезок не задан."""
    a, b = parse_time(start), parse_time(end)
    if a is None and b is None:
        return None
    a = a or 0.0
    if b is not None and b <= a:
        raise ValueError("Конец отрезка должен быть позже начала")
    return (a, b)


def parse_range(text: str) -> tuple[float, float | None] | None:
    """«1:00-5:00», «1:00–5:00», «1:00-» (до конца)."""
    parts = re.split(r"\s*[-–—]\s*|\s+до\s+|\s+to\s+", text.strip(), maxsplit=1)
    if len(parts) != 2:
        raise ValueError("Отрезок пишется так: 1:00-5:00")
    return parse_clip(parts[0], parts[1])


def format_time(seconds: float | None) -> str:
    if seconds is None:
        return "конец"
    seconds = int(round(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def clip_label(clip: tuple[float, float | None] | None, sep: str = "–") -> str:
    if clip is None:
        return ""
    return f"{format_time(clip[0])}{sep}{format_time(clip[1])}"
