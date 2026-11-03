#!/usr/bin/env python3
# Copyright (C) 2026 cyberyak
# SPDX-License-Identifier: GPL-3.0-only
"""Kaleidoscope reference model for a Videomancer program (pre-VHDL).

Emulates the hardware constraints rather than doing ideal image math:
  * no frame buffer: the source is a coarse "tile store" captured from a
    window of the input (Y 6-bit at STORE_W x STORE_H, U/V 4-bit at half
    resolution in both axes, i.e. 4:2:0) -- 28 of the 32 EBR on the HX4K;
  * every output pixel maps to a store address through rotate/zoom (a
    per-pixel / per-line DDA in hardware: adders only) and a mirror fold.

Fold modes:
  radial  N-wedge mandala (polar: CORDIC atan2/rotate in hardware)
  p4m     square kaleidoscope, *442 group (two triangle waves + swap)
  tri     classic 3-mirror toy kaleidoscope, *333 group (bounded reflections)
  p6m     30-60-90 kaleidoscope, *632 group (tri + one more mirror)

Usage: kaleido_model.py INPUT.png OUTDIR
"""
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

STORE_W, STORE_H = 128, 112  # Y grid; chroma grid is 64 x 56
OUT_W, OUT_H = 480, 270      # preview size (1080p / 4, like the image tester)


def rgb_to_yuv(rgb):
    r, g, b = (rgb[..., i] for i in range(3))
    y = 0.299 * r + 0.587 * g + 0.114 * b
    return y, (b - y) * 0.564, (r - y) * 0.713


def yuv_to_rgb(y, u, v):
    r = y + 1.403 * v
    g = y - 0.344 * u - 0.714 * v
    b = y + 1.773 * u
    return np.clip(np.stack([r, g, b], -1), 0, 1)


def quant(x, bits, lo, hi):
    n = (1 << bits) - 1
    return np.round((np.clip(x, lo, hi) - lo) / (hi - lo) * n) / n * (hi - lo) + lo


def capture_store(img, win):
    """Box-average a window (cx, cy, size as fractions of height) into the store."""
    h, w = img.shape[:2]
    cx, cy, size = win
    sh = size * h
    sw = sh * STORE_W / STORE_H
    x0, y0 = cx * w - sw / 2, cy * h - sh / 2
    crop = Image.fromarray((img * 255).astype(np.uint8)).crop(
        (int(x0), int(y0), int(x0 + sw), int(y0 + sh)))
    y, u, v = rgb_to_yuv(np.asarray(crop.resize((STORE_W, STORE_H), Image.BOX)) / 255.0)
    cu = np.asarray(Image.fromarray(u.astype(np.float32)).resize((STORE_W // 2, STORE_H // 2), Image.BOX))
    cv = np.asarray(Image.fromarray(v.astype(np.float32)).resize((STORE_W // 2, STORE_H // 2), Image.BOX))
    return quant(y, 6, 0, 1), quant(cu, 4, -0.5, 0.5), quant(cv, 4, -0.5, 0.5)


def tri_wave(t):
    """Mirror fold onto [0, 1]: in hardware, invert the fraction when the
    integer LSB is set."""
    f = t - np.floor(t)
    odd = np.floor(t).astype(np.int64) & 1
    return np.where(odd == 1, 1 - f, f)


def fold_radial(u, v, n):
    r = np.hypot(u, v)
    a = np.arctan2(v, u)
    seg = math.pi / n                  # wedge half-angle: 2n mirrored wedges
    a = tri_wave(a / seg) * seg
    return r * np.cos(a), r * np.sin(a)


def fold_p4m(u, v):
    fu, fv = tri_wave(u), tri_wave(v)
    swap = fv > fu
    return np.where(swap, fv, fu), np.where(swap, fu, fv)


SQ3 = math.sqrt(3)
# Fundamental triangle of *333: vertices (0,0), (1,0), (1/2, sqrt3/2).
_TRI_MIRRORS = [  # (normal, offset): inside when n.p >= d
    ((0.0, 1.0), 0.0),
    ((SQ3 / 2, -0.5), 0.0),
    ((-SQ3 / 2, -0.5), -SQ3 / 2),
]


def fold_tri(u, v, iters=8):
    # Translation subgroup of *333 is the hex lattice spanned by
    # (3/2, sqrt3/2) and (0, sqrt3); reduce into one cell first (cheap in
    # hardware: lattice coordinates come straight out of the DDA).
    a = (2 / 3) * u
    b = (v - u / SQ3) / SQ3
    a -= np.floor(a)
    b -= np.floor(b)
    u = 1.5 * a
    v = SQ3 / 2 * a + SQ3 * b
    for _ in range(iters):            # bounded: a fixed pipeline in hardware
        for (nx, ny), d in _TRI_MIRRORS:
            s = nx * u + ny * v - d
            out = s < 0
            u = np.where(out, u - 2 * s * nx, u)
            v = np.where(out, v - 2 * s * ny, v)
    return u, v


def fold_p6m(u, v):
    u, v = fold_tri(u, v)
    # Extra mirror through the apex (1/2, sqrt3/2) and the base midpoint.
    out = u > 0.5
    return np.where(out, 1 - u, u), v


def render(store, mode, n=6, zoom=1.0, angle=0.0, src=(0.5, 0.5), scale=0.45):
    ys, us, vs = store
    yy, xx = np.mgrid[0:OUT_H, 0:OUT_W].astype(np.float64)
    # Screen -> canvas, in units of screen height, centred.
    x = (xx - OUT_W / 2) / OUT_H
    y = (yy - OUT_H / 2) / OUT_H
    ca, sa = math.cos(angle), math.sin(angle)
    u = (x * ca - y * sa) / (zoom * scale)
    v = (x * sa + y * ca) / (zoom * scale)

    if mode == "radial":
        fu, fv = fold_radial(u, v, n)
        # The wedge apex sits at the store's source point; radius past the
        # store edge mirrors back (tri_wave), so the mandala never runs out.
        sx = tri_wave(src[0] + fu * 0.5) * (STORE_W - 1)
        sy = tri_wave(src[1] + fv * 0.5) * (STORE_H - 1)
    else:
        fu, fv = {"p4m": fold_p4m, "tri": fold_tri, "p6m": fold_p6m}[mode](u, v)
        if mode != "p4m":
            fv = fv / (SQ3 / 2)  # triangle height -> store height
        sx = np.clip(fu, 0, 1) * (STORE_W - 1)
        sy = np.clip(fv, 0, 1) * (STORE_H - 1)

    ix = sx.astype(int)
    iy = sy.astype(int)
    return yuv_to_rgb(ys[iy, ix], us[iy // 2, ix // 2], vs[iy // 2, ix // 2])


def save(rgb, path):
    Image.fromarray((rgb * 255).astype(np.uint8)).save(path)


def main():
    src = Path(sys.argv[1])
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    img = np.asarray(Image.open(src).convert("RGB")).astype(np.float64) / 255.0
    store = capture_store(img, (0.38, 0.5, 0.6))

    # What the store holds, scaled up nearest-neighbour.
    save(yuv_to_rgb(store[0], np.repeat(np.repeat(store[1], 2, 0), 2, 1),
                    np.repeat(np.repeat(store[2], 2, 0), 2, 1)), out / "store.png")
    shots = [
        ("radial_n6", dict(mode="radial", n=6)),
        ("radial_n12_rot", dict(mode="radial", n=12, angle=0.4, zoom=0.8)),
        ("p4m", dict(mode="p4m", zoom=1.4, angle=0.3)),
        ("tri", dict(mode="tri", zoom=1.3)),
        ("p6m", dict(mode="p6m", zoom=1.1, angle=0.2)),
    ]
    for name, kw in shots:
        save(render(store, **kw), out / f"{name}.png")
        print("wrote", out / f"{name}.png")


if __name__ == "__main__":
    main()
