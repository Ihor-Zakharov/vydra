"""Язык консоли: английский по умолчанию, русский — `vydra lang ru` (или VYDRA_LANG=ru).

Ключ каждой строки — её русский текст (так код остаётся читаемым, а веб-интерфейс, у которого свои тексты
на русском, не меняется). Английские переводы — только здесь: словарь EN для строк консоли и сообщений других
модулей (ошибки загрузки, стадии задач, доктор…), PATTERNS — для сообщений с подставленными значениями.

    tr("Порт {port} занят", port=8765)   — строка консоли (шаблон str.format)
    tr_msg(job.error)                     — готовый текст из другого модуля: точное совпадение, шаблон, по частям
"""

from __future__ import annotations

import json
import os
from pathlib import Path

LANGS = ("en", "ru")
DEFAULT = "en"
_ALIASES = {
    "en": "en", "eng": "en", "english": "en", "анг": "en", "англ": "en", "английский": "en", "англійська": "en",
    "ru": "ru", "rus": "ru", "russian": "ru", "ру": "ru", "рус": "ru", "русский": "ru",
}  # fmt: skip


def normalize(value: str | None) -> str | None:
    """«ру», «Русский», «EN» → ru / en; непонятное — None."""
    return _ALIASES.get((value or "").strip().lower().rstrip("."))


def _config_dir() -> Path:
    from platformdirs import user_config_dir

    return Path(os.environ.get("VD_CONFIG_DIR") or user_config_dir("vydra", appauthor=False))


def saved() -> str | None:
    """Язык, выбранный командой `vydra lang` (prefs.json — общий с остальными настройками)."""
    try:
        return normalize(json.loads((_config_dir() / "prefs.json").read_text(encoding="utf-8")).get("lang"))
    except (OSError, ValueError, AttributeError):
        return None


def resolve() -> str:
    """VYDRA_LANG перекрывает сохранённый выбор; по умолчанию — английский."""
    return normalize(os.environ.get("VYDRA_LANG")) or saved() or DEFAULT


LANG = resolve()


def set_lang(lang: str) -> None:
    global LANG
    LANG = lang


def ru() -> bool:
    return LANG == "ru"


def tr(text: str, /, **values) -> str:
    """Строка консоли на текущем языке. text — русский шаблон (ключ), values — подстановки str.format."""
    template = text if LANG == "ru" else EN.get(text, text)
    return template.format(**values) if values else template


def tr_msg(text: str | None) -> str | None:
    """Готовый русский текст из другого модуля (ошибка, стадия, заметка) — по-английски, если язык en."""
    if not text or LANG == "ru":
        return text
    return _translate(text)


def _translate(text: str) -> str:
    if text in EN:
        return EN[text]
    stripped = text.rstrip(".")
    if stripped in EN:
        return EN[stripped]
    for pattern, repl in PATTERNS:
        m = pattern.fullmatch(text)
        if m:
            return repl(m) if callable(repl) else m.expand(repl)
    if (single := _part(text)) is not None:  # «Без постера: 1» — шаблон написан со строчной
        return single
    for sep in (" — ", "; "):  # составное сообщение «причина — что делаем», «a; b»: переводим по частям
        if sep in text:
            parts = text.split(sep)
            done = [_part(p) for p in parts]
            if any(d is not None for d in done):
                return sep.join(d if d is not None else p for d, p in zip(done, parts, strict=True))
    return text


def _recase(value: str, like: str) -> str:
    """Регистр первой буквы перевода — как у исходника."""
    if like[:1].isupper():
        return value[:1].upper() + value[1:]
    if like[:1].islower():
        return value[:1].lower() + value[1:]
    return value


def _part(text: str) -> str | None:
    for candidate in dict.fromkeys((text, text[:1].upper() + text[1:], text[:1].lower() + text[1:])):
        key = candidate if candidate in EN else candidate.rstrip(".")
        if key in EN:
            return EN[key] if candidate == text else _recase(EN[key], text)
        for pattern, repl in PATTERNS:
            m = pattern.fullmatch(candidate)
            if m:
                value = repl(m) if callable(repl) else m.expand(repl)
                return value if candidate == text else _recase(value, text)
    return None


def plural(n: int, one: str, few: str, many: str) -> str:
    """«1 файл, 2 файла, 5 файлов» по-русски; по-английски — перевод формы «one» и «many» (ключи — русские формы)."""
    if LANG == "ru":
        n10, n100 = n % 10, n % 100
        word = one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many
        return f"{n} {word}"
    return f"{n} {EN.get(one, one) if n == 1 else EN.get(many, many)}"


def units() -> tuple[str, ...]:
    return ("Б", "КБ", "МБ", "ГБ", "ТБ") if LANG == "ru" else ("B", "KB", "MB", "GB", "TB")


def number(value: float, digits: int = 1) -> str:
    """1,5 по-русски, 1.5 по-английски."""
    text = f"{value:.{digits}f}"
    return text.replace(".", ",") if LANG == "ru" else text


from .i18n_en import EN, PATTERNS  # noqa: E402  (таблица — отдельным файлом: она большая)
