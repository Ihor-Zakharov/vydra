#!/usr/bin/env python3
"""Быстрый перебор токенов сцены на одном состоянии: черновик CSS + набор переопределений, кадр на каждое.
Сцена — .sprint/proto/cosmos-proto.js (как у rig.py), ?motion=0, reduced-motion (время постера t=14).
Запуск: uv run --with playwright python .sprint/proto/sweep.py <label> <viewports> <state> name='css' [name='css' ...]
Кадры: .sprint/shots/<label>/<окно>/<name>.png"""
import asyncio, re, sys
from pathlib import Path
PROTO = Path(__file__).resolve().parent
ROOT = PROTO.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import ui_rig  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

LABEL, VPS, STATE = sys.argv[1], sys.argv[2].split(","), sys.argv[3]
VARS = [a.split("=", 1) for a in sys.argv[4:]] or [["base", ""]]
BASE_CSS = PROTO / "v4-hybrid.css"


async def main():
    async with async_playwright() as p:
        b = await asyncio.wait_for(p.chromium.connect_over_cdp(ui_rig.CDP_URL), 8)
        for v in VPS:
            vp = ui_rig.VIEWPORTS[v]
            out = ROOT / ".sprint" / "shots" / LABEL / v
            out.mkdir(parents=True, exist_ok=True)
            for name, css in VARS:
                ctx = await b.new_context(viewport={"width": vp["width"], "height": vp["height"]},
                                          device_scale_factor=vp.get("device_scale_factor", 1),
                                          color_scheme="dark", service_workers="block", reduced_motion="reduce")
                await ctx.add_init_script(ui_rig.EVENTSOURCE_SHIM)
                await ctx.route(re.compile(r".*/static/cosmos\.js(\?.*)?$"),
                                lambda r: r.fulfill(path=str(PROTO / "cosmos-proto.js"), content_type="text/javascript; charset=utf-8"))

                async def scene(route):
                    f = PROTO / "scene" / route.request.url.split("/static/scene/", 1)[1].split("?", 1)[0]
                    await (route.fulfill(path=str(f)) if f.is_file() else route.fulfill(status=404, body=""))
                await ctx.route(re.compile(r".*/static/scene/.*"), scene)
                page = await ctx.new_page()
                await page.clock.set_fixed_time(getattr(ui_rig, "FIXED_NOW_MS", None) or ui_rig.FIXED_NOW)
                page._rig_rt = {"value": ui_rig.default_rt()}
                await page.route("**/api/**", lambda r: ui_rig.api_router(r, r.request, page._rig_rt["value"]))
                await page.route(re.compile(r".*/lib/.*"), lambda r: ui_rig.lib_router(r, r.request))
                await page.route(re.compile(r".*/_fixture/.*"), lambda r: ui_rig.fixture_asset_router(r, r.request))
                st = ui_rig.match_states([STATE])[0]
                rt = ui_rig.default_rt(); st.rt(rt); page._rig_rt["value"] = rt
                await page.goto(await ui_rig.build_url(ui_rig.DEFAULT_BASE_URL, st.query, True), wait_until="domcontentloaded")
                await page.wait_for_timeout(160)
                await page.add_style_tag(path=str(BASE_CSS))
                if css:
                    await page.add_style_tag(content=css)
                await st.action(page, rt)
                await page.wait_for_timeout(1400)
                await page.screenshot(path=str(out / f"{name}.png"))
                await ctx.close()
            print("кадры:", out)

asyncio.run(main())
