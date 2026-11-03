#!/usr/bin/env python3
# Copyright (C) 2026 cyberyak
# SPDX-License-Identifier: GPL-3.0-only
"""Build and run every program in fastsim, several at a time.

  sweep.py [program_dir ...] [--jobs 4] [--out build/fastsim/sweep]

With no program dirs, sweeps the SDK examples, every community program and
our own. For each: export + Verilator build, then 3 frames on
test-images/shapes.png at ÷4. Writes a preview PNG per program and a
results table (results.md, results.json). Build time and run time are
reported separately, since builds are cached for later runs.
Run with the bundled Python: `source tools/env.sh` first.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def all_programs() -> list[Path]:
    dirs = sorted((ROOT / "videomancer-sdk/programs").iterdir())
    dirs += sorted((ROOT / "videomancer-community-programs/programs").glob("*/*"))
    dirs += sorted((ROOT / "programs/cyberyak").iterdir())
    return [d for d in dirs if (d / f"{d.name}.toml").exists()]


def run_one(prog: str, out: str) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import fastsim
    from PIL import Image
    from vhdl_image_tester.core.config import resolve_video_settings

    p = Path(prog)
    rel = str(p.relative_to(ROOT))
    res = {"program": rel, "status": "ok", "build_s": 0.0, "run_s": 0.0, "error": ""}
    try:
        program = fastsim.load_program(p.name, p.parent)
        values = fastsim._load_registers(program, [], None)
        t0 = time.time()
        fastsim.build(p, resolve_video_settings("1080p2997", 4).fpga_config, log=lambda *_: None)
        res["build_s"] = round(time.time() - t0, 1)
        t0 = time.time()
        frame = fastsim.simulate(p, Image.open(ROOT / "test-images/shapes.png"), values,
                                 decimation=4, log=lambda *_: None)[-1]
        res["run_s"] = round(time.time() - t0, 2)
        frame.save(Path(out) / (rel.replace("/", "__") + ".png"))
    except Exception as e:  # report and keep sweeping
        res["status"] = "build failed" if res["build_s"] == 0 else "run failed"
        res["error"] = str(e).strip().splitlines()[-1][:300] if str(e).strip() else type(e).__name__
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("program_dirs", type=Path, nargs="*")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out", type=Path, default=ROOT / "build/fastsim/sweep")
    a = ap.parse_args()
    progs = [p.resolve() for p in a.program_dirs] or all_programs()
    a.out.mkdir(parents=True, exist_ok=True)

    results = []
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=a.jobs) as ex:
        futs = [ex.submit(run_one, str(p), str(a.out)) for p in progs]
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            results.append(r)
            print(f"[{i}/{len(progs)}] {r['status']:12s} {r['program']}"
                  + (f"  ({r['error']})" if r["error"] else ""), flush=True)
    results.sort(key=lambda r: r["program"])
    ok = sum(r["status"] == "ok" for r in results)

    (a.out / "results.json").write_text(json.dumps(results, indent=1))
    lines = [f"# fastsim sweep: {ok}/{len(results)} build and run",
             "", "| Program | Status | Build s | Run s (3 frames) | Error |", "|---|---|---|---|---|"]
    lines += [f"| {r['program']} | {r['status']} | {r['build_s']} | {r['run_s']} | {r['error']} |"
              for r in results]
    (a.out / "results.md").write_text("\n".join(lines) + "\n")
    print(f"\n{ok}/{len(results)} ok in {time.time() - t0:.0f} s -> {a.out / 'results.md'}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
