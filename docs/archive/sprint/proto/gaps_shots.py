#!/usr/bin/env python3
"""Кадры для пробелов DIRECTION (S3): .art в превью/сетке/списке/плеере, кадр задачи с перфорацией,
тег в настройках, строки проверки — на живой странице, фикстурах стенда и черновике CSS.

    uv run --with playwright --with pillow python .sprint/proto/gaps_shots.py <метка> <css|-> <окна> [имена через запятую]

Кадры: .sprint/shots/<метка>/<окно>/<имя>.png (вырезка по элементу), рядом <имя>-full.png (всё окно).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

PROTO = Path(__file__).resolve().parent
ROOT = PROTO.parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import ui_rig  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

THUMB = ROOT / "tools" / "rig_fixtures" / "media" / "thumb-landscape.png"


def st(state_id: str):
    return ui_rig.match_states([state_id])[0]


async def a_search_everywhere(page, rt):
    await page.wait_for_selector(".tile", timeout=6000)
    await page.fill("#lib-search", "а")
    await page.wait_for_timeout(300)
    if await page.get_attribute("#search-scope", "aria-pressed") != "true":
        await page.click("#search-scope")
    await page.wait_for_timeout(500)


async def a_list(page, rt):
    await ui_rig.a_pill(".view-toggle", "list")(page, rt)
    await page.wait_for_timeout(300)


def rt_settings(key):
    def setup(rt):
        rt["settings"] = ui_rig.CATALOG[key]
    return setup


def rt_cover_jobs(*keys):
    def setup(rt):
        rt["jobs"] = [dict(ui_rig.CATALOG["jobs"][k], thumb=True) for k in keys]
    return setup


async def a_settings_with_youtube(page, rt):
    await page.fill("#url", ui_rig.CATALOG["preview"]["youtube"]["url"])
    await page.wait_for_timeout(600)
    await page.click("#open-settings")
    await page.wait_for_selector("#settings[open]", timeout=4000)
    await page.wait_for_timeout(400)


# имя: (состояние стенда | None, доп. rt, query, доп. действие, селектор вырезки, поле, прокрутка к селектору)
SHOTS = {
    "art-preview": (None, None, {}, ui_rig.a_ready_preview("youtube"), ".hero .preview", 12, None),
    "art-preview-portrait": (None, None, {}, ui_rig.a_ready_preview("tiktok"), ".hero .preview", 12, None),
    "art-grid": ("library-grid", None, {}, a_search_everywhere, "#ex-files", 12, "#ex-files"),
    "art-list": ("library-grid", None, {}, [a_search_everywhere, a_list], "#ex-files", 12, "#ex-files"),
    "art-audio": (None, ui_rig.rt_library(), {"folder": "YouTube/Аудио", "player": "first"}, ui_rig.a_freeze_player_media,
                  "#player .player-box", 0, None),
    "job-downloading": ("job-downloading", None, {}, None, "#jobs .job", 10, None),
    "job-done": ("job-done", None, {}, None, "#jobs .job", 10, None),
    "job-queued": ("job-queued", None, {}, None, "#jobs .job", 10, None),
    "job-cancelled": ("job-cancelled", None, {}, None, "#jobs .job", 10, None),
    "job-cover": ("job-downloading", rt_cover_jobs("downloading", "done"), {}, None, "#jobs", 10, None),
    "settings-default": ("settings", None, {}, None, "#settings .drawer-box", 0, None),
    "settings-custom": ("settings", rt_settings("settings_custom"), {}, None, "#settings .drawer-box", 0, None),
    "settings-yt": (None, rt_settings("settings_custom"), {}, a_settings_with_youtube, "#settings .card-block", 12, None),
    "cookies-default": ("settings", None, {}, None, "#cookies-block", 12, "#cookies-block"),
    "cookies-custom": ("settings", rt_settings("settings_custom"), {}, None, "#cookies-block", 12, "#cookies-block"),
    "health-ok": ("health-ok", None, {}, None, "#health .drawer-box", 0, None),
    "health-problems": ("health-problems", None, {}, None, "#health .drawer-box", 0, None),
    "health-checking": ("health-ok", None, {}, "HANG_DOCTOR", "#health .drawer-box", 0, None),
}


# GAPS_MEASURE=1 — вместо кадров печатает коробки элементов (x, y, ш, в) для таблиц DIRECTION
MEASURE = {
    "art-grid": {"tile-media": ".explorer .tile .tile-media", "art-kind": ".tile .art-kind"},
    "art-list": {"list-media": ".explorer .tile .tile-media"},
    "art-preview": {"pv-media": ".hero .pv-media", "art-kind": ".pv-media .art-kind", "pv-dur": ".pv-dur"},
    "art-audio": {"stage": ".player-stage .audio-view", "kind": ".player-stage .art-kind", "eq": ".player-stage .eq", "audio": ".player-stage audio"},
    "job-cover": {"frame": "#jobs .job-frame", "thumb": "#jobs .job-thumb", "badge": "#jobs .job-badge"},
    "settings-default": {"h4": "#settings .card-block h4", "lib-tag": "#lib-tag", "cookie-tag": "#cookie-tag"},
    "cookies-custom": {"h4": "#cookies-block h4", "cookie-tag": "#cookie-tag"},
    "health-problems": {"check": "#checks .check", "title": "#checks .check-title", "detail": "#checks .check-detail",
                        "hint": "#checks .check-hint", "btn": "#checks .check .btn", "icon": "#checks .check-icon",
                        "chips": ".sum-chip", "acts": ".health-actions .btn"},
    "health-ok": {"check": "#checks .check"},
    "health-checking": {"skel": "#checks .check.skeleton"},
}
MEASURE_JS = """(sels) => { const o = {}; for (const [k, s] of Object.entries(sels)) {
  o[k] = [...document.querySelectorAll(s)].slice(0, 6).map(e => { const r = e.getBoundingClientRect();
    return [r.left, r.top, r.width, r.height].map(v => Math.round(v * 10) / 10); }); } return o; }"""


async def shoot(browser, vp_name: str, name: str, css: Path | None, out: Path):
    state_id, extra_rt, query, extra, clip_sel, pad, scroll_sel = SHOTS[name]
    vp = ui_rig.VIEWPORTS[vp_name]
    ctx = await browser.new_context(viewport={"width": vp["width"], "height": vp["height"]},
                                    device_scale_factor=vp.get("device_scale_factor", 1), color_scheme="dark",
                                    service_workers="block", reduced_motion="reduce")
    await ctx.add_init_script(ui_rig.EVENTSOURCE_SHIM)
    page = await ctx.new_page()
    rt = ui_rig.default_rt()
    await page.route("**/api/**", lambda r: ui_rig.api_router(r, r.request, rt))
    await page.route(re.compile(r".*/_fixture/.*"), lambda r: ui_rig.fixture_asset_router(r, r.request))
    await page.route(re.compile(r".*/api/jobs/[^/]+/thumbnail$"), lambda r: r.fulfill(path=str(THUMB)))
    if extra == "NO_THUMB":
        await page.route(re.compile(r".*/_fixture/thumb-.*"), lambda r: r.fulfill(status=404, body=""))
    if extra == "HANG_DOCTOR":
        async def hang(route):
            await asyncio.sleep(30)
        await page.route(re.compile(r".*/api/doctor$"), hang)
    state = st(state_id) if state_id else None
    if state and state.rt:
        state.rt(rt)
    if extra_rt:
        extra_rt(rt)
    q = dict(state.query if state else {})
    q.update(query)
    q["motion"] = "0"
    url = ui_rig.DEFAULT_BASE_URL + "/?" + "&".join(f"{k}={v}" for k, v in q.items())
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(250)
    if css:
        await page.add_style_tag(path=str(css))
    if extra == "HANG_DOCTOR":
        await page.click("#open-health")
        await page.wait_for_selector("#health[open] .check.skeleton", timeout=6000)
    elif state:
        await state.action(page, rt)
    if callable(extra):
        await extra(page, rt)
    elif isinstance(extra, list):
        for fn in extra:
            await fn(page, rt)
    if scroll_sel:
        await page.evaluate("(s) => document.querySelector(s)?.scrollIntoView({ block: 'start' })", scroll_sel)
        if not scroll_sel.startswith("#cookies"):
            await page.evaluate("() => window.scrollBy(0, -(56 + 24))")
    await page.wait_for_timeout(700)
    if os.environ.get("GAPS_OVERFLOW"):
        res = await page.evaluate("""() => { const sc = document.querySelector('dialog[open] .drawer-scroll'); if (!sc) return null;
          const out = { scroll: [sc.clientWidth, sc.scrollWidth], blocks: [...sc.children].map(c => [c.className, Math.round(c.getBoundingClientRect().width)]) };
          const wide = []; sc.querySelectorAll('*').forEach(e => { const r = e.getBoundingClientRect(); const pr = sc.getBoundingClientRect();
            if (r.right > pr.right - 24 + 0.5 && r.width > 0) wide.push([e.tagName + '.' + (e.className && e.className.baseVal === undefined ? e.className : ''), Math.round(r.left - pr.left), Math.round(r.width)]); });
          out.over = wide.slice(0, 12);
          const probe = []; sc.querySelectorAll('.card-block *').forEach(e => { const w = e.scrollWidth, cw = e.clientWidth;
            const cs = getComputedStyle(e); if (e.getBoundingClientRect().width > 300 || w > cw + 1) probe.push([e.tagName, (typeof e.className === 'string' ? e.className : ''), e.id, Math.round(e.getBoundingClientRect().width), w, cw, cs.display, cs.whiteSpace]); });
          out.probe = probe.slice(0, 30);
          const mc = []; sc.querySelectorAll('.card-block, .card-block > *, .card-block > * > *, .tree li, .tree li > *').forEach(e => { const old = e.style.width; e.style.width = 'min-content';
            const w = e.getBoundingClientRect().width; e.style.width = old; if (w > 200) mc.push([e.tagName, (typeof e.className === 'string' ? e.className : ''), e.id, Math.round(w), (e.textContent || '').trim().slice(0, 40)]); });
          out.mc = mc; return out; }""")
        print(vp_name, name, json.dumps(res, ensure_ascii=False), flush=True)
        await ctx.close()
        return
    if os.environ.get("GAPS_MEASURE"):
        print(vp_name, name, json.dumps(await page.evaluate(MEASURE_JS, MEASURE.get(name, {})), ensure_ascii=False), flush=True)
        await ctx.close()
        return
    d = out / vp_name
    d.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(d / f"{name}-full.png"))
    box = await page.evaluate("(s) => { const el = document.querySelector(s); if (!el) return null; const r = el.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }", clip_sel)
    if box:
        x, y, w, h = box
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(vp["width"], x + w + pad), min(vp["height"], y + h + pad)
        await page.screenshot(path=str(d / f"{name}.png"), clip={"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0})
    print(vp_name, name, "box", [round(v) for v in box] if box else None, flush=True)
    await ctx.close()


async def main():
    label, css_arg, vps = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
    names = sys.argv[4].split(",") if len(sys.argv) > 4 else list(SHOTS)
    css = None if css_arg == "-" else Path(css_arg)
    out = ROOT / ".sprint" / "shots" / label
    async with async_playwright() as p:
        browser = await asyncio.wait_for(p.chromium.connect_over_cdp(ui_rig.CDP_URL), 8)
        for vp in vps:
            for n in names:
                try:
                    await asyncio.wait_for(shoot(browser, vp, n, css, out), 60)
                except Exception as exc:  # noqa: BLE001
                    print(vp, n, "FAIL", type(exc).__name__, str(exc)[:200], flush=True)


asyncio.run(main())
