#!/usr/bin/env python3
# Copyright (C) 2026 cyberyak
# SPDX-License-Identifier: GPL-3.0-only
"""Golden check: fastsim (Verilator) against the SDK's GHDL image tester.

  compare.py <program_dir> [<program_dir> ...] [--image IMG] [--decimation 8]
             [--set KEY=V ...]

Runs both engines with identical settings and prints, per program, whether the
outputs are bit-identical, plus the share of differing pixels and the largest
channel difference. A diff image goes to build/fastsim/compare/<name>.png
(GHDL | fastsim | amplified difference).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fastsim import ROOT, _load_registers, load_program, simulate  # noqa: E402
from vhdl_image_tester.core.pipeline import run_pipeline  # noqa: E402


def compare(prog_dir: Path, image: Image.Image, sets: list[str], decimation: int) -> bool:
    program = load_program(prog_dir.name, prog_dir.parent)
    values = _load_registers(program, sets, None)

    t0 = time.time()
    g = run_pipeline(program=program, source_image=image, register_values=values,
                     video_mode="1080p2997", decimation=decimation, log_callback=lambda *_: None)
    t_ghdl = time.time() - t0
    if not g.success:
        print(f"{prog_dir.name:22s} GHDL FAILED")
        return False

    t0 = time.time()
    try:
        fast = simulate(prog_dir, image, values, decimation=decimation, log=lambda *_: None)[0]
    except RuntimeError as e:
        print(f"{prog_dir.name:22s} FASTSIM FAILED: {str(e).splitlines()[0]}")
        return False
    t_fast = time.time() - t0

    a = np.asarray(g.output_image.convert("RGB"), dtype=np.int16)
    b = np.asarray(fast.convert("RGB"), dtype=np.int16)
    d = np.abs(a - b)
    differing = (d.max(axis=2) > 0).mean() * 100
    ok = d.max() == 0
    print(f"{prog_dir.name:22s} {'IDENTICAL' if ok else 'DIFFERS  '} "
          f"diff px {differing:6.2f}%  max {int(d.max()):3d}   "
          f"ghdl {t_ghdl:5.1f}s  fast {t_fast:5.1f}s")

    out = ROOT / "build/fastsim/compare"
    out.mkdir(parents=True, exist_ok=True)
    amp = np.clip(d * 8, 0, 255).astype(np.uint8)
    sheet = np.concatenate([a.astype(np.uint8), b.astype(np.uint8), amp], axis=1)
    Image.fromarray(sheet).save(out / f"{prog_dir.name}.png")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("program_dirs", type=Path, nargs="+")
    ap.add_argument("--image", type=Path, default=ROOT / "test-images/shapes.png")
    ap.add_argument("--decimation", type=int, default=8)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=V")
    a = ap.parse_args()
    image = Image.open(a.image)
    results = [compare(p.resolve(), image, a.set, a.decimation) for p in a.program_dirs]
    print(f"\n{sum(results)}/{len(results)} identical")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
