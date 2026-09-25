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

from . import __version__, argv, clipboard, diagnostics, system, tools
from .config import Prefs, Settings
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
    "other": ("Сайт", "#8b7bff"),
    "file": ("Файл", "#9aa4b2"),
}
MODE_LABEL = {"mp4": "MP4 · видео", "mp3": "MP3 · аудио", "both": "MP4 + MP3"}
GRADIENT = ["#7c5cff", "#6f7cff", "#5f9bff", "#43b8ff", "#00d4ff"]


class Fmt(str, Enum):
    mp4 = "mp4"
    mp3 = "mp3"
    both = "both"
    мп4 = "мп4"
    мп3 = "мп3"
    оба = "оба"


class Quality(str, Enum):
    max = "max"
    q1080 = "1080"
    q720 = "720"
    q480 = "480"
    q360 = "360"
    макс = "макс"


class Bitrate(str, Enum):
    b320 = "320"
    b256 = "256"
    b192 = "192"
    b128 = "128"


def fmt_value(value: Fmt | str) -> str:
    return argv.FORMAT_VALUES[getattr(value, "value", value)]


def quality_value(value: Quality | str) -> str:
    return argv.QUALITY_VALUES[getattr(value, "value", value)]


_EXAMPLES = [
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
    ("выдра доктор", "", "--починить", "проверить и починить всё"),
]


def _examples() -> str:
    plain = [" ".join(x for x in (c, u, o) if x) for c, u, o, _ in _EXAMPLES]
    width = max(len(p) for p in plain) + 3
    lines = []
    for (cmd, url, opts, desc), text in zip(_EXAMPLES, plain, strict=True):
        markup = f"[cyan]{cmd}[/]" + (f" [green]{url}[/]" if url else "") + (f" [yellow]{opts}[/]" if opts else "")
        lines.append(f"  {markup}{' ' * (width - len(text))}[dim]{desc}[/]")
    return "\n".join(lines)


HELP = f"""[bold]Скачивает видео с YouTube, TikTok, Instagram и сотен сайтов — без водяных знаков, сразу в MP4 или MP3.[/]

[dim]Проще всего:[/] скопируйте ссылку в браузере и запустите [cyan]выдра скачать[/] [yellow]-ф мп3[/]
[dim](ссылка возьмётся из буфера обмена).[/]

[dim]Примеры (vydra и выдра — одна и та же программа):[/]
{_examples()}

[yellow]Ссылку берите в кавычки[/] [dim]— в bash, zsh и PowerShell символ & из адреса YouTube ломает команду.
Или скопируйте ссылку и не указывайте её вовсе. После[/] [cyan]выдра автодополнение[/] [dim]кавычки ставятся сами,
а Tab подсказывает команды и ключи. Раскладку переключать не нужно: -а ьз3 = -f mp3.[/]

[dim]Для скриптов:[/] [cyan]--json[/] [dim]— итог одной строкой JSON. Коды выхода: 0 — готово, 1 — не скачалось,
2 — неверная команда, 3 — скачано не всё, 130 — прервано (Ctrl+C).[/]"""

app = typer.Typer(
    name="vydra",
    help=HELP,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help", "--справка", "--помощь"]},
    invoke_without_command=True,
    no_args_is_help=False,
    add_completion=False,  # своя команда completion — для vydra и выдра, с умными ссылками
    pretty_exceptions_show_locals=False,
)


# --- оформление --------------------------------------------------------------------------


def wordmark() -> Text:
    text = Text()
    for ch, color in zip("выдра", GRADIENT, strict=True):
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
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if num < 1024 or unit == "ТБ":
            return f"{num:.0f} {unit}" if unit in ("Б", "КБ") else f"{num:.1f} {unit}".replace(".", ",")
        num /= 1024
    return str(num)


def plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    word = one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many
    return f"{n} {word}"


def seconds(value: float) -> str:
    return f"{value:.1f}".replace(".", ",") + " с"


def ago(ts: float) -> str:
    delta = time.time() - ts
    for limit, div, unit in ((60, 1, "с"), (3600, 60, "мин"), (86400, 3600, "ч"), (86400 * 30, 86400, "дн")):
        if delta < limit:
            return f"{int(delta // div)} {unit} назад"
    return time.strftime("%d.%m.%Y", time.localtime(ts))


def fail(message: str, hint: str | None = None, code: int = 1) -> typer.Exit:
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
        raise fail(f"Папка хранилища недоступна: {where} ({exc.strerror or 'нет доступа'})",
                   "Диск отключён или папку удалили? Выберите другую: выдра папка \"D:\\Видео\"")  # fmt: skip
    return Env(settings, library, JobManager(settings, library))


def _out_dir(out: Path) -> Path:
    """-o/--куда: папка для этой загрузки. «D:\\Видео» в WSL — диск Windows; нет такой папки — создаём."""
    raw = str(out)
    try:
        path = system.parse_user_path(raw) if (raw[1:2] == ":" or raw.startswith("\\\\")) else out.expanduser()
    except ValueError as exc:
        raise fail(f"-o {raw}: {exc}") from exc
    path = path.resolve()
    if path.exists() and not path.is_dir():
        raise fail(f"-o {raw}: это файл, а нужна папка")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise fail(f"Не удалось создать папку {system.display_path(path)}: {exc.strerror or exc}",
                   "Проверьте, что диск подключён и в эту папку можно писать") from exc  # fmt: skip
    return path


def resolve_clip(clip: str | None, start: str | None, end: str | None):
    try:
        return parse_range(clip) if clip else parse_clip(start, end)
    except ValueError as exc:
        raise fail(str(exc), "Примеры: -о 1:00-5:00, --с 90 --по 2:30") from exc


def links_or_clipboard(urls: list[str] | None) -> list[str]:
    """Ссылки из аргументов, а если их нет — из буфера обмена."""
    if urls:
        return [_normalize(u) for u in urls]
    quote_hint = "или укажите ссылку в кавычках: выдра скачать 'https://…' -ф мп3"
    try:
        with console.status(Text("Смотрю буфер обмена…", style="dim"), spinner="dots"):
            text = clipboard.read_strict()
    except clipboard.Unavailable as exc:
        raise fail(f"Ссылка не указана, а буфер обмена прочитать не удалось: {exc}", quote_hint.capitalize()) from exc
    found = clipboard.extract_links(text)
    if not found:
        what = "там текст, но не ссылка" if text.strip() else "он пуст"  # сам текст не показываем: вдруг пароль
        raise fail(
            f"Ссылка не указана, а в буфере обмена ссылки нет ({what})",
            f"Скопируйте ссылку в браузере и повторите — {quote_hint}",
        )
    for link in found:
        console.print(Text("  Ссылка из буфера: ", style="dim") + Text(link, style="cyan"))
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
    console.print(Text("  ? ", style="bold #7c5cff") + Text(question.get("title") or "Нужно решение", style="bold"))
    console.print(Text(f"    {(job.title or job.source)[:80]}", style="dim"))
    if question.get("message"):
        console.print(Text(f"    {question['message']}"))
    for n, option in enumerate(options, 1):
        line = Text(f"    {n}) ", style="bold") + Text(option["label"], style="bold" if option["id"] == default else "")
        if option["id"] == default:
            line.append("  ← Enter", style="dim")
        if option.get("hint"):
            line.append(f"\n       {option['hint']}", style="dim")
        console.print(line)
    others = [o["id"] for o in options if o["id"] != default]
    while True:
        try:
            raw = Prompt.ask(Text("    Выбор", style="bold"), default="", show_default=False).strip().lower()
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
        console.print(Text(f"    Введите номер от 1 до {len(options)} или просто Enter", style="yellow"))


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
        stage = job.stage
        if job.retry_at and job.status == "queued":
            left = max(0, int(job.retry_at - time.time()))
            stage = f"повтор через {left} с · попытка {job.attempt} из {job.max_attempts}"
        stats.append(f"  {stage}", style="#9aa4b2" if not job.retry_at else "yellow")
        if job.speed:
            stats.append(f" · {size(job.speed)}/с", style="dim")
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
                kind = "видео" if f["type"] == "mp4" else "аудио"
                path = root / f["path"]
                folder = f.get("folder") or str(Path(f["path"]).parent.as_posix())
                console.print(
                    Text("  + ", style="bold green") + Text(kind, style="bold")
                    + Text(f"  {size(f['size'])} · за {took} · папка ", style="dim")
                    + linked(path.parent, "cyan", rel_folder(folder))
                )  # fmt: skip
                console.print(Text("    ") + linked(path, "#e6d9a8"), soft_wrap=True)  # путь — одной строкой
            if job.warning:
                console.print(Text("  ! ", style="bold yellow") + Text(job.warning, style="yellow"))
        elif job.status == "error":
            console.print(
                Text("  ✗ ", style="bold red") + Text((job.title or job.source)[:60], style="bold")
                + Text(f" — {job.error}", style="red")
            )  # fmt: skip
            if hint := error_hint(job.error):
                console.print(Text("    → ", style="dim") + Text(hint, style="dim"))
        elif job.status == "cancelled":
            console.print(Text("  ○ ", style="dim") + Text(f"{job.title or job.source} — отменено", style="dim"))
        for note in getattr(job, "notes", None) or []:
            console.print(Text("    · ", style="dim") + Text(note, style="#9aa4b2"))

    def view() -> Group:
        active = [j for j in jobs if j.id not in reported]
        rows: list = [Padding(row(j), (0, 0, 1, 0)) for j in active]
        if interrupted:
            rows.append(Text("  Останавливаю и убираю временные файлы… Ctrl+C ещё раз — выйти сразу", style="yellow"))
        return Group(*rows)

    def on_sigint(*_):
        nonlocal interrupted
        if interrupted:  # второй Ctrl+C: не ждём (например, долгого копирования на /mnt/c)
            for job in jobs:
                job.cancel.set()
            sys.stdout.write("\n")
            err.print(Text("Прервано, не дожидаясь остановки загрузок.", style="bold yellow"))
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
                                console.print(Text(f"    ! {exc}", style="yellow"))
                        live.start()
                    if not job.active and job.id not in reported:
                        reported.add(job.id)
                        live.stop()
                        report(job)
                        live.start()
                    elif not console.is_terminal and job.active and stages.get(job.id) != job.stage:
                        # без терминала прогресс-бара не видно — пишем в журнал смену этапов (без процентов)
                        stages[job.id] = job.stage
                        console.print(Text(f"  · {(job.title or job.source)[:70]}: {job.stage}", style="dim"))
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
        console.print(Text("Отменено.", style="bold yellow"), Text(f"Готовых файлов: {len(files)}", style="dim"))
        return EXIT_INTERRUPTED
    if not files:
        reason = failed[0].error if failed else "ничего не получилось"
        console.print(Text("Не удалось скачать: ", style="bold red") + Text(reason or "", style="red"))
        log_path = diagnostics.log_dir(env.settings) / diagnostics.LOG_NAME
        console.print(Text("Подробности в журнале: ", style="dim") + linked(log_path, "dim"), soft_wrap=True)
        return EXIT_FAILED
    ok = not failed
    summary = Text("Готово! " if ok else "Готово с ошибками. ", style="bold green" if ok else "bold yellow")
    summary.append(plural(len(files), "файл создан", "файла создано", "файлов создано"), style="bold")
    if failed:
        summary.append(f", {plural(len(failed), 'ошибка', 'ошибки', 'ошибок')}", style="red")
    summary.append(f" · {size(sum(f['size'] for f in files))} за {seconds(elapsed)}", style="dim")
    console.print(summary)
    console.print(Text("Хранилище: ", style="dim") + linked(env.library.root, "cyan"), soft_wrap=True)
    if not _json_mode and len(files) and _tty(sys.stdout):
        console.print(Text("Показать в папке: ", style="dim") + Text("выдра показать", style="cyan"))
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
    """Что сделать пользователю при этой ошибке (или None, если сказать нечего)."""
    low = (message or "").lower()
    if any(s in low for s in ("vydra ", "выдра ", "запустите")):
        return None  # в тексте ошибки уже сказано, что делать
    return next((hint for needles, hint in _HINTS if any(n in low for n in needles)), None)


# --- команды: основное -------------------------------------------------------------------

UrlsArg = Annotated[
    list[str] | None,
    typer.Argument(metavar="[ССЫЛКИ]…", help="Ссылки на видео (можно несколько). Нет ссылки — возьму из буфера обмена", show_default=False),
]
FmtOpt = Annotated[
    Fmt,
    typer.Option("--format", "-f", "--формат", "-ф", case_sensitive=False,
                 help="Что сохранить: mp4/мп4 — видео, mp3/мп3 — только звук, both/оба — оба файла"),
]  # fmt: skip
QualityOpt = Annotated[
    Quality,
    typer.Option("--quality", "-q", "--качество", "-к", case_sensitive=False,
                 help="Качество видео (по меньшей стороне): max/макс, 1080, 720, 480, 360"),
]  # fmt: skip
BitrateOpt = Annotated[Bitrate, typer.Option("--bitrate", "-b", "--битрейт", "-б", help="Битрейт MP3, кбит/с")]
ClipOpt = Annotated[
    str | None,
    typer.Option("--clip", "-c", "--отрезок", "-о", show_default=False,
                 help="Отрезок: [yellow]1:00-5:00[/], [yellow]90-150[/], [yellow]1:00-[/] (до конца)"),
]  # fmt: skip
FromOpt = Annotated[str | None, typer.Option("--from", "-s", "--с", "--от", help="Начало отрезка (1:00, 90, 1м30с)", show_default=False)]  # noqa: E501
ToOpt = Annotated[str | None, typer.Option("--to", "-e", "--по", "--до", help="Конец отрезка", show_default=False)]
OutOpt = Annotated[
    Path | None,
    typer.Option("--out", "-o", "--выход", "--куда", help="Другая папка-хранилище для этой загрузки",
                 show_default=False, file_okay=False),
]  # fmt: skip
FolderOpt = Annotated[
    str | None,
    typer.Option("--folder", "-F", "--папка", show_default=False,
                 help="Папка внутри хранилища, например [yellow]\"TikTok/Танцы\"[/]"),
]  # fmt: skip
YesOpt = Annotated[
    bool,
    typer.Option("--yes", "-y", "--да", "-д", help="Не задавать вопросов: соглашаться с вариантом по умолчанию"),
]
ForceOpt = Annotated[bool, typer.Option("--force", "--заново", help="Скачать ещё раз, даже если уже есть в хранилище")]
PlaylistOpt = Annotated[bool, typer.Option("--yes-playlist", "--весь-плейлист", help="Разрешить плейлист больше 50 роликов")]  # noqa: E501


JsonOpt = Annotated[
    bool,
    typer.Option("--json", help="Итог — одной строкой JSON на stdout (для скриптов); без прогресса и вопросов, как -y"),
]


def _auto_accept(yes: bool) -> bool:
    if yes or _json_mode:
        return True
    if not _tty(sys.stdin):
        console.print(Text("  Терминала для вопросов нет — соглашаюсь с вариантами по умолчанию (как -y)", style="dim"))
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
    show: Annotated[bool, typer.Option("--show", "--показать", help="Когда скачается — показать файл в папке")] = False,
    as_json: JsonOpt = False,
) -> None:
    """Скачать видео или звук по ссылке. [dim](синонимы: скачать, d)[/]"""
    json_mode(as_json)
    mode, qual = fmt_value(fmt), quality_value(quality)
    cut = resolve_clip(clip, start, end)
    banner(f"{MODE_LABEL[mode]} · {qual if mode != 'mp3' else bitrate.value + ' кбит/с'}")
    links = _unique_links(links_or_clipboard(urls))
    env = make_env(out)
    auto = _auto_accept(yes)
    folder_rel = _folder(env, folder, auto)
    console.print()
    jobs, skipped = submit_links(env, links, mode=mode, quality=qual, bitrate=int(bitrate.value), clip=cut,
                                 folder=folder_rel, confirm_playlist=yes_playlist, auto=auto, force=force)  # fmt: skip
    if not jobs:
        env.manager.shutdown()
        console.print(Text("\nНечего делать: всё уже скачано.", style="bold green"))
        if _json_mode:
            emit_json({"ok": True, "exit_code": EXIT_OK, "files": skipped, "jobs": []})
        if show and skipped:
            _show(Path(skipped[0]["path"]))
        raise typer.Exit(EXIT_OK)
    code = run_jobs(env, jobs)
    files = [f for j in jobs if j.status == "done" for f in j.files]
    if _json_mode:
        emit_json(_jobs_json(env, jobs, skipped, code))
    if show and files:
        _show(env.library.root / files[0]["path"])
    raise typer.Exit(code)


def submit_links(env: Env, links: list[str], *, mode: str, quality: str, bitrate: int, clip, folder: str | None = None,
                 confirm_playlist: bool = False, auto: bool = False, force: bool = False) -> tuple[list[Job], list[dict]]:
    """Задачи на загрузку; то, что уже лежит в хранилище (та же ссылка, формат и отрезок), не качаем повторно."""
    jobs, skipped = [], []
    for url in links:
        existing = None if force else env.manager.find_existing(url, mode, clip)
        if existing:
            for f in existing:
                kind = "видео" if f["type"] == "mp4" else "аудио"
                console.print(
                    Text("  = ", style="bold blue") + Text(kind, style="bold")
                    + Text("  уже в хранилище — повторно не качаю (--заново — скачать ещё раз)", style="dim")
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
            console.print(Text(f"  Повтор ссылки пропущен: {link}", style="dim"))
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
            "error": job.error, "warning": job.warning, "notes": list(job.notes),
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
        raise fail(f"Папка «{folder}»: {exc}") from exc
    if rel and not env.library.folder_exists(rel):
        create = auto or not _tty(sys.stdin)
        if not create:
            answer = Prompt.ask(Text(f"  Папки «{rel}» в хранилище нет. Создать?", style="bold"),
                                choices=["д", "н"], default="д")  # fmt: skip
            create = answer == "д"
        if not create:
            env.manager.shutdown()
            raise fail(f"Папки «{rel}» нет в хранилище", "Создайте её или укажите другую: выдра папка — что есть")
        try:
            (env.library.root / rel).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            env.manager.shutdown()
            raise fail(f"Не удалось создать папку «{rel}»: {exc.strerror or exc}") from exc
        console.print(Text("  + ", style="bold green") + Text(f"папка «{rel}» создана в хранилище"))
    return rel or None


def info(
    url: Annotated[str | None, typer.Argument(metavar="[ССЫЛКА]", help="Ссылка на видео (нет — из буфера обмена)", show_default=False)] = None,
    as_json: JsonOpt = False,
) -> None:
    """Показать, что будет скачано — как [bold]terraform plan[/]. [dim](синонимы: инфо, plan)[/]"""
    from .downloader import PLAYLIST_LIMIT, DownloadFailed, preview

    json_mode(as_json)
    settings = Settings.from_env()
    diagnostics.setup_logging(settings)
    banner("план")
    url = links_or_clipboard([url] if url else None)[0]
    with console.status(Text("Смотрю, что там по ссылке…", style="dim"), spinner="dots"):
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
    console.print(Text("выдра составила план загрузки:", style="bold"))
    console.print()
    head = Text("  + ", style="bold green") + Text("video ", style="bold") + quoted(data.get("title") or "?", "bold")
    console.print(head)
    heights = data.get("heights") or []
    rows = [
        (" ", "платформа", Text(name, style=f"bold {color}")),
        (" ", "автор", quoted(data.get("uploader") or "—")),
        (" ", "длительность", quoted(format_time(data.get("duration")) if data.get("duration") else "—")),
        (" ", "качества", Text(", ".join(f"{h}p" for h in heights) or "—", style="cyan")),
        (" ", "ссылка", quoted(data.get("url") or url, "dim")),
    ]
    if data.get("playlist"):
        rows.insert(1, (" ", "роликов", Text(str(data.get("count") or "?"), style="cyan")))
    if data.get("is_live"):
        console.print(attr_table(rows))
        console.print()
        raise fail("Это прямой эфир — скачать его можно, когда трансляция закончится",
                   "Запись эфира появится на канале; тогда повторите")  # fmt: skip
    root = Prefs(settings).library_dir
    platform_dir = PLATFORM_DIRS.get(platform, PLATFORM_DIRS["other"])
    rows += [
        ("+", "видео", linked(root / platform_dir / TYPE_DIRS["video"], "green")),
        ("+", "аудио", linked(root / platform_dir / TYPE_DIRS["audio"], "green")),
    ]
    console.print(attr_table(rows))
    console.print()
    count = (data.get("count") or 0) if data.get("playlist") else 1
    console.print(
        Text("План: ", style="bold") + Text(f"{count or '?'} к скачиванию", style="green")
        + Text(", 0 к изменению, 0 к удалению.")
    )  # fmt: skip
    if data.get("playlist") and count > PLAYLIST_LIMIT:
        console.print(Text(f"Плейлист больше {PLAYLIST_LIMIT} роликов — добавьте --весь-плейлист", style="yellow"))
    console.print(Text("Скачать: ", style="dim") + Text(f"выдра скачать '{data.get('url') or url}'", style="cyan"),
                  soft_wrap=True)  # fmt: skip


def convert(
    files: Annotated[list[Path], typer.Argument(metavar="ФАЙЛЫ…", help="Видео или аудиофайлы", exists=True, dir_okay=False, show_default=False)],
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
    banner(f"конвертер · {MODE_LABEL[mode]}")
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
    port: Annotated[int, typer.Option("--port", "-p", "--порт", min=1, max=65535, help="Порт веб-интерфейса")] = 8765,
    no_browser: Annotated[bool, typer.Option("--no-browser", "--без-браузера", help="Не открывать браузер")] = False,
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
            raise fail(f"Порт {port} нельзя занять без прав администратора",
                       "Порты до 1024 — системные. Возьмите обычный: выдра интерфейс --порт 8765")  # fmt: skip
        free = next((p for p in range(port + 1, min(port + 21, 65536))
                     if _port_state(p) == "free" and not servers.lock_held(servers.lock_path(base.work_dir, p))), None)  # fmt: skip
        if free is None:
            raise fail(f"Порт {port} и соседние заняты другими программами", "Укажите свой: выдра интерфейс --порт 9000")
        console.print(Text(f"  Порт {port} занят другой программой — запускаю на {free}", style="yellow"))
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
        console.print(Text(f"  Выдра уже запускается на порту {port} — жду…", style="dim"))
        if not _wait_alive(port, 8):
            raise fail(f"Выдра на порту {port} запущена, но не отвечает",
                       f"Перезапустите её: выдра stop --port {port}, затем выдра интерфейс")  # fmt: skip
    banner("уже запущена")
    console.print(Text(f"  {url}", style=f"bold cyan link {url}"))
    console.print(Text("  Остановить: ", style="dim") + Text("выдра stop", style="cyan"))
    if not no_browser:
        _open_browser(url)


def _wait_alive(port: int, timeout: float) -> bool:
    from .servers import wait_alive

    return wait_alive(port, timeout)


def _open_browser(url: str) -> None:
    try:
        system.open_url(url)
    except system.NotSupported as exc:
        err.print(Text(f"  Браузер не открылся ({exc}) — откройте {url} сами", style="yellow"))


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
    body.add_row("Интерфейс", Text(url, style=f"bold cyan underline link {url}"))
    body.add_row("Хранилище", linked(root, "cyan"))
    body.add_row("", "")
    body.add_row("", Text("Не закрывайте это окно — выдра работает, пока оно открыто.\n"
                          "Остановить: Ctrl+C здесь или «выдра stop» в другом терминале.", style="dim"))  # fmt: skip
    title = Text("▲ ") + wordmark() + Text(" обновлена и перезапущена" if restarted else " работает", style="bold")
    console.print()
    console.print(Panel(body, title=title, title_align="left", border_style="#7c5cff", box=box.ROUNDED, padding=(1, 2)))
    servers.write_pid(settings.work_dir, port)
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

    def on_signal(signum, _frame) -> None:
        why.append({signal.SIGINT: "ctrl-c", signal.SIGTERM: "stop"}.get(signum, "restart"))
        SHUTDOWN.set()
        server.should_exit = True

    uvicorn_exit = server.handle_exit

    def handle_exit(signum, frame) -> None:  # Ctrl+C и SIGTERM, пока работает uvicorn
        SHUTDOWN.set()
        uvicorn_exit(signum, frame)

    server.handle_exit = handle_exit  # type: ignore[method-assign]

    # uvicorn на время работы ставит свои обработчики, а после остановки вызывает наши — так
    # мы узнаём, почему он остановился; SIGUSR1 он не трогает, и тот приходит сразу сюда
    handled = [signal.SIGINT, signal.SIGTERM] + ([servers.RESTART_SIGNAL] if servers.RESTART_SIGNAL else [])
    previous = {sig: signal.signal(sig, on_signal) for sig in handled}
    try:
        server.run()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    if "restart" in why:
        console.print(Text("\n  Перезапускаюсь новой версией…", style="dim"))
        sys.stdout.flush()
        sys.stderr.flush()
        servers.clear_pid(settings.work_dir, port)
        os.environ["_VYDRA_RESTARTED"] = "1"
        os.execv(sys.executable, [sys.executable, "-m", "vydra", "ui", "--port", str(port), "--no-browser"])  # noqa: S606
    reason = "остановлена командой stop" if "stop" in why else "остановлена"
    console.print(Text(f"\n  Выдра {reason}.", style="dim"))


def stop(
    port: Annotated[int | None, typer.Option("--port", "-p", "--порт", help="Только этот порт", show_default=False)] = None,
) -> None:
    """Остановить запущенный веб-интерфейс. [dim](синоним: стоп)[/]"""
    from . import servers

    settings = Settings.from_env()
    found = servers.running(settings.work_dir, port)
    if not found:
        where = f" на порту {port}" if port else ""
        console.print(Text(f"Выдра не запущена{where} — останавливать нечего.", style="dim"))
        return
    failed = []
    for server in found:
        with console.status(Text(f"Останавливаю выдру на порту {server.port}…", style="dim"), spinner="dots"):
            ok = servers.stop(settings.work_dir, server)
        if ok:
            console.print(Text("  ✓ ", style="bold green") + Text(f"Остановлена выдра на порту {server.port}"))
        else:
            failed.append(server)
    if failed:
        pids = " ".join(str(s.pid) for s in failed if s.pid) or "<pid>"
        raise fail(f"Не удалось остановить выдру на порту {', '.join(str(s.port) for s in failed)}",
                   f"Завершите процесс вручную: kill -9 {pids}")  # fmt: skip


def restart_cmd(
    port: Annotated[int | None, typer.Option("--port", "-p", "--порт", help="Только этот порт", show_default=False)] = None,
    quiet: Annotated[bool, typer.Option("--quiet", hidden=True, help="Молчать, если выдра не запущена")] = False,
) -> None:
    """Перезапустить работающий веб-интерфейс новой версией. [dim](синоним: перезапустить)[/]"""
    from . import servers

    settings = Settings.from_env()
    found = servers.running(settings.work_dir, port)
    if not found:
        if not quiet:
            console.print(Text("Выдра не запущена — перезапускать нечего.", style="dim"))
        return
    failed = []
    for server in found:
        with console.status(Text(f"Перезапускаю выдру на порту {server.port}…", style="dim"), spinner="dots"):
            ok = servers.restart(settings.work_dir, server)
        if ok:
            log = system.display_path(servers.log_path(settings.work_dir, server.port))
            where = "в том же окне" if server.restartable else f"в фоне (журнал: {log})"
            console.print(Text("  ✓ ", style="bold green") + Text(f"Выдра на порту {server.port} перезапущена {where}"))
        else:
            failed.append(server)
    if failed:
        raise fail(f"Не удалось перезапустить выдру на порту {', '.join(str(s.port) for s in failed)}",
                   "Остановите и запустите заново: выдра stop, затем выдра интерфейс")  # fmt: skip


# --- команды: хранилище ------------------------------------------------------------------


def list_items(
    kind: Annotated[str | None, typer.Option("--type", "-t", "--тип", help="video/видео или audio/аудио", show_default=False)] = None,  # noqa: E501
    search: Annotated[str | None, typer.Option("--search", "-s", "--поиск", help="Поиск по названию", show_default=False)] = None,  # noqa: E501
    limit: Annotated[int, typer.Option("--limit", "-n", "--сколько", help="Сколько показать")] = 25,
    paths: Annotated[bool, typer.Option("--paths", "--пути", help="Показать полные пути файлов")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Список в JSON на stdout (для скриптов)")] = False,
) -> None:
    """Что лежит в хранилище. [dim](синонимы: список, ls)[/]"""
    json_mode(as_json)
    settings = Settings.from_env()
    library = Library(Prefs(settings), Media(settings), enrich=False)
    items = library.items()
    stats = library.stats()
    if kind:
        wanted = {"video": "video", "видео": "video", "audio": "audio", "аудио": "audio"}.get(kind.lower(), kind)
        if wanted not in ("video", "audio"):
            raise fail(f"Непонятный тип «{kind}»", "Бывает: видео (video) или аудио (audio)", code=EXIT_USAGE)
        items = [i for i in items if i["type"] == wanted]
    if search:
        items = [i for i in items if search.casefold() in (i["title"] or "").casefold()]
    if _json_mode:
        root = library.root
        emit_json({"ok": True, "exit_code": EXIT_OK, "root": str(root), "display_root": system.display_path(root),
                   "stats": stats, "total": len(items),
                   "items": [{**i, "abs_path": str(root / i["path"])} for i in items[:limit]]})  # fmt: skip
        return
    banner("хранилище")
    console.print(Text("  ") + linked(library.root, "cyan"), soft_wrap=True)
    if not items:
        console.print(Text("\n  Пусто. Скачайте что-нибудь: ", style="dim") + Text("выдра скачать -ф мп3", style="cyan"))
        return
    if paths:
        for item in items[:limit]:
            icon = Text("▶ ", style="#7c5cff") if item["type"] == "video" else Text("♪ ", style="#00d4ff")
            console.print(Text("  ") + icon + linked(library.root / item["path"]), soft_wrap=True)
    else:
        table = Table(box=box.SIMPLE_HEAD, header_style="bold #9aa4b2", pad_edge=False, expand=False)
        table.add_column("", width=2)
        table.add_column("Название", max_width=max(24, console.width - 58), overflow="ellipsis", no_wrap=True)
        table.add_column("Папка", no_wrap=True, max_width=24, overflow="ellipsis", style="dim")
        table.add_column("Длина", justify="right", style="cyan", no_wrap=True, min_width=5)
        table.add_column("Размер", justify="right", no_wrap=True, min_width=8)
        table.add_column("Добавлено", style="dim", no_wrap=True, min_width=10)
        for item in items[:limit]:
            icon = Text("▶", style="#7c5cff") if item["type"] == "video" else Text("♪", style="#00d4ff")
            title = Text(item["title"], style=f"link {file_uri(library.root / item['path'])}")
            table.add_row(
                icon, title, rel_folder(item.get("folder") or ""),
                format_time(item["duration"]) if item["duration"] else "—", size(item["size"]), ago(item["added"]),
            )  # fmt: skip
        console.print(table)
    console.print(
        Text(f"  {stats['videos']} видео · {stats['audios']} аудио · {size(stats['size'])}", style="dim")
        + (Text(f"   (показано {limit} из {len(items)}, все: --сколько 1000)", style="dim") if len(items) > limit else Text(""))
    )


def open_cmd(
    query: Annotated[str | None, typer.Argument(metavar="[НАЗВАНИЕ]", help="Часть названия; без него — последний скачанный файл", show_default=False)] = None,  # noqa: E501
    play: Annotated[bool, typer.Option("--play", "--запустить", help="Открыть в плеере, а не показать в папке")] = False,
) -> None:
    """Показать скачанный файл выделенным в Проводнике / Finder. [dim](синонимы: показать, открыть)[/]"""
    settings = Settings.from_env()
    library = Library(Prefs(settings), Media(settings), enrich=False)
    items = library.items()
    if query:
        wanted = query.casefold()
        items = [i for i in items if wanted in (i["title"] or "").casefold() or wanted in Path(i["path"]).name.casefold()]
    if not items:
        if query:
            raise fail(f"В хранилище нет файла, в названии которого есть «{query}»", "Что есть: выдра список")
        raise fail("Хранилище пусто — показывать нечего", "Скачайте что-нибудь: выдра скачать -ф мп3")
    path = library.root / items[0]["path"]
    _show(path, play)
    if query and len(items) > 1:
        console.print(Text(f"    Подходит ещё {len(items) - 1} — взяла самый свежий; уточните название, если не тот", style="dim"))


def _show(path: Path, play: bool = False) -> None:
    try:
        (system.open_path if play else system.reveal)(path)
    except FileNotFoundError as exc:
        raise fail(f"Файла уже нет: {system.display_path(path)}", "Его удалили или переместили: выдра список") from exc
    except system.NotSupported as exc:
        raise fail(str(exc)) from exc
    action = "Открываю " if play else "Показываю в папке "
    console.print(Text("  ✓ ", style="bold green") + Text(action) + linked(path, "#e6d9a8"))


def folder(
    path: Annotated[str | None, typer.Argument(metavar="[ПАПКА]", help="Новая папка-хранилище (например D:\\Видео)", show_default=False)] = None,
    move: Annotated[bool, typer.Option("--move", "--перенести", help="Перенести уже скачанное в новую папку")] = False,
    reset: Annotated[bool, typer.Option("--reset", "--сброс", help="Вернуть папку по умолчанию")] = False,
    pick: Annotated[bool, typer.Option("--pick", "--выбрать", help="Выбрать в системном окне")] = False,
    open_: Annotated[bool, typer.Option("--open", "--открыть", help="Открыть в файловом менеджере")] = False,
) -> None:
    """Где лежат файлы; сменить папку (с --перенести — вместе со скачанным). [dim](синоним: папка)[/]"""
    settings = Settings.from_env()
    diagnostics.setup_logging(settings)
    library = Library(Prefs(settings), Media(settings), enrich=False)
    banner("хранилище")
    new: Path | None = None
    try:
        if reset:
            new = settings.default_library
        elif pick:
            new = system.pick_folder(library.root)
            if new is None:
                raise fail("Выбор отменён", code=0)
        elif path:
            new = system.parse_user_path(path)
    except (ValueError, system.NotSupported) as exc:
        raise fail(str(exc)) from exc

    if new is not None:
        if settings.fixed_library:
            raise fail("Папка задана переменной окружения VD_LIBRARY_DIR — здесь её не сменить")
        old = library.root
        try:
            if move and old.is_dir():
                _move_with_progress(library, new)
            else:
                library.set_root(new)
        except (ValueError, OSError) as exc:
            raise fail(str(exc)) from exc
        console.print(
            Text("  ~ ", style="bold yellow") + Text("хранилище ", style="bold") + quoted(system.display_path(old), "dim")
            + Text(" → ", style="bold yellow") + linked(new, "#e6d9a8")
        )  # fmt: skip
        if not move and any(old.glob("*/*/*")):
            console.print(Text("    Уже скачанное осталось в старой папке. Перенести: ", style="dim")
                          + Text(f"выдра папка \"{system.display_path(new)}\" --перенести", style="cyan"))  # fmt: skip
        console.print()

    try:
        library.ensure_layout()
    except OSError as exc:
        raise fail(f"Папка хранилища недоступна: {exc}") from exc
    library.scan(force=True)
    _print_layout(library)
    if settings.fixed_library:
        console.print(Text("  Папка задана переменной VD_LIBRARY_DIR", style="dim"))
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
    tree.add(Text(".vydra/", style="dim") + Text("  служебное: индекс и постеры", style="dim"))
    console.print(tree)
    console.print()
    console.print(Text("  Куда что попадает: ", style="dim") + Text("<Платформа>/Видео", style="cyan")
                  + Text(" и ", style="dim") + Text("<Платформа>/Аудио", style="cyan")
                  + Text("; своя папка — ключ ", style="dim") + Text("--папка \"TikTok/Танцы\"", style="cyan"))  # fmt: skip
    console.print(Text("  Сменить: ", style="dim") + Text("выдра папка \"D:\\Видео\" --перенести", style="cyan"))


def _move_with_progress(library: Library, new: Path) -> None:
    started = time.monotonic()
    with Live(Text("  Переношу…", style="dim"), console=console, transient=True, refresh_per_second=10) as live:

        def progress(done: int, total: int, name: str) -> None:
            bar = ProgressBar(total=total or 1, completed=done, width=30, complete_style="#7c5cff", style="#2a2f3a")
            line = Table.grid(padding=(0, 1))
            line.add_row("  ", bar, Text(f"{size(done)} из {size(total)}", style="dim"),
                         Text(name[-50:], style="dim", no_wrap=True))  # fmt: skip
            live.update(line)

        result = library.move_root(new, progress)
    console.print(
        Text("  ✓ ", style="bold green") + Text(f"Перенесено {size(result['bytes'])} за {seconds(time.monotonic() - started)}")
        + (Text(f", переименовано из-за совпадений: {result['renamed']}", style="dim") if result["renamed"] else Text(""))
    )


# --- команды: обслуживание ---------------------------------------------------------------

STATUS_ICON = {"ok": ("✓", "green"), "warn": ("!", "yellow"), "fail": ("✗", "red")}


def doctor(
    fix: Annotated[bool, typer.Option("--fix", "--починить", help="Починить всё, что можно")] = False,
    offline: Annotated[bool, typer.Option("--offline", "--без-сети", help="Не проверять сеть")] = False,
    report: Annotated[bool, typer.Option("--report", "--отчёт", "--отчет", help="Собрать zip-отчёт для поддержки (без cookies)")] = False,  # noqa: E501
) -> None:
    """Проверить систему и починить неполадки. [dim](синонимы: доктор, health)[/]"""
    from .health import Doctor, summary

    settings = Settings.from_env()
    diagnostics.setup_logging(settings)
    library = Library(Prefs(settings), Media(settings), enrich=False)
    doc = Doctor(settings, library)
    banner("доктор")
    with console.status(Text("Проверяю систему…", style="dim"), spinner="dots"):
        checks = doc.run(network=not offline)
    if report:
        extra = {"stats": library.stats(), "audit": library.audit(), "root": system.display_path(library.root)}
        target = Path.cwd() / time.strftime("vydra-report-%Y%m%d-%H%M.zip")
        target.write_bytes(diagnostics.build_report(settings, [c.public() for c in checks], extra))
        console.print(Text("  + ", style="bold green") + Text("отчёт ") + linked(target, "#e6d9a8"))
        console.print(Text("  Внутри версии, результаты проверки и журналы. Cookies и ссылки с токенами туда не попадают.", style="dim"))
        return

    def show(checks) -> None:
        table = Table.grid(padding=(0, 2))
        table.add_column(width=3)
        table.add_column(style="bold", no_wrap=True)
        table.add_column(overflow="fold")  # длинный путь хранилища — целиком
        for c in checks:
            icon, color = STATUS_ICON[c.status]
            detail = Text(c.detail, style="" if c.status == "ok" else color)
            if c.hint:
                detail.append(f"\n{c.hint}", style="dim")
            if c.fix and c.status != "ok" and not fix:
                detail.append(f"\n→ выдра доктор --починить  ({c.fix.lower()})", style="dim cyan")
            table.add_row(Text(f" {icon}", style=f"bold {color}"), c.title, detail)
        console.print()
        console.print(table)

    show(checks)
    if fix:
        todo = [c for c in checks if c.status != "ok" and c.fix]
        if not todo:
            console.print(Text("\n  Чинить нечего.", style="dim"))
        for c in todo:
            console.print()
            console.print(Text("  ~ ", style="bold yellow") + Text(f"{c.title}: ", style="bold") + Text(f"{c.fix}…"))
            with Live(Text("    …", style="dim"), console=console, transient=True, refresh_per_second=10) as live:

                def progress(done: int, total: int | None, live=live) -> None:
                    bar = ProgressBar(total=total or 1, completed=done if total else 0, pulse=not total, width=30,
                                      complete_style="#7c5cff", style="#2a2f3a")  # fmt: skip
                    line = Table.grid(padding=(0, 1))
                    line.add_row("   ", bar, Text(f"{size(done)}" + (f" из {size(total)}" if total else ""), style="dim"))
                    live.update(line)

                result = doc.fix(c.id, progress)
            style = "green" if result.ok else "red"
            console.print(Text("    ✓ " if result.ok else "    ✗ ", style=f"bold {style}") + Text(result.message, style=style))
        with console.status(Text("Проверяю ещё раз…", style="dim"), spinner="dots"):
            checks = doc.run(network=not offline)
        show(checks)
    s = summary(checks)
    console.print()
    line = Text("Итог: ", style="bold")
    line.append(f"{s['ok']} в порядке", style="green")
    line.append(" · ")
    line.append(plural(s["warn"], "предупреждение", "предупреждения", "предупреждений"), style="yellow" if s["warn"] else "dim")
    line.append(" · ")
    line.append(plural(s["fail"], "ошибка", "ошибки", "ошибок"), style="red" if s["fail"] else "dim")
    console.print(line)
    if s["fail"]:
        raise typer.Exit(1)


def update() -> None:
    """Обновить yt-dlp — когда сайт перестал качаться. [dim](синоним: обновить)[/]"""
    banner("обновление")
    current = tools.ytdlp_version()
    with console.status(Text("Проверяю версию…", style="dim"), spinner="dots"):
        latest = tools.ytdlp_latest()
    if latest and current and tools.version_tuple(latest) <= tools.version_tuple(current):
        console.print(Text(f"  ✓ yt-dlp {current} — последняя версия", style="green"))
        return
    console.print(Text("  ~ ", style="bold yellow") + Text("yt-dlp ", style="bold") + quoted(current or "?", "dim")
                  + Text(" → ", style="bold yellow") + quoted(latest or "новее"))  # fmt: skip
    with console.status(Text("Обновляю…", style="dim"), spinner="dots"):
        try:
            message = tools.update_ytdlp()
        except RuntimeError as exc:
            raise fail(str(exc)) from exc
    console.print(Text(f"  ✓ {message}", style="green"))


def cookies(
    path: Annotated[Path | None, typer.Argument(metavar="[ФАЙЛ]", help="cookies.txt из браузера (формат Netscape)", show_default=False,
                                                exists=True, dir_okay=False)] = None,  # fmt: skip
    remove: Annotated[bool, typer.Option("--remove", "--удалить", help="Отключить и удалить cookies")] = False,
) -> None:
    """Cookies для сайтов, которые просят войти (Instagram, возрастные ролики YouTube). [dim](синоним: куки)[/]"""
    settings = Settings.from_env()
    target = settings.config_dir / "cookies.txt"
    banner("cookies")
    if remove:
        existed = target.exists()
        target.unlink(missing_ok=True)
        console.print(Text("  - cookies удалены" if existed else "  cookies и так не подключены", style="dim"))
        return
    if path is not None:
        data = path.read_bytes()
        if len(data) > 5 * 1024 * 1024 or b"\t" not in data:
            raise fail("Это не cookies.txt", "Нужен формат Netscape: расширение «Get cookies.txt LOCALLY» → Export")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name("cookies.txt.tmp")
        tmp.write_bytes(data)
        tmp.chmod(0o600)
        tmp.replace(target)
        console.print(Text("  + ", style="bold green") + Text("cookies подключены — повторите загрузку"))
    current = settings.cookies_file
    if current is None:
        console.print(Text("  Cookies не подключены. Нужны, только если сайт просит войти.", style="dim"))
        console.print(Text("  Подключить: ", style="dim") + Text("выдра cookies ~/Downloads/cookies.txt", style="cyan"))
        return
    lines = current.read_text(encoding="utf-8", errors="replace").splitlines()
    entries = [ln for ln in lines if ln.strip() and not ln.startswith("#") and len(ln.split("\t")) >= 7]
    sites = sorted({ln.split("\t")[0].lstrip(".").removeprefix("www.") for ln in entries})
    console.print(Text(f"  Подключены: {plural(len(entries), 'запись', 'записи', 'записей')}", style="green"))
    if sites:
        shown = ", ".join(sites[:8]) + (f" и ещё {len(sites) - 8}" if len(sites) > 8 else "")
        console.print(Text(f"  Сайты: {shown}", style="dim"))
    console.print(Text("  Файл: ", style="dim") + linked(current, "dim"), soft_wrap=True)


def shortcut() -> None:
    """Создать ярлык «Выдра» на рабочем столе. [dim](синоним: ярлык)[/]"""
    try:
        message = tools.create_shortcut()
    except RuntimeError as exc:
        raise fail(str(exc)) from exc
    console.print(Text("  + ", style="bold green") + Text(message))


class Shell(str, Enum):
    bash = "bash"
    zsh = "zsh"
    fish = "fish"
    powershell = "powershell"


def completion(
    shell_name: Annotated[Shell | None, typer.Option("--shell", "--оболочка", help="Только для этой оболочки", show_default=False)] = None,  # noqa: E501
    show: Annotated[bool, typer.Option("--show", "--показать", help="Показать скрипт, ничего не устанавливая")] = False,
    uninstall: Annotated[bool, typer.Option("--uninstall", "--удалить", help="Убрать автодополнение и умные ссылки")] = False,  # noqa: E501
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
        console.print(Text("  Готово." if removed else "  Нечего убирать.", style="dim"))
        return
    targets = shell.install(settings.config_dir, [shell_name.value] if shell_name else None)
    if not targets:
        raise fail("Не нашёл поддерживаемых оболочек", "Поддерживаются bash, zsh, fish и PowerShell")
    for target in targets:
        where = target.rc or target.script
        console.print(Text("  + ", style="bold green") + Text(f"{target.shell}: ") + Text(str(where), style="#e6d9a8"))
    console.print(Text("  Откройте новый терминал: Tab подсказывает команды и ключи, а ссылки с & можно не брать в кавычки.",
                       style="dim"))  # fmt: skip
    if system.OS == "wsl":
        console.print(Text("  Для PowerShell и cmd в Windows: ", style="dim") + Text("выдра мост", style="cyan"))


def bridge_cmd(
    uninstall: Annotated[bool, typer.Option("--uninstall", "--удалить", help="Убрать команды из Windows")] = False,
) -> None:
    """WSL: команды vydra и выдра в PowerShell и cmd Windows. [dim](синоним: мост)[/]"""
    from . import bridge

    banner("мост WSL → Windows")
    try:
        if uninstall:
            removed = bridge.uninstall()
            console.print(Text(f"  - убрано: {', '.join(removed) or 'ничего'}", style="dim"))
            return
        for message in bridge.install():
            console.print(Text("  + ", style="bold green") + Text(message))
    except bridge.BridgeError as exc:
        raise fail(str(exc)) from exc


# --- интерактивный режим -----------------------------------------------------------------


def interactive() -> None:
    from .downloader import DownloadFailed, preview

    banner("интерактивный режим")
    console.print(Text("  Вставьте ссылку и нажмите Enter (кавычки не нужны). Пустая строка или Ctrl+C — выход.", style="dim"))
    settings = Settings.from_env()
    diagnostics.setup_logging(settings)
    last = {"fmt": "1", "quality": "1080", "bitrate": "192"}
    offered: set[str] = set()
    while True:
        console.print()
        found = [link for link in clipboard.links() if link not in offered][:1]
        label = Text("Ссылка", style="bold #7c5cff")
        if found:
            label += Text(f" [Enter — из буфера: {found[0][:60]}]", style="dim")
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
            with console.status(Text("Смотрю, что там…", style="dim"), spinner="dots"):
                try:
                    data = preview(urls[0], js_runtime=settings.js_runtime, cookies=settings.cookies_file)
                except DownloadFailed as exc:
                    data = None
                    console.print(Text(f"  ! {exc}", style="yellow"))
            if data:
                platform = detect_platform(data.get("url") or urls[0])
                meta = " · ".join(x for x in (data.get("uploader"), format_time(data.get("duration")) if data.get("duration") else None) if x)
                console.print(Text("  ") + badge(platform) + Text(" ") + Text(data.get("title") or "", style="bold") + Text(f"  {meta}", style="dim"))
        try:
            choice = Prompt.ask(
                Text("Формат", style="bold") + Text("  1 MP4 видео · 2 MP3 аудио · 3 оба", style="dim"),
                choices=["1", "2", "3"], default=last["fmt"], show_choices=False,
            )  # fmt: skip
            mode = {"1": "mp4", "2": "mp3", "3": "both"}[choice]
            quality, bitrate = last["quality"], last["bitrate"]
            if mode != "mp3":
                quality = Prompt.ask(Text("Качество", style="bold"), choices=["max", "1080", "720", "480", "360"], default=quality)
            if mode != "mp4":
                bitrate = Prompt.ask(Text("MP3, кбит/с", style="bold"), choices=[b.value for b in Bitrate], default=bitrate)
            while True:
                clip_raw = Prompt.ask(
                    Text("Отрезок", style="bold") + Text("  например 1:00-5:00, Enter — целиком", style="dim"),
                    default="", show_default=False,
                )  # fmt: skip
                try:
                    cut = parse_range(clip_raw) if clip_raw.strip() else None
                    break
                except ValueError as exc:
                    console.print(Text(f"  ! {exc}", style="yellow"))
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

MAIN, STORE, SERVICE, RU = "Основное", "Хранилище", "Обслуживание", "По-русски"
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
    (shortcut, "shortcut", SERVICE),
    (completion, "completion", SERVICE),
    (bridge_cmd, "bridge", SERVICE),
]
for func, name, panel in COMMANDS:
    app.command(name, rich_help_panel=panel)(func)
for func, name, panel in COMMANDS:  # синонимы вторым проходом — панель «По-русски» будет последней
    for alias in argv.COMMAND_ALIASES[name][1:]:
        cyrillic = any("а" <= ch.lower() <= "я" or ch == "ё" for ch in alias)
        primary = cyrillic and alias == next(
            (a for a in argv.COMMAND_ALIASES[name][1:] if any("а" <= c <= "я" for c in a)), None
        )
        app.command(
            alias,
            rich_help_panel=RU if cyrillic else panel,
            short_help=f"→ [cyan]{name}[/]",  # в списке команд; своя справка — полная
            hidden=not primary,  # в справке — по одному русскому имени на команду
        )(func)


def _version(value: bool) -> None:
    if value:
        console.print(wordmark() + Text(f" {__version__}", style="bold") + Text(f"  yt-dlp {tools.ytdlp_version()}", style="dim"))
        raise typer.Exit


@app.callback()
def _root(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option("--version", "-V", "--версия", help="Версия", callback=_version, is_eager=True)] = False,  # noqa: E501
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
        raise fail(str(exc), "Нужна ссылка вида https://… — скопируйте её из адресной строки браузера") from exc
    parts = urlsplit(result)
    host = (parts.hostname or "").lower()
    if host.endswith("youtube.com") and parts.path == "/watch" and "v" not in parse_qs(parts.query):
        # bash и zsh режут ссылку на «&», если она не в кавычках: watch?feature=share&v=… → watch?feature=share
        raise fail(f"В ссылке нет номера видео (v=…): {result}",
                   "Похоже, она обрезалась на «&». Возьмите ссылку в кавычки: выдра скачать '…' "
                   "или скопируйте её и запустите выдра скачать без ссылки")  # fmt: skip
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

    def patched(self, ctx):
        option = original(self, ctx)
        if option is not None:
            option.help = "Показать эту справку"
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


def _russian_errors() -> None:
    """Ошибки разбора (typer печатает их по-английски в рамке) — одной строкой по-русски и где справка."""
    from typer import rich_utils

    def show(exc) -> None:
        if exc.__class__.__name__ == "NoArgsIsHelpError":
            return
        ctx = getattr(exc, "ctx", None)
        command = ctx.command_path if ctx is not None else "vydra"
        err.print(Text("✗ ", style="bold red") + Text(translate_usage_error(exc.format_message()), style="red"))
        err.print(Text("  → Справка: ", style="dim") + Text(f"{command} --help", style="cyan"))

    rich_utils.rich_format_error = show
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


_russian_errors()


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
            err.print(Text(f"  {normalized.hint}", style="dim"))
    # complete_var задан явно: из «выдра» Typer вывел бы _ВЫДРА_COMPLETE — недопустимое имя переменной в bash
    app(args=args, prog_name=Path(sys.argv[0]).stem or "vydra", complete_var="_VYDRA_COMPLETE")
