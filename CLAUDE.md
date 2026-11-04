# Videomancer workspace

Developing FPGA video programs for an LZX Videomancer (iCE40 HX4K, VHDL).

## Layout
- Git repo: github.com/cyberyak/videomancer-programs (public, GPL-3.0-only).
  Jira project tracks the work (see Tracking below).
- `videomancer-sdk/`, `videomancer-community-programs/`: upstream git submodules. Read them; don't edit them.
- `programs/cyberyak/<name>/`: our own programs (`<name>.vhd` + `<name>.toml`, optional `<name>.py` build hook,
  `model/`, `README.md`). This mirrors the community repo layout, so a finished program upstreams by copying
  its folder. Follow `docs/upstreaming.md`.
- `programs/cyberyak/kaleido/`: kaleidoscope design (`DESIGN.md`) + Python reference model.
- `docs/`: plans and notes (`fast-sim-scope.md`: Verilator fast simulation).
- `tools/`: `env.sh` (toolchain), `vm_build.sh` (synth + resources), `vm_preview.py` (simulate → sheet).
- `test-images/`: inputs for simulation. `build/`: generated output (safe to delete).

## Toolchain
- Always `source tools/env.sh` first. It provides GHDL/Yosys/nextpnr and Python 3.11 with Pillow, NumPy, PyQt6, jsonschema, OpenCV, mido, python-rtmidi, syphon-python and cyndilib.
  System Python is 3.9 and too old. Homebrew is not installed.
- The FPGA Makefile breaks on paths with spaces.

## Hardware budget (measured)
- 7,680 LC and 32 EBR (4 Kbit each). No DSP multipliers, no frame buffer.
- The stock core uses ~1,600 LC and 0 EBR, so a program gets ~6,000 LC.
- Pixel clock is 74.25 MHz HD / 13.5 MHz SD. Deep pipelines, 1–2 adds per stage.
- 1080p is at most 30 fps on the device (no 1080p60); 60 Hz modes are 720p, 480p and 1080i.
  LFOs and video in the live window run on the mode's frame clock (`fastsim.FRAME_RATES`).
- nextpnr prints `ADV7611_CLK … FAIL at 74.25 MHz` even for passthru, so treat it as baseline noise, not a regression.
  Compare against the passthru figure instead.

## Validation
- Two simulation engines, both driving the same raster as the SDK tester:
  - `tools/fastsim` (Verilator, default in `vm_preview.py`): ~0.05 s per ÷4 frame. It can capture many frames (GIF).
  - GHDL (`--engine ghdl`, the reference): ~60 s per frame.
  - `tools/fastsim/compare.py` diffs the two. Known mismatches are in `docs/fastsim-compat.md`;
    don't trust fastsim alone for those programs yet.
- fastsim reuses private helpers from the SDK tester (`_ordered_sdk_sources`, `_load_registers`).
  Recheck them after updating the SDK submodule.
- `tools/fastsim/live.py [dir] [--mode M] [--video F]`: live window, 15-45 fps at ÷4 depending on the program, Program picker (all 136),
  Resolution menu, knobs plus per-knob LFOs.
  - 93 programs read the timing ID (`registers_in(8)`): they need real timing (`--real-timing`,
    "480p real"), which live picks automatically. The compact raster (SDK tester default) mis-scales them,
    and Bishop renders black. See `docs/fastsim-compat.md`.
  - Sources: moving circles, images, video files (OpenCV), camera,
    Syphon (`syphon-python`) and NDI (`cyndilib`, bundles libndi).
  - MIDI (`mido` + `python-rtmidi`, `tools/fastsim/midi.py`): port menu, Learn, Faderfox UC4 preset.
    The UC4 preset's CCs (encoders 8-15, faders 32-39) are inferred, not from a Faderfox table.
    Mappings are per port in `~/.config/fastsim-live/midi.json`.
  - `tools/fastsim/vpanel.py`: virtual front panel. Its Time/Space/Slope mapping is ours, not LZX firmware.
  - Errors are logged to `build/live.log`, never fatal; the simulator process logs to `build/live_harness.log`.
  - Read those logs first when the user reports a crash.
- `tools/fastsim/sweep.py`: builds and runs every program in parallel. All 133 YUV-core programs pass;
  the 3 RGB-core ones are unsupported (`docs/fastsim-compat.md`).
- `tools/sim_gui.sh`: SDK GHDL window with logging to `build/ghdl_gui.log`.
  - It aborts with `QThread: Destroyed while thread is still running` if closed mid-simulation. That's an upstream bug.
- Packaging: `tools/vm_package.sh <dir>` builds an unsigned `out/rev_b/<name>.vmprog`.
  - The SDK's script hard-codes `/usr/bin/python3`, so the wrapper patches a copy.
  - Use unsigned, not self-signed: invalid signatures are rejected, so Developer Mode is likely needed.
  - Steps and checklist: `docs/hardware-test.md`.

## Tracking
- Jira space **VM – Videomancer** at cyberyak.atlassian.net (cloudId `dd2327c3-0138-475a-be96-743584522509`), Kanban.
  Use the Atlassian connector's `createJiraIssue` with `projectKey: "VM"`.
- VM-1: fast simulation (Verilator), scoped in `docs/fast-sim-scope.md`.
- **Do not push to GitHub for now**: the user wants all work kept local (commit locally only) until they say otherwise.
- Publishing to GitHub or the community repo is public. Confirm with the user before pushing a PR upstream.

## Memory and skills
- `.claude/rules/`: project knowledge. `vhdl-conventions.md` loads when touching `.vhd`/`.toml`.
  Add a new rule file when we learn something non-obvious, such as a hardware quirk or a timing trick.
- `.claude/skills/`: `/vm-build`, `/vm-preview`, `/vm-new-program`.
