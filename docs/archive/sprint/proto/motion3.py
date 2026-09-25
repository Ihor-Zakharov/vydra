#!/usr/bin/env python3
"""Раскадровка движения сцены S1d: время, указатель, вдох и появление задаются вручную (window.__cosmos, ?scene-debug).
Запуск: uv run --with playwright --with pillow --with numpy python .sprint/proto/motion3.py [css] [label] [viewport]
Кадры: .sprint/shots/<label>/<окно>/; замеры (средняя |разница| в области сцены, 0–255) — в конце вывода."""
import asyncio, re, sys
from pathlib import Path
import numpy as np
from PIL import Image
PROTO = Path(__file__).resolve().parent
ROOT = PROTO.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import ui_rig  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

CSS = Path(sys.argv[1] if len(sys.argv) > 1 else PROTO / "v4-hybrid.css")
LABEL = sys.argv[2] if len(sys.argv) > 2 else "s1d-motion"
VP = sys.argv[3] if len(sys.argv) > 3 else "l"
OUT = ROOT / ".sprint" / "shots" / LABEL / VP
OUT.mkdir(parents=True, exist_ok=True)

# кадр: t сцены, указатель (сглаженный = целевой), вдох (swell), энергия, возраст (появление)
SET = """([t, px, py, swell, energy, age]) => {
  const c = window.__cosmos; c.pause();
  c._t = t; c._last = 0; c.energyT = energy; c.energy = energy;
  c._attack = 0; c.swell = swell;
  c._pt = [px, py]; c._ptS = [px, py];
  c._age = age - 0.016;
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
        await page.wait_for_timeout(2200)   # текстура плана, появление секций
        await page.evaluate("() => { window.__cosmos._far = 1; }")
        shots = []
        for i in range(0, 15):                     # покой: 7 с по 0,5 с
            shots.append((f"r{i:02d}-t{14 + i * 0.5:05.2f}", 14 + i * 0.5, 0, 0, 0, 0, 10))
        shots += [
            ("p1-left", 14.0, -1, 0, 0, 0, 10), ("p2-right", 14.0, 1, 0, 0, 0, 10),
            ("p3-up", 14.0, 0, 1, 0, 0, 10), ("p4-down", 14.0, 0, -1, 0, 0, 10),
            ("g1-breath-0_30", 14.0, 0, 0, 0.30, 0, 10), ("g2-breath-peak", 14.0, 0, 0, 0.89, 0, 10),
            ("g3-breath-0_40", 14.0, 0, 0, 0.40, 0, 10), ("e1-energy", 14.0, 0, 0, 0, 1, 10),
        ]
        for age in (0.15, 0.4, 0.7, 1.0, 1.3, 1.8):
            shots.append((f"o-open-{age:.2f}s", 14.0, 0, 0, 0, 0, age))
        for name, t, px, py, sw, en, age in shots:
            await page.evaluate(SET, [t, px, py, sw, en, age])
            await page.wait_for_timeout(110)
            await page.screenshot(path=str(OUT / f"{name}.png"))
        await ctx.close()

    def L(n):
        a = np.asarray(Image.open(OUT / f"{n}.png").convert("RGB"), dtype=np.float32) @ np.array([.2126, .7152, .0722], np.float32)
        h, w = a.shape
        return a[int(56 * h / vp["height"]):, int(0.60 * w):]
    def d(a, b):
        x = np.abs(L(a) - L(b)); return f"{x.mean():.2f} (p99 {np.percentile(x, 99):.0f})"
    rest = [f"r{i:02d}-t{14 + i * 0.5:05.2f}" for i in range(15)]
    steps = [np.abs(L(rest[i]) - L(rest[i + 1])).mean() for i in range(14)]
    print(f"покой: соседние кадры через 0,5 с — среднее {np.mean(steps):.2f}, макс {np.max(steps):.2f}; t14 → t21: {d(rest[0], rest[-1])}")
    print("параллакс ←/→ (полное отклонение):", d("p1-left", "p2-right"), "· ↑/↓:", d("p3-up", "p4-down"))
    print("вдох пик против покоя:", d(rest[0], "g2-breath-peak"), "· энергия:", d(rest[0], "e1-energy"))
    print("появление к 1,8 с против покоя:", d(rest[0], "o-open-1.80s"), "· 0,7 с:", d(rest[0], "o-open-0.70s"))
    print("кадры:", OUT)

asyncio.run(main())
