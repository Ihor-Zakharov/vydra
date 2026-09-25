#!/usr/bin/env python3
"""Стенд интерфейса выдры — детерминированные кадры через Edge (CDP) и Playwright.

Снимает состояния из docs/sprint/STATES.md в нескольких окнах, подменяя /api/** фикстурами
(tools/rig_fixtures/) и поддельным EventSource, чтобы кадры были попиксельно одинаковы
между прогонами. Пишет .sprint/shots/<label>/<viewport>/<state>.png, contact-<viewport>.png
и report.json (консоль, вылет по ширине, обрезанный текст, наложения, axe).

Запуск:
    python3 tools/ui_rig.py --label baseline
    python3 tools/ui_rig.py --label baseline --states idle,job-*,library-grid
    python3 tools/ui_rig.py --label mech --css .sprint/proto/hero.css --viewports s,m
    python3 tools/ui_rig.py --label after --diff baseline

Если playwright не импортируется, скрипт сам перезапускает себя через
`uv run --with playwright --with pillow`.
"""

from __future__ import annotations

import os
import sys


def _relaunch_if_needed() -> None:
    try:
        import playwright  # noqa: F401
        import PIL  # noqa: F401
        return
    except ImportError:
        os.execvp(
            "uv",
            ["uv", "run", "--with", "playwright", "--with", "pillow", "python3",
             os.path.abspath(__file__), *sys.argv[1:]],
        )


_relaunch_if_needed()

import argparse  # noqa: E402
import asyncio  # noqa: E402
import fnmatch  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Awaitable, Callable, Optional  # noqa: E402
from urllib.parse import urlencode, urlparse, parse_qs  # noqa: E402

from playwright.async_api import async_playwright, Page, Route, Request, BrowserContext  # noqa: E402
from PIL import Image, ImageChops, ImageDraw, ImageFont  # noqa: E402


TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent
SHOTS_ROOT = ROOT / ".sprint" / "shots"
FIXTURES_DIR = TOOLS_DIR / "rig_fixtures"
MEDIA_DIR = FIXTURES_DIR / "media"
AXE_PATH = TOOLS_DIR / "vendor" / "axe.min.js"
CATALOG_PATH = FIXTURES_DIR / "catalog.json"

CDP_URL = "http://localhost:9333"
DEFAULT_BASE_URL = "http://localhost:8799"

VIEWPORTS: dict[str, dict[str, Any]] = {
    "s": {"width": 1366, "height": 657},
    "m": {"width": 1536, "height": 730, "device_scale_factor": 1.25},
    "l": {"width": 1920, "height": 960},
    "xl": {"width": 2560, "height": 1305},
}
DEFAULT_VIEWPORTS = ["s", "m", "l"]

# «Сейчас» для fmtAgo() и других относительных дат — заморожено, чтобы кадры не менялись
# от прогона к прогону. 2026-09-25T12:00:00Z.
FIXED_NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)
FIXED_NOW_MS = int(FIXED_NOW.timestamp() * 1000)

PLAT_ORDER = ["youtube", "tiktok", "instagram", "other", "file"]
PLAT_NAME = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram", "other": "Другие сайты", "file": "Мои файлы"}
NAME_PLAT = {v: k for k, v in PLAT_NAME.items()}
TYPE_NAME = {"video": "Видео", "audio": "Аудио"}

with open(CATALOG_PATH, encoding="utf-8") as f:
    CATALOG = json.load(f)


# ============================== виртуальная файловая система (как explorer.js) ==============================

def item_folder(it: dict) -> str:
    if it.get("folder") is not None:
        return str(it["folder"])
    plat = PLAT_NAME.get(it.get("platform"), PLAT_NAME["other"])
    typ = TYPE_NAME.get(it.get("type"), "Видео")
    return f"{plat}/{typ}"


def virtual_fs(path: str, items: list[dict]) -> dict:
    folders: dict[str, dict] = {}
    files: list[dict] = []
    for it in items:
        f = item_folder(it)
        if f == path:
            files.append({**it, "name": it["path"].split("/")[-1], "folder": f})
            continue
        prefix = f"{path}/" if path else ""
        if not f.startswith(prefix):
            continue
        name = f[len(prefix):].split("/")[0]
        if not name:
            continue
        p = f"{prefix}{name}"
        cur = folders.setdefault(p, {
            "name": name, "path": p,
            "platform": (NAME_PLAT.get(name) if not path else None),
            "system": (not path) or name in TYPE_NAME.values(),
            "count": 0, "size": 0, "modified": 0,
        })
        cur["count"] += 1
        cur["size"] += it.get("size") or 0
        cur["modified"] = max(cur["modified"], it.get("added") or 0)
    if not path:
        for k in PLAT_ORDER:
            nm = PLAT_NAME[k]
            folders.setdefault(nm, {"name": nm, "path": nm, "platform": k, "system": True, "count": 0, "size": 0, "modified": 0})
    crumbs = []
    acc = ""
    for part in [p for p in path.split("/") if p]:
        acc = f"{acc}/{part}" if acc else part
        crumbs.append({"name": part, "path": acc})
    return {
        "path": path, "name": path.split("/")[-1] if path else "",
        "breadcrumbs": crumbs, "folders": list(folders.values()), "files": files,
        "rev": len(items),
    }


def virtual_tree(items: list[dict]) -> dict:
    def build(path: str):
        fs = virtual_fs(path, items)
        return [{**fo, "children": build(fo["path"])} for fo in fs["folders"]]
    return {
        "name": "Хранилище", "path": "", "platform": None, "system": True,
        "count": len(items), "size": sum(i.get("size") or 0 for i in items),
        "children": build(""),
    }


def library_stats(items: list[dict]) -> dict:
    videos = sum(1 for i in items if i.get("type") == "video")
    audios = sum(1 for i in items if i.get("type") == "audio")
    return {"count": len(items), "size": sum(i.get("size") or 0 for i in items), "videos": videos, "audios": audios}


# ============================== состояние фикстур для одной страницы ==============================

def default_rt() -> dict:
    return {
        "info": CATALOG["info"],
        "settings": CATALOG["settings_default"],
        "doctor": CATALOG["doctor_ok"],
        "jobs": [],
        "library_items": [],
        "down": False,  # /api/** отвечает ошибкой — состояние "offline"
        "_preview_thumb_override": None,
    }


async def api_router(route: Route, request: Request, rt: dict) -> None:
    if rt.get("down"):
        await route.abort("connectionrefused")
        return
    url = urlparse(request.url)
    path = url.path
    method = request.method
    qs = parse_qs(url.query)

    def j(data, status=200):
        return route.fulfill(status=status, content_type="application/json", body=json.dumps(data))

    try:
        if path == "/api/health":
            await j({"ok": True, "version": CATALOG["info"]["version"]}); return
        if path == "/api/info":
            await j(rt["info"]); return
        if path == "/api/settings" and method == "GET":
            await j(rt["settings"]); return
        if path == "/api/settings/library" and method == "POST":
            await j(rt["settings"]); return
        if path == "/api/settings/library/pick" and method == "POST":
            await j({"cancelled": True}); return
        if path == "/api/settings/cookies":
            await j(rt["settings"]); return
        if path == "/api/doctor" and method == "GET":
            await j(rt["doctor"]); return
        if path == "/api/doctor/fix" and method == "POST":
            await j({"results": [{"ok": True, "message": "Починено"}], "checks": rt["doctor"]["checks"]}); return
        if path == "/api/folder/open":
            await j({"ok": True}); return
        if path == "/api/jobs" and method == "GET":
            await j(rt["jobs"]); return
        if path == "/api/jobs" and method == "POST":
            await j([]); return
        if path == "/api/jobs/clear":
            await j({"ok": True}); return
        m = re.match(r"^/api/jobs/([^/]+)/(cancel|retry|answer)$", path)
        if m:
            await j({"ok": True}); return
        if re.match(r"^/api/jobs/[^/]+/thumbnail$", path):
            await route.fulfill(status=404, body=""); return
        if path == "/api/convert":
            await j([]); return
        if path == "/api/library" and method == "GET":
            items = rt["library_items"]
            await j({"items": items, "stats": library_stats(items), "root": rt["info"]["library"]["path"]}); return
        m = re.match(r"^/api/library/([^/]+)$", path)
        if m and method == "DELETE":
            await j({"ok": True}); return
        m = re.match(r"^/api/library/([^/]+)/(open|reveal)$", path)
        if m:
            await j({"ok": True}); return
        if path == "/api/library/rev":
            await j({"rev": len(rt["library_items"])}); return
        if path == "/api/fs" and method == "GET":
            p = (qs.get("path") or [""])[0]
            await j(virtual_fs(p, rt["library_items"])); return
        if path == "/api/fs" and method == "DELETE":
            await j({"ok": True}); return
        if path == "/api/fs/tree":
            await j({"tree": virtual_tree(rt["library_items"])}); return
        if path in ("/api/fs/rename", "/api/fs/move", "/api/fs/folder"):
            body = {}
            try:
                body = json.loads(request.post_data or "{}")
            except Exception:
                pass
            if path == "/api/fs/folder":
                name = body.get("name", "Новая папка")
                parent = body.get("parent", "")
                p = f"{parent}/{name}" if parent else name
                await j({"name": name, "path": p}); return
            if path == "/api/fs/move":
                await j({"moved": body.get("paths", [])}); return
            await j({"ok": True}); return
        if path == "/api/preview" and method == "POST":
            body = json.loads(request.post_data or "{}")
            requested = body.get("url", "")
            data = None
            for p in CATALOG["preview"].values():
                if p["url"] == requested:
                    data = dict(p)
                    break
            if data is None:
                data = dict(CATALOG["preview"]["other"])
                data["url"] = requested
            override = rt.get("_preview_thumb_override")
            if override:
                data = dict(data)
                data["thumbnail"] = f"/_fixture/{override}"
            await j(data); return
        # неизвестный путь — не должен встречаться; помогает найти дыры в контракте
        await j({"detail": f"стенд: не замокано {method} {path}"}, status=404)
    except Exception as exc:  # не роняем страницу — но помечаем как ошибку стенда
        await j({"detail": f"стенд: ошибка фикстуры: {exc}"}, status=500)


async def lib_router(route: Route, request: Request) -> None:
    url = urlparse(request.url).path
    is_audio = url.lower().endswith((".mp3", ".m4a", ".opus", ".ogg", ".wav", ".flac"))
    src = MEDIA_DIR / ("sample.mp3" if is_audio else "sample.mp4")
    await route.fulfill(path=str(src))


async def fixture_asset_router(route: Route, request: Request) -> None:
    name = urlparse(request.url).path.rsplit("/", 1)[-1]
    src = MEDIA_DIR / name
    if src.exists():
        await route.fulfill(path=str(src))
    else:
        await route.fulfill(status=404, body="")


EVENTSOURCE_SHIM = """
(() => {
  class FakeEventSource extends EventTarget {
    constructor(url) {
      super();
      this.url = url; this.readyState = 0; this.onopen = null; this.onerror = null; this.onmessage = null;
      queueMicrotask(() => {
        if (this.readyState === 2) return;
        this.readyState = 1;
        const ev = new Event('open');
        this.dispatchEvent(ev);
        if (this.onopen) this.onopen(ev);
      });
    }
    close() { this.readyState = 2; }
  }
  FakeEventSource.CONNECTING = 0; FakeEventSource.OPEN = 1; FakeEventSource.CLOSED = 2;
  window.EventSource = FakeEventSource;
  try {
    if ('serviceWorker' in navigator) {
      Object.defineProperty(navigator.serviceWorker, 'register', { value: () => Promise.resolve({ unregister: () => Promise.resolve(true) }) });
    }
  } catch (e) { /* */ }
  try { localStorage.clear(); } catch (e) { /* приватный режим */ }
  try { sessionStorage.clear(); } catch (e) { /* */ }
})();
"""


# ============================== состояния ==============================

Action = Callable[[Page, dict], Awaitable[None]]


async def a_noop(page: Page, rt: dict) -> None:
    return None


def a_seq(*actions: Action) -> Action:
    async def run(page: Page, rt: dict) -> None:
        for act in actions:
            await act(page, rt)
    return run


def a_fill_url(text: str) -> Action:
    async def run(page: Page, rt: dict) -> None:
        await page.fill("#url", text)
    return run


def a_click(selector: str, wait_for: Optional[str] = None, timeout: int = 4000) -> Action:
    async def run(page: Page, rt: dict) -> None:
        await page.click(selector)
        if wait_for:
            await page.wait_for_selector(wait_for, timeout=timeout, state="visible")
    return run


def a_wait(selector: str, timeout: int = 4000, state: str = "visible") -> Action:
    async def run(page: Page, rt: dict) -> None:
        await page.wait_for_selector(selector, timeout=timeout, state=state)
    return run


async def a_show_queue(page: Page, rt: dict) -> None:
    # секция очереди идёт под героем во весь экран — без прокрутки карточка задачи не попадает
    # в кадр даже на xl. Сам остров — фиксированный элемент, скролл его не двигает.
    await page.wait_for_selector("#jobs .job", timeout=6000)
    await page.evaluate("() => document.getElementById('queue')?.scrollIntoView({ block: 'start' })")
    await page.wait_for_timeout(60)


async def a_expand_island(page: Page, rt: dict) -> None:
    # Playwright.click() по этой хит-зоне в связке CDP→реальный Edge в этом окружении иногда
    # не долетает до обработчика (воспроизводимо и на #ctx, см. a_rename_folder_favorite) —
    # настоящий клик надёжнее диспетчить прямо в JS.
    await page.eval_on_selector("#island-hit", "el => el.click()")
    await page.wait_for_selector('.island[data-state="expanded"]', timeout=6000)


def a_pill(group_sel: str, value: str) -> Action:
    return a_click(f'{group_sel} [data-value="{value}"]')


def a_ready_preview(preview_key: str, thumb: Optional[str] = None) -> Action:
    async def run(page: Page, rt: dict) -> None:
        if thumb:
            rt["_preview_thumb_override"] = thumb
        url = CATALOG["preview"][preview_key]["url"]
        await page.fill("#url", url)
        await page.wait_for_selector(".preview:not(.loading)", timeout=4000)
        if thumb:
            await page.wait_for_function(
                "document.querySelector('.pv-media img')?.complete === true", timeout=4000,
            )
            await page.wait_for_timeout(60)
    return run


def a_trim(preview_key: str, start: str, end: str) -> Action:
    async def run(page: Page, rt: dict) -> None:
        await a_ready_preview(preview_key)(page, rt)
        await page.fill(".pv-start", start)
        await page.fill(".pv-end", end)
    return run


async def a_drag_files_over_window(page: Page, rt: dict) -> None:
    await page.evaluate(
        """() => {
            const dt = new DataTransfer();
            dt.items.add(new File([new Uint8Array([0])], 'demo.mp4', { type: 'video/mp4' }));
            const opts = { bubbles: true, cancelable: true, dataTransfer: dt };
            window.dispatchEvent(new DragEvent('dragenter', opts));
            window.dispatchEvent(new DragEvent('dragover', opts));
        }"""
    )
    await page.wait_for_selector("#dragveil:not([hidden])", timeout=6000)


def a_select_tile(n: int, modifier: Optional[str] = None) -> Action:
    async def run(page: Page, rt: dict) -> None:
        tiles = page.locator("#ex-files .tile")
        opts = {"modifiers": [modifier]} if modifier else {}
        await tiles.nth(n).click(**opts)
    return run


async def a_confirm_delete(page: Page, rt: dict) -> None:
    await a_select_tile(0)(page, rt)
    await page.keyboard.press("Delete")
    await page.wait_for_selector("#confirm-dialog[open]", timeout=6000)


async def a_rename_folder_favorite(page: Page, rt: dict) -> None:
    # клик по папке в explorer.js её открывает (навигация), а не выделяет — переименование
    # берём через контекстное меню, как в реальном использовании. Папка должна быть не корневой:
    # верхнеуровневые папки помечены "system" (нельзя переименовать) — таково поведение explorer.js.
    # Синтетический contextmenu через CDP-курсор в этом окружении ненадёжен — диспатчим DOM-событие
    # напрямую в то же место, что и настоящий правый клик.
    path = "YouTube/Избранное"
    sel = f'#ex-folders .folder[data-path="{path}"]'
    await page.wait_for_selector(sel, timeout=6000)
    await page.locator(sel).scroll_into_view_if_needed()
    await page.wait_for_timeout(80)
    dispatch_js = """el => {
        const r = el.getBoundingClientRect();
        const x = r.left + r.width / 2, y = r.top + r.height / 2;
        el.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: x, clientY: y }));
    }"""
    await page.eval_on_selector(sel, dispatch_js)
    try:
        await page.wait_for_selector("#ctx:not([hidden])", timeout=2000)
    except Exception:
        # под нагрузкой (--jobs > 1) первый диспатч изредка не успевает — пробуем ещё раз
        await page.eval_on_selector(sel, dispatch_js)
        await page.wait_for_selector("#ctx:not([hidden])", timeout=4000)
    await page.evaluate(
        """() => {
            const b = [...document.querySelectorAll('#ctx button')].find(x => x.textContent.includes('Переименовать'));
            if (b) b.click();
        }"""
    )
    await page.wait_for_selector(f'{sel} input.rename', timeout=6000)


async def a_freeze_player_media(page: Page, rt: dict) -> None:
    await page.wait_for_selector("#player[open]", timeout=6000)
    await page.evaluate(
        "() => document.querySelectorAll('#player video, #player audio').forEach(m => { m.pause(); try { m.currentTime = 0; } catch (e) {} })"
    )
    await page.wait_for_timeout(60)


async def a_move_dialog(page: Page, rt: dict) -> None:
    await a_select_tile(0)(page, rt)
    await a_select_tile(1, "Control")(page, rt)
    await page.click('#selbar [data-sel="move"]')
    await page.wait_for_selector("#folder-dialog[open]", timeout=6000)


async def a_toast_warn_empty_submit(page: Page, rt: dict) -> None:
    await page.click("#go")
    await page.wait_for_selector(".toast.warn", timeout=6000)


async def a_toast_ok_auto_dest(page: Page, rt: dict) -> None:
    await page.click("#dest-btn")
    await page.wait_for_selector("#folder-dialog[open]", timeout=6000)
    await page.click('#folder-tree [data-path=""]')
    await page.click("#folder-confirm")
    await page.wait_for_selector(".toast.ok", timeout=6000)


def rt_jobs(*keys: str):
    def setup(rt: dict) -> None:
        rt["jobs"] = [dict(CATALOG["jobs"][k]) for k in keys]
    return setup


def rt_library(with_items: bool = True):
    def setup(rt: dict) -> None:
        rt["library_items"] = [dict(i) for i in CATALOG["library_items"]] if with_items else []
    return setup


def rt_doctor(key: str):
    def setup(rt: dict) -> None:
        rt["doctor"] = CATALOG[key]
    return setup


def rt_combine(*fns):
    def setup(rt: dict) -> None:
        for fn in fns:
            fn(rt)
    return setup


@dataclass
class StateDef:
    id: str
    rt: Callable[[dict], None] = lambda rt: None
    query: dict = field(default_factory=dict)
    action: Action = a_noop
    note: str = ""
    expect_errors: bool = False  # состояние намеренно провоцирует ошибки сети (напр. offline)


STATES: list[StateDef] = [
    # ---------- главный экран ----------
    StateDef("idle"),
    StateDef("typing-youtube", action=a_fill_url(CATALOG["preview"]["youtube"]["url"])),
    StateDef("typing-tiktok", action=a_fill_url(CATALOG["preview"]["tiktok"]["url"])),
    StateDef("typing-instagram", action=a_fill_url(CATALOG["preview"]["instagram"]["url"])),
    StateDef("typing-other", action=a_fill_url(CATALOG["preview"]["other"]["url"])),
    StateDef("multi-links", action=a_fill_url(
        CATALOG["preview"]["youtube"]["url"] + "\n" + CATALOG["preview"]["tiktok"]["url"]
    )),
    StateDef("preview-landscape", action=a_ready_preview("youtube", thumb="thumb-landscape.png")),
    StateDef("preview-portrait", action=a_ready_preview("tiktok", thumb="thumb-portrait.png")),
    StateDef("preview-trim", action=a_trim("youtube", "1:00", "5:30")),
    StateDef("format-mp4", action=a_pill('[data-name="mode"]', "mp4")),
    StateDef("format-mp3", action=a_pill('[data-name="mode"]', "mp3")),
    StateDef("format-both", action=a_pill('[data-name="mode"]', "both")),
    StateDef("controls-open", action=a_click("#controls-toggle")),
    StateDef("dest-menu", action=a_click("#dest-btn", wait_for="#folder-dialog[open]")),
    StateDef("converter", action=a_click("#tab-file", wait_for='#panel-file:not([hidden])')),
    StateDef("converter-drag", action=a_seq(
        a_click("#tab-file", wait_for='#panel-file:not([hidden])'), a_drag_files_over_window,
    )),

    # ---------- загрузки ----------
    # секция очереди — под героем на всю высоту экрана, поэтому каждое job-* состояние
    # прокручивает её в кадр (a_show_queue), иначе виден только герой и пилюля Dynamic Island.
    StateDef("job-queued", rt=rt_jobs("queued"), action=a_show_queue),
    StateDef("job-downloading", rt=rt_jobs("downloading"), action=a_show_queue),
    StateDef("job-downloading-indet", rt=rt_jobs("downloading_indet"), action=a_show_queue),
    StateDef("job-converting", rt=rt_jobs("converting"), action=a_show_queue),
    StateDef("job-saving", rt=rt_jobs("saving"), action=a_show_queue),
    StateDef("job-waiting", rt=rt_jobs("waiting"), action=a_seq(a_show_queue, a_wait(".job-ask"))),
    StateDef("job-done", rt=rt_jobs("done"), action=a_show_queue),
    StateDef("job-error", rt=rt_jobs("error"), action=a_seq(a_show_queue, a_wait(".job-note"))),
    StateDef("job-cancelled", rt=rt_jobs("cancelled"), action=a_show_queue),
    StateDef("jobs-many", rt=rt_jobs("queued", "downloading", "converting", "waiting", "done"),
             action=a_seq(a_show_queue, a_expand_island)),

    # ---------- хранилище ----------
    # на корне всегда есть 5 папок-отделов по платформам (так у explorer.js даже при пустой
    # библиотеке) — по-настоящему пусто внутри конкретной папки типа.
    StateDef("library-empty", rt=rt_library(False), query={"folder": "YouTube/Видео"}, action=a_wait("#lib-empty:not([hidden])")),
    StateDef("library-grid", rt=rt_library(), query={"folder": "YouTube/Видео"}, action=a_wait(".tile")),
    StateDef("library-list", rt=rt_library(), query={"folder": "YouTube/Видео"},
             action=a_seq(a_wait(".tile"), a_pill(".view-toggle", "list"))),
    StateDef("library-search-none", rt=rt_library(), query={"folder": "YouTube/Видео"}, action=a_seq(
        a_wait(".tile"),
        lambda page, rt: page.fill("#lib-search", "zzz-нет-такого"),
        a_wait("#lib-empty:not([hidden])"),
    )),
    StateDef("library-selection", rt=rt_library(), query={"folder": "YouTube/Видео"}, action=a_seq(
        a_wait(".tile"), a_select_tile(0), a_select_tile(1, "Control"), a_wait("#selbar:not([hidden])"),
    )),
    StateDef("explorer-tree", rt=rt_library(), query={"folder": "YouTube"}, action=a_seq(
        a_wait('#ex-folders .folder[data-path="YouTube/Избранное"]'), a_rename_folder_favorite,
    )),

    # ---------- диалоги и системное ----------
    StateDef("player", rt=rt_library(), query={"folder": "YouTube/Видео", "player": "first"},
             action=a_freeze_player_media),
    StateDef("settings", action=a_click("#open-settings", wait_for="#settings[open]")),
    StateDef("health-ok", rt=rt_doctor("doctor_ok"), action=a_click("#open-health", wait_for="#checks .check")),
    StateDef("health-problems", rt=rt_doctor("doctor_problems"), action=a_click("#open-health", wait_for="#checks .check.fail")),
    StateDef("folder-dialog", rt=rt_library(), query={"folder": "YouTube/Видео"}, action=a_seq(
        a_wait(".tile"), a_move_dialog,
    )),
    StateDef("confirm", rt=rt_library(), query={"folder": "YouTube/Видео"}, action=a_seq(
        a_wait(".tile"), a_confirm_delete,
    )),
    StateDef("cheats", action=a_click("#open-cheats", wait_for="#cheats[open]")),
    StateDef("offline", rt=lambda rt: rt.update(down=True), action=a_wait("#offline:not([hidden])", timeout=6000),
             note="сеть намеренно оборвана — ошибки в консоли ожидаемы", expect_errors=True),

    # ---------- тосты ----------
    StateDef("toast-warn", action=a_toast_warn_empty_submit),
    StateDef("toast-ok", rt=rt_library(), action=a_toast_ok_auto_dest),
]

STATE_IDS = [s.id for s in STATES]


def match_states(patterns: list[str]) -> list[StateDef]:
    if not patterns or patterns == ["all"]:
        return list(STATES)
    chosen: list[StateDef] = []
    seen = set()
    for pat in patterns:
        matched = [s for s in STATES if fnmatch.fnmatch(s.id, pat)]
        if not matched:
            print(f"предупреждение: маска «{pat}» не совпала ни с одним состоянием", file=sys.stderr)
        for s in matched:
            if s.id not in seen:
                seen.add(s.id)
                chosen.append(s)
    return chosen


# ============================== проверки страницы ==============================

LAYOUT_JS = "() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth })"

TRUNCATION_JS = """
() => {
  const out = [];
  const els = document.querySelectorAll('body *');
  for (const el of els) {
    if (!el.textContent || !el.textContent.trim()) continue;
    if (el.closest('[hidden]')) continue;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const clipped = cs.textOverflow === 'ellipsis' || cs.whiteSpace === 'nowrap' || cs.overflow === 'hidden';
    if (clipped && el.scrollWidth > el.clientWidth + 1 && el.clientWidth > 0) {
      out.push({ tag: el.tagName.toLowerCase(), cls: (el.className || '').toString().slice(0, 60), text: el.textContent.trim().slice(0, 70) });
    }
    if (out.length >= 30) break;
  }
  return out;
}
"""

OVERLAP_JS = """
() => {
  const sel = 'button, a[href], input, textarea, select, [role="button"], [role="tab"], [role="radio"], [role="slider"]';
  const els = [...document.querySelectorAll(sel)];
  const out = [];
  const openDialog = document.querySelector('dialog[open]');
  for (const el of els) {
    if (el.closest('[hidden]') || el.disabled) continue;
    if (el.getAttribute('tabindex') === '-1') continue; // намеренно вне табуляции — обычно неактивно сейчас
    if (openDialog && !openDialog.contains(el)) continue; // фон намеренно инертен под модальным diалогом
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity || '1') === 0) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    if (cx < 0 || cy < 0 || cx > innerWidth || cy > innerHeight) continue;
    const top = document.elementFromPoint(cx, cy);
    if (top && el !== top && !el.contains(top) && !top.contains(el)) {
      const label = (n) => n ? `${n.tagName.toLowerCase()}${n.id ? '#' + n.id : ''}` : '?';
      out.push({ el: label(el), coveredBy: label(top) });
    }
    if (out.length >= 30) break;
  }
  return out;
}
"""


async def run_axe(page: Page) -> list[dict]:
    try:
        await page.add_script_tag(path=str(AXE_PATH))
        result = await page.evaluate(
            """async () => {
                const r = await axe.run(document, { resultTypes: ['violations'] });
                return r.violations
                  .filter(v => v.impact === 'serious' || v.impact === 'critical')
                  .map(v => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length }));
            }"""
        )
        return result or []
    except Exception as exc:
        return [{"id": "axe-error", "impact": "unknown", "help": str(exc), "nodes": 0}]


class Collector:
    def __init__(self) -> None:
        self.console: list[dict] = []
        self.errors: list[str] = []
        self.failed: list[dict] = []

    def reset(self) -> None:
        self.console.clear()
        self.errors.clear()
        self.failed.clear()


def attach_collectors(page: Page, coll: Collector) -> None:
    def on_console(msg):
        if msg.type in ("error", "warning"):
            coll.console.append({"type": msg.type, "text": msg.text})

    def on_pageerror(exc):
        coll.errors.append(str(exc))

    def on_requestfailed(req):
        failure = req.failure
        text = failure.get("errorText") if isinstance(failure, dict) else str(failure)
        if "net::ERR_ABORTED" in (text or "") and "/_fixture/" not in req.url and "/api/" not in req.url:
            return
        coll.failed.append({"url": req.url, "error": text})

    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    page.on("requestfailed", on_requestfailed)


# ============================== кадр одного состояния ==============================

async def build_url(base_url: str, query: dict, motion_off: bool) -> str:
    q = dict(query)
    if motion_off:
        q["motion"] = "0"
    qstr = urlencode(q)
    return f"{base_url}/{('?' + qstr) if qstr else ''}"


async def capture_state(
    page: Page, coll: Collector, state: StateDef, base_url: str, out_dir: Path,
    motion_off: bool, css_path: Optional[Path], no_axe: bool,
) -> dict:
    rt = default_rt()
    state.rt(rt)
    page._rig_rt["value"] = rt  # noqa: SLF001 — свой атрибут, не приватность Playwright

    coll.reset()
    url = await build_url(base_url, state.query, motion_off)
    entry: dict[str, Any] = {"state": state.id, "note": state.note}
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(160)
        if css_path is not None:
            await page.add_style_tag(path=str(css_path))
        try:
            await state.action(page, rt)
        except Exception as exc:
            entry["action_error"] = f"{exc.__class__.__name__}: {exc}"
        await page.wait_for_timeout(80)

        layout = await page.evaluate(LAYOUT_JS)
        truncated = await page.evaluate(TRUNCATION_JS)
        overlaps = await page.evaluate(OVERLAP_JS)
        axe_violations = [] if no_axe else await run_axe(page)

        shot_path = out_dir / f"{state.id}.png"
        await page.screenshot(path=str(shot_path))

        entry.update({
            "screenshot": str(shot_path.relative_to(SHOTS_ROOT)),
            "console": list(coll.console),
            "page_errors": list(coll.errors),
            "failed_requests": list(coll.failed),
            "scroll_width": layout["scrollWidth"],
            "client_width": layout["clientWidth"],
            "horizontal_overflow": layout["scrollWidth"] > layout["clientWidth"] + 1,
            "truncated_text": truncated,
            "overlaps": overlaps,
            "axe_violations": axe_violations,
        })
        unexpected_noise = not state.expect_errors and (
            entry["console"] or entry["page_errors"] or entry["failed_requests"]
        )
        entry["ok"] = not (
            unexpected_noise or entry["horizontal_overflow"] or axe_violations or entry.get("action_error")
        )
    except Exception as exc:
        entry["rig_error"] = f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc(limit=3)}"
        entry["ok"] = False
    return entry


async def capture_kbd(page: Page, coll: Collector, base_url: str, out_dir: Path, motion_off: bool, steps: int) -> list[dict]:
    rt = default_rt()
    page._rig_rt["value"] = rt  # noqa: SLF001
    coll.reset()
    frames = []
    url = await build_url(base_url, {}, motion_off)
    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
    await page.wait_for_timeout(220)
    for i in range(steps):
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(60)
        shot_path = out_dir / f"kbd-{i:02d}.png"
        await page.screenshot(path=str(shot_path))
        active = await page.evaluate(
            "() => { const el = document.activeElement; return el ? `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}` : null; }"
        )
        frames.append({"step": i, "screenshot": str(shot_path.relative_to(SHOTS_ROOT)), "focused": active})
    return frames


# ============================== контакт-лист ==============================

def make_contact_sheet(out_dir: Path, viewport: str, state_ids: list[str]) -> Optional[Path]:
    tiles = []
    thumb_w = 320
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    for sid in state_ids:
        p = out_dir / f"{sid}.png"
        if not p.exists():
            continue
        with Image.open(p) as im:
            ratio = thumb_w / im.width
            thumb = im.convert("RGB").resize((thumb_w, max(1, int(im.height * ratio))))
        tiles.append((sid, thumb))
    if not tiles:
        return None
    cols = 6
    cap_h = 22
    pad = 8
    row_h = max(t[1].height for t in tiles) + cap_h
    rows = (len(tiles) + cols - 1) // cols
    sheet_w = cols * (thumb_w + pad) + pad
    sheet_h = rows * (row_h + pad) + pad
    sheet = Image.new("RGB", (sheet_w, sheet_h), (10, 10, 14))
    draw = ImageDraw.Draw(sheet)
    for idx, (sid, thumb) in enumerate(tiles):
        r, c = divmod(idx, cols)
        x = pad + c * (thumb_w + pad)
        y = pad + r * (row_h + pad)
        sheet.paste(thumb, (x, y))
        draw.text((x, y + thumb.height + 3), f"{sid} [{viewport}]", fill=(220, 220, 230), font=font)
    out_path = out_dir / f"../contact-{viewport}.png"
    out_path = out_path.resolve()
    sheet.save(out_path)
    return out_path


# ============================== --diff ==============================

def diff_images(a_path: Path, b_path: Path, out_path: Path) -> float:
    with Image.open(a_path) as ia, Image.open(b_path) as ib:
        ia = ia.convert("RGB")
        ib = ib.convert("RGB")
        if ia.size != ib.size:
            ib = ib.resize(ia.size)
        diff = ImageChops.difference(ia, ib)
        bbox = diff.getbbox()
        hist = diff.convert("L").histogram()
        total = ia.width * ia.height
        changed = sum(hist[8:])  # порог ~3% яркости — не шум сглаживания
        pct = changed / total if total else 0.0
        if bbox:
            highlighted = ib.copy()
            mask = diff.convert("L").point(lambda v: 255 if v > 20 else 0)
            red = Image.new("RGB", ia.size, (255, 40, 40))
            highlighted.paste(red, mask=mask)
            highlighted.save(out_path)
        else:
            diff.save(out_path)
        return pct


# ============================== запуск ==============================

async def run_viewport(
    ctx_browser, base_url: str, label: str, viewport_id: str, states: list[StateDef],
    motion_off: bool, css_path: Optional[Path], kbd_steps: Optional[int], no_axe: bool,
    live: bool = False,
) -> dict:
    vp = VIEWPORTS[viewport_id]
    context: BrowserContext = await ctx_browser.new_context(
        viewport={"width": vp["width"], "height": vp["height"]},
        device_scale_factor=vp.get("device_scale_factor", 1),
        color_scheme="dark",
        service_workers="block",
        reduced_motion="reduce" if motion_off else "no-preference",
    )
    await context.add_init_script(EVENTSOURCE_SHIM)
    context.set_default_timeout(8000)
    page = await context.new_page()
    # Date.now()/new Date() заморожены — иначе "только что" / "сегодня" / "вчера" в интерфейсе
    # плыли бы от реальных часов и ломали детерминизм между прогонами (и в разные дни).
    # Таймеры (setTimeout/rAF) at set_fixed_time не трогает — polling и debounce идут как обычно.
    await page.clock.set_fixed_time(FIXED_NOW_MS)
    page._rig_rt = {"value": default_rt()}  # noqa: SLF001

    if not live:
        async def route_api(route: Route) -> None:
            await api_router(route, route.request, page._rig_rt["value"])  # noqa: SLF001

        await page.route("**/api/**", route_api)
        await page.route(re.compile(r".*/lib/.*"), lambda route: lib_router(route, route.request))
        await page.route(re.compile(r".*/_fixture/.*"), lambda route: fixture_asset_router(route, route.request))

    coll = Collector()
    attach_collectors(page, coll)

    out_dir = SHOTS_ROOT / label / viewport_id
    out_dir.mkdir(parents=True, exist_ok=True)

    frames: dict[str, Any] = {}
    for state in states:
        frames[state.id] = await capture_state(page, coll, state, base_url, out_dir, motion_off, css_path, no_axe)

    kbd_frames = None
    if kbd_steps:
        kbd_frames = await capture_kbd(page, coll, base_url, out_dir, motion_off, kbd_steps)

    make_contact_sheet(out_dir, viewport_id, [s.id for s in states])
    await context.close()
    return {"frames": frames, "kbd": kbd_frames}


async def run_diff(label: str, other: str, viewports: list[str], states: list[StateDef]) -> dict:
    result: dict[str, Any] = {}
    for vp in viewports:
        a_dir = SHOTS_ROOT / label / vp
        b_dir = SHOTS_ROOT / other / vp
        if not a_dir.exists() or not b_dir.exists():
            continue
        result[vp] = {}
        for state in states:
            a_png, b_png = a_dir / f"{state.id}.png", b_dir / f"{state.id}.png"
            if not a_png.exists() or not b_png.exists():
                continue
            diff_out = a_dir / f"diff-{other}-{state.id}.png"
            pct = diff_images(b_png, a_png, diff_out)
            result[vp][state.id] = {"pct_diff": round(pct, 5), "diff_image": str(diff_out.relative_to(SHOTS_ROOT))}
    return result


async def main_async(args: argparse.Namespace) -> int:
    states = match_states(args.states)
    if not states and not args.kbd:
        print("ошибка: ни одно состояние не выбрано (--states)", file=sys.stderr)
        return 2
    viewports = args.viewports
    for v in viewports:
        if v not in VIEWPORTS:
            print(f"ошибка: неизвестное окно «{v}» (доступны: {', '.join(VIEWPORTS)})", file=sys.stderr)
            return 2

    label_dir = SHOTS_ROOT / args.label
    label_dir.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    async with async_playwright() as p:
        try:
            browser = await asyncio.wait_for(p.chromium.connect_over_cdp(CDP_URL), timeout=8)
        except Exception as exc:
            print(f"ошибка: не подключился к Edge на {CDP_URL} — {exc}", file=sys.stderr)
            print("Подними Edge (скилл ui-rig) и повтори.", file=sys.stderr)
            return 3

        sem = asyncio.Semaphore(max(1, args.jobs))
        results: dict[str, Any] = {}

        async def worker(vp: str):
            async with sem:
                results[vp] = await run_viewport(
                    browser, args.base_url, args.label, vp, states,
                    motion_off=not args.motion, css_path=args.css, kbd_steps=args.kbd, no_axe=args.no_axe,
                    live=args.live,
                )

        await asyncio.gather(*(worker(vp) for vp in viewports))
        # соединение с общим Edge не закрываем — им пользуются другие агенты

    duration = time.monotonic() - started

    report: dict[str, Any] = {
        "label": args.label,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "command": "python3 tools/ui_rig.py " + " ".join(sys.argv[1:]),
        "base_url": args.base_url,
        "viewports": viewports,
        "states": [s.id for s in states],
        "duration_seconds": round(duration, 1),
        "live": args.live,
        "motion_frozen": not args.motion,
        "frames": {vp: results[vp]["frames"] for vp in viewports},
    }
    if args.kbd:
        report["kbd"] = {vp: results[vp]["kbd"] for vp in viewports}

    total = 0
    failed = 0
    for vp in viewports:
        for sid, entry in results[vp]["frames"].items():
            total += 1
            if not entry.get("ok", False):
                failed += 1
    report["summary"] = {"frames_total": total, "frames_with_issues": failed}

    if args.diff:
        report["diff"] = await run_diff(args.label, args.diff, viewports, states)

    report_path = label_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"кадров: {total}, с замечаниями: {failed}, время: {report['duration_seconds']}с")
    print(f"report: {report_path}")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="ui_rig.py",
        description="Детерминированный стенд интерфейса выдры (Edge/CDP + Playwright).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--label", default="adhoc", help="метка прогона — папка в .sprint/shots/")
    p.add_argument("--states", default="all",
                    help="состояния через запятую, маски вида job-* (по умолчанию all)")
    p.add_argument("--viewports", default=",".join(DEFAULT_VIEWPORTS),
                    help="окна через запятую: s,m,l,xl или all")
    p.add_argument("--css", default=None, type=Path, help="CSS-черновик, подмешать поверх страницы")
    p.add_argument("--diff", default=None, help="сравнить попиксельно с другой меткой")
    p.add_argument("--live", action="store_true",
                    help="без фикстур — реальный сервер (только безопасные состояния для чтения)")
    p.add_argument("--motion", action="store_true", help="не замораживать движение (по умолчанию ?motion=0)")
    p.add_argument("--kbd", type=int, nargs="?", const=16, default=None,
                    help="серия кадров обхода клавиатурой (Tab), число шагов (по умолчанию 16)")
    p.add_argument("--jobs", type=int, default=1, help="сколько окон снимать параллельно")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL, help="адрес сервера выдры (только чтение)")
    p.add_argument("--no-axe", action="store_true", help="пропустить axe (быстрее, для отладки стенда)")
    args = p.parse_args(argv)

    args.states = [s.strip() for s in args.states.split(",") if s.strip()]
    vp_arg = [v.strip() for v in args.viewports.split(",") if v.strip()]
    args.viewports = list(VIEWPORTS.keys()) if vp_arg == ["all"] else vp_arg

    if args.live:
        # без фикстур опасно гонять состояния, которые шлют изменяющие запросы (папки, удаление,
        # настройки, submit ссылок) на боевую библиотеку пользователя — оставляем только просмотр.
        safe = {"idle", "typing-youtube", "typing-tiktok", "typing-instagram", "typing-other",
                "multi-links", "library-grid", "library-empty", "library-search-none",
                "settings", "health-ok", "health-problems", "cheats"}
        args.states = [s for s in args.states if (s in safe or any(fnmatch.fnmatch(sid, s) for sid in safe))] or ["idle"]
        print("--live: фикстуры выключены, оставлены только состояния только для чтения:",
              ", ".join(args.states), file=sys.stderr)

    return args


def main() -> int:
    args = parse_args(sys.argv[1:])
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
