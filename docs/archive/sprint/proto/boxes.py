#!/usr/bin/env python3
"""Коробки ключевых элементов первого экрана с черновиком CSS во всех окнах (для таблицы DIRECTION)."""
import asyncio, re, sys, json
from pathlib import Path
PROTO = Path(__file__).resolve().parent
ROOT = PROTO.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import ui_rig  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402
CSS = Path(sys.argv[1]); STATE = sys.argv[2] if len(sys.argv) > 2 else 'idle'
SEL = {'topbar': '#topbar', 'kicker': '.hero .kicker', 'h1': '.hero h1', 'thin': 'h1 .thin', 'slab': 'h1 .slab', 'lede': '.lede', 'tabs': '.tabs',
       'portal': '.portal', 'go': '#go', 'paste': '#paste', 'dest-row': '.dest-row', 'formats': '.dest-row .formats', 'dest': '#dest-btn', 'more': '#controls-toggle',
       'readout': '#readout', 'preview': '.hero .preview', 'console': '.console'}
async def main():
    async with async_playwright() as p:
        b = await asyncio.wait_for(p.chromium.connect_over_cdp(ui_rig.CDP_URL), 8)
        for v in ('s', 'm', 'l', 'xl'):
            vp = ui_rig.VIEWPORTS[v]
            ctx = await b.new_context(viewport={"width": vp["width"], "height": vp["height"]}, device_scale_factor=vp.get("device_scale_factor", 1), color_scheme="dark", service_workers="block", reduced_motion="reduce")
            await ctx.add_init_script(ui_rig.EVENTSOURCE_SHIM)
            page = await ctx.new_page()
            rt = ui_rig.default_rt()
            await page.route("**/api/**", lambda r: ui_rig.api_router(r, r.request, rt))
            await page.route(re.compile(r".*/_fixture/.*"), lambda r: ui_rig.fixture_asset_router(r, r.request))
            st = ui_rig.match_states([STATE])[0]; st.rt(rt)
            await page.goto(ui_rig.DEFAULT_BASE_URL + "/?motion=0", wait_until="domcontentloaded")
            await page.wait_for_timeout(200)
            await page.add_style_tag(path=str(CSS))
            await st.action(page, rt)
            await page.wait_for_timeout(300)
            res = await page.evaluate("""(sel) => { const o = {}; for (const [k, s] of Object.entries(sel)) { const el = document.querySelector(s); if (!el) continue;
                const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); o[k] = [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height), cs.fontSize]; } return o; }""", SEL)
            print(v, STATE, json.dumps(res, ensure_ascii=False))
            await ctx.close()
asyncio.run(main())
