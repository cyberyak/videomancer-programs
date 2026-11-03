---
name: vm-preview
description: Simulate a Videomancer program on a still image and produce a labelled before/after sheet, optionally sweeping one knob or switch, or render several frames as a GIF. Use to visually validate a program's output on this Mac without hardware.
argument-hint: "<program_dir> [--sweep KEY=V1,V2,...] [--set KEY=V] [--image IMG] [--engine fast|ghdl]"
allowed-tools: Bash(source tools/env.sh*) Bash(python3 tools/vm_preview.py:*) Bash(python3 tools/fastsim/fastsim.py:*)
---

Run from the workspace root:

```bash
source tools/env.sh && python3 tools/vm_preview.py $ARGUMENTS
```

- The default engine is **fast** (Verilator via `tools/fastsim`).
  - The first run per program builds a cached simulator in about 4 s.
  - After that, each frame takes well under a second, even at `--decimation 1` (full 1080p).
- `--engine ghdl` is the SDK's reference simulator, at about 60 s per frame at ÷4.
  Use it when fast and ghdl might disagree; `tools/fastsim/compare.py` diffs the two.
- Parameter keys:
  - `rotary_potentiometer_1`…`_6`
  - `toggle_switch_7`…`_11` (0 or 1023)
  - `linear_potentiometer_12`
  - See `<name>.toml` for labels and defaults.
- `--warmup N`: frames before capture (default 2, like the SDK tester). Programs that measure the frame
  or fill a store over several frames need more (kaleido: 4). Otherwise the preview shows a
  half-settled frame.
- Default image is `test-images/shapes.png`. Other test images are in `test-images/`.
- The tool prints the sheet's path (`build/preview/<name>/sheet.png`).
  Open it with Read to check it, then send it to the user.

## Motion (feedback, animated programs)

```bash
python3 tools/fastsim/fastsim.py simulate <program_dir> --capture-frames 60 --output build/<name>.gif
```

This captures consecutive frames after the warm-up frames. Programs that animate from a frame counter
or have feedback show real motion. `--fps` sets the GIF playback rate (default 12).
