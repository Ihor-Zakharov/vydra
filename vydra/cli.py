"""Консольная выдра: `выдра --help` / `vydra --help`. Без аргументов — интерактивный режим.

Командную строку перед разбором нормализует vydra.argv: русские команды и ключи
(`выдра скачать <ссылка> -ф мп3`), набор не в той раскладке (`-а ьз3` → `-f mp3`)
и ссылка без команды (`выдра <ссылка>` → скачать). Без ссылки ссылки берутся из буфера обмена.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from enum import Enum
from pathlib import Path, PureWindowsPath
from typing import Annotated

import typer
from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.padding import Padding
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.prompt import Prompt
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from . import __version__, argv, clipboard, diagnostics, i18n, system, tools
from .config import Prefs, Settings
from .i18n import tr, tr_msg
from .jobs import Job, JobManager, detect_platform
from .library import PLATFORM_DIRS, TYPE_DIRS, Library
from .media import Media
from .timecode import clip_label, format_time, parse_clip, parse_range


def _tty(stream) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


# Без терминала (пайп, файл) строки не переносим: путь или ссылка должны остаться одной строкой
console = Console(highlight=False, soft_wrap=not _tty(sys.stdout))
err = Console(stderr=True, highlight=False, soft_wrap=not _tty(sys.stderr))
_json_mode = False  # --json: на stdout — только итог в JSON, человеку — ничего (ошибки — ещё и в stderr)

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_PARTIAL, EXIT_INTERRUPTED = 0, 1, 2, 3, 130

PLATFORM = {
    "youtube": ("YouTube", "#ff4d4d"),
    "tiktok": ("TikTok", "#25f4ee"),
    "instagram": ("Instagram", "#e1306c"),
    "other": (tr("Сайт"), "#8b7bff"),
    "file": (tr("Файл"), "#9aa4b2"),
}
MODE_LABEL = {"mp4": tr("MP4 · видео"), "mp3": tr("MP3 · аудио"), "both": "MP4 + MP3"}
RU = i18n.ru()  # язык выбирается до разбора команды: от него зависят справка и имена ключей в ней


def _cyrillic(text: str) -> bool:
    return any("а" <= ch.lower() <= "я" or ch in "ёЁ" for ch in text)


def opt(*names: str, **kw):
    """typer.Option: русские имена ключей работают всегда (их переводит vydra.argv), а в справке — только
    по-русски; help — через перевод."""
    for key in ("help", "metavar"):
        if key in kw:
            kw[key] = tr(kw[key])
    return typer.Option(*names, **kw)


def arg(**kw):
    if "help" in kw:
        kw["help"] = tr(kw["help"])
    if "metavar" in kw:
        kw["metavar"] = tr(kw["metavar"])
    return typer.Argument(**kw)


GRADIENT = ["#7c5cff", "#6f7cff", "#5f9bff", "#43b8ff", "#00d4ff"]


# русские значения (-ф мп3, -к макс) переводит vydra.argv; в справке они видны только по-русски
Fmt = Enum("Fmt", {v: v for v in ("mp4", "mp3", "both") + (("мп4", "мп3", "оба") if RU else ())}, type=str)
Quality = Enum("Quality", {("q" + v if v.isdigit() else v): v for v in ("max", "1080", "720", "480", "360")
                           + (("макс",) if RU else ())}, type=str)  # fmt: skip


class Bitrate(str, Enum):
    b320 = "320"
    b256 = "256"
    b192 = "192"
    b128 = "128"


def fmt_value(value: Fmt | str) -> str:
    return argv.FORMAT_VALUES[getattr(value, "value", value)]


def quality_value(value: Quality | str) -> str:
    return argv.QUALITY_VALUES[getattr(value, "value", value)]


_EXAMPLES_RU = [
    ("выдра", "", "", "интерактивный режим"),
    ("выдра скачать", "'<ссылка>'", "", "видео в MP4"),
    ("выдра скачать", "'<ссылка>'", "-ф мп3 -б 320", "только звук"),
    ("выдра скачать", "'<ссылка>'", "-о 1:00-5:00", "отрезок с 1-й по 5-ю минуту"),
    ("vydra d", "'<ссылка>' '<ссылка>'", "-f both -q 720", "несколько сразу, латиницей"),
    ("выдра инфо", "'<ссылка>'", "", "что будет скачано (план)"),
    ("выдра показать", "", "", "последний файл — в Проводнике / Finder"),
    ("выдра папка", "", "", "где лежат файлы, сменить папку"),
    ("выдра интерфейс", "", "", "веб-интерфейс в браузере"),
    ("выдра stop", "", "", "остановить веб-интерфейс"),
    ("выдра обновить", "", "", "выдра и yt-dlp — до последней версии"),
    ("выдра доктор", "", "--починить", "проверить и починить всё"),
    ("выдра настройки", "", "--заставка выкл", "без заставки при входе (или VYDRA_NO_ART=1)"),
]
_EXAMPLES_EN = [
    ("vydra", "", "", "interactive mode"),
    ("vydra download", "'<link>'", "", "video as MP4"),
    ("vydra download", "'<link>'", "-f mp3 -b 320", "audio only"),
    ("vydra download", "'<link>'", "--clip 1:00-5:00", "a clip from minute 1 to 5"),
    ("vydra d", "'<link>' '<link>'", "-f both -q 720", "several at once"),
    ("vydra info", "'<link>'", "", "what will be downloaded (a plan)"),
    ("vydra open", "", "", "the latest file in Explorer / Finder"),
    ("vydra folder", "", "", "where files are, change the folder"),
    ("vydra ui", "", "", "web interface in the browser"),
    ("vydra stop", "", "", "stop the web interface"),
    ("vydra update", "", "", "vydra and yt-dlp to the latest version"),
    ("vydra doctor", "", "--fix", "check and fix everything"),
    ("vydra settings", "", "--art off", "no splash on start (or VYDRA_NO_ART=1)"),
]
_EXAMPLES = _EXAMPLES_RU if RU else _EXAMPLES_EN


def _examples() -> str:
    plain = [" ".join(x for x in (c, u, o) if x) for c, u, o, _ in _EXAMPLES]
    width = max(len(p) for p in plain) + 3
    lines = []
    for (cmd, url, opts, desc), text in zip(_EXAMPLES, plain, strict=True):
        markup = f"[cyan]{cmd}[/]" + (f" [green]{url}[/]" if url else "") + (f" [yellow]{opts}[/]" if opts else "")
        lines.append(f"  {markup}{' ' * (width - len(text))}[dim]{desc}[/]")
    return "\n".join(lines)


# Смена языка — первой строкой справки, над «Как вызвать» (печатает _help_and_errors)
LANG_LINE = ("[bold #7c5cff on #1c1830] Язык: [/][bold cyan on #1c1830]выдра язык ru | en [/]  [dim]English: vydra lang en[/]"
             if RU else
             "[bold #7c5cff on #1c1830] Language: [/][bold cyan on #1c1830]vydra lang ru | en [/]  [dim]По-русски: выдра язык ру[/]")  # fmt: skip

HELP_RU = f"""[bold]Скачивает видео с YouTube, TikTok, Instagram и сотен сайтов — без водяных знаков, сразу в MP4 или MP3.[/]

[dim]Проще всего:[/] скопируйте ссылку в браузере и запустите [cyan]выдра скачать[/] [yellow]-ф мп3[/]
[dim](ссылка возьмётся из буфера обмена).[/]

[dim]Примеры (vydra и выдра — одна и та же программа):[/]
{_examples()}

[yellow]Ссылку берите в кавычки[/] [dim]— в bash, zsh и PowerShell символ & из адреса YouTube ломает команду.
Или скопируйте ссылку и не указывайте её вовсе. После[/] [cyan]выдра автодополнение[/] [dim]кавычки ставятся сами,
а Tab подсказывает команды и ключи. Раскладку переключать не нужно: -а ьз3 = -f mp3.[/]

[dim]Для скриптов:[/] [cyan]--json[/] [dim]— итог одной строкой JSON. Коды выхода: 0 — готово, 1 — не скачалось,
2 — неверная команда, 3 — скачано не всё, 130 — прервано (Ctrl+C).[/]"""

HELP_EN = f"""[bold]Downloads videos from YouTube, TikTok, Instagram and hundreds of other sites — without watermarks, straight to MP4 or MP3.[/]

[dim]Easiest:[/] copy a link in the browser and run [cyan]vydra download[/] [yellow]-f mp3[/]
[dim](the link is taken from the clipboard).[/]

[dim]Examples (vydra and выдра are the same program):[/]
{_examples()}

[yellow]Put links in quotes[/] [dim]— in bash, zsh and PowerShell the & in a YouTube address breaks the command.
Or copy the link and leave it out. After[/] [cyan]vydra completion[/] [dim]quotes are added automatically,
and Tab completes commands and options.[/]

[dim]For scripts:[/] [cyan]--json[/] [dim]— the result as one line of JSON. Exit codes: 0 — done, 1 — nothing downloaded,
2 — wrong command, 3 — not everything downloaded, 130 — interrupted (Ctrl+C).[/]"""
HELP = HELP_RU if RU else HELP_EN

app = typer.Typer(
    name="vydra",
    help=HELP,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help", "--справка", "--помощь"]},  # русские — прячем в en
    invoke_without_command=True,
    no_args_is_help=False,
    add_completion=False,  # своя команда completion — для vydra и выдра, с умными ссылками
    pretty_exceptions_show_locals=False,
)


# --- оформление --------------------------------------------------------------------------


def wordmark() -> Text:
    text = Text()
    for ch, color in zip("выдра" if RU else "vydra", GRADIENT, strict=True):
        text.append(ch, style=f"bold {color}")
    return text


def banner(subtitle: str = "") -> None:
    line = Text("▲ ", style="bold #7c5cff") + wordmark()
    line.append(f"  {__version__}", style="dim")
    if subtitle:
        line.append(f"  ·  {subtitle}", style="dim")
    console.print(line)


def badge(platform: str) -> Text:
    name, color = PLATFORM.get(platform, PLATFORM["other"])
    return Text(f" {name} ", style=f"bold black on {color}")


def size(num: float | None) -> str:
    if not num:
        return "—"
    names = i18n.units()
    for n, unit in enumerate(names):
        if num < 1024 or n == len(names) - 1:
            return f"{num:.0f} {unit}" if n < 2 else f"{i18n.number(num)} {unit}"
        num /= 1024
    return str(num)


plural = i18n.plural  # «1 файл, 2 файла, 5 файлов» / «1 file, 5 files»


def seconds(value: float) -> str:
    return f"{i18n.number(value)} {tr('с')}"


def ago(ts: float) -> str:
    delta = time.time() - ts
    for limit, div, unit in ((60, 1, "с"), (3600, 60, "мин"), (86400, 3600, "ч"), (86400 * 30, 86400, "дн")):
        if delta < limit:
            return tr("{n} {unit} назад", n=int(delta // div), unit=tr(unit))
    return time.strftime("%d.%m.%Y" if RU else "%Y-%m-%d", time.localtime(ts))


def fail(message: str, hint: str | None = None, code: int = 1) -> typer.Exit:
    """Ошибка человеку (в stderr) и, с --json, — в итоге. Русский текст других модулей переводится сам."""
    message, hint = tr_msg(message), tr_msg(hint)
    err.print(Text("✗ ", style="bold red") + Text(message, style="red"))
    if hint:
        err.print(Text("  → ", style="dim") + Text(hint, style="dim"))
    if _json_mode:
        emit_json({"ok": False, "exit_code": code, "error": message, "hint": hint})
    return typer.Exit(code)


def emit_json(data) -> None:
    """Машинный итог (--json): одна строка JSON на stdout, мимо rich — без переносов и цветов."""
    sys.stdout.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def json_mode(on: bool) -> None:
    """--json: человеческий вывод на stdout молчит, остаётся только итог в JSON."""
    global _json_mode
    _json_mode = on
    console.quiet = on


def file_record(root: Path, f: dict, **extra) -> dict:
    path = root / f["path"]
    return {
        "path": str(path),
        "display_path": system.display_path(path),
        "type": f["type"],
        "size": f.get("size"),
        "folder": f.get("folder") or Path(f["path"]).parent.as_posix(),
        **extra,
    }


def attr_table(rows: list[tuple[str, str | Text, str]]) -> Table:
    """Атрибуты в стиле плана Terraform: «  + ключ = значение»."""
    table = Table.grid(padding=(0, 1))
    table.add_column(width=3)
    table.add_column(style="#9aa4b2", no_wrap=True)
    table.add_column(style="dim", width=1)
    table.add_column(overflow="fold")  # пути и ссылки — целиком
    for sign, key, value in rows:
        style = {"+": "bold green", "~": "bold yellow", "-": "bold red"}.get(sign, "dim")
        table.add_row(Text(f"  {sign}", style=style), key, "=", value if isinstance(value, Text) else Text(value))
    return table


def quoted(value: str, style: str = "#e6d9a8") -> Text:
    return Text(f'"{value}"', style=style)


def file_uri(path: Path) -> str:
    """file:// для ссылки в терминале; в WSL — из пути Windows, чтобы Ctrl+клик в Windows Terminal открыл файл."""
    if system.OS == "wsl" and (win := system.to_windows(path)) and len(win) > 2 and win[1] == ":":
        return PureWindowsPath(win).as_uri()
    try:
        return path.resolve().as_uri()
    except ValueError:
        return ""


def linked(path: Path, style: str = "", label: str | None = None) -> Text:
    """Полный путь (как его показывает ОС), кликабельный в современных терминалах."""
    uri = file_uri(path)
    text = label or system.display_path(path)
    return Text(text, style=f"{style} link {uri}".strip() if uri else style)


def rel_folder(folder: str) -> str:
    return folder.replace("/", "\\") if system.WINDOWS_LIKE else folder


# --- окружение ---------------------------------------------------------------------------


@dataclasses.dataclass
class Env:
    settings: Settings
    library: Library
    manager: JobManager


def make_env(out: Path | None = None) -> Env:
    settings = Settings.from_env()
    diagnostics.setup_logging(settings)  # подробности и ошибки — в журнал, а не поверх прогресс-баров
    if out is not None:
        target = _out_dir(out)
        settings = dataclasses.replace(settings, fixed_library=target)
    library = Library(Prefs(settings), Media(settings), enrich=False)
    try:
        library.ensure_layout()
    except OSError as exc:
        where = system.display_path(library.root)
        raise fail(tr("Папка хранилища недоступна: {where} ({why})", where=where, why=exc.strerror or tr("нет доступа")),
                   tr("Диск отключён или папку удалили? Выберите другую: выдра папка \"D:\\Видео\""))  # fmt: skip
    return Env(settings, library, JobManager(settings, library))


def _out_dir(out: Path) -> Path:
    """-o/--куда: папка для этой загрузки. «D:\\Видео» в WSL — диск Windows; нет такой папки — создаём."""
    raw = str(out)
    try:
        path = system.parse_user_path(raw) if (raw[1:2] == ":" or raw.startswith("\\\\")) else out.expanduser()
    except ValueError as exc:
        raise fail(f"-o {raw}: {tr_msg(str(exc))}") from exc
    path = path.resolve()
    if path.exists() and not path.is_dir():
        raise fail(tr("-o {raw}: это файл, а нужна папка", raw=raw))
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise fail(tr("Не удалось создать папку {path}: {why}", path=system.display_path(path), why=exc.strerror or exc),
                   tr("Проверьте, что диск подключён и в эту папку можно писать")) from exc  # fmt: skip
    return path


def resolve_clip(clip: str | None, start: str | None, end: str | None):
    try:
        return parse_range(clip) if clip else parse_clip(start, end)
    except ValueError as exc:
        raise fail(str(exc), tr("Примеры: -о 1:00-5:00, --с 90 --по 2:30")) from exc


def links_or_clipboard(urls: list[str] | None) -> list[str]:
    """Ссылки из аргументов, а если их нет — из буфера обмена."""
    if urls:
        return [_normalize(u) for u in urls]
    try:
        with console.status(Text(tr("Смотрю буфер обмена…"), style="dim"), spinner="dots"):
            text = clipboard.read_strict()
    except clipboard.Unavailable as exc:
        raise fail(tr("Ссылка не указана, а буфер обмена прочитать не удалось: {why}", why=tr_msg(str(exc))),
                   tr("Или укажите ссылку в кавычках: выдра скачать 'https://…' -ф мп3")) from exc  # fmt: skip
    found = clipboard.extract_links(text)
    if not found:
        # сам текст не показываем: вдруг пароль
        what = tr("там текст, но не ссылка") if text.strip() else tr("он пуст")
        raise fail(
            tr("Ссылка не указана, а в буфере обмена ссылки нет ({what})", what=what),
            tr("Скопируйте ссылку в браузере и повторите — или укажите ссылку в кавычках: выдра скачать 'https://…' -ф мп3"),
        )
    for link in found:
        console.print(Text(tr("  Ссылка из буфера: "), style="dim") + Text(link, style="cyan"))
    return [_normalize(u) for u in found]


# --- прогресс ----------------------------------------------------------------------------


def ask(job: Job) -> str | None:
    """Вопрос задачи в терминале. Enter — вариант по умолчанию, «д»/«н» — да/нет."""
    question = job.question or {}
    options = question.get("options") or []
    if not options:
        return None
    default = question.get("default") or next((o["id"] for o in options if o.get("primary")), options[0]["id"])
    console.print()
    console.print(Text("  ? ", style="bold #7c5cff")
                  + Text(tr_msg(question.get("title")) or tr("Нужно решение"), style="bold"))  # fmt: skip
    console.print(Text(f"    {(job.title or job.source)[:80]}", style="dim"))
    if question.get("message"):
        console.print(Text(f"    {tr_msg(question['message'])}"))
    for n, option in enumerate(options, 1):
        label = tr_msg(option["label"])
        line = Text(f"    {n}) ", style="bold") + Text(label, style="bold" if option["id"] == default else "")
        if option["id"] == default:
            line.append("  ← Enter", style="dim")
        if option.get("hint"):
            line.append(f"\n       {tr_msg(option['hint'])}", style="dim")
        console.print(line)
    others = [o["id"] for o in options if o["id"] != default]
    while True:
        try:
            raw = Prompt.ask(Text(tr("    Выбор"), style="bold"), default="", show_default=False).strip().lower()
        except EOFError:
            return default
        if raw in ("", "д", "да", "y", "yes", "l", "lf"):
            return default
        if raw in ("н", "нет", "n", "no", "ytn") and len(others) == 1:
            return others[0]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]["id"]
        if raw in {o["id"] for o in options}:
            return raw
        console.print(Text(tr("    Введите номер от 1 до {n} или просто Enter", n=len(options)), style="yellow"))


def run_jobs(env: Env, jobs: list[Job]) -> int:
    """Живой прогресс всех задач, итог в стиле `terraform apply`. Возвращает код выхода."""
    started = time.monotonic()
    reported: set[str] = set()
    asked: set[tuple[str, str]] = set()
    stages: dict[str, str] = {}  # последний показанный этап (вывод без терминала)
    interrupted = False

    def row(job: Job) -> Table:
        title = job.title or job.source
        grid = Table.grid(padding=(0, 1), expand=True)
        grid.add_column(width=2)
        grid.add_column(ratio=1)
        _, color = PLATFORM.get(job.platform, PLATFORM["other"])
        spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[int(time.monotonic() * 10) % 10]
        head = Text(spinner + " ", style=color) if job.active else Text("• ", style="dim")
        name = badge(job.platform) + Text(" ") + Text(title, style="bold", overflow="ellipsis", no_wrap=True)
        if job.count > 1:
            name.append(f"  {job.index}/{job.count}", style="dim")
        if job.clip:
            name.append(f"  ✂ {clip_label(job.clip)}", style="#e6d9a8")
        grid.add_row(head, name)

        bar = ProgressBar(
            total=100,
            completed=job.progress or 0,
            pulse=job.progress is None and job.active,
            width=34,
            style="#2a2f3a",
            complete_style=color,
            finished_style="green",
            pulse_style=color,
        )
        stats = Text(f" {job.progress:>3.0f}%" if job.progress is not None else "  ···", style="bold")
        stage = tr_msg(job.stage)
        if job.retry_at and job.status == "queued":
            left = max(0, int(job.retry_at - time.time()))
            stage = tr("повтор через {left} с · попытка {n} из {total}", left=left, n=job.attempt, total=job.max_attempts)
        stats.append(f"  {stage}", style="#9aa4b2" if not job.retry_at else "yellow")
        if job.speed:
            stats.append(f" · {size(job.speed)}/{tr('с')}", style="dim")
        if job.eta:
            stats.append(f" · {format_time(job.eta)}", style="dim")
        line = Table.grid(padding=0)
        line.add_row(bar, stats)
        grid.add_row("", line)
        return grid

    def report(job: Job) -> None:
        took = seconds((job.finished or time.time()) - job.created)
        if job.status == "done":
            root = env.library.root
            for f in job.files:
                kind = tr("видео") if f["type"] == "mp4" else tr("аудио")
                path = root / f["path"]
                folder = f.get("folder") or str(Path(f["path"]).parent.as_posix())
                console.print(
                    Text("  + ", style="bold green") + Text(kind, style="bold")
                    + Text(tr("  {size} · за {took} · папка ", size=size(f["size"]), took=took), style="dim")
                    + linked(path.parent, "cyan", rel_folder(folder))
                )  # fmt: skip
                console.print(Text("    ") + linked(path, "#e6d9a8"), soft_wrap=True)  # путь — одной строкой
            if job.warning:
                console.print(Text("  ! ", style="bold yellow") + Text(tr_msg(job.warning), style="yellow"))
        elif job.status == "error":
            console.print(
                Text("  ✗ ", style="bold red") + Text((job.title or job.source)[:60], style="bold")
                + Text(f" — {tr_msg(job.error)}", style="red")
            )  # fmt: skip
            if hint := error_hint(job.error):
                console.print(Text("    → ", style="dim") + Text(hint, style="dim"))
        elif job.status == "cancelled":
            console.print(Text("  ○ ", style="dim") + Text(tr("{title} — отменено", title=job.title or job.source), style="dim"))
        for note in getattr(job, "notes", None) or []:
            console.print(Text("    · ", style="dim") + Text(tr_msg(note), style="#9aa4b2"))

    def view() -> Group:
        active = [j for j in jobs if j.id not in reported]
        rows: list = [Padding(row(j), (0, 0, 1, 0)) for j in active]
        if interrupted:
            rows.append(Text(tr("  Останавливаю и убираю временные файлы… Ctrl+C ещё раз — выйти сразу"), style="yellow"))
        return Group(*rows)

    def on_sigint(*_):
        nonlocal interrupted
        if interrupted:  # второй Ctrl+C: не ждём (например, долгого копирования на /mnt/c)
            from .naming import abandon_in_flight

            for job in jobs:
                job.cancel.set()
            abandon_in_flight()  # недописанные файлы в хранилище не оставляем
            sys.stdout.write("\n")
            err.print(Text(tr("Прервано, не дожидаясь остановки загрузок."), style="bold yellow"))
            os._exit(EXIT_INTERRUPTED)
        interrupted = True
        for job in jobs:
            env.manager.cancel(job.id)

    previous = signal.signal(signal.SIGINT, on_sigint)
    try:
        with Live(view(), console=console, refresh_per_second=12, transient=True) as live:
            while True:
                for job in jobs:
                    question = getattr(job, "question", None)
                    if job.status == "waiting" and question and (job.id, question.get("id")) not in asked:
                        asked.add((job.id, question.get("id")))
                        live.stop()
                        signal.signal(signal.SIGINT, signal.default_int_handler)
                        try:
                            choice = ask(job)
                        except KeyboardInterrupt:
                            choice = None
                            on_sigint()
                        finally:
                            signal.signal(signal.SIGINT, on_sigint)
                        if choice is not None:
                            try:
                                env.manager.answer(job.id, choice)
                            except (KeyError, ValueError) as exc:
                                console.print(Text(f"    ! {tr_msg(str(exc))}", style="yellow"))
                        live.start()
                    if not job.active and job.id not in reported:
                        reported.add(job.id)
                        live.stop()
                        report(job)
                        live.start()
                    elif not console.is_terminal and job.active and stages.get(job.id) != job.stage:
                        # без терминала прогресс-бара не видно — пишем в журнал смену этапов (без процентов)
                        stages[job.id] = job.stage
                        console.print(Text(f"  · {(job.title or job.source)[:70]}: {tr_msg(job.stage)}", style="dim"))
                live.update(view())
                if all(not j.active for j in jobs):
                    break
                time.sleep(0.08)
    finally:
        signal.signal(signal.SIGINT, previous)
        env.manager.shutdown()

    done = [j for j in jobs if j.status == "done"]
    failed = [j for j in jobs if j.status == "error"]
    files = [f for j in done for f in j.files]
    elapsed = time.monotonic() - started
    console.print()
    if interrupted:
        console.print(Text(tr("Отменено."), style="bold yellow"), Text(tr("Готовых файлов: {n}", n=len(files)), style="dim"))
        return EXIT_INTERRUPTED
    if not files:
        reason = tr_msg(failed[0].error) if failed else tr("ничего не получилось")
        console.print(Text(tr("Не удалось скачать: "), style="bold red") + Text(reason or "", style="red"))
        log_path = diagnostics.log_dir(env.settings) / diagnostics.LOG_NAME
        console.print(Text(tr("Подробности в журнале: "), style="dim") + linked(log_path, "dim"), soft_wrap=True)
        return EXIT_FAILED
    ok = not failed
    summary = Text(tr("Готово! ") if ok else tr("Готово с ошибками. "), style="bold green" if ok else "bold yellow")
    summary.append(plural(len(files), "файл создан", "файла создано", "файлов создано"), style="bold")
    if failed:
        summary.append(f", {plural(len(failed), 'ошибка', 'ошибки', 'ошибок')}", style="red")
    summary.append(tr(" · {size} за {took}", size=size(sum(f["size"] for f in files)), took=seconds(elapsed)), style="dim")
    console.print(summary)
    console.print(Text(tr("Хранилище: "), style="dim") + linked(env.library.root, "cyan"), soft_wrap=True)
    if not _json_mode and len(files) and _tty(sys.stdout):
        console.print(Text(tr("Показать в папке: "), style="dim") + Text(tr("выдра показать"), style="cyan"))
    return EXIT_OK if ok else EXIT_PARTIAL


_HINTS = [
    (("cookies",), "Выгрузите cookies из браузера (расширение «Get cookies.txt LOCALLY») и подключите: "
                   "выдра cookies ~/Downloads/cookies.txt"),
    (("обновите yt-dlp", "выдра обновить"), "Обновите загрузчик: выдра обновить"),
    (("нет связи",), "Проверьте интернет и повторите; выдра сама повторяет временные сбои"),
    (("ffmpeg",), "Почините: выдра доктор --починить"),
    (("не хватает места",), "Освободите место или смените папку хранилища: выдра папка"),
    (("плейлист",), "Чтобы скачать плейлист целиком, добавьте --весь-плейлист"),
    (("приватное", "недоступно"), "Проверьте, открывается ли видео в браузере без входа в аккаунт"),
]  # fmt: skip


def error_hint(message: str | None) -> str | None:
    """Что сделать пользователю при этой ошибке (или None, если сказать нечего). message — русский текст."""
    low = (message or "").lower()
    if any(s in low for s in ("vydra ", "выдра ", "запустите")):
        return None  # в тексте ошибки уже сказано, что делать
    return next((tr(hint) for needles, hint in _HINTS if any(n in low for n in needles)), None)


# --- команды: основное -------------------------------------------------------------------

UrlsArg = Annotated[
    list[str] | None,
    arg(metavar="[ССЫЛКИ]…", help="Ссылки на видео (можно несколько). Нет ссылки — возьму из буфера обмена", show_default=False),
]
FmtOpt = Annotated[
    Fmt,
    opt("--format", "-f", "--формат", "-ф", case_sensitive=False,
                 help="Что сохранить: mp4/мп4 — видео, mp3/мп3 — только звук, both/оба — оба файла"),
]  # fmt: skip
QualityOpt = Annotated[
    Quality,
    opt("--quality", "-q", "--качество", "-к", case_sensitive=False,
                 help="Качество видео (по меньшей стороне): max/макс, 1080, 720, 480, 360"),
]  # fmt: skip
BitrateOpt = Annotated[Bitrate, opt("--bitrate", "-b", "--битрейт", "-б", help="Битрейт MP3, кбит/с")]
ClipOpt = Annotated[
    str | None,
    opt("--clip", "-c", "--отрезок", "-о", show_default=False,
                 help="Отрезок: [yellow]1:00-5:00[/], [yellow]90-150[/], [yellow]1:00-[/] (до конца)"),
]  # fmt: skip
FromOpt = Annotated[str | None, opt("--from", "-s", "--с", "--от", help="Начало отрезка (1:00, 90, 1м30с)", show_default=False)]  # noqa: E501
ToOpt = Annotated[str | None, opt("--to", "-e", "--по", "--до", help="Конец отрезка", show_default=False)]
OutOpt = Annotated[
    Path | None,
    opt("--out", "-o", "--выход", "--куда", help="Другая папка-хранилище для этой загрузки",
                 show_default=False, file_okay=False),
]  # fmt: skip
FolderOpt = Annotated[
    str | None,
    opt("--folder", "-F", "--папка", show_default=False,
                 help="Папка внутри хранилища, например [yellow]\"TikTok/Танцы\"[/]"),
]  # fmt: skip
YesOpt = Annotated[
    bool,
    opt("--yes", "-y", "--да", "-д", help="Не задавать вопросов: соглашаться с вариантом по умолчанию"),
]
ForceOpt = Annotated[bool, opt("--force", "--заново", help="Скачать ещё раз, даже если уже есть в хранилище")]
PlaylistOpt = Annotated[bool, opt("--yes-playlist", "--весь-плейлист", help="Разрешить плейлист больше 50 роликов")]  # noqa: E501


JsonOpt = Annotated[
    bool,
    opt("--json", help="Итог — одной строкой JSON на stdout (для скриптов); без прогресса и вопросов, как -y"),
]


def _auto_accept(yes: bool) -> bool:
    if yes or _json_mode:
        return True
    if not _tty(sys.stdin):
        console.print(Text(tr("  Терминала для вопросов нет — соглашаюсь с вариантами по умолчанию (как -y)"), style="dim"))
        return True
    return False


def download(
    urls: UrlsArg = None,
    fmt: FmtOpt = Fmt.mp4,
    quality: QualityOpt = Quality.q1080,
    bitrate: BitrateOpt = Bitrate.b192,
    clip: ClipOpt = None,
    start: FromOpt = None,
    end: ToOpt = None,
    out: OutOpt = None,
    folder: FolderOpt = None,
    yes: YesOpt = False,
    force: ForceOpt = False,
    yes_playlist: PlaylistOpt = False,
    show: Annotated[bool, opt("--show", "--показать", help="Когда скачается — показать файл в папке")] = False,
    as_json: JsonOpt = False,
) -> None:
    """Скачать видео или звук по ссылке. [dim](синонимы: скачать, d)[/]"""
    json_mode(as_json)
    mode, qual = fmt_value(fmt), quality_value(quality)
    cut = resolve_clip(clip, start, end)
    banner(f"{MODE_LABEL[mode]} · {qual if mode != 'mp3' else bitrate.value + ' ' + tr('кбит/с')}")
    links = _unique_links(links_or_clipboard(urls))
    env = make_env(out)
    auto = _auto_accept(yes)
    folder_rel = _folder(env, folder, auto)
    console.print()
    jobs, skipped = submit_links(env, links, mode=mode, quality=qual, bitrate=int(bitrate.value), clip=cut,
                                 folder=folder_rel, confirm_playlist=yes_playlist, auto=auto, force=force)  # fmt: skip
    if not jobs:
        env.manager.shutdown()
        console.print(Text("\n" + tr("Нечего делать: всё уже скачано."), style="bold green"))
        if _json_mode:
            emit_json({"ok": True, "exit_code": EXIT_OK, "files": skipped, "jobs": []})
        if show and skipped:
            _show(Path(skipped[0]["path"]), soft=True)
        raise typer.Exit(EXIT_OK)
    code = run_jobs(env, jobs)
    files = [f for j in jobs if j.status == "done" for f in j.files]
    if _json_mode:
        emit_json(_jobs_json(env, jobs, skipped, code))
    if show and files:
        _show(env.library.root / files[0]["path"], soft=True)
    raise typer.Exit(code)


def submit_links(env: Env, links: list[str], *, mode: str, quality: str, bitrate: int, clip, folder: str | None = None,
                 confirm_playlist: bool = False, auto: bool = False, force: bool = False) -> tuple[list[Job], list[dict]]:
    """Задачи на загрузку; то, что уже лежит в хранилище (та же ссылка, формат и отрезок), не качаем повторно."""
    jobs, skipped = [], []
    for url in links:
        existing = None if force else env.manager.find_existing(url, mode, clip)
        if existing:
            for f in existing:
                kind = tr("видео") if f["type"] == "mp4" else tr("аудио")
                console.print(
                    Text("  = ", style="bold blue") + Text(kind, style="bold")
                    + Text(tr("  уже в хранилище — повторно не качаю (--заново — скачать ещё раз)"), style="dim")
                )  # fmt: skip
                console.print(Text("    ") + linked(env.library.root / f["path"], "#e6d9a8"), soft_wrap=True)
                skipped.append(file_record(env.library.root, f, url=url, existing=True))
            continue
        job = Job(kind="url", source=url, mode=mode, quality=quality, bitrate=bitrate, clip=clip, folder=folder,
                  confirm_playlist=confirm_playlist, auto_accept=auto)  # fmt: skip
        jobs.append(env.manager.submit(job))
    return jobs, skipped


def _unique_links(links: list[str]) -> list[str]:
    """Одна и та же ссылка дважды (или youtu.be/x и youtube.com/watch?v=x) — качаем один раз."""
    from .jobs import canonical_url

    seen: set[str] = set()
    result = []
    for link in links:
        key = canonical_url(link) or link
        if key in seen:
            console.print(Text(tr("  Повтор ссылки пропущен: {link}", link=link), style="dim"))
            continue
        seen.add(key)
        result.append(link)
    return result


def _jobs_json(env: Env, jobs: list[Job], skipped: list[dict], code: int) -> dict:
    root = env.library.root
    records = []
    for job in jobs:
        records.append({
            "url": job.source, "status": job.status, "title": job.title, "platform": job.platform,
            # тексты — на языке консоли, как и в человеческом выводе; ключи, статусы и коды не переводятся
            "error": tr_msg(job.error), "warning": tr_msg(job.warning), "notes": [tr_msg(n) for n in job.notes],
            "files": [file_record(root, f, title=job.title, url=job.source) for f in job.files],
        })  # fmt: skip
    return {
        "ok": code == EXIT_OK,
        "exit_code": code,
        "files": skipped + [f for r in records for f in r["files"]],
        "jobs": records,
    }


def _folder(env: Env, folder: str | None, auto: bool = False) -> str | None:
    """--папка: своя папка внутри хранилища. Нет такой — создаём (с вопросом, если есть терминал)."""
    if not folder:
        return None
    from .library import FsError, validate_name, validate_rel

    try:
        rel = validate_rel(folder.replace("\\", "/"))
        for part in rel.split("/") if rel else []:
            validate_name(part)
    except FsError as exc:
        env.manager.shutdown()
        raise fail(tr("Папка «{folder}»: {why}", folder=folder, why=tr_msg(str(exc)))) from exc
    if rel and not env.library.folder_exists(rel):
        create = auto or not _tty(sys.stdin)
        if not create:
            yes_no = ["д", "н"] if RU else ["y", "n"]
            answer = Prompt.ask(Text(tr("  Папки «{rel}» в хранилище нет. Создать?", rel=rel), style="bold"),
                                choices=yes_no, default=yes_no[0])  # fmt: skip
            create = answer == yes_no[0]
        if not create:
            env.manager.shutdown()
            raise fail(tr("Папки «{rel}» нет в хранилище", rel=rel), tr("Создайте её или укажите другую: выдра папка — что есть"))
        try:
            (env.library.root / rel).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            env.manager.shutdown()
            raise fail(tr("Не удалось создать папку «{rel}»: {why}", rel=rel, why=exc.strerror or exc)) from exc
        console.print(Text("  + ", style="bold green") + Text(tr("папка «{rel}» создана в хранилище", rel=rel)))
    return rel or None


def info(
    url: Annotated[str | None, arg(metavar="[ССЫЛКА]", help="Ссылка на видео (нет — из буфера обмена)", show_default=False)] = None,
    as_json: JsonOpt = False,
) -> None:
    """Показать, что будет скачано — как [bold]terraform plan[/]. [dim](синонимы: инфо, plan)[/]"""
    from .downloader import PLAYLIST_LIMIT, DownloadFailed, preview

    json_mode(as_json)
    settings = Settings.from_env()
    diagnostics.setup_logging(settings)
    banner(tr("план"))
    url = links_or_clipboard([url] if url else None)[0]
    with console.status(Text(tr("Смотрю, что там по ссылке…"), style="dim"), spinner="dots"):
        try:
            data = preview(url, js_runtime=settings.js_runtime, cookies=settings.cookies_file)
        except DownloadFailed as exc:
            raise fail(str(exc), error_hint(str(exc))) from exc
    platform = detect_platform(data.get("url") or url)
    if _json_mode:
        emit_json({"ok": True, "exit_code": EXIT_OK, **data, "platform": platform})
        return
    name, color = PLATFORM[platform]
    console.print()
    console.print(Text(tr("выдра составила план загрузки:"), style="bold"))
    console.print()
    head = Text("  + ", style="bold green") + Text("video ", style="bold") + quoted(data.get("title") or "?", "bold")
    console.print(head)
    heights = data.get("heights") or []
    rows = [
        (" ", tr("платформа"), Text(name, style=f"bold {color}")),
        (" ", tr("автор"), quoted(data.get("uploader") or "—")),
        (" ", tr("длительность"), quoted(format_time(data.get("duration")) if data.get("duration") else "—")),
        (" ", tr("качества"), Text(", ".join(f"{h}p" for h in heights) or "—", style="cyan")),
        (" ", tr("ссылка"), quoted(data.get("url") or url, "dim")),
    ]
    if data.get("playlist"):
        rows.insert(1, (" ", tr("роликов"), Text(str(data.get("count") or "?"), style="cyan")))
    if data.get("is_live"):
        console.print(attr_table(rows))
        console.print()
        raise fail(tr("Это прямой эфир — скачать его можно, когда трансляция закончится"),
                   tr("Запись эфира появится на канале; тогда повторите"))  # fmt: skip
    root = Prefs(settings).library_dir
    platform_dir = PLATFORM_DIRS.get(platform, PLATFORM_DIRS["other"])
    rows += [
        ("+", tr("видео"), linked(root / platform_dir / TYPE_DIRS["video"], "green")),
        ("+", tr("аудио"), linked(root / platform_dir / TYPE_DIRS["audio"], "green")),
    ]
    console.print(attr_table(rows))
    console.print()
    count = (data.get("count") or 0) if data.get("playlist") else 1
    console.print(
        Text(tr("План: "), style="bold") + Text(tr("{n} к скачиванию", n=count or "?"), style="green")
        + Text(tr(", 0 к изменению, 0 к удалению."))
    )  # fmt: skip
    if data.get("playlist") and count > PLAYLIST_LIMIT:
        console.print(Text(tr("Плейлист больше {n} роликов — добавьте --весь-плейлист", n=PLAYLIST_LIMIT), style="yellow"))
    console.print(Text(tr("Скачать: "), style="dim")
                  + Text(tr("выдра скачать '{url}'", url=data.get("url") or url), style="cyan"), soft_wrap=True)  # fmt: skip


def convert(
    files: Annotated[list[Path], arg(metavar="ФАЙЛЫ…", help="Видео или аудиофайлы", exists=True, dir_okay=False, show_default=False)],
    fmt: FmtOpt = Fmt.mp3,
    bitrate: BitrateOpt = Bitrate.b192,
    clip: ClipOpt = None,
    start: FromOpt = None,
    end: ToOpt = None,
    out: OutOpt = None,
    folder: FolderOpt = None,
    yes: YesOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Сконвертировать свои файлы в MP4/MP3 (можно вырезать отрезок). [dim](синоним: конвертировать)[/]"""
    json_mode(as_json)
    mode = fmt_value(fmt)
    cut = resolve_clip(clip, start, end)
    env = make_env(out)
    banner(f"{tr('конвертер')} · {MODE_LABEL[mode]}")
    auto = _auto_accept(yes)
    folder_rel = _folder(env, folder, auto)
    console.print()
    jobs = []
    for file in files:
        copy = env.manager.new_upload_dir() / file.name
        shutil.copyfile(file, copy)
        job = Job(kind="file", source=file.name, mode=mode, bitrate=int(bitrate.value), clip=cut, input_path=copy,
                  folder=folder_rel, auto_accept=auto)  # fmt: skip
        jobs.append(env.manager.submit(job))
    code = run_jobs(env, jobs)
    if _json_mode:
        emit_json(_jobs_json(env, jobs, [], code))
    raise typer.Exit(code)


def ui(
    port: Annotated[int, opt("--port", "-p", "--порт", min=1, max=65535, help="Порт веб-интерфейса")] = 8765,
    no_browser: Annotated[bool, opt("--no-browser", "--без-браузера", help="Не открывать браузер")] = False,
) -> None:
    """Запустить веб-интерфейс и открыть его в браузере. [dim](синонимы: интерфейс, web)[/]"""
    from . import servers
    from .fsutil import FileLock

    restarted = os.environ.pop("_VYDRA_RESTARTED", None) is not None  # перезапуск после обновления
    no_browser = no_browser or restarted
    base = Settings.from_env()
    lock = FileLock(servers.lock_path(base.work_dir, port), timeout=0)
    lock.__enter__()
    other = lock._fh is not None and not lock.acquired  # noqa: SLF001 — блокировку держит другой `vydra ui`
    state = _port_state(port)
    if other or (state == "busy" and _alive(port)):
        lock.__exit__(None, None, None)
        _already_running(port, no_browser, starting=other and state != "busy")
        return
    if state != "free":
        lock.__exit__(None, None, None)
        if state == "denied":
            raise fail(tr("Порт {port} нельзя занять без прав администратора", port=port),
                       tr("Порты до 1024 — системные. Возьмите обычный: выдра интерфейс --порт 8765"))  # fmt: skip
        free = next((p for p in range(port + 1, min(port + 21, 65536))
                     if _port_state(p) == "free" and not servers.lock_held(servers.lock_path(base.work_dir, p))), None)  # fmt: skip
        if free is None:
            raise fail(tr("Порт {port} и соседние заняты другими программами", port=port),
                       tr("Укажите свой: выдра интерфейс --порт 9000"))  # fmt: skip
        console.print(Text(tr("  Порт {port} занят другой программой — запускаю на {free}", port=port, free=free), style="yellow"))
        port = free
        lock = FileLock(servers.lock_path(base.work_dir, port), timeout=0)
        lock.__enter__()
    try:
        _serve(port, no_browser, restarted)
    finally:
        servers.clear_pid(base.work_dir, port)
        lock.__exit__(None, None, None)


def _already_running(port: int, no_browser: bool, starting: bool) -> None:
    url = f"http://localhost:{port}"
    if starting:  # второй запуск сразу после первого (двойной клик по ярлыку) — ждём, пока поднимется
        console.print(Text(tr("  Выдра уже запускается на порту {port} — жду…", port=port), style="dim"))
        if not _wait_alive(port, 8):
            raise fail(tr("Выдра на порту {port} запущена, но не отвечает", port=port),
                       tr("Перезапустите её: выдра stop --port {port}, затем выдра интерфейс", port=port))  # fmt: skip
    banner(tr("уже запущена"))
    console.print(Text(f"  {url}", style=f"bold cyan link {url}"))
    console.print(Text(tr("  Остановить: "), style="dim") + Text(tr("выдра stop"), style="cyan"))
    if not no_browser:
        _open_browser(url)


def _wait_alive(port: int, timeout: float) -> bool:
    from .servers import wait_alive

    return wait_alive(port, timeout)


def _open_browser(url: str) -> None:
    try:
        system.open_url(url)
    except system.NotSupported as exc:
        err.print(Text(tr("  Браузер не открылся ({why}) — откройте {url} сами", why=tr_msg(str(exc)), url=url), style="yellow"))


def _serve(port: int, no_browser: bool, restarted: bool) -> None:
    """uvicorn в этом окне. Ctrl+C / `vydra stop` — остановка, SIGUSR1 — перезапуск новой версией (exec)."""
    import uvicorn

    from . import servers

    url = f"http://localhost:{port}"
    os.environ["VD_PORT"] = str(port)
    settings = Settings.from_env()
    root = Prefs(settings).library_dir
    body = Table.grid(padding=(0, 2))
    body.add_column(style="#9aa4b2")
    body.add_column(overflow="fold")  # путь к хранилищу — целиком, а не с «…»
    body.add_row(tr("Интерфейс"), Text(url, style=f"bold cyan underline link {url}"))
    body.add_row(tr("Хранилище"), linked(root, "cyan"))
    body.add_row("", "")
    body.add_row("", Text(tr("Не закрывайте это окно — выдра работает, пока оно открыто.\n"
                             "Остановить: Ctrl+C здесь или «выдра stop» в другом терминале."), style="dim"))  # fmt: skip
    title = Text("▲ ") + wordmark() + Text(tr(" обновлена и перезапущена") if restarted else tr(" работает"), style="bold")
    console.print()
    console.print(Panel(body, title=title, title_align="left", border_style="#7c5cff", box=box.ROUNDED, padding=(1, 2)))
    if not no_browser:

        def opener() -> None:
            if _wait_alive(port, 15):
                _open_browser(url)

        threading.Thread(target=opener, daemon=True).start()

    server = uvicorn.Server(
        uvicorn.Config(
            "vydra.main:create_app",
            factory=True,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
            timeout_graceful_shutdown=3,  # открытые вкладки (SSE) не должны задерживать остановку
        )
    )
    why: list[str] = []
    from .main import SHUTDOWN

    hangup = getattr(signal, "SIGHUP", None)

    def on_signal(signum, _frame) -> None:
        why.append({signal.SIGINT: "ctrl-c", signal.SIGTERM: "stop", hangup: "hangup"}.get(signum, "restart"))
        SHUTDOWN.set()
        server.should_exit = True

    uvicorn_exit = server.handle_exit

    def handle_exit(signum, frame) -> None:  # Ctrl+C и SIGTERM, пока работает uvicorn
        SHUTDOWN.set()
        uvicorn_exit(signum, frame)

    server.handle_exit = handle_exit  # type: ignore[method-assign]

    # uvicorn на время работы ставит свои обработчики, а после остановки вызывает наши — так
    # мы узнаём, почему он остановился; SIGUSR1 он не трогает, и тот приходит сразу сюда
    # SIGHUP — закрыли окно терминала: останавливаемся так же мягко (очередь сохранится), а не падаем
    handled = [signal.SIGINT, signal.SIGTERM, *(s for s in (servers.RESTART_SIGNAL, hangup) if s)]
    previous = {sig: signal.signal(sig, on_signal) for sig in handled}
    # .pid с «умею перезапускаться» — только пока обработчик SIGUSR1 стоит: иначе сигнал по умолчанию убил бы процесс
    servers.write_pid(settings.work_dir, port)
    # Windows: сигналов нет — `vydra stop` просит остановиться файлом ui-<порт>.stop
    stopped = threading.Event()

    def stop_requested() -> None:
        on_signal(signal.SIGTERM, None)

    threading.Thread(target=servers.watch_stop_request, args=(settings.work_dir, port, stop_requested, stopped),
                     name="stop-watch", daemon=True).start()  # fmt: skip
    try:
        server.run()
    finally:
        stopped.set()
        servers.clear_pid(settings.work_dir, port)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    if "restart" in why:
        console.print(Text("\n" + tr("  Перезапускаюсь новой версией…"), style="dim"))
        sys.stdout.flush()
        sys.stderr.flush()
        servers.clear_pid(settings.work_dir, port)
        os.environ["_VYDRA_RESTARTED"] = "1"
        # -I: из текущей папки пользователя не подхватится чужой пакет vydra (например, клон репозитория)
        os.execv(sys.executable, [sys.executable, "-I", "-m", "vydra", "ui", "--port", str(port), "--no-browser"])  # noqa: S606
    reason = tr("  Выдра остановлена командой stop.") if "stop" in why else tr("  Выдра остановлена.")
    try:
        console.print(Text("\n" + reason, style="dim"))
    except OSError:
        pass  # окно терминала уже закрыто


def stop(
    port: Annotated[int | None, opt("--port", "-p", "--порт", help="Только этот порт", show_default=False)] = None,
) -> None:
    """Остановить запущенный веб-интерфейс. [dim](синоним: стоп)[/]"""
    from . import servers

    settings = Settings.from_env()
    found = servers.running(settings.work_dir, port)
    if not found:
        text = tr("Выдра не запущена на порту {port} — останавливать нечего.", port=port) if port else \
            tr("Выдра не запущена — останавливать нечего.")
        console.print(Text(text, style="dim"))
        return
    failed = []
    for server in found:
        with console.status(Text(tr("Останавливаю выдру на порту {port}…", port=server.port), style="dim"), spinner="dots"):
            ok = servers.stop(settings.work_dir, server)
        if ok:
            console.print(Text("  ✓ ", style="bold green") + Text(tr("Остановлена выдра на порту {port}", port=server.port)))
        else:
            failed.append(server)
    if failed:
        pids = " ".join(str(s.pid) for s in failed if s.pid) or "<pid>"
        how = tr("Завершите процесс вручную: taskkill /F /T /PID {pids}", pids=pids) if system.OS == "windows" else \
            tr("Завершите процесс вручную: kill -9 {pids}", pids=pids)
        raise fail(tr("Не удалось остановить выдру на порту {ports}", ports=", ".join(str(s.port) for s in failed)), how)


def restart_cmd(
    port: Annotated[int | None, opt("--port", "-p", "--порт", help="Только этот порт", show_default=False)] = None,
    quiet: Annotated[bool, opt("--quiet", hidden=True, help="Молчать, если выдра не запущена")] = False,
) -> None:
    """Перезапустить работающий веб-интерфейс новой версией. [dim](синоним: перезапустить)[/]"""
    from . import servers

    settings = Settings.from_env()
    found = servers.running(settings.work_dir, port)
    if not found:
        if not quiet:
            console.print(Text(tr("Выдра не запущена — перезапускать нечего."), style="dim"))
        return
    failed = []
    for server in found:
        with console.status(Text(tr("Перезапускаю выдру на порту {port}…", port=server.port), style="dim"), spinner="dots"):
            ok = servers.restart(settings.work_dir, server)
        if ok:
            log = system.display_path(servers.log_path(settings.work_dir, server.port))
            text = tr("Выдра на порту {port} перезапущена в том же окне", port=server.port) if server.restartable else \
                tr("Выдра на порту {port} перезапущена в фоне (журнал: {log})", port=server.port, log=log)
            console.print(Text("  ✓ ", style="bold green") + Text(text))
        else:
            failed.append(server)
    if failed:
        raise fail(tr("Не удалось перезапустить выдру на порту {ports}", ports=", ".join(str(s.port) for s in failed)),
                   tr("Остановите и запустите заново: выдра stop, затем выдра интерфейс"))  # fmt: skip


# --- команды: хранилище ------------------------------------------------------------------


def list_items(
    kind: Annotated[str | None, opt("--type", "-t", "--тип", help="video/видео или audio/аудио", show_default=False)] = None,  # noqa: E501
    search: Annotated[str | None, opt("--search", "-s", "--поиск", help="Поиск по названию и имени файла (все слова)", show_default=False)] = None,  # noqa: E501
    limit: Annotated[int, opt("--limit", "-n", "--сколько", min=1, help="Сколько на странице")] = 25,
    page: Annotated[int, opt("--page", "-p", "--страница", min=1, help="Номер страницы")] = 1,
    sort: Annotated[str, opt("--sort", "--сортировка", metavar="added|name|size|duration",
                                      help="По дате (новые сверху), имени, размеру или длительности")] = "added",  # fmt: skip
    folder_rel: Annotated[str | None, opt("--folder", "-F", "--папка", show_default=False,
                                                   help="Только файлы этой папки хранилища, например \"TikTok/Видео\"")] = None,  # fmt: skip
    paths: Annotated[bool, opt("--paths", "--пути", help="Показать полные пути файлов")] = False,
    as_json: Annotated[bool, opt("--json", help="Список в JSON на stdout (для скриптов)")] = False,
) -> None:
    """Что лежит в хранилище — постранично, с поиском и сортировкой. [dim](синонимы: список, ls)[/]"""
    from . import servers
    from .library import FsError

    json_mode(as_json)
    settings = Settings.from_env()
    library = Library(Prefs(settings), Media(settings), enrich=False)
    wanted = None
    if kind:
        wanted = {"video": "video", "видео": "video", "audio": "audio", "аудио": "audio"}.get(kind.lower())
        if wanted is None:
            raise fail(tr("Непонятный тип «{kind}»", kind=kind), tr("Бывает: видео (video) или аудио (audio)"), code=EXIT_USAGE)
    sorts = {"added": "added", "дата": "added", "name": "name", "имя": "name", "size": "size", "размер": "size",
             "duration": "duration", "длина": "duration", "длительность": "duration"}  # fmt: skip
    if sort.lower() not in sorts:
        raise fail(tr("Непонятная сортировка «{sort}»", sort=sort), tr("Бывает: added, name, size, duration"), code=EXIT_USAGE)
    # работает сервер — его наблюдатель и так держит индекс свежим: полный обход диска не нужен
    fresh = bool(servers.running(settings.work_dir))
    try:
        result = library.query(q=search, kind=wanted, folder=folder_rel.replace("\\", "/") if folder_rel else None,
                               sort=sorts[sort.lower()], offset=(page - 1) * limit, limit=limit, refresh=not fresh)  # fmt: skip
    except FsError as exc:
        raise fail(f"--folder: {tr_msg(str(exc))}", code=EXIT_USAGE) from exc
    items, total, stats = result["items"], result["total"], library.stats()
    pages = max(1, -(-total // limit))
    if _json_mode:
        root = library.root
        emit_json({"ok": True, "exit_code": EXIT_OK, "root": str(root), "display_root": system.display_path(root),
                   "stats": stats, "total": total, "page": page, "pages": pages, "limit": limit,
                   "items": [{**i, "abs_path": str(root / i["path"])} for i in items]})  # fmt: skip
        return
    banner(tr("хранилище"))
    console.print(Text("  ") + linked(library.root, "cyan"), soft_wrap=True)
    if not items:
        if total:
            raise fail(tr("Страницы {page} нет — всего {pages}", page=page, pages=pages), code=EXIT_USAGE)
        if search or wanted or folder_rel:
            console.print(Text("\n" + tr("  Ничего не нашлось."), style="dim"))
            return
        console.print(Text("\n" + tr("  Пусто. Скачайте что-нибудь: "), style="dim") + Text(tr("выдра скачать -ф мп3"), style="cyan"))
        return
    if paths:
        for item in items:
            icon = Text("▶ ", style="#7c5cff") if item["type"] == "video" else Text("♪ ", style="#00d4ff")
            console.print(Text("  ") + icon + linked(library.root / item["path"]), soft_wrap=True)
    else:
        table = Table(box=box.SIMPLE_HEAD, header_style="bold #9aa4b2", pad_edge=False, expand=False)
        table.add_column("", width=2)
        table.add_column(tr("Название"), max_width=max(20, console.width - 76), overflow="ellipsis", no_wrap=True)
        table.add_column(tr("Папка"), no_wrap=True, max_width=22, overflow="ellipsis", style="dim")
        table.add_column(tr("Откуда"), no_wrap=True, max_width=16, overflow="ellipsis", style="dim")
        table.add_column(tr("Длина"), justify="right", style="cyan", no_wrap=True, min_width=5)
        table.add_column(tr("Размер"), justify="right", no_wrap=True, min_width=8)
        table.add_column(tr("Добавлено"), style="dim", no_wrap=True, min_width=10)
        for item in items:
            icon = Text("▶", style="#7c5cff") if item["type"] == "video" else Text("♪", style="#00d4ff")
            title = Text(item["title"] or item["name"], style=f"link {file_uri(library.root / item['path'])}")
            origin = Text(short_source(item.get("source")), style=f"link {item['source']}" if item.get("source") else "")
            table.add_row(
                icon, title, rel_folder(item.get("folder") or ""), origin,
                format_time(item["duration"]) if item["duration"] else "—", size(item["size"]), ago(item["added"]),
            )  # fmt: skip
        console.print(table)
    line = Text(tr("  {videos} видео · {audios} аудио · {size}", videos=stats["videos"], audios=stats["audios"],
                   size=size(stats["size"])), style="dim")  # fmt: skip
    if pages > 1 or total != stats["count"]:
        first = (page - 1) * limit + 1
        line.append(tr("   · {first}–{last} из {total}, страница {page} из {pages}", first=first,
                       last=first + len(items) - 1, total=total, page=page, pages=pages), style="dim")  # fmt: skip
        if page < pages:
            line.append(tr("   дальше: --страница {next}", next=page + 1), style="dim cyan")
    console.print(line)


def open_cmd(
    query: Annotated[str | None, arg(metavar="[НАЗВАНИЕ]", help="Часть названия; без него — последний скачанный файл", show_default=False)] = None,  # noqa: E501
    play: Annotated[bool, opt("--play", "--запустить", help="Открыть в плеере, а не показать в папке")] = False,
    source: Annotated[bool, opt("--source", "--оригинал", help="Открыть страницу оригинала в браузере")] = False,
) -> None:
    """Показать скачанный файл в Проводнике / Finder или открыть оригинал. [dim](синонимы: показать, открыть)[/]"""
    settings = Settings.from_env()
    library = Library(Prefs(settings), Media(settings), enrich=False)
    items = library.query(q=query)["items"] if query else library.items()
    if not items:
        if query:
            raise fail(tr("В хранилище нет файла, в названии которого есть «{query}»", query=query), tr("Что есть: выдра список"))
        raise fail(tr("Хранилище пусто — показывать нечего"), tr("Скачайте что-нибудь: выдра скачать -ф мп3"))
    if source:
        _open_source(library, items[0])
    else:
        _show(library.root / items[0]["path"], play)
    if query and len(items) > 1:
        console.print(Text(tr("    Подходит ещё {n} — взяла самый свежий; уточните название, если не тот", n=len(items) - 1), style="dim"))


def _open_source(library: Library, item: dict) -> None:
    url = item.get("source") or _source_from_file(library, item)
    if not url:
        raise fail(tr("У «{title}» нет ссылки на оригинал", title=item["title"] or item["name"]),
                   tr("Ссылка есть у скачанного выдрой; у своих файлов её нет"))  # fmt: skip
    try:
        system.open_url(url)
    except system.NotSupported as exc:
        raise fail(tr("Браузер не открылся: {why}", why=tr_msg(str(exc))), tr("Откройте сами: {url}", url=url)) from exc
    console.print(Text("  ✓ ", style="bold green") + Text(tr("Открываю оригинал "))
                  + Text(url, style=f"cyan link {url}"), soft_wrap=True)  # fmt: skip


def _source_from_file(library: Library, item: dict) -> str | None:
    """Ссылка из тегов самого файла (comment), если в индексе её ещё нет (файл нашло сканирование)."""
    from .media import MediaError

    try:
        tags = library.media.probe(library.root / item["path"]).tags or {}
    except MediaError:
        return None
    url = str(tags.get("comment") or tags.get("purl") or "").strip()
    return url if url.lower().startswith(("http://", "https://")) else None


def short_source(url: str | None) -> str:
    """youtube.com, tiktok.com… — для столбца «Откуда»."""
    if not url:
        return "—"
    from urllib.parse import urlsplit

    host = (urlsplit(url).hostname or "").lower()
    for prefix in ("www.", "m.", "music.", "vm.", "vt."):
        host = host.removeprefix(prefix)
    return {"youtu.be": "youtube.com"}.get(host, host) or "—"


def _show(path: Path, play: bool = False, soft: bool = False) -> None:
    """Показать файл в Проводнике / Finder (или открыть). soft — это довесок к загрузке: не вышло — только
    предупреждение, без смены кода выхода и без второго JSON-итога."""
    try:
        (system.open_path if play else system.reveal)(path)
    except (FileNotFoundError, system.NotSupported) as exc:
        missing = isinstance(exc, FileNotFoundError)
        message = tr("Файла уже нет: {path}", path=system.display_path(path)) if missing else tr_msg(str(exc))
        if soft:
            err.print(Text(tr("  ! Показать в папке не вышло: {why}", why=message), style="yellow"))
            return
        raise fail(message, tr("Его удалили или переместили: выдра список") if missing else None) from exc
    action = tr("Открываю ") if play else tr("Показываю в папке ")
    console.print(Text("  ✓ ", style="bold green") + Text(action) + linked(path, "#e6d9a8"))


def folder(
    path: Annotated[str | None, arg(metavar="[ПАПКА]", help="Новая папка-хранилище (например D:\\Видео)", show_default=False)] = None,
    move: Annotated[bool, opt("--move", "--перенести", help="Перенести уже скачанное в новую папку")] = False,
    reset: Annotated[bool, opt("--reset", "--сброс", help="Вернуть папку по умолчанию")] = False,
    pick: Annotated[bool, opt("--pick", "--выбрать", help="Выбрать в системном окне")] = False,
    open_: Annotated[bool, opt("--open", "--открыть", help="Открыть в файловом менеджере")] = False,
) -> None:
    """Где лежат файлы; сменить папку (с --перенести — вместе со скачанным). [dim](синоним: папка)[/]"""
    settings = Settings.from_env()
    diagnostics.setup_logging(settings)
    library = Library(Prefs(settings), Media(settings), enrich=False)
    banner(tr("хранилище"))
    new: Path | None = None
    try:
        if reset:
            new = settings.default_library
        elif pick:
            new = system.pick_folder(library.root)
            if new is None:
                raise fail(tr("Выбор отменён"), code=0)
        elif path:
            new = system.parse_user_path(path)
    except (ValueError, system.NotSupported) as exc:
        raise fail(str(exc)) from exc

    if new is not None:
        if settings.fixed_library:
            raise fail(tr("Папка задана переменной окружения VD_LIBRARY_DIR — здесь её не сменить"))
        old = library.root
        source = None
        if move and new.expanduser().resolve() == old.resolve():  # папку уже сменили — переносим из прежней
            source = library.prefs.previous_library_dir
            if source is None or not source.is_dir():
                raise fail(tr("Переносить нечего: прежней папки с файлами нет"))
            old = source
        try:
            if move and old.is_dir():
                _move_with_progress(library, new, source)
            else:
                library.set_root(new)
        except (ValueError, OSError) as exc:
            raise fail(str(exc)) from exc
        console.print(
            Text("  ~ ", style="bold yellow") + Text(tr("хранилище "), style="bold") + quoted(system.display_path(old), "dim")
            + Text(" → ", style="bold yellow") + linked(new, "#e6d9a8")
        )  # fmt: skip
        if not move and any(old.glob("*/*/*")):
            console.print(Text(tr("    Уже скачанное осталось в старой папке. Перенести: "), style="dim")
                          + Text(tr("выдра папка \"{path}\" --перенести", path=system.display_path(new)), style="cyan"))  # fmt: skip
        console.print()

    try:
        library.ensure_layout()
    except OSError as exc:
        raise fail(tr("Папка хранилища недоступна: {why}", why=tr_msg(str(exc)))) from exc
    library.scan(force=True)
    _print_layout(library)
    if settings.fixed_library:
        console.print(Text(tr("  Папка задана переменной VD_LIBRARY_DIR"), style="dim"))
    if open_:
        try:
            system.open_path(library.root)
        except (OSError, system.NotSupported) as exc:
            raise fail(str(exc)) from exc


def _print_layout(library: Library) -> None:
    root = library.root
    items = library.items()
    tree = Tree(linked(root, "bold cyan"), guide_style="#3a4150")
    for platform, platform_dir in PLATFORM_DIRS.items():
        mine = [i for i in items if (i.get("folder") or "").split("/")[0].casefold() == platform_dir.casefold()]
        branch = tree.add(badge(platform) + Text(f"  {platform_dir}", style="bold") + Text(
            f"  {plural(len(mine), 'файл', 'файла', 'файлов')}" if mine else "", style="dim"))  # fmt: skip
        for type_key, type_dir in TYPE_DIRS.items():
            count = sum(1 for i in mine if i["type"] == type_key)
            branch.add(linked(root / platform_dir / type_dir, "", type_dir) + Text(f"  {count}" if count else "", style="dim"))
    own = sorted({(i.get("folder") or "").split("/")[0] for i in items} - set(PLATFORM_DIRS.values()) - {""})
    for name in own:
        count = sum(1 for i in items if (i.get("folder") or "").split("/")[0] == name)
        tree.add(Text(f"{name}/", style="bold") + Text(f"  {count}", style="dim"))
    tree.add(Text(".vydra/", style="dim") + Text(tr("  служебное: индекс и постеры"), style="dim"))
    console.print(tree)
    console.print()
    console.print(Text(tr("  Куда что попадает: "), style="dim") + Text(tr("<Платформа>/Видео"), style="cyan")
                  + Text(tr(" и "), style="dim") + Text(tr("<Платформа>/Аудио"), style="cyan")
                  + Text(tr("; своя папка — ключ "), style="dim") + Text(tr("--папка \"TikTok/Танцы\""), style="cyan"))  # fmt: skip
    console.print(Text(tr("  Сменить: "), style="dim") + Text(tr("выдра папка \"D:\\Видео\" --перенести"), style="cyan"))


def _move_with_progress(library: Library, new: Path, source: Path | None = None) -> None:
    started = time.monotonic()
    with Live(Text(tr("  Переношу…"), style="dim"), console=console, transient=True, refresh_per_second=10) as live:

        def progress(done: int, total: int, name: str) -> None:
            bar = ProgressBar(total=total or 1, completed=done, width=30, complete_style="#7c5cff", style="#2a2f3a")
            line = Table.grid(padding=(0, 1))
            line.add_row("  ", bar, Text(tr("{done} из {total}", done=size(done), total=size(total)), style="dim"),
                         Text(name[-50:], style="dim", no_wrap=True))  # fmt: skip
            live.update(line)

        result = library.move_root(new, progress, source=source)
    console.print(
        Text("  ✓ ", style="bold green")
        + Text(tr("Перенесено {size} за {took}", size=size(result["bytes"]), took=seconds(time.monotonic() - started)))
        + (Text(tr(", переименовано из-за совпадений: {n}", n=result["renamed"]), style="dim") if result["renamed"] else Text(""))
    )


# --- команды: обслуживание ---------------------------------------------------------------

STATUS_ICON = {"ok": ("✓", "green"), "warn": ("!", "yellow"), "fail": ("✗", "red")}


def doctor(
    fix: Annotated[bool, opt("--fix", "--починить", help="Починить всё, что можно")] = False,
    offline: Annotated[bool, opt("--offline", "--без-сети", help="Не проверять сеть")] = False,
    report: Annotated[bool, opt("--report", "--отчёт", "--отчет", help="Собрать zip-отчёт для поддержки (без cookies)")] = False,  # noqa: E501
) -> None:
    """Проверить систему и починить неполадки. [dim](синонимы: доктор, health)[/]"""
    from .health import Doctor, summary

    settings = Settings.from_env()
    diagnostics.setup_logging(settings)
    library = Library(Prefs(settings), Media(settings), enrich=False)
    doc = Doctor(settings, library)
    banner(tr("доктор"))
    with console.status(Text(tr("Проверяю систему…"), style="dim"), spinner="dots"):
        checks = doc.run(network=not offline)
    if report:
        extra = {"stats": library.stats(), "audit": library.audit(), "root": system.display_path(library.root)}
        target = Path.cwd() / time.strftime("vydra-report-%Y%m%d-%H%M.zip")
        target.write_bytes(diagnostics.build_report(settings, [c.public() for c in checks], extra))
        console.print(Text("  + ", style="bold green") + Text(tr("отчёт ")) + linked(target, "#e6d9a8"))
        console.print(Text(tr("  Внутри версии, результаты проверки и журналы. Cookies и ссылки с токенами туда не попадают."),
                           style="dim"))  # fmt: skip
        return

    def show(checks) -> None:
        table = Table.grid(padding=(0, 2))
        table.add_column(width=3)
        table.add_column(style="bold", no_wrap=True)
        table.add_column(overflow="fold")  # длинный путь хранилища — целиком
        for c in checks:
            icon, color = STATUS_ICON[c.status]
            detail = Text(tr_msg(c.detail) or "", style="" if c.status == "ok" else color)
            if c.hint:
                detail.append(f"\n{tr_msg(c.hint)}", style="dim")
            if c.fix and c.status != "ok" and not fix:
                action = tr_msg(c.fix) or ""
                detail.append(tr("\n→ выдра доктор --починить  ({action})", action=action[:1].lower() + action[1:]),
                              style="dim cyan")  # fmt: skip
            table.add_row(Text(f" {icon}", style=f"bold {color}"), tr_msg(c.title), detail)
        console.print()
        console.print(table)

    show(checks)
    if fix:
        todo = [c for c in checks if c.status != "ok" and c.fix]
        if not todo:
            console.print(Text("\n" + tr("  Чинить нечего."), style="dim"))
        for c in todo:
            console.print()
            console.print(Text("  ~ ", style="bold yellow") + Text(f"{tr_msg(c.title)}: ", style="bold") + Text(f"{tr_msg(c.fix)}…"))
            with Live(Text("    …", style="dim"), console=console, transient=True, refresh_per_second=10) as live:

                def progress(done: int, total: int | None, live=live) -> None:
                    bar = ProgressBar(total=total or 1, completed=done if total else 0, pulse=not total, width=30,
                                      complete_style="#7c5cff", style="#2a2f3a")  # fmt: skip
                    line = Table.grid(padding=(0, 1))
                    shown = tr("{done} из {total}", done=size(done), total=size(total)) if total else size(done)
                    line.add_row("   ", bar, Text(shown, style="dim"))
                    live.update(line)

                result = doc.fix(c.id, progress)
            style = "green" if result.ok else "red"
            console.print(Text("    ✓ " if result.ok else "    ✗ ", style=f"bold {style}") + Text(tr_msg(result.message), style=style))
        with console.status(Text(tr("Проверяю ещё раз…"), style="dim"), spinner="dots"):
            checks = doc.run(network=not offline)
        show(checks)
    s = summary(checks)
    console.print()
    line = Text(tr("Итог: "), style="bold")
    line.append(tr("{n} в порядке", n=s["ok"]), style="green")
    line.append(" · ")
    line.append(plural(s["warn"], "предупреждение", "предупреждения", "предупреждений"), style="yellow" if s["warn"] else "dim")
    line.append(" · ")
    line.append(plural(s["fail"], "ошибка", "ошибки", "ошибок"), style="red" if s["fail"] else "dim")
    console.print(line)
    if s["fail"]:
        raise typer.Exit(1)


def update(
    repo: Annotated[str | None, opt("--repo", "--из", show_default=False, metavar="URL|ПАПКА",
                                             help="Откуда ставить: адрес архива или папка с копией репозитория")] = None,  # fmt: skip
    only_ytdlp: Annotated[bool, opt("--only-ytdlp", "--только-ytdlp", help="Обновить только yt-dlp")] = False,
    force: Annotated[bool, opt("--force", "--заново", help="Переустановить, даже если версия последняя")] = False,
    check: Annotated[bool, opt("--check", "--проверить", help="Только проверить, есть ли что обновлять")] = False,
    record: Annotated[str | None, opt("--record", hidden=True)] = None,
    no_banner: Annotated[bool, opt("--no-banner", hidden=True)] = False,
) -> None:
    """Обновить выдру до последней версии из репозитория — и yt-dlp. [dim](синоним: обновить)[/]"""
    from . import selfupdate

    settings = Settings.from_env()
    if record:  # установщик: запомнить, из какого коммита стоит выдра
        rev = selfupdate.inspect(selfupdate.normalize(record))
        rev.version = __version__
        selfupdate.write_record(settings.config_dir, rev)
        return
    if not no_banner:
        banner(tr("обновление"))
    if only_ytdlp:
        _update_ytdlp(check)
        return
    env = selfupdate.installed_env()
    if env is None:
        root = tools._project_root()  # noqa: SLF001
        if root:
            console.print(Text(tr("  Выдра запущена из исходников ({root}) — их обновляют через git pull; "
                                  "обновляю только yt-dlp", root=root), style="dim"))  # fmt: skip
        else:
            console.print(Text(tr("  Выдра поставлена не установщиком — саму выдру так не обновить (поставьте "
                                  "установщиком из README); обновляю только yt-dlp"), style="yellow"))  # fmt: skip
        _update_ytdlp(check)
        return
    try:
        source, why = selfupdate.choose_source(repo, settings.config_dir, env)
    except selfupdate.UpdateError as exc:
        raise fail(str(exc), exc.hint) from exc
    console.print(Text(tr("  Откуда: "), style="dim") + Text(tr_msg(selfupdate.describe(source)), style="cyan")
                  + Text(f"  ({tr_msg(why)})", style="dim"), soft_wrap=True)  # fmt: skip
    current = selfupdate.read_record(settings.config_dir) or selfupdate.Revision(source)
    current.version = __version__
    with console.status(Text(tr("Узнаю, что нового…"), style="dim"), spinner="dots"):
        latest = selfupdate.inspect(source)
    if current.same_as(latest) and not force:
        console.print(Text("  ✓ ", style="bold green") + Text(tr("выдра — последняя версия ({label})",
                                                                 label=tr_msg(current.label()))))  # fmt: skip
        _update_ytdlp(check)
        return
    old = tr_msg(current.label()) if current.commit else tr("{version} · коммит неизвестен", version=current.version)
    new = tr_msg(latest.label()) if latest.commit else tr("последняя из источника")
    console.print(Text("  ~ ", style="bold yellow") + Text(tr("выдра "), style="bold") + quoted(old, "dim")
                  + Text(" → ", style="bold yellow") + quoted(new))  # fmt: skip
    if not latest.commit:
        console.print(Text(tr("    Узнать, что изменилось, не удалось (нет сети, не git или не GitHub) — переустанавливаю"),
                           style="dim"))  # fmt: skip
    if check:
        console.print(Text(tr("  Обновить: "), style="dim") + Text(tr("выдра обновить"), style="cyan"))
        _update_ytdlp(check=True)
        return
    _self_update(settings, env, source, current, latest)


def _self_update(settings: Settings, env: Path, source: str, current, latest) -> None:
    from . import selfupdate, shell
    from .fsutil import FileLock

    had_tab = any(t.script.is_file() for t in shell.targets(settings.config_dir))
    lock = FileLock(settings.work_dir / "update.lock", timeout=0)
    lock.__enter__()
    if lock._fh is not None and not lock.acquired:  # noqa: SLF001
        raise fail(tr("Обновление уже идёт в другом окне"), tr("Дождитесь его конца"))
    try:
        with console.status(Text(tr("Ставлю новую версию (uv) — обычно до минуты…"), style="dim"), spinner="dots"):
            latest.version = selfupdate.install(source, env, settings.work_dir / "update-backup")
    except selfupdate.UpdateError as exc:
        raise fail(str(exc), exc.hint) from exc
    finally:
        lock.__exit__(None, None, None)
    selfupdate.write_record(settings.config_dir, latest)
    was = current.label() if current.commit else current.version
    console.print(Text("  ✓ ", style="bold green") + Text(tr("выдра обновлена: ")) + Text(tr_msg(str(was)), style="dim")
                  + Text(" → ") + Text(tr_msg(latest.label()), style="bold"))  # fmt: skip
    # дальше — уже новой версией: у этого процесса под ногами поменялись файлы пакета
    new = [str(env / "bin" / "python"), "-I", "-m", "vydra"]
    quiet = {"stdin": subprocess.DEVNULL, "check": False}
    try:
        subprocess.run([*new, "update", "--only-ytdlp", "--no-banner"], timeout=900, **quiet)
        if had_tab:
            ok = subprocess.run([*new, "completion"], stdout=subprocess.DEVNULL, timeout=120, **quiet).returncode == 0
            console.print(Text(tr("  ✓ Tab-подсказки обновлены"), style="green") if ok
                          else Text(tr("  ! Tab-подсказки не обновились — выдра автодополнение"), style="yellow"))  # fmt: skip
        subprocess.run([*new, "restart", "--quiet"], timeout=120, **quiet)
    except (OSError, subprocess.SubprocessError) as exc:
        console.print(Text(tr("  ! Выдра обновлена, но последний шаг не прошёл: {why}", why=exc), style="yellow"))


def _update_ytdlp(check: bool = False) -> None:
    current = tools.ytdlp_version()
    with console.status(Text(tr("Проверяю версию yt-dlp…"), style="dim"), spinner="dots"):
        latest = tools.ytdlp_latest()
    if latest and current and tools.version_tuple(latest) <= tools.version_tuple(current):
        console.print(Text(tr("  ✓ yt-dlp {version} — последняя версия", version=current), style="green"))
        return
    console.print(Text("  ~ ", style="bold yellow") + Text("yt-dlp ", style="bold") + quoted(current or "?", "dim")
                  + Text(" → ", style="bold yellow") + quoted(latest or tr("новее")))  # fmt: skip
    if check:
        return
    with console.status(Text(tr("Обновляю yt-dlp…"), style="dim"), spinner="dots"):
        try:
            message = tools.update_ytdlp()
        except RuntimeError as exc:
            raise fail(str(exc), tr("Проверьте интернет и повторите: выдра обновить --только-ytdlp")) from exc
    console.print(Text(f"  ✓ {tr_msg(message)}", style="green"))


def cookies(
    path: Annotated[Path | None, arg(metavar="[ФАЙЛ]", help="cookies.txt из браузера (формат Netscape)", show_default=False,
                                                exists=True, dir_okay=False)] = None,  # fmt: skip
    remove: Annotated[bool, opt("--remove", "--удалить", help="Отключить и удалить cookies")] = False,
) -> None:
    """Cookies для сайтов, которые просят войти (Instagram, возрастные ролики YouTube). [dim](синоним: куки)[/]"""
    settings = Settings.from_env()
    target = settings.config_dir / "cookies.txt"
    banner("cookies")
    if remove:
        existed = target.exists()
        target.unlink(missing_ok=True)
        console.print(Text(tr("  - cookies удалены") if existed else tr("  cookies и так не подключены"), style="dim"))
        return
    if path is not None:
        data = path.read_bytes()
        if len(data) > 5 * 1024 * 1024 or b"\t" not in data:
            raise fail(tr("Это не cookies.txt"), tr("Нужен формат Netscape: расширение «Get cookies.txt LOCALLY» → Export"))
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name("cookies.txt.tmp")
        tmp.write_bytes(data)
        tmp.chmod(0o600)
        tmp.replace(target)
        console.print(Text("  + ", style="bold green") + Text(tr("cookies подключены — повторите загрузку")))
    current = settings.cookies_file
    if current is None:
        console.print(Text(tr("  Cookies не подключены. Нужны, только если сайт просит войти."), style="dim"))
        console.print(Text(tr("  Подключить: "), style="dim") + Text(tr("выдра cookies ~/Downloads/cookies.txt"), style="cyan"))
        return
    lines = current.read_text(encoding="utf-8", errors="replace").splitlines()
    entries = [ln for ln in lines if ln.strip() and not ln.startswith("#") and len(ln.split("\t")) >= 7]
    sites = sorted({ln.split("\t")[0].lstrip(".").removeprefix("www.") for ln in entries})
    console.print(Text(tr("  Подключены: {n}", n=plural(len(entries), "запись", "записи", "записей")), style="green"))
    if sites:
        shown = ", ".join(sites[:8]) + (tr(" и ещё {n}", n=len(sites) - 8) if len(sites) > 8 else "")
        console.print(Text(tr("  Сайты: {sites}", sites=shown), style="dim"))
    console.print(Text(tr("  Файл: "), style="dim") + linked(current, "dim"), soft_wrap=True)


def shortcut() -> None:
    """Создать ярлык «Выдра» на рабочем столе. [dim](синоним: ярлык)[/]"""
    try:
        message = tools.create_shortcut()
    except RuntimeError as exc:
        raise fail(str(exc)) from exc
    console.print(Text("  + ", style="bold green") + Text(tr_msg(message)))


class Shell(str, Enum):
    bash = "bash"
    zsh = "zsh"
    fish = "fish"
    powershell = "powershell"


def completion(
    shell_name: Annotated[Shell | None, opt("--shell", "--оболочка", help="Только для этой оболочки", show_default=False)] = None,  # noqa: E501
    show: Annotated[bool, opt("--show", "--показать", help="Показать скрипт, ничего не устанавливая")] = False,
    uninstall: Annotated[bool, opt("--uninstall", "--удалить", help="Убрать автодополнение и умные ссылки")] = False,  # noqa: E501
) -> None:
    """Tab-подсказки и умные ссылки (кавычки ставятся сами). [dim](синоним: автодополнение)[/]"""
    from . import shell

    settings = Settings.from_env()
    if show:
        print(shell.script(shell_name.value if shell_name else (shell.detect_shell() or "bash")))
        return
    if uninstall:
        removed = shell.uninstall(settings.config_dir)
        for path in removed:
            console.print(Text("  - ", style="bold red") + Text(system.display_path(path)))
        console.print(Text(tr("  Готово.") if removed else tr("  Нечего убирать."), style="dim"))
        return
    targets = shell.install(settings.config_dir, [shell_name.value] if shell_name else None)
    if not targets:
        raise fail(tr("Не нашёл поддерживаемых оболочек"), tr("Поддерживаются bash, zsh, fish и PowerShell"))
    for target in targets:
        where = target.rc or target.script
        console.print(Text("  + ", style="bold green") + Text(f"{target.shell}: ") + Text(str(where), style="#e6d9a8"))
    console.print(Text(tr("  Откройте новый терминал: Tab подсказывает команды и ключи, а ссылки с & можно не брать в кавычки."),
                       style="dim"))  # fmt: skip
    if system.OS == "wsl":
        console.print(Text(tr("  Для PowerShell и cmd в Windows: "), style="dim") + Text(tr("выдра мост"), style="cyan"))


def bridge_cmd(
    uninstall: Annotated[bool, opt("--uninstall", "--удалить", help="Убрать команды из Windows")] = False,
) -> None:
    """WSL: команды vydra и выдра в PowerShell и cmd Windows. [dim](синоним: мост)[/]"""
    from . import bridge

    banner(tr("мост WSL → Windows"))
    try:
        if uninstall:
            removed = bridge.uninstall()
            console.print(Text(tr("  - убрано: {what}", what=", ".join(removed) or tr("ничего")), style="dim"))
            return
        for message in bridge.install():
            console.print(Text("  + ", style="bold green") + Text(tr_msg(message)))
    except bridge.BridgeError as exc:
        raise fail(str(exc)) from exc


# --- интерактивный режим -----------------------------------------------------------------


def show_art(settings: Settings) -> bool:
    """Заставка при входе в интерактивный режим: только в настоящем терминале и если не выключена."""
    from . import art

    if _json_mode or not console.is_terminal or console.is_dumb_terminal or not art.enabled(Prefs(settings).console_art):
        return False
    size = art.size_for(console.width, console.height)
    if size is None:
        return False
    mode = None if console.no_color else art.color_mode(console.color_system)
    lines = art.render(*size, mode)
    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.flush()
    return True


def settings_cmd(
    art_value: Annotated[str | None, opt("--art", "--заставка", metavar="on|off",
                                                  help="Заставка при входе в интерактивный режим: on/вкл или off/выкл",
                                                  show_default=False)] = None,  # fmt: skip
    storage: Annotated[str | None, opt("--storage", "--папка", metavar="ПАПКА", show_default=False,
                                                help="Новая папка-хранилище (например D:\\Видео или ~/Movies)")] = None,  # fmt: skip
    storage_reset: Annotated[bool, opt("--storage-reset", "--папка-сброс",
                                                help="Вернуть папку-хранилище по умолчанию («Загрузки»)")] = False,  # fmt: skip
    move: Annotated[bool, opt("--move", "--перенести",
                                       help="Вместе с новой папкой перенести в неё уже скачанное")] = False,  # fmt: skip
) -> None:
    """Настройки консоли: заставка, язык, папка-хранилище (с переносом файлов), cookies. [dim](синоним: настройки)[/]"""
    from . import art

    if storage or storage_reset or move:
        if storage and storage_reset:
            raise fail(tr("Либо --storage, либо --storage-reset"), code=EXIT_USAGE)
        if move and not (storage or storage_reset):
            raise fail(tr("--move переносит в новую папку — укажите её: --storage <папка>"), code=EXIT_USAGE)
        folder(path=storage, move=move, reset=storage_reset, pick=False, open_=False)
        console.print()
    settings = Settings.from_env()
    prefs = Prefs(settings)
    if art_value is not None:
        value = {"on": True, "вкл": True, "yes": True, "да": True, "1": True,
                 "off": False, "выкл": False, "no": False, "нет": False, "0": False}.get(art_value.strip().lower())  # fmt: skip
        if value is None:
            raise fail(tr("Непонятное значение «{value}»", value=art_value), tr("Можно: --заставка вкл или --заставка выкл"),
                       code=EXIT_USAGE)  # fmt: skip
        try:
            prefs.set_console_art(value)
        except OSError as exc:
            raise fail(tr("Не удалось сохранить настройки: {why}", why=exc.strerror or exc)) from exc
    banner(tr("настройки"))
    table = Table.grid(padding=(0, 2))
    table.add_column(style="#9aa4b2", no_wrap=True)
    table.add_column(overflow="fold")
    on = art.enabled(prefs.console_art)
    state = Text(tr("включена"), style="green") if on else Text(tr("выключена"), style="dim")
    if prefs.console_art is not False and not on:
        state.append(tr("  (переменной VYDRA_NO_ART)"), style="dim")
    state.append(tr("   выдра настройки --заставка выкл") if on else tr("   выдра настройки --заставка вкл"), style="dim cyan")
    table.add_row(tr("Заставка консоли"), state)
    lang = Text("English" if i18n.LANG == "en" else "русский", style="green")
    if i18n.normalize(os.environ.get("VYDRA_LANG")):
        lang.append(tr("  (переменной VYDRA_LANG)"), style="dim")
    lang.append("   vydra lang ru" if i18n.LANG == "en" else "   выдра язык en", style="dim cyan")
    table.add_row(tr("Язык"), lang)
    table.add_row(tr("Хранилище"), linked(Prefs(settings).library_dir, "cyan")
                  + Text(tr("   выдра настройки --папка <папка> [--перенести]"), style="dim cyan"))  # fmt: skip
    table.add_row("Cookies", Text(tr("подключены"), style="green") if settings.cookies_file else Text(tr("нет"), style="dim"))
    table.add_row(tr("Файл настроек"), linked(prefs.path, "dim"))
    console.print(table)


def interactive() -> None:
    from .downloader import DownloadFailed, preview

    settings = Settings.from_env()
    shown = show_art(settings)
    banner(tr("интерактивный режим"))
    console.print(Text(tr("  Вставьте ссылку и нажмите Enter (кавычки не нужны). Пустая строка или Ctrl+C — выход."), style="dim"))
    if shown:
        console.print(Text(tr("  Заставку можно выключить: выдра настройки --заставка выкл"), style="dim"))
    diagnostics.setup_logging(settings)
    last = {"fmt": "1", "quality": "1080", "bitrate": "192"}
    offered: set[str] = set()
    while True:
        console.print()
        found = [link for link in clipboard.links() if link not in offered][:1]
        label = Text(tr("Ссылка"), style="bold #7c5cff")
        if found:
            label += Text(tr(" [Enter — из буфера: {link}]", link=found[0][:60]), style="dim")
        try:
            raw = Prompt.ask(label + Text(" ›", style="dim"), default="", show_default=False)
        except (KeyboardInterrupt, EOFError):
            console.print()
            return
        urls = clipboard.extract_links(raw) or raw.split()
        if not raw.strip() and found:
            urls = found
            offered.update(found)
        if not urls:
            return
        try:
            urls = [_normalize(u) for u in urls]
        except typer.Exit:
            continue
        if len(urls) == 1:
            with console.status(Text(tr("Смотрю, что там…"), style="dim"), spinner="dots"):
                try:
                    data = preview(urls[0], js_runtime=settings.js_runtime, cookies=settings.cookies_file)
                except DownloadFailed as exc:
                    data = None
                    console.print(Text(f"  ! {tr_msg(str(exc))}", style="yellow"))
            if data:
                platform = detect_platform(data.get("url") or urls[0])
                meta = " · ".join(x for x in (data.get("uploader"), format_time(data.get("duration")) if data.get("duration") else None) if x)
                console.print(Text("  ") + badge(platform) + Text(" ") + Text(data.get("title") or "", style="bold") + Text(f"  {meta}", style="dim"))
        try:
            choice = Prompt.ask(
                Text(tr("Формат"), style="bold") + Text(tr("  1 MP4 видео · 2 MP3 аудио · 3 оба"), style="dim"),
                choices=["1", "2", "3"], default=last["fmt"], show_choices=False,
            )  # fmt: skip
            mode = {"1": "mp4", "2": "mp3", "3": "both"}[choice]
            quality, bitrate = last["quality"], last["bitrate"]
            if mode != "mp3":
                quality = Prompt.ask(Text(tr("Качество"), style="bold"), choices=["max", "1080", "720", "480", "360"], default=quality)
            if mode != "mp4":
                bitrate = Prompt.ask(Text(tr("MP3, кбит/с"), style="bold"), choices=[b.value for b in Bitrate], default=bitrate)
            while True:
                clip_raw = Prompt.ask(
                    Text(tr("Отрезок"), style="bold") + Text(tr("  например 1:00-5:00, Enter — целиком"), style="dim"),
                    default="", show_default=False,
                )  # fmt: skip
                try:
                    cut = parse_range(clip_raw) if clip_raw.strip() else None
                    break
                except ValueError as exc:
                    console.print(Text(f"  ! {tr_msg(str(exc))}", style="yellow"))
        except (KeyboardInterrupt, EOFError):
            console.print()
            return
        last.update(fmt=choice, quality=quality, bitrate=bitrate)
        console.print()
        env = make_env()
        jobs, _ = submit_links(env, _unique_links(urls), mode=mode, quality=quality, bitrate=int(bitrate), clip=cut)
        if jobs:
            run_jobs(env, jobs)
        else:
            env.manager.shutdown()
        console.print(Rule(style="#2a2f3a"))


# --- регистрация -------------------------------------------------------------------------

MAIN, STORE, SERVICE, RU_PANEL = tr("Основное"), tr("Хранилище"), tr("Обслуживание"), "По-русски"


def lang_cmd(
    value: Annotated[str | None, arg(metavar="[ru|en]", show_default=False, autocompletion=lambda: ["en", "ru"],
                                     help="Язык консоли: ru — русский, en — английский")] = None,  # fmt: skip
) -> None:
    """Язык консоли: английский или русский. [dim](синоним: язык)[/]"""
    settings = Settings.from_env()
    prefs = Prefs(settings)
    forced = i18n.normalize(os.environ.get("VYDRA_LANG"))
    if value is not None:
        lang = i18n.normalize(value)
        if lang is None:
            raise fail(tr("Непонятный язык «{value}»", value=value), tr("Можно: ru (русский) или en (English)"),
                       code=EXIT_USAGE)  # fmt: skip
        try:
            prefs.set_lang(lang)
        except OSError as exc:
            raise fail(tr("Не удалось сохранить настройки: {why}", why=exc.strerror or exc)) from exc
        if not forced:
            i18n.set_lang(lang)
    current = i18n.LANG
    name = "English" if current == "en" else "русский"
    if value is not None and not forced:
        console.print(Text("  ✓ ", style="bold green") + Text(tr("Язык консоли: {name}", name=name)))
    else:
        console.print(Text(tr("  Язык консоли: "), style="dim") + Text(name, style="bold green"))
    if forced:
        console.print(Text(tr("  Задан переменной VYDRA_LANG={value} — она главнее выбора командой",
                              value=os.environ.get("VYDRA_LANG", "")), style="yellow"))  # fmt: skip
    other = "en" if current == "ru" else "ru"
    console.print(Text(tr("  Переключить: "), style="dim") + Text(tr("выдра язык {lang}", lang=other), style="cyan")
                  + Text(tr("  (Tab подсказывает: ru, en)"), style="dim"))  # fmt: skip


COMMANDS = [
    (download, "download", MAIN),
    (info, "info", MAIN),
    (convert, "convert", MAIN),
    (ui, "ui", MAIN),
    (stop, "stop", MAIN),
    (list_items, "list", STORE),
    (open_cmd, "open", STORE),
    (folder, "folder", STORE),
    (doctor, "doctor", SERVICE),
    (restart_cmd, "restart", SERVICE),
    (update, "update", SERVICE),
    (cookies, "cookies", SERVICE),
    (settings_cmd, "settings", SERVICE),
    (shortcut, "shortcut", SERVICE),
    (completion, "completion", SERVICE),
    (bridge_cmd, "bridge", SERVICE),
    (lang_cmd, "lang", SERVICE),
]


def _command_help(func) -> str:
    """Докстринг команды — на языке консоли; в английской — без русских синонимов."""
    return tr((func.__doc__ or "").strip())


for func, name, panel in COMMANDS:
    app.command(name, rich_help_panel=panel, help=_command_help(func))(func)
for func, name, panel in COMMANDS:  # синонимы вторым проходом — панель «По-русски» будет последней
    for alias in argv.COMMAND_ALIASES[name][1:]:
        cyrillic = _cyrillic(alias)
        primary = cyrillic and alias == next((a for a in argv.COMMAND_ALIASES[name][1:] if _cyrillic(a)), None)
        app.command(
            alias,
            rich_help_panel=RU_PANEL if cyrillic else panel,
            help=_command_help(func),
            short_help=f"→ [cyan]{name}[/]",  # в списке команд; своя справка — полная
            hidden=not (primary and RU),  # в справке — по одному русскому имени на команду и только по-русски
        )(func)


def _version(value: bool) -> None:
    if value:
        console.print(wordmark() + Text(f" {__version__}", style="bold") + Text(f"  yt-dlp {tools.ytdlp_version()}", style="dim"))
        raise typer.Exit


@app.callback()
def _root(
    ctx: typer.Context,
    version: Annotated[bool, opt("--version", "-V", "--версия", help="Версия", callback=_version, is_eager=True)] = False,  # noqa: E501
) -> None:
    json_mode(False)
    if ctx.invoked_subcommand is None:
        interactive()


def _normalize(url: str) -> str:
    from urllib.parse import parse_qs, urlsplit

    from .main import normalize_url

    try:
        result = normalize_url(url)
    except ValueError as exc:
        raise fail(str(exc), tr("Нужна ссылка вида https://… — скопируйте её из адресной строки браузера")) from exc
    parts = urlsplit(result)
    host = (parts.hostname or "").lower()
    if host.endswith("youtube.com") and parts.path == "/watch" and "v" not in parse_qs(parts.query):
        # bash и zsh режут ссылку на «&», если она не в кавычках: watch?feature=share&v=… → watch?feature=share
        raise fail(tr("В ссылке нет номера видео (v=…): {url}", url=result),
                   tr("Похоже, она обрезалась на «&». Возьмите ссылку в кавычки: выдра скачать '…' "
                      "или скопируйте её и запустите выдра скачать без ссылки"))  # fmt: skip
    return result


def _alive(port: int) -> bool:
    from .servers import alive

    return alive(port)


def _port_state(port: int) -> str:
    from .servers import port_state

    return port_state(port)


def _port_busy(port: int) -> bool:
    return _port_state(port) != "free"


def _russian_help_option() -> None:
    try:  # новые версии Typer вшивают свою копию Click
        from typer._click.core import Command
    except ImportError:
        from click import Command

    original = Command.get_help_option
    names = Command.get_help_option_names

    def ordered_names(self, ctx) -> list[str]:  # Click отдаёт множество — порядок в справке прыгал бы
        found = set(names(self, ctx))
        return [n for n in ctx.help_option_names if n in found]

    Command.get_help_option_names = ordered_names

    def patched(self, ctx):
        option = original(self, ctx)
        if option is not None:
            option.help = tr("Показать эту справку")
        return option

    Command.get_help_option = patched


_ARG_NAMES = {"files": "файлы", "urls": "ссылки", "url": "ссылка", "path": "путь", "query": "название"}


def _short_hint(hint: str) -> str:
    """«'--format' / '-f' / '--формат' / '-ф'» → «--format (-f)»."""
    names = [n.strip().strip("'\"") for n in hint.split("/")]
    names = [_ARG_NAMES.get(n, n) for n in names if n]
    return names[0] + (f" ({names[1]})" if len(names) > 1 else "") if names else hint


def _translate_value(text: str) -> str:
    rules = [
        (r"^'(.*)' is not one of (.+)\.$",
         lambda m: f"«{m[1]}» — такого варианта нет. Можно: {m[2].replace(chr(39), '')}"),
        (r"^'(.*)' is not a valid (?:int|integer|int range|float|float range)\.?$", lambda m: f"«{m[1]}» — это не число"),
        (r"^(\S+) is not in the range (\d+)<=x<=(\d+)\.$", lambda m: f"{m[1]} — можно от {m[2]} до {m[3]}"),
        (r"^(?:File|Path|Directory) '(.*)' does not exist\.$", lambda m: f"нет такого файла или папки: {m[1]}"),
        (r"^Directory '(.*)' is a file\.$", lambda m: f"«{m[1]}» — это файл, а нужна папка"),
        (r"^File '(.*)' is a directory\.$", lambda m: f"«{m[1]}» — это папка, а нужен файл"),
    ]
    for pattern, repl in rules:
        if m := re.match(pattern, text, re.S):
            return repl(m)
    return text


def english_usage_error(message: str) -> str:
    """По-английски текст Click оставляем, только имена ключа — без русских синонимов:
    «Invalid value for '--format' / '-f' / '--формат' / '-ф'» → «Invalid value for --format (-f)»."""
    m = re.match(r"^Invalid value for (.+?): (.*)$", message, re.S)
    if not m:
        return message
    names = [n.strip().strip("'\"") for n in m[1].split("/")]
    names = [n for n in names if n and not _cyrillic(n)] or names
    return f"Invalid value for {names[0]}" + (f" ({names[1]})" if len(names) > 1 else "") + f": {m[2]}"


def _did_you_mean(name: str) -> str:
    from difflib import get_close_matches

    close = get_close_matches(name.lower(), list(argv.COMMANDS), n=1, cutoff=0.6)
    return f" — может быть, «{close[0]}»?" if close else ""


def translate_usage_error(message: str) -> str:
    """Ошибки разбора командной строки (Click) — по-русски и с тем, что делать."""
    rules = [
        (r"^No such option: (\S+)(?: \(Possible options: (.+)\))?(?:\s*Did you mean (\S+)\?)?",
         lambda m: f"Нет ключа {m[1]}" + (f" — может быть, {m[2] or m[3]}?" if (m[2] or m[3]) else "")),
        (r"^Invalid value for (.+?): (.*)$", lambda m: f"Неверное значение {_short_hint(m[1])}: {_translate_value(m[2])}"),
        (r"^Invalid value: (.*)$", lambda m: f"Неверное значение: {_translate_value(m[1])}"),
        (r"^No such command '(.+)'\.", lambda m: f"Нет команды «{m[1]}»" + _did_you_mean(m[1])),
        (r"^Option '(.+)' requires an argument\.", lambda m: f"Ключу {m[1]} нужно значение"),
        (r"^Missing argument '(.+)'\.", lambda m: f"Не хватает аргумента: {_ARG_NAMES.get(m[1], m[1])}"),
        (r"^Got unexpected extra argument(?:s|\(s\))? \((.+)\)", lambda m: f"Лишнее в команде: {m[1]}"),
    ]
    for pattern, repl in rules:
        if m := re.match(pattern, message, re.S):
            return repl(m)
    return message


def _help_and_errors() -> None:
    """Справка и ошибки разбора на языке консоли. Ошибки typer печатает по-английски в рамке — у нас одной
    строкой (по-русски — в переводе) и где справка; в справке верхней строкой — как сменить язык."""
    from rich.markup import render

    from typer import rich_utils

    original_help = rich_utils.rich_format_help

    def format_help(*, obj, ctx, markup_mode) -> None:
        if ctx.parent is None:  # только общая справка: `vydra --help`
            rich_utils._get_rich_console().print(Padding(render(LANG_LINE), (1, 1, 0, 1)))  # noqa: SLF001
        return original_help(obj=obj, ctx=ctx, markup_mode=markup_mode)

    rich_utils.rich_format_help = format_help

    def show(exc) -> None:
        if exc.__class__.__name__ == "NoArgsIsHelpError":
            return
        ctx = getattr(exc, "ctx", None)
        command = ctx.command_path if ctx is not None else "vydra"
        message = exc.format_message()
        err.print(Text("✗ ", style="bold red") + Text(translate_usage_error(message) if RU else english_usage_error(message), style="red"))
        err.print(Text(tr("  → Справка: "), style="dim") + Text(f"{command} --help", style="cyan"))

    rich_utils.rich_format_error = show
    original_panel = rich_utils._print_options_panel  # noqa: SLF001

    def options_panel(*, params, **kw) -> None:
        """Русские имена ключей (--формат, -ф) работают всегда, а в английской справке не шумят."""
        if RU:
            return original_panel(params=params, **kw)
        saved = [(p, p.opts, p.secondary_opts) for p in params]
        try:
            for p in params:
                if len(p.opts) > 1:
                    p.opts = [o for o in p.opts if not _cyrillic(o)] or p.opts
                p.secondary_opts = [o for o in p.secondary_opts if not _cyrillic(o)]
            return original_panel(params=params, **kw)
        finally:
            for p, opts, secondary in saved:
                p.opts, p.secondary_opts = opts, secondary

    rich_utils._print_options_panel = options_panel  # noqa: SLF001
    if not RU:
        return
    # заголовки справки и подписи — тоже по-русски
    rich_utils.ARGUMENTS_PANEL_TITLE = "Аргументы"
    rich_utils.OPTIONS_PANEL_TITLE = "Ключи"
    rich_utils.COMMANDS_PANEL_TITLE = "Команды"
    rich_utils.DEFAULT_STRING = "[по умолчанию: {}]"
    rich_utils.REQUIRED_LONG_STRING = "[обязательно]"
    rich_utils.ABORTED_TEXT = "Прервано."
    try:
        from typer._click.formatting import HelpFormatter
    except ImportError:  # старые Typer — с внешним Click
        from click.formatting import HelpFormatter
    original_usage = HelpFormatter.write_usage

    def write_usage(self, prog: str, args: str = "", prefix: str | None = None) -> None:
        args = args.replace("[OPTIONS]", "[ключи]").replace("COMMAND [ARGS]...", "команда [аргументы]…")
        original_usage(self, prog, args, "Как вызвать: " if prefix is None else prefix)

    HelpFormatter.write_usage = write_usage


_help_and_errors()


def _enable_completion() -> None:
    """Tab-подсказки. Классы оболочек Typer регистрирует, только когда включена его собственная команда
    автодополнения (add_completion=True), а у нас своя (`vydra completion`) — без этого вызова оболочка получала
    «Shell bash not supported» и Tab молчал."""
    try:
        from typer._completion_classes import completion_init
    except ImportError:  # другие версии Typer
        return
    completion_init()


_enable_completion()


def main() -> None:
    _russian_help_option()
    if sys.platform == "win32":
        # старые консоли Windows: UTF-8, иначе кириллица в пайпах превращается в «?»
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
            except (AttributeError, ValueError):
                pass
    args = sys.argv[1:]
    if not os.environ.get("_VYDRA_COMPLETE"):  # при автодополнении строку разбирает сам Click
        normalized = argv.normalize(args)
        args = normalized.args
        if normalized.hint:
            err.print(Text(f"  {tr_msg(normalized.hint)}", style="dim"))
    # complete_var задан явно: из «выдра» Typer вывел бы _ВЫДРА_COMPLETE — недопустимое имя переменной в bash
    prog = Path(sys.argv[0]).stem
    if prog in ("", "__main__", "-c"):  # python -m vydra (так запускают мост, перезапуск, обновление)
        prog = "vydra"
    app(args=args, prog_name=prog, complete_var="_VYDRA_COMPLETE")
