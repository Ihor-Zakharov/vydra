"""Консольная выдра: `vydra --help`. Без аргументов — интерактивный режим."""

from __future__ import annotations

import dataclasses
import shutil
import signal
import socket
import sys
import threading
import time
import urllib.request
from enum import Enum
from pathlib import Path
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

from . import __version__, system, tools
from .config import Prefs, Settings
from .jobs import Job, JobManager, detect_platform
from .library import CINEMA, PLATFORM_DIRS, TYPE_DIRS, Library
from .media import Media
from .timecode import clip_label, format_time, parse_clip, parse_range

console = Console(highlight=False)
err = Console(stderr=True, highlight=False)

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


class Quality(str, Enum):
    max = "max"
    q1080 = "1080"
    q720 = "720"
    q480 = "480"
    q360 = "360"


class Bitrate(str, Enum):
    b320 = "320"
    b256 = "256"
    b192 = "192"
    b128 = "128"


HELP = """[bold]Скачивает видео с YouTube, TikTok, Instagram и сотен сайтов — без водяных знаков, сразу в MP4 или MP3.[/]

[dim]Примеры:[/]
  [cyan]vydra[/]                               [dim]интерактивный режим[/]
  [cyan]vydra d[/] [green]<ссылка>[/]                    [dim]скачать видео в MP4[/]
  [cyan]vydra d[/] [green]<ссылка>[/][yellow] -f mp3 -b 320[/]      [dim]только звук[/]
  [cyan]vydra d[/] [green]<ссылка>[/][yellow] --clip 1:00-5:00[/]   [dim]отрезок с 1-й по 5-ю минуту[/]
  [cyan]vydra info[/] [green]<ссылка>[/]                 [dim]что будет скачано (план)[/]
  [cyan]vydra ui[/]                            [dim]веб-интерфейс в браузере[/]
  [cyan]vydra doctor --fix[/]                  [dim]проверить и починить всё[/]

[dim]Автодополнение по Tab:[/] [cyan]vydra completion[/] [dim](bash, zsh, fish, PowerShell)[/]"""

app = typer.Typer(
    name="vydra",
    help=HELP,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
    no_args_is_help=False,
    add_completion=False,  # своя команда completion — с русской справкой
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
    return typer.Exit(code)


def attr_table(rows: list[tuple[str, str | Text, str]]) -> Table:
    """Атрибуты в стиле плана Terraform: «  + ключ = значение»."""
    table = Table.grid(padding=(0, 1))
    table.add_column(width=3)
    table.add_column(style="#9aa4b2", no_wrap=True)
    table.add_column(style="dim", width=1)
    table.add_column()
    for sign, key, value in rows:
        style = {"+": "bold green", "~": "bold yellow", "-": "bold red"}.get(sign, "dim")
        table.add_row(Text(f"  {sign}", style=style), key, "=", value if isinstance(value, Text) else Text(value))
    return table


def quoted(value: str, style: str = "#e6d9a8") -> Text:
    return Text(f'"{value}"', style=style)


# --- окружение ---------------------------------------------------------------------------


@dataclasses.dataclass
class Env:
    settings: Settings
    library: Library
    manager: JobManager


def make_env(out: Path | None = None) -> Env:
    settings = Settings.from_env()
    if out is not None:
        settings = dataclasses.replace(settings, fixed_library=out.expanduser().resolve())
    library = Library(Prefs(settings), Media(settings))
    try:
        library.ensure_layout()
    except OSError as exc:
        raise fail(f"Папка хранилища недоступна: {exc}", "Проверьте путь: vydra folder") from exc
    return Env(settings, library, JobManager(settings, library))


def resolve_clip(clip: str | None, start: str | None, end: str | None):
    try:
        return parse_range(clip) if clip else parse_clip(start, end)
    except ValueError as exc:
        raise fail(str(exc), "Примеры: --clip 1:00-5:00, --from 90 --to 2:30") from exc


# --- прогресс ----------------------------------------------------------------------------


def run_jobs(env: Env, jobs: list[Job]) -> int:
    """Живой прогресс всех задач, итог в стиле `terraform apply`. Возвращает код выхода."""
    started = time.monotonic()
    reported: set[str] = set()
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
        stats.append(f"  {job.stage}", style="#9aa4b2")
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
            for f in job.files:
                kind = "video" if f["type"] == "mp4" else "audio"
                console.print(
                    Text("  + ", style="bold green")
                    + Text(f"{kind} ", style="bold")
                    + quoted(f["path"])
                    + Text(f"  {size(f['size'])} · за {took}", style="dim")
                )
            if job.warning:
                console.print(Text("  ! ", style="bold yellow") + Text(job.warning, style="yellow"))
        elif job.status == "error":
            console.print(
                Text("  ✗ ", style="bold red") + Text((job.title or job.source)[:60], style="bold")
                + Text(f" — {job.error}", style="red")
            )  # fmt: skip
        elif job.status == "cancelled":
            console.print(Text("  ○ ", style="dim") + Text(f"{job.title or job.source} — отменено", style="dim"))

    def view() -> Group:
        active = [j for j in jobs if j.id not in reported]
        return Group(*[Padding(row(j), (0, 0, 1, 0)) for j in active])

    def on_sigint(*_):
        nonlocal interrupted
        interrupted = True
        for job in jobs:
            env.manager.cancel(job.id)

    previous = signal.signal(signal.SIGINT, on_sigint)
    try:
        with Live(view(), console=console, refresh_per_second=12, transient=True) as live:
            while True:
                for job in jobs:
                    if not job.active and job.id not in reported:
                        reported.add(job.id)
                        live.stop()
                        report(job)
                        live.start()
                live.update(view())
                if all(not j.active for j in jobs):
                    break
                time.sleep(0.08)
    finally:
        signal.signal(signal.SIGINT, previous)
        env.manager.shutdown()

    done = [j for j in jobs if j.status == "done"]
    files = [f for j in done for f in j.files]
    errors = sum(1 for j in jobs if j.status == "error")
    elapsed = time.monotonic() - started
    console.print()
    if interrupted:
        console.print(Text("Отменено.", style="bold yellow"), Text(f"Готовых файлов: {len(files)}", style="dim"))
        return 130
    summary = Text("Готово! " if not errors else "Готово с ошибками. ", style="bold green" if not errors else "bold yellow")
    summary.append(plural(len(files), "файл создан", "файла создано", "файлов создано"), style="bold")
    summary.append(f", {plural(errors, 'ошибка', 'ошибки', 'ошибок')}", style="red" if errors else "")
    summary.append(f" · {size(sum(f['size'] for f in files))} за {seconds(elapsed)}", style="dim")
    console.print(summary)
    if files:
        console.print(
            Text("Хранилище: ", style="dim") + Text(system.display_path(env.library.root), style="cyan")
            + Text("   кинотеатр: ", style="dim") + Text("vydra cinema", style="cyan")
        )  # fmt: skip
    return 1 if errors and not done else 0


# --- команды: основное -------------------------------------------------------------------

UrlsArg = Annotated[list[str], typer.Argument(help="Ссылки на видео (можно несколько)", show_default=False)]
FmtOpt = Annotated[Fmt, typer.Option("--format", "-f", help="Что сохранить: mp4, mp3 или both (оба)")]
QualityOpt = Annotated[Quality, typer.Option("--quality", "-q", help="Качество видео (по меньшей стороне)")]
BitrateOpt = Annotated[Bitrate, typer.Option("--bitrate", "-b", help="Битрейт MP3, кбит/с")]
ClipOpt = Annotated[str | None, typer.Option("--clip", "-c", help="Отрезок: [yellow]1:00-5:00[/], [yellow]90-150[/], [yellow]1:00-[/] (до конца)", show_default=False)]  # noqa: E501
FromOpt = Annotated[str | None, typer.Option("--from", "-s", help="Начало отрезка (1:00, 90, 1м30с)", show_default=False)]
ToOpt = Annotated[str | None, typer.Option("--to", "-e", help="Конец отрезка", show_default=False)]
OutOpt = Annotated[Path | None, typer.Option("--out", "-o", help="Другая папка-хранилище для этой загрузки", show_default=False, file_okay=False)]  # noqa: E501


def download(
    urls: UrlsArg,
    fmt: FmtOpt = Fmt.mp4,
    quality: QualityOpt = Quality.q1080,
    bitrate: BitrateOpt = Bitrate.b192,
    clip: ClipOpt = None,
    start: FromOpt = None,
    end: ToOpt = None,
    out: OutOpt = None,
) -> None:
    """Скачать видео или звук по ссылке. [dim](синонимы: скачать, d)[/]"""
    cut = resolve_clip(clip, start, end)
    env = make_env(out)
    banner(f"{MODE_LABEL[fmt.value]} · {quality.value if fmt != Fmt.mp3 else bitrate.value + ' кбит/с'}")
    console.print()
    jobs = [
        env.manager.submit(
            Job(kind="url", source=_normalize(u), mode=fmt.value, quality=quality.value, bitrate=int(bitrate.value),
                clip=cut)  # fmt: skip
        )
        for u in urls
    ]
    raise typer.Exit(run_jobs(env, jobs))


def info(url: Annotated[str, typer.Argument(help="Ссылка на видео", show_default=False)]) -> None:
    """Показать, что будет скачано — как [bold]terraform plan[/]. [dim](синонимы: инфо, plan)[/]"""
    from .downloader import DownloadFailed, preview

    settings = Settings.from_env()
    url = _normalize(url)
    banner("план")
    with console.status(Text("Смотрю, что там по ссылке…", style="dim"), spinner="dots"):
        try:
            data = preview(url, js_runtime=settings.js_runtime, cookies=settings.cookies_file)
        except DownloadFailed as exc:
            raise fail(str(exc)) from exc
    platform = detect_platform(data.get("url") or url)
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
        (" ", "водяной знак", Text("нет", style="green")),
        (" ", "ссылка", quoted(data.get("url") or url, "dim")),
    ]
    if data.get("playlist"):
        rows.insert(1, (" ", "роликов", Text(str(data.get("count") or "?"), style="cyan")))
    root = Prefs(settings).library_dir
    folder = PLATFORM_DIRS.get(platform, PLATFORM_DIRS["other"])
    rows += [
        ("+", "файл mp4", Text(f"{TYPE_DIRS['video']}/{folder}/…mp4", style="green")),
        ("+", "файл mp3", Text("(если выбрать -f mp3 или both)", style="dim italic")),
        ("+", "размер", Text("(станет известен после загрузки)", style="dim italic")),
    ]
    console.print(attr_table(rows))
    console.print()
    console.print(
        Text("План: ", style="bold") + Text("1 к скачиванию", style="green") + Text(", 0 к изменению, 0 к удалению.")
    )
    console.print(Text(f"Хранилище: {system.display_path(root)}", style="dim"))
    console.print(Text("Скачать: ", style="dim") + Text(f"vydra d {data.get('url') or url}", style="cyan"))


def convert(
    files: Annotated[list[Path], typer.Argument(help="Видео или аудиофайлы", exists=True, dir_okay=False, show_default=False)],
    fmt: FmtOpt = Fmt.mp3,
    bitrate: BitrateOpt = Bitrate.b192,
    clip: ClipOpt = None,
    start: FromOpt = None,
    end: ToOpt = None,
    out: OutOpt = None,
) -> None:
    """Сконвертировать свои файлы в MP4/MP3 (можно вырезать отрезок). [dim](синоним: конвертировать)[/]"""
    cut = resolve_clip(clip, start, end)
    env = make_env(out)
    banner(f"конвертер · {MODE_LABEL[fmt.value]}")
    console.print()
    jobs = []
    for file in files:
        copy = env.manager.new_upload_dir() / file.name
        shutil.copyfile(file, copy)
        jobs.append(
            env.manager.submit(
                Job(kind="file", source=file.name, mode=fmt.value, bitrate=int(bitrate.value), clip=cut, input_path=copy)
            )
        )
    raise typer.Exit(run_jobs(env, jobs))


def ui(
    port: Annotated[int, typer.Option("--port", "-p", help="Порт веб-интерфейса")] = 8765,
    no_browser: Annotated[bool, typer.Option("--no-browser", help="Не открывать браузер")] = False,
) -> None:
    """Запустить веб-интерфейс и открыть его в браузере. [dim](синонимы: интерфейс, web)[/]"""
    import os

    import uvicorn

    url = f"http://localhost:{port}"
    if _alive(port):
        banner("уже запущена")
        console.print(Text(f"  {url}", style="bold cyan"))
        if not no_browser:
            system.open_url(url)
        return
    if _port_busy(port):
        raise fail(f"Порт {port} занят другой программой", f"Запустите на другом: vydra ui --port {port + 1}")

    os.environ["VD_PORT"] = str(port)
    settings = Settings.from_env()
    root = Prefs(settings).library_dir
    body = Table.grid(padding=(0, 2))
    body.add_column(style="#9aa4b2")
    body.add_column()
    body.add_row("Интерфейс", Text(url, style="bold cyan underline"))
    body.add_row("Хранилище", Text(system.display_path(root), style="cyan"))
    body.add_row("Кинотеатр", Text(f"{url}/lib/{CINEMA}", style="cyan"))
    body.add_row("", "")
    body.add_row("", Text("Не закрывайте это окно — выдра работает, пока оно открыто. Остановить: Ctrl+C", style="dim"))
    console.print()
    console.print(
        Panel(body, title=Text("▲ ") + wordmark() + Text(" работает", style="bold"), title_align="left",
              border_style="#7c5cff", box=box.ROUNDED, padding=(1, 2))
    )  # fmt: skip
    if not no_browser:

        def opener() -> None:
            for _ in range(100):
                if _alive(port):
                    system.open_url(url)
                    return
                time.sleep(0.1)

        threading.Thread(target=opener, daemon=True).start()
    uvicorn.run(
        "vydra.main:create_app",
        factory=True,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )


# --- команды: хранилище ------------------------------------------------------------------


def list_items(
    kind: Annotated[str | None, typer.Option("--type", "-t", help="video или audio", show_default=False)] = None,
    search: Annotated[str | None, typer.Option("--search", "-s", help="Поиск по названию", show_default=False)] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Сколько показать")] = 25,
) -> None:
    """Что лежит в хранилище. [dim](синонимы: список, ls)[/]"""
    settings = Settings.from_env()
    library = Library(Prefs(settings), Media(settings))
    items = library.items()
    stats = library.stats()
    if kind:
        items = [i for i in items if i["type"] == kind]
    if search:
        items = [i for i in items if search.lower() in (i["title"] or "").lower()]
    banner("хранилище")
    if not items:
        console.print(Text("\n  Пусто. Скачайте что-нибудь: ", style="dim") + Text("vydra d <ссылка>", style="cyan"))
        return
    table = Table(box=box.SIMPLE_HEAD, header_style="bold #9aa4b2", pad_edge=False, expand=False)
    table.add_column("", width=2)
    table.add_column("Название", max_width=max(24, console.width - 58), overflow="ellipsis", no_wrap=True)
    table.add_column("Откуда", no_wrap=True, min_width=11)
    table.add_column("Длина", justify="right", style="cyan", no_wrap=True, min_width=5)
    table.add_column("Размер", justify="right", no_wrap=True, min_width=8)
    table.add_column("Добавлено", style="dim", no_wrap=True, min_width=10)
    for item in items[:limit]:
        icon = Text("▶", style="#7c5cff") if item["type"] == "video" else Text("♪", style="#00d4ff")
        table.add_row(
            icon, item["title"], badge(item["platform"]), format_time(item["duration"]) if item["duration"] else "—",
            size(item["size"]), ago(item["added"]),
        )  # fmt: skip
    console.print(table)
    console.print(
        Text(f"  {stats['videos']} видео · {stats['audios']} аудио · {size(stats['size'])}", style="dim")
        + (Text(f"   (показано {limit} из {len(items)})", style="dim") if len(items) > limit else Text(""))
    )


def folder(
    path: Annotated[str | None, typer.Argument(help="Новая папка-хранилище (например D:\\Кино)", show_default=False)] = None,
    reset: Annotated[bool, typer.Option("--reset", help="Вернуть папку по умолчанию")] = False,
    pick: Annotated[bool, typer.Option("--pick", help="Выбрать в системном окне")] = False,
    open_: Annotated[bool, typer.Option("--open", help="Открыть в файловом менеджере")] = False,
) -> None:
    """Показать или сменить папку-хранилище. [dim](синоним: папка)[/]"""
    settings = Settings.from_env()
    library = Library(Prefs(settings), Media(settings))
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
        if new is not None:
            old = library.root
            library.set_root(new)
            console.print(
                Text("  ~ ", style="bold yellow") + Text("хранилище ", style="bold") + quoted(system.display_path(old), "dim")
                + Text(" → ", style="bold yellow") + quoted(system.display_path(new))
            )  # fmt: skip
    except (ValueError, system.NotSupported) as exc:
        raise fail(str(exc)) from exc

    library.ensure_layout()
    library.scan(force=True)
    root = library.root
    tree = Tree(Text(system.display_path(root), style="bold cyan"), guide_style="#3a4150")
    items = library.items()
    for kind, dirname in TYPE_DIRS.items():
        branch = tree.add(Text(dirname, style="bold"))
        for platform, sub in PLATFORM_DIRS.items():
            count = sum(1 for i in items if i["type"] == kind and i["platform"] == platform)
            if count:
                branch.add(Text(sub) + Text(f"  {count}", style="dim"))
    tree.add(Text(CINEMA, style="#e6d9a8") + Text("  офлайн-кинотеатр, открывается двойным кликом", style="dim"))
    tree.add(Text(".vydra/", style="dim") + Text("  служебное: индекс и постеры", style="dim"))
    banner("хранилище")
    console.print(tree)
    if settings.fixed_library:
        console.print(Text("  Папка задана переменной VD_LIBRARY_DIR", style="dim"))
    if open_:
        system.open_path(root)


def cinema() -> None:
    """Открыть офлайн-кинотеатр. [dim](синоним: кинотеатр)[/]"""
    settings = Settings.from_env()
    library = Library(Prefs(settings), Media(settings))
    library.ensure_layout()
    library.scan(force=True)
    target = library.root / CINEMA
    console.print(Text("▶ ", style="bold #7c5cff") + Text(f"Открываю кинотеатр: {system.display_path(target)}"))
    system.open_path(target)


# --- команды: обслуживание ---------------------------------------------------------------

STATUS_ICON = {"ok": ("✓", "green"), "warn": ("!", "yellow"), "fail": ("✗", "red")}


def doctor(
    fix: Annotated[bool, typer.Option("--fix", help="Починить всё, что можно")] = False,
    offline: Annotated[bool, typer.Option("--offline", help="Не проверять сеть")] = False,
) -> None:
    """Проверить систему и починить неполадки. [dim](синонимы: доктор, health)[/]"""
    from .health import Doctor, summary

    settings = Settings.from_env()
    library = Library(Prefs(settings), Media(settings))
    doc = Doctor(settings, library)
    banner("доктор")
    with console.status(Text("Проверяю систему…", style="dim"), spinner="dots"):
        checks = doc.run(network=not offline)

    def show(checks) -> None:
        table = Table.grid(padding=(0, 2))
        table.add_column(width=3)
        table.add_column(style="bold", no_wrap=True)
        table.add_column()
        for c in checks:
            icon, color = STATUS_ICON[c.status]
            detail = Text(c.detail, style="" if c.status == "ok" else color)
            if c.hint:
                detail.append(f"\n{c.hint}", style="dim")
            if c.fix and c.status != "ok" and not fix:
                detail.append(f"\n→ vydra doctor --fix  ({c.fix.lower()})", style="dim cyan")
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
    pwsh = "pwsh"


def completion(
    shell: Annotated[Shell | None, typer.Option("--shell", help="Оболочка (по умолчанию — текущая)", show_default=False)] = None,
    show: Annotated[bool, typer.Option("--show", help="Только показать скрипт, не устанавливать")] = False,
) -> None:
    """Включить автодополнение команд по Tab. [dim](синоним: автодополнение)[/]"""
    from typer import _completion_shared as tc

    name = shell.value if shell else tc._get_shell_name()
    if show:
        print(tc.get_completion_script(prog_name="vydra", complete_var="_VYDRA_COMPLETE", shell=name or "bash"))
        return
    try:
        installed, path = tc.install(shell=name, prog_name="vydra", complete_var="_VYDRA_COMPLETE")
    except (typer.Exit, SystemExit) as exc:
        raise fail(f"Оболочка «{name}» не поддерживается", "Поддерживаются bash, zsh, fish и PowerShell") from exc
    console.print(Text("  + ", style="bold green") + Text(f"автодополнение для {installed}: ") + quoted(str(path)))
    console.print(Text("  Откройте новый терминал — и жмите Tab после «vydra ».", style="dim"))


# --- интерактивный режим -----------------------------------------------------------------


def interactive() -> None:
    from .downloader import DownloadFailed, preview

    banner("интерактивный режим")
    console.print(Text("  Вставьте ссылку и нажмите Enter. Пустая строка или Ctrl+C — выход. Все команды: vydra --help", style="dim"))
    settings = Settings.from_env()
    last = {"fmt": "1", "quality": "1080", "bitrate": "192"}
    while True:
        console.print()
        try:
            raw = Prompt.ask(Text("Ссылка", style="bold #7c5cff") + Text(" ›", style="dim"), default="", show_default=False)
        except (KeyboardInterrupt, EOFError):
            console.print()
            return
        urls = raw.split()
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
                quality = Prompt.ask(Text("Качество", style="bold"), choices=[q.value for q in Quality], default=quality)
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
        jobs = [
            env.manager.submit(Job(kind="url", source=u, mode=mode, quality=quality, bitrate=int(bitrate), clip=cut))
            for u in urls
        ]
        run_jobs(env, jobs)
        console.print(Rule(style="#2a2f3a"))


# --- регистрация -------------------------------------------------------------------------

MAIN, STORE, SERVICE, RU = "Основное", "Хранилище", "Обслуживание", "По-русски"
COMMANDS = [
    (download, "download", ["скачать", "d"], MAIN),
    (info, "info", ["инфо", "plan"], MAIN),
    (convert, "convert", ["конвертировать"], MAIN),
    (ui, "ui", ["интерфейс", "web"], MAIN),
    (list_items, "list", ["список", "ls"], STORE),
    (folder, "folder", ["папка"], STORE),
    (cinema, "cinema", ["кинотеатр"], STORE),
    (doctor, "doctor", ["доктор", "health"], SERVICE),
    (update, "update", ["обновить"], SERVICE),
    (shortcut, "shortcut", ["ярлык"], SERVICE),
    (completion, "completion", ["автодополнение"], SERVICE),
]
for func, name, _, panel in COMMANDS:
    app.command(name, rich_help_panel=panel)(func)
for func, name, aliases, panel in COMMANDS:  # синонимы вторым проходом — панель «По-русски» будет последней
    for alias in aliases:
        cyrillic = any("а" <= ch <= "я" for ch in alias)
        app.command(
            alias,
            rich_help_panel=RU if cyrillic else panel,
            help=f"→ [cyan]{name}[/]",
            hidden=not cyrillic,  # короткие латинские синонимы работают, но не мозолят глаза в справке
        )(func)


def _version(value: bool) -> None:
    if value:
        console.print(wordmark() + Text(f" {__version__}", style="bold") + Text(f"  yt-dlp {tools.ytdlp_version()}", style="dim"))
        raise typer.Exit


@app.callback()
def _root(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option("--version", "-V", help="Версия", callback=_version, is_eager=True)] = False,
) -> None:
    if ctx.invoked_subcommand is None:
        interactive()


def _normalize(url: str) -> str:
    from .main import normalize_url

    try:
        return normalize_url(url)
    except ValueError as exc:
        raise fail(str(exc)) from exc


def _alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as resp:
            return b'"ok"' in resp.read()
    except Exception:  # noqa: BLE001
        return False


def _port_busy(port: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


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


def main() -> None:
    _russian_help_option()
    if sys.platform == "win32":
        # старые консоли Windows: UTF-8, иначе кириллица в пайпах превращается в «?»
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
            except (AttributeError, ValueError):
                pass
    app()
