#!/usr/bin/env python3
"""Раскадровка движения сцены S1b: время сцены задаётся вручную (window.__cosmos, ?scene-debug), кадры детерминированы.
Запуск: uv run --with playwright --with pillow python .sprint/proto/motion2.py [css] [label] [viewport]"""
import asyncio, re, sys
from pathlib import Path
PROTO = Path(__file__).resolve().parent
ROOT = PROTO.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import ui_rig  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

CSS = Path(sys.argv[1] if len(sys.argv) > 1 else PROTO / "v4-hybrid.css")
LABEL = sys.argv[2] if len(sys.argv) > 2 else "s1b-motion"
VP = sys.argv[3] if len(sys.argv) > 3 else "l"
OUT = ROOT / ".sprint" / "shots" / LABEL / VP
OUT.mkdir(parents=True, exist_ok=True)

SET = """([t, k, kind, energy, mx, my, mz]) => {
  const c = window.__cosmos; c.pause();
  c._t = t; c._last = 0; c.energyT = energy; c.energy = energy;
  if (k >= 0) { c.pulseAt = t - k; c.pulseKind = kind; } else { c.pulseAt = -1; c.swell = 0; c.line = 0; }
  c._pt = [mx, my]; c._ptS = [mx, my]; c._ptSeen = mz > 0; c._mz = mz;
  c._frame(performance.now());
}"""

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
        await page.goto(ui_rig.DEFAULT_BASE_URL + "/?scene-debug=1", wait_until="domcontentloaded")
        await page.add_style_tag(path=str(CSS))
        await page.wait_for_function("!!window.__cosmos", timeout=8000)
        await page.wait_for_timeout(1800)   # текстура плана, появление секций
        # стоп-кадры: [имя, t сцены, k после импульса (-1 — нет), вид (1 go / 0 done), энергия, курсор x, y (-1..1), сила линзы]
        shots = [
            ("m01-rest-t14", 14.0, -1, 1, 0, 0, 0, 0),
            ("m02-rest-t17_5", 17.5, -1, 1, 0, 0, 0, 0),
            ("m03-rest-t21", 21.0, -1, 1, 0, 0, 0, 0),
            ("m04-cursor-lens", 14.0, -1, 1, 0, 0.18, -0.02, 1),
            ("m05-go-0_15s", 14.0, 0.15, 1, 0, 0, 0, 0),
            ("m06-go-0_30s", 14.0, 0.30, 1, 0, 0, 0, 0),
            ("m07-go-0_55s", 14.0, 0.55, 1, 0, 0, 0, 0),
            ("m08-go-0_90s", 14.0, 0.90, 1, 0, 0, 0, 0),
            ("m09-go-2_0s", 14.0, 2.0, 1, 0, 0, 0, 0),
            ("m10-done-0_2s", 14.0, 0.20, 0, 0, 0, 0, 0),
            ("m11-energy", 14.0, -1, 1, 1, 0, 0, 0),
        ]
        # открытие сцены: экспозиция по возрасту кадра
        for age in (0.15, 0.45, 0.8, 1.4):
            await page.evaluate("(age) => { const c = window.__cosmos; c.pause(); c._age = age - 0.016; c._last = 0; c.pulseAt = -1; c.swell = 0; c.line = 0; c._frame(performance.now()); }", age)
            await page.wait_for_timeout(120)
            await page.screenshot(path=str(OUT / f"m00-open-{age:.2f}s.png"))
        await page.evaluate("() => { window.__cosmos._age = 10; }")
        for name, t, k, kind, en, mx, my, mz in shots:
            await page.evaluate(SET, [t, k, kind, en, mx, my, mz])
            await page.wait_for_timeout(120)
            await page.screenshot(path=str(OUT / f"{name}.png"))
        await ctx.close()
    print("кадры:", OUT)

asyncio.run(main())
