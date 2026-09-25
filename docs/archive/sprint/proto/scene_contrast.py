#!/usr/bin/env python3
"""Контраст текста героя поверх сцены (холст axe не видит): прячем текст, снимаем фон, считаем по коробкам элементов."""
import asyncio, re, sys, io
from pathlib import Path
PROTO = Path(__file__).resolve().parent
ROOT = PROTO.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import ui_rig  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402
from PIL import Image
import numpy as np
CSS = Path(sys.argv[1]); VPS = (sys.argv[2] if len(sys.argv) > 2 else 's,m,l,xl').split(','); STATE = sys.argv[3] if len(sys.argv) > 3 else 'idle'
SEL = {'kicker': '.hero .kicker', 'h1-thin': 'h1 .thin', 'lede': '.lede', 'tabs': '.tabs', 'dest-row': '.dest-row', 'controls-toggle': '#controls-toggle',
       'readout': '#readout', 'readout-dt': '#readout dt', 'hero-frame': '.hero-frame .hf-br',
       'row-quality': '#quality-tuner .row-label', 'switch-clip': '#clip .switch-text', 'switch-auto': '#autostart-wrap .switch-text',
       'switch-accept': '.extras .switch:last-child .switch-text', 'dz-sub': '.dz-sub', 'range-legend': '.range-legend'}
INK = {'ink': '#f4f4f6', 'ink-2': '#b4b6be', 'ink-3': '#8b8d96'}
def lin(c): c = c / 255; return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
def relL(rgb): return 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2])
def hexL(h): h = h.lstrip('#'); return relL((int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)))
async def main():
    async with async_playwright() as p:
        b = await asyncio.wait_for(p.chromium.connect_over_cdp(ui_rig.CDP_URL), 8)
        for v in VPS:
            vp = ui_rig.VIEWPORTS[v]; dpr = vp.get('device_scale_factor', 1)
            ctx = await b.new_context(viewport={"width": vp["width"], "height": vp["height"]}, device_scale_factor=dpr, color_scheme="dark", service_workers="block", reduced_motion="reduce")
            await ctx.add_init_script(ui_rig.EVENTSOURCE_SHIM)
            await ctx.route(re.compile(r".*/static/cosmos\.js(\?.*)?$"), lambda r: r.fulfill(path=str(Path(__import__("os").environ.get("PROTO_COSMOS") or (PROTO / "cosmos-proto.js"))), content_type="text/javascript; charset=utf-8"))
            async def scene(route):
                f = PROTO / "scene" / route.request.url.split("/static/scene/", 1)[1].split("?", 1)[0]
                await (route.fulfill(path=str(f)) if f.is_file() else route.fulfill(status=404, body=""))
            await ctx.route(re.compile(r".*/static/scene/.*"), scene)
            page = await ctx.new_page()
            rt = ui_rig.default_rt()
            await page.route("**/api/**", lambda r: ui_rig.api_router(r, r.request, rt))
            await page.route(re.compile(r".*/_fixture/.*"), lambda r: ui_rig.fixture_asset_router(r, r.request))
            st = ui_rig.match_states([STATE])[0]; st.rt(rt)
            await page.goto(ui_rig.DEFAULT_BASE_URL + "/?motion=0", wait_until="domcontentloaded")
            await page.wait_for_timeout(160)
            await page.add_style_tag(path=str(CSS))
            await st.action(page, rt)
            await page.wait_for_timeout(700)
            boxes = await page.evaluate("(sel) => Object.fromEntries(Object.entries(sel).map(([k, s]) => { const el = document.querySelector(s); if (!el) return [k, null]; const r = el.getBoundingClientRect(); return [k, [r.left, r.top, r.right, r.bottom]]; }))", SEL)
            await page.add_style_tag(content=".hero-copy, #readout, .hero-frame, .topbar { visibility: hidden !important; }")
            await page.wait_for_timeout(200)
            img = Image.open(io.BytesIO(await page.screenshot())).convert('RGB'); a = np.asarray(img).astype(float)
            print(f'== {v} ({STATE})')
            for k, bx in boxes.items():
                if not bx: continue
                x0, y0, x1, y1 = [int(round(c * dpr)) for c in bx]
                reg = a[max(0, y0):max(1, y1), max(0, x0):max(1, x1)].reshape(-1, 3)
                if reg.size == 0: continue
                Ls = np.array([relL(px) for px in reg[::max(1, len(reg) // 4000)]])
                l95 = np.percentile(Ls, 95); srgb = (l95 ** (1/2.2)) * 255
                res = ' '.join(f"{n}:{(hexL(h) + .05) / (l95 + .05):.1f}" for n, h in INK.items())
                print(f'  {k:16s} фон p95 ≈ {srgb:5.1f}/255  контраст {res}')
            await ctx.close()
asyncio.run(main())
