#!/usr/bin/env python3
"""Стенд с черновиком сцены: tools/ui_rig.py + подмена /static/cosmos.js на .sprint/proto/cosmos-proto.js
и раздача /static/scene/* из .sprint/proto/scene/. vydra/static не трогается.

Запуск (те же аргументы, что у ui_rig.py):
    uv run --with playwright --with pillow python .sprint/proto/rig.py --label s1-v1 --css .sprint/proto/v1.css \
        --states idle,typing-youtube,preview-landscape,job-downloading --viewports s,l --no-axe
PROTO_SHADER=0 — снять с живым cosmos.js (для сравнения).
PROTO_COSMOS=<путь> — подменить сцену другим файлом (например, эталоном .sprint/art/approved/cosmos-proto-approved-0937.js).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

PROTO = Path(__file__).resolve().parent
ROOT = PROTO.parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import ui_rig  # noqa: E402

_orig_run_viewport = ui_rig.run_viewport
USE_SHADER = os.environ.get("PROTO_SHADER", "1") != "0"
COSMOS = Path(os.environ.get("PROTO_COSMOS") or (PROTO / "cosmos-proto.js")).resolve()


async def _scene(route):
    name = route.request.url.split("/static/scene/", 1)[1].split("?", 1)[0]
    f = PROTO / "scene" / name
    if f.is_file():
        await route.fulfill(path=str(f))
    else:
        await route.fulfill(status=404, body="")


async def _cosmos(route):
    await route.fulfill(path=str(COSMOS), content_type="text/javascript; charset=utf-8")


class _Browser:
    def __init__(self, b):
        self._b = b

    def __getattr__(self, name):
        return getattr(self._b, name)

    async def new_context(self, **kw):
        ctx = await self._b.new_context(**kw)
        if USE_SHADER:
            await ctx.route(re.compile(r".*/static/cosmos\.js(\?.*)?$"), _cosmos)
        await ctx.route(re.compile(r".*/static/scene/.*"), _scene)
        return ctx


async def run_viewport(ctx_browser, *a, **kw):
    return await _orig_run_viewport(_Browser(ctx_browser), *a, **kw)


ui_rig.run_viewport = run_viewport

if __name__ == "__main__":
    raise SystemExit(ui_rig.main())
