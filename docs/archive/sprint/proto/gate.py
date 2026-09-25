#!/usr/bin/env python3
"""Ворота сцены: кадр idle (стенд, ?motion=0) против эталона 09:37 (.sprint/art/approved/hybrid-approved-idle-<окно>.png).

Запуск: uv run --with pillow --with numpy python .sprint/proto/gate.py <метка> [окна=s,m,l]
Область сцены — правее колонки текста (x ≥ 0,60 ширины), ниже панели (y ≥ 56) — там, где форма и текст не меняются вместе со сценой.
Печатает по окну: mean|d| (0–255, яркость), p99, долю пикселей с |d| > 8, и то же по зонам: «небо» (выше линии горизонта − 1,6 R),
«кольцо» (круг 1,6 R вокруг центра), «океан» (ниже линии горизонта). Порог ворот без намеренных добавок: mean|d| ≲ 3.
Кадр разницы ×4 — рядом с кадром: <метка>/<окно>/gate-diff.png.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
REF = ROOT / ".sprint" / "art" / "approved"
VP = {"s": (1366, 657), "m": (1536, 730), "l": (1920, 960)}


def layout(vw: float, vh: float):
    hero_h = max(674, 0.94 * vh)
    k = min(0.14, max(0.09, 0.115 * vw / 1440))
    return 0.70 * vw, 68 + hero_h / 2, k * hero_h


def lum(p: Path, size):
    im = Image.open(p).convert("RGB")
    if im.size != size:
        im = im.resize(size, Image.BILINEAR)
    a = np.asarray(im, dtype=np.float32)
    return a @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32), a


def main() -> int:
    label = sys.argv[1]
    vps = (sys.argv[2] if len(sys.argv) > 2 else "s,m,l").split(",")
    rc = 0
    for v in vps:
        size = VP[v]
        new = ROOT / ".sprint" / "shots" / label / v / "idle.png"
        ref = REF / f"hybrid-approved-idle-{v}.png"
        if not new.is_file():
            print(f"{v}: нет кадра {new}")
            rc = 1
            continue
        # кадры m сняты с dpr 1.25 — сравниваем в CSS px
        a, _ = lum(ref, size)
        b, _ = lum(new, size)
        d = np.abs(a - b)
        W, H = size
        cx, cy, R = layout(W, H)
        yy, xx = np.mgrid[0:H, 0:W]
        region = (xx >= 0.60 * W) & (yy >= 56)
        horizon = cy + 1.45 * R
        zones = {
            "сцена": region,
            "небо": region & (yy < cy - 1.6 * R),
            "кольцо": region & ((xx - cx) ** 2 + (yy - cy) ** 2 < (1.6 * R) ** 2),
            "океан": region & (yy > horizon),
        }
        out = []
        for name, m in zones.items():
            if m.sum() == 0:
                continue
            dz = d[m]
            out.append(f"{name} {dz.mean():.2f} (p99 {np.percentile(dz, 99):.0f}, >8: {100 * (dz > 8).mean():.1f}%)")
        print(f"{v}: " + " · ".join(out))
        vis = np.clip(d * 4, 0, 255).astype(np.uint8)
        Image.fromarray(vis).save(new.parent / "gate-diff.png")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
