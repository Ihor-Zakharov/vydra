"""Нормализация командной строки до разбора: команды и ключи по-русски и «не в той раскладке».

    выдра скачать <ссылка> -ф мп3                 → download <ссылка> -f mp3
    vydra d <ссылка> -а ьз3                        → «-а ьз3» набрано в русской раскладке: -f mp3
    vydra вщцтдщфв <ссылка> --ащкьфе ищер          → download … --format both
    vydra <ссылка>                                 → download <ссылка>

Трогаем только имена команд, имена ключей и значения ключей-выборов (формат, качество, битрейт).
Ссылки, пути и таймкоды не меняются никогда.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ЙЦУКЕН ↔ QWERTY: какая буква получается на той же клавише в другой раскладке
_RU = "йцукенгшщзхъфывапролджэячсмитьбюё"
_EN = "qwertyuiop[]asdfghjkl;'zxcvbnm,.`"


def _table(src: str, dst: str) -> dict[int, str]:
    table = {ord(a): b for a, b in zip(src, dst, strict=True)}
    # заглавные — только для букв: у «,» «.» «[» заглавной формы нет, иначе «,» стала бы «Б»
    table |= {ord(a.upper()): b.upper() for a, b in zip(src, dst, strict=True) if a.isalpha() and a.upper() != a}
    return table


RU_TO_EN = _table(_RU, _EN)
EN_TO_RU = _table(_EN, _RU)


def to_en(text: str) -> str:
    return text.translate(RU_TO_EN)


def to_ru(text: str) -> str:
    return text.translate(EN_TO_RU)


# --- значения ключей-выборов -------------------------------------------------------------

FORMAT_VALUES = {
    "mp4": "mp4", "мп4": "mp4", "video": "mp4", "видео": "mp4",
    "mp3": "mp3", "мп3": "mp3", "audio": "mp3", "аудио": "mp3", "звук": "mp3", "music": "mp3", "музыка": "mp3",
    "both": "both", "оба": "both", "all": "both", "все": "both", "всё": "both",
}  # fmt: skip
QUALITY_VALUES = {
    "max": "max", "макс": "max", "максимум": "max", "best": "max", "лучшее": "max", "4k": "max", "4к": "max",
    "2160": "max", "1440": "max", "2k": "max", "2к": "max",
    "1080": "1080", "720": "720", "480": "480", "360": "360",
}  # fmt: skip
BITRATE_VALUES = {v: v for v in ("320", "256", "192", "128")}
CHOICES = {"format": FORMAT_VALUES, "quality": QUALITY_VALUES, "bitrate": BITRATE_VALUES}


def normalize_choice(kind: str, value: str) -> tuple[str, bool] | None:
    """(каноническое значение, была ли это опечатка раскладки) или None, если не распознано."""
    table = CHOICES[kind]
    raw = value.strip().lower().rstrip(".")
    if kind == "quality":
        raw = re.sub(r"(?<=\d)\s*[pр]$", "", raw)  # 1080p, 1080р (кириллическая р)
    if kind == "bitrate":
        raw = re.sub(r"\s*(kbps|kbit/s|kbit|k|кбит/с|кбит|кб/с|к)$", "", raw)
    if raw in table:
        return table[raw], False
    for convert in (to_en, to_ru):
        alt = convert(raw)
        if kind == "quality":
            alt = re.sub(r"(?<=\d)\s*[pр]$", "", alt)
        if alt in table:
            return table[alt], True
    return None


# --- команды и ключи ---------------------------------------------------------------------


@dataclass(frozen=True)
class Opt:
    names: tuple[str, ...]  # все зарегистрированные в Click имена (латиница и кириллица)
    takes_value: bool = False
    choice: str | None = None  # format | quality | bitrate


SHARED = {
    "format": Opt(("--format", "-f", "--формат", "-ф"), True, "format"),
    "quality": Opt(("--quality", "-q", "--качество", "-к"), True, "quality"),
    "bitrate": Opt(("--bitrate", "-b", "--битрейт", "-б"), True, "bitrate"),
    "clip": Opt(("--clip", "-c", "--отрезок", "-о"), True),
    "from": Opt(("--from", "-s", "--с", "--от"), True),
    "to": Opt(("--to", "-e", "--по", "--до"), True),
    "out": Opt(("--out", "-o", "--выход", "--куда"), True),
    "folder": Opt(("--folder", "-F", "--папка"), True),
    "yes": Opt(("--yes", "-y", "--да", "-д"), False),
    "json": Opt(("--json",)),
}
COMMAND_OPTS: dict[str, dict[str, Opt]] = {
    "download": {
        **{k: SHARED[k] for k in ("format", "quality", "bitrate", "clip", "from", "to", "out", "folder", "yes", "json")},
        "force": Opt(("--force", "--заново")),
        "yes_playlist": Opt(("--yes-playlist", "--весь-плейлист")),
        "show": Opt(("--show", "--показать")),
    },
    "convert": {k: SHARED[k] for k in ("format", "bitrate", "clip", "from", "to", "out", "folder", "yes", "json")},
    "info": {"json": SHARED["json"]},
    "ui": {"port": Opt(("--port", "-p", "--порт"), True), "no_browser": Opt(("--no-browser", "--без-браузера"))},
    "stop": {"port": Opt(("--port", "-p", "--порт"), True)},
    "restart": {"port": Opt(("--port", "-p", "--порт"), True), "quiet": Opt(("--quiet",))},
    "list": {
        "type": Opt(("--type", "-t", "--тип"), True),
        "search": Opt(("--search", "-s", "--поиск"), True),
        "limit": Opt(("--limit", "-n", "--сколько"), True),
        "paths": Opt(("--paths", "--пути")),
        "json": Opt(("--json",)),
    },
    "open": {"play": Opt(("--play", "--запустить"))},
    "folder": {
        "reset": Opt(("--reset", "--сброс")),
        "pick": Opt(("--pick", "--выбрать")),
        "open": Opt(("--open", "--открыть")),
        "move": Opt(("--move", "--перенести")),
    },
    "doctor": {
        "fix": Opt(("--fix", "--починить")),
        "offline": Opt(("--offline", "--без-сети")),
        "report": Opt(("--report", "--отчёт", "--отчет")),
    },
    "update": {
        "repo": Opt(("--repo", "--из"), True),
        "only_ytdlp": Opt(("--only-ytdlp", "--только-ytdlp")),
        "force": Opt(("--force", "--заново")),
        "check": Opt(("--check", "--проверить")),
        "record": Opt(("--record",), True),
        "no_banner": Opt(("--no-banner",)),
    },
    "cookies": {"remove": Opt(("--remove", "--удалить"))},
    "settings": {"art": Opt(("--art", "--заставка"), True)},
    "shortcut": {},
    "completion": {
        "shell": Opt(("--shell", "--оболочка"), True),
        "show": Opt(("--show", "--показать")),
        "uninstall": Opt(("--uninstall", "--удалить")),
    },
    "bridge": {"uninstall": Opt(("--uninstall", "--удалить"))},
}
GLOBAL_OPTS = {
    "version": Opt(("--version", "-V", "--версия")),
    "help": Opt(("--help", "-h", "--справка", "--помощь")),
}
COMMAND_ALIASES = {
    "download": ("download", "скачать", "d", "dl", "качать"),
    "info": ("info", "инфо", "plan", "план"),
    "convert": ("convert", "конвертировать", "конверт"),
    "ui": ("ui", "интерфейс", "web", "веб"),
    "stop": ("stop", "стоп", "остановить"),
    "restart": ("restart", "перезапустить"),
    "list": ("list", "список", "ls"),
    "open": ("open", "показать", "открыть", "show"),
    "folder": ("folder", "папка"),
    "doctor": ("doctor", "доктор", "health"),
    "update": ("update", "обновить"),
    "cookies": ("cookies", "куки"),
    "settings": ("settings", "настройки"),
    "shortcut": ("shortcut", "ярлык"),
    "completion": ("completion", "автодополнение"),
    "bridge": ("bridge", "мост"),
}
COMMANDS = {alias: canon for canon, aliases in COMMAND_ALIASES.items() for alias in aliases}
# Короткие синонимы, которые легко спутать при наборе в другой раскладке, — только явно
LAYOUT_ONLY = {"в": "d", "вд": "dl"}

_URL = re.compile(r"^(https?://|www\.|(m\.|vm\.|vt\.)?(youtube\.com|youtu\.be|tiktok\.com|instagram\.com)/)", re.I)
_TIME_RANGE = re.compile(r"^\s*[\dчмсhms:.,\s]*\s*[-–—]\s*[\dчмсhms:.,\s]*$", re.I)


@dataclass
class Normalized:
    args: list[str]
    corrections: list[tuple[str, str]] = field(default_factory=list)  # (было, стало) — для подсказки

    @property
    def hint(self) -> str | None:
        if not self.corrections:
            return None
        before = " ".join(b for b, _ in self.corrections)
        after = " ".join(a for _, a in self.corrections)
        return f"понял «{before}» как «{after}»"


def _resolve_command(token: str) -> tuple[str, bool] | None:
    low = token.lower()
    if low in COMMANDS:
        return token if token in COMMANDS else low, False
    if low in LAYOUT_ONLY:
        return LAYOUT_ONLY[low], True
    for convert in (to_en, to_ru):
        alt = convert(low)
        if alt in COMMANDS:
            return alt, True
    return None


def _option_table(command: str | None) -> dict[str, Opt]:
    table = dict(GLOBAL_OPTS)
    if command:
        table |= COMMAND_OPTS.get(COMMANDS.get(command, command), {})
    return table


def _resolve_option(name: str, table: dict[str, Opt]) -> tuple[str, Opt, bool] | None:
    """(имя, как его понимает Click; ключ; была ли опечатка раскладки)."""
    for opt in table.values():
        if name in opt.names:
            return name, opt, False
    dashes = "--" if name.startswith("--") else "-"
    body = name[len(dashes):]
    for convert in (to_en, to_ru):
        alt = dashes + convert(body)
        for opt in table.values():
            if alt in opt.names:
                return alt, opt, True
            if alt.lower() in opt.names and len(body) > 1:  # --ФОРМАТ, --Format
                return alt.lower(), opt, True
    lowered = name.lower()
    if len(body) > 1:
        for opt in table.values():
            if lowered in opt.names:
                return lowered, opt, False
    return None


def normalize(argv: list[str]) -> Normalized:
    result = Normalized([])
    out = result.args
    command: str | None = None
    i = 0
    passthrough = False
    while i < len(argv):
        token = argv[i]
        i += 1
        if passthrough:
            out.append(token)
            continue
        if token == "--":
            passthrough = True
            out.append(token)
            continue
        if token.startswith("-") and len(token) > 1 and not re.match(r"^-\d", token):
            name, sep, inline = token.partition("=")
            resolved = _resolve_option(name, _option_table(command))
            if resolved is None:
                out.append(token)
                continue
            canon_name, opt, typo = resolved
            value = None
            if opt.takes_value:
                if sep:
                    value = inline
                elif i < len(argv):
                    value = argv[i]
                    i += 1
            # -o / -о выглядят одинаково: решаем по значению — отрезок это или папка
            if name in ("-o", "-о") and value is not None and COMMANDS.get(command or "") in ("download", "convert"):
                wanted = "-о" if _TIME_RANGE.match(value) and re.search(r"\d", value) else "-o"
                if wanted != canon_name:
                    canon_name, opt = wanted, SHARED["clip" if wanted == "-о" else "out"]
                    typo = True
            fixed_value = value
            value_typo = False
            if value is not None and opt.choice:
                normalized = normalize_choice(opt.choice, value)
                if normalized is not None:
                    fixed_value, value_typo = normalized
            if typo or value_typo:
                before = name if value is None else f"{name} {value}"
                after = canon_name if value is None else f"{canon_name} {fixed_value}"
                result.corrections.append((before, after))
            if value is None:
                out.append(canon_name)
            elif sep:
                out.append(f"{canon_name}={fixed_value}")
            else:
                out += [canon_name, fixed_value]
            continue
        if command is None:
            resolved_cmd = _resolve_command(token)
            if resolved_cmd is not None:
                command, typo = resolved_cmd
                if typo:
                    result.corrections.append((token, command))
                out.append(command)
                continue
            if _URL.match(token):  # «выдра <ссылка>» — это скачать
                command = "download"
                out += ["download", token]
                continue
        out.append(token)
    return result
