#!/usr/bin/env python3
"""Стенд + замер «главный экран без прокрутки» (арт-директор, 2026-09-25).

Запуск (аргументы — как у tools/ui_rig.py):
  uv run --with playwright --with pillow python .sprint/proto/noscroll_rig.py --label ns-1 \
      --states idle,typing-youtube,preview-landscape,preview-trim,controls-open,multi-links,converter \
      --viewports s,l --css .sprint/proto/noscroll.css --no-axe
После каждого кадра меряет: scrollHeight документа против innerHeight, коробки поля, «Скачать», ряда форматов,
превью, «Ещё», зоны конвертера и самого нижнего видимого элемента главного экрана. Пишет в report.json → frames[*].measure
и печатает таблицу. Ноль прокрутки: scrollHeight ≤ innerHeight.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import ui_rig  # noqa: E402

MEASURE_JS = r"""
() => {
  const se = document.scrollingElement || document.documentElement;
  const vis = (e) => { if (!e) return false; const cs = getComputedStyle(e); if (cs.visibility === 'hidden' || cs.display === 'none') return false;
    const b = e.getBoundingClientRect(); return b.width > 0 && b.height > 0; };
  const box = (sel) => { const e = document.querySelector(sel); if (!vis(e)) return null; const b = e.getBoundingClientRect();
    return [Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)]; };
  let low = null;
  const home = document.getElementById('screen-home');
  if (home) for (const e of home.querySelectorAll('*')) {
    if (!vis(e)) continue; const b = e.getBoundingClientRect();
    if (e.closest('.readout, .hero-frame')) continue;
    if (!low || b.bottom > low.bottom) low = { bottom: Math.round(b.bottom), el: e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\s+/).join('.') : '') };
  }
  return {
    scrollHeight: se.scrollHeight, innerHeight: innerHeight, scrollY: Math.round(scrollY), overflowY: getComputedStyle(se).overflowY,
    kicker: box('.hero .kicker'), h1: box('.hero h1'), lede: box('.hero .lede'), tabs: box('.console .tabs'),
    field: box('.portal'), go: box('#go'), row: box('.dest-row'), more: box('#controls-toggle'),
    preview: box('#preview-slot > *'), controls: box('#controls .controls-inner'), dropzone: box('#dropzone'), lowest: low,
  };
}
"""

_orig = ui_rig.capture_state


async def capture_state(page, coll, state, *a, **kw):
    entry = await _orig(page, coll, state, *a, **kw)
    try:
        entry["measure"] = await page.evaluate(MEASURE_JS)
    except Exception as exc:  # noqa: BLE001
        entry["measure"] = {"error": str(exc)}
    return entry


ui_rig.capture_state = capture_state


def main() -> int:
    args = ui_rig.parse_args(sys.argv[1:])
    import asyncio
    rc = asyncio.run(ui_rig.main_async(args))
    rep = ROOT / ".sprint" / "shots" / args.label / "report.json"
    if rep.is_file():
        data = json.loads(rep.read_text())
        vps = data.get("frames") or {}
        print(f"\n{'окно':4} {'состояние':20} {'scrollH':>7} {'innerH':>6} {'ок':>3}  поле(y,h)  «Скачать»низ  ряд низ  самый нижний")
        for vp, frames in vps.items():
            for sid, fr in frames.items():
                m = fr.get("measure") or {}
                if "scrollHeight" not in m:
                    print(vp, sid, m)
                    continue
                ok = "да" if m["scrollHeight"] <= m["innerHeight"] else "НЕТ"
                f = m.get("field"); g = m.get("go"); r = m.get("row"); low = m.get("lowest") or {}
                print(f"{vp:4} {sid:20} {m['scrollHeight']:7} {m['innerHeight']:6} {ok:>3}  "
                      f"{(str(f[1]) + ',' + str(f[3])) if f else '—':>9}  {(g[1] + g[3]) if g else '—':>11}  "
                      f"{(r[1] + r[3]) if r else '—':>7}  {low.get('bottom')} {low.get('el', '')[:60]}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
