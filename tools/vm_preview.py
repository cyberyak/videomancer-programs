#!/usr/bin/env python3
"""Simulate a Videomancer program on a still image and build a labelled sheet.

Runs one simulation per setting and lays the input and every output side by side.
Engines:
  fast  Verilator via tools/fastsim (default; well under a second per frame
        after a one-off ~4 s build per program)
  ghdl  the SDK's VHDL image tester (reference; about 60 s per frame at ÷4)

  tools/vm_preview.py <program_dir> [--image IMG] [--set KEY=V ...]
                      [--sweep KEY=V1,V2,...] [--decimation N]
                      [--engine fast|ghdl] [--out PNG]

Examples:
  tools/vm_preview.py videomancer-sdk/programs/sabattier
  tools/vm_preview.py programs/cyberyak/kaleido --sweep rotary_potentiometer_1=0,512,1023
  tools/vm_preview.py videomancer-community-programs/programs/czartroniks/reflector \
      --set toggle_switch_9=1023 --sweep rotary_potentiometer_2=200,800

Run with the bundled Python: `source tools/env.sh` first.
"""
import argparse
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "videomancer-sdk/tools/vhdl-image-tester/run.py"
DEFAULT_IMAGE = ROOT / "test-images/shapes.png"
TILE_W, LABEL_H, GAP = 480, 22, 8


def simulate_fast(prog_dir, image, sets, decimation, warmup, out_png):
    sys.path.insert(0, str(ROOT / "tools/fastsim"))
    import fastsim
    program = fastsim.load_program(prog_dir.name, prog_dir.parent)
    values = fastsim._load_registers(program, sets, None)
    try:
        frames = fastsim.simulate(prog_dir, Image.open(image), values, decimation=decimation,
                                  warmup_frames=warmup, log=lambda *_: None)
    except RuntimeError as e:
        sys.exit(f"simulation failed for {sets or 'defaults'}:\n{e}")
    frames[-1].save(out_png)


def simulate(prog_dir, image, sets, decimation, warmup, out_png):
    cmd = [sys.executable, str(CLI), "simulate", prog_dir.name,
           "--programs-dir", str(prog_dir.parent), "--image", str(image),
           "--decimation", str(decimation), "--warmup-frames", str(warmup),
           "--output", str(out_png)]
    for s in sets:
        cmd += ["--set", s]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        lines = [l for l in (r.stdout + r.stderr).splitlines() if "DEPRECATION" not in l]
        sys.exit(f"simulation failed for {sets or 'defaults'}:\n" + "\n".join(lines[-30:]))


def sheet(tiles, path):
    scaled = []
    for label, im in tiles:
        h = round(im.height * TILE_W / im.width)
        scaled.append((label, im.convert("RGB").resize((TILE_W, h), Image.NEAREST)))
    cols = min(3, len(scaled))
    rows = (len(scaled) + cols - 1) // cols
    th = max(im.height for _, im in scaled) + LABEL_H
    out = Image.new("RGB", (cols * (TILE_W + GAP) - GAP, rows * (th + GAP) - GAP), (24, 24, 24))
    d = ImageDraw.Draw(out)
    for i, (label, im) in enumerate(scaled):
        x, y = (i % cols) * (TILE_W + GAP), (i // cols) * (th + GAP)
        d.text((x + 4, y + 5), label, fill=(230, 230, 230))
        out.paste(im, (x, y + LABEL_H))
    out.save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("program_dir", type=Path)
    ap.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=V")
    ap.add_argument("--sweep", metavar="KEY=V1,V2,...")
    ap.add_argument("--decimation", type=int, default=4)
    ap.add_argument("--engine", choices=["fast", "ghdl"], default="fast")
    ap.add_argument("--warmup", type=int, default=2,
                    help="frames before capture; raise it for programs that settle over frames")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    run_sim = simulate_fast if a.engine == "fast" else simulate

    prog = a.program_dir.resolve()
    if not (prog / f"{prog.name}.toml").exists():
        sys.exit(f"{prog} has no {prog.name}.toml")
    work = ROOT / "build/preview" / prog.name
    work.mkdir(parents=True, exist_ok=True)

    runs = [("defaults" if not a.set else " ".join(a.set), a.set)]
    if a.sweep:
        key, vals = a.sweep.split("=", 1)
        runs = [(f"{key}={v}", a.set + [f"{key}={v}"]) for v in vals.split(",")]

    tiles = [("input", Image.open(a.image))]
    for i, (label, sets) in enumerate(runs):
        png = work / f"run{i}.png"
        print(f"[{i + 1}/{len(runs)}] {label} ...", flush=True)
        run_sim(prog, a.image, sets, a.decimation, a.warmup, png)
        tiles.append((label, Image.open(png)))

    out = a.out or work / "sheet.png"
    sheet(tiles, out)
    print(out)


if __name__ == "__main__":
    main()
