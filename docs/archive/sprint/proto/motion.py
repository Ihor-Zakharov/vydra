#!/usr/bin/env python3
"""Серия кадров сцены с включённым движением: дыхание/нити, линза курсора, импульс «Скачать», оттенок платформы.
Запуск: uv run --with playwright --with pillow python .sprint/proto/motion.py [css] [label] [viewport]"""
import asyncio, re, sys
from pathlib import Path
PROTO = Path(__file__).resolve().parent
ROOT = PROTO.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import ui_rig  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

CSS = Path(sys.argv[1] if len(sys.argv) > 1 else PROTO / "v1-singularity.css")
LABEL = sys.argv[2] if len(sys.argv) > 2 else "s1-motion"
VP = sys.argv[3] if len(sys.argv) > 3 else "l"
OUT = ROOT / ".sprint" / "shots" / LABEL / VP
OUT.mkdir(parents=True, exist_ok=True)

async def main():
    vp = ui_rig.VIEWPORTS[VP]
    async with async_playwright() as p:
        b = await asyncio.wait_for(p.chromium.connect_over_cdp(ui_rig.CDP_URL), 8)
        ctx = await b.new_context(viewport={"width": vp["width"], "height": vp["height"]}, device_scale_factor=vp.get("device_scale_factor", 1),
                                  color_scheme="dark", service_workers="block", reduced_motion="no-preference")
        await ctx.add_init_script(ui_rig.EVENTSOURCE_SHIM)
        await ctx.route(re.compile(r".*/static/cosmos\.js(\?.*)?$"), lambda r: r.fulfill(path=str(PROTO / "cosmos-proto.js"), content_type="text/javascript; charset=utf-8"))
        async def scene(route):
            f = PROTO / "scene" / route.request.url.split("/static/scene/", 1)[1].split("?", 1)[0]
            await (route.fulfill(path=str(f)) if f.is_file() else route.fulfill(status=404, body=""))
        await ctx.route(re.compile(r".*/static/scene/.*"), scene)
        page = await ctx.new_page()
        rt = ui_rig.default_rt()
        await page.route("**/api/**", lambda r: ui_rig.api_router(r, r.request, rt))
        await page.route(re.compile(r".*/_fixture/.*"), lambda r: ui_rig.fixture_asset_router(r, r.request))
        await page.goto(ui_rig.DEFAULT_BASE_URL + "/", wait_until="domcontentloaded")
        await page.add_style_tag(path=str(CSS))
        await page.mouse.move(vp["width"] * 0.5, vp["height"] * 0.45)
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(OUT / "m01-t0.png"))
        await page.wait_for_timeout(2500)
        await page.screenshot(path=str(OUT / "m02-t2.5s.png"))
        # линза курсора у кольца слева
        await page.mouse.move(vp["width"] * 0.12, vp["height"] * 0.42, steps=12)
        await page.wait_for_timeout(900)
        await page.screenshot(path=str(OUT / "m03-cursor-lens.png"))
        await page.mouse.move(vp["width"] * 0.5, vp["height"] * 0.45, steps=6)
        await page.wait_for_timeout(700)
        # импульс «Скачать»
        await page.evaluate("() => { const g = document.getElementById('go'); g.classList.remove('fire'); void g.offsetWidth; g.classList.add('fire'); }")
        await page.wait_for_timeout(140)
        await page.screenshot(path=str(OUT / "m04-pulse-140ms.png"))
        await page.wait_for_timeout(260)
        await page.screenshot(path=str(OUT / "m05-pulse-400ms.png"))
        await page.wait_for_timeout(1200)
        # оттенок платформы
        for key, name in (("youtube", "m06-tint-youtube"), ("tiktok", "m07-tint-tiktok"), ("instagram", "m08-tint-instagram")):
            await page.fill("#url", ui_rig.CATALOG["preview"][key]["url"])
            await page.wait_for_timeout(1600)
            await page.screenshot(path=str(OUT / f"{name}.png"))
        await ctx.close()
    print("кадры:", OUT)

asyncio.run(main())
