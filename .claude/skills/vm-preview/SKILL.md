---
name: vm-preview
description: Simulate a Videomancer program (GHDL) on a still image and produce a labelled before/after sheet, optionally sweeping one knob or switch. Use to visually validate a program's output on this Mac without hardware.
argument-hint: "<program_dir> [--sweep KEY=V1,V2,...] [--set KEY=V] [--image IMG]"
allowed-tools: Bash(source tools/env.sh*) Bash(python3 tools/vm_preview.py:*)
---

Run from the workspace root:

```bash
source tools/env.sh && python3 tools/vm_preview.py $ARGUMENTS
```

- Each simulated frame takes about 60 s at the default `--decimation 4` (480×270).
  Keep sweeps to 2–4 values, and run long sweeps in the background.
- Parameter keys:
  - `rotary_potentiometer_1`…`_6`
  - `toggle_switch_7`…`_11` (0 or 1023)
  - `linear_potentiometer_12`
  - See `<name>.toml` for labels and defaults.
- Default image is `test-images/shapes.png`. Other test images are in `test-images/`.
- The tool prints the sheet's path (`build/preview/<name>/sheet.png`).
  Open it with Read to check it, then send it to the user.
- The simulator renders one still frame after 2 warm-up frames. It can't show motion or real-time behaviour.
  For animated effects, sweep the parameter that drives the motion instead.
