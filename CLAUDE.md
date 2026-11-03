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
- Always `source tools/env.sh` first. It provides GHDL/Yosys/nextpnr and Python 3.11 with Pillow, NumPy and PyQt6.
  System Python is 3.9 and too old. Homebrew is not installed.
- The FPGA Makefile breaks on paths with spaces.

## Hardware budget (measured)
- 7,680 LC and 32 EBR (4 Kbit each). No DSP multipliers, no frame buffer.
- The stock core uses ~1,600 LC and 0 EBR, so a program gets ~6,000 LC.
- Pixel clock is 74.25 MHz HD / 13.5 MHz SD. Deep pipelines, 1–2 adds per stage.
- nextpnr prints `ADV7611_CLK … FAIL at 74.25 MHz` even for passthru, so treat it as baseline noise, not a regression.
  Compare against the passthru figure instead.

## Validation
- Simulation is GHDL, still image only, about 60 s per frame at decimation 4. There is no live or real-time input.
- Final check is on hardware: copy the `.vmprog` to the SD card `programs/` folder (FAT32).

## Memory and skills
- `.claude/rules/`: project knowledge. `vhdl-conventions.md` loads when touching `.vhd`/`.toml`.
  Add a new rule file when we learn something non-obvious, such as a hardware quirk or a timing trick.
- `.claude/skills/`: `/vm-build`, `/vm-preview`, `/vm-new-program`.
