#!/usr/bin/env python3
"""Подробности axe (цели и сообщения) для выбранных состояний с черновиком CSS. Только для арт-директора."""
import asyncio, re, sys, json
from pathlib import Path
PROTO = Path(__file__).resolve().parent
ROOT = PROTO.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import ui_rig  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402
CSS = Path(sys.argv[1]); STATES = sys.argv[2].split(','); VP = sys.argv[3] if len(sys.argv) > 3 else 'l'; RULE = sys.argv[4] if len(sys.argv) > 4 else 'color-contrast'
async def main():
    vp = ui_rig.VIEWPORTS[VP]
    async with async_playwright() as p:
        b = await asyncio.wait_for(p.chromium.connect_over_cdp(ui_rig.CDP_URL), 8)
        ctx = await b.new_context(viewport={"width": vp["width"], "height": vp["height"]}, device_scale_factor=vp.get("device_scale_factor", 1), color_scheme="dark", service_workers="block", reduced_motion="reduce")
        await ctx.add_init_script(ui_rig.EVENTSOURCE_SHIM)
        await ctx.route(re.compile(r".*/static/cosmos\.js(\?.*)?$"), lambda r: r.fulfill(path=str(PROTO / "cosmos-proto.js"), content_type="text/javascript; charset=utf-8"))
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
        for st in ui_rig.match_states(STATES):
            rt = ui_rig.default_rt(); st.rt(rt); page._rig_rt["value"] = rt
            await page.goto(await ui_rig.build_url(ui_rig.DEFAULT_BASE_URL, st.query, True), wait_until="domcontentloaded")
            await page.wait_for_timeout(160)
            await page.add_style_tag(path=str(CSS))
            await st.action(page, rt)
            await page.wait_for_timeout(120)
            await page.add_script_tag(path=str(ui_rig.AXE_PATH))
            res = await page.evaluate("""async (rule) => { const r = await axe.run(document, { runOnly: [rule], resultTypes: ['violations'] });
              return r.violations.flatMap(v => v.nodes.map(n => ({ t: n.target.join(' '), m: (n.any[0]||n.all[0]||n.none[0]||{}).message || n.failureSummary }))); }""", RULE)
            print('==', st.id)
            for n in res[:12]: print('  ', n['t'][:90], '|', (n['m'] or '')[:170].replace('\n', ' '))
        await ctx.close()
asyncio.run(main())
