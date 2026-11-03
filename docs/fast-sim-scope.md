# Fast simulation: Verilator-based near-live preview

**Goal:** see a Videomancer program's real output on this Mac at interactive speed,
from still images, video files, or a live source such as the VS2 app,
instead of GHDL's ~60 s per quarter-resolution frame.

## Why the current simulator is slow

The SDK's VHDL image tester:
1. Re-analyses the whole SDK with GHDL on every run.
2. Simulates every clock event-by-event: ~2.5 M clocks per 1080p frame, 3 frames per run.
3. Passes every pixel through `stimulus.txt` / `output.txt` text files.
4. Uses a single core.

## Feasibility spike (2026-11-03, M-series MacBook Air)

Everything needed already ships in the SDK's OSS CAD Suite: GHDL plugin for Yosys,
**Verilator 5.037**.

1. `yosys -m ghdl` elaborates `program_top` alone (no core/hardware) and
   `write_verilog` emits a flat module. Ports: `clk`, `registers_in[319:0]`, the
   `data_in`/`data_out` Y/U/V + sync bits. Under 1 s per program.
2. A 10-line Verilog wrapper renames the escaped ports. A ~25-line C++ harness
   drives full 1080p raster timing (2200 × 1125 clocks per frame).

| Program | Character | Full 1080p | Equivalent at ÷2 (960×540) |
|---|---|---|---|
| sabattier | pure pixel pipeline, 0 EBR | **3.7 fps** (9.3 M clk/s) | ~15 fps |
| czartroniks/feedback | coarse frame store, 25 EBR | **1.1 fps** (2.6 M clk/s) | ~4.4 fps |
| howler | 3 BRAM line buffers | exports cleanly (not benchmarked) | |

GHDL today: ~1 frame per **60 s at ÷4**. Verilator at full resolution is
roughly **1,000–3,000× faster**.

Not yet verified: whether the Verilator output is **bit-identical** to GHDL.
Yosys warned that some VHDL signal initial values on memories were dropped.
The iCE40 powers RAM up as specified, so this matters for programs that rely
on initialised RAM.

## Plan

### Phase 1: `vm_fastsim` CLI (≈1–2 days)
- `tools/fastsim/export.sh <program_dir>`: GHDL→Verilog using the Makefile's file list.
  It also generates the wrapper and the register-index mapping from the TOML.
- C++ harness built once per program (≈3 s compile, cached by source hash):
  - Reads PNG or raw frames and writes PNG or raw out.
  - Generates proper sync/blanking from `video_sync_pkg` for each video mode.
  - Takes register values from the CLI or JSON (same keys as the SDK tester).
  - Handles BT.601 RGB↔YUV like the SDK tester.
- Acceptance: `vm_preview.py --engine fast` produces the same sheet as GHDL.

### Phase 2: correctness gate (≈1 day)
- Golden test that diffs Verilator against GHDL (decimation 8) per program.
  Target: identical except where the program uses LFSR noise.
- Fix memory-init handling: `memory -nomap` plus explicit `$meminit`, or a
  patch to the GHDL plugin options.
- Run it across the SDK examples and the community programs. Record which
  ones pass in `docs/fastsim-compat.md`.

### Phase 3: live preview app (≈2–3 days)
- Window showing input and output side by side at ÷2 (≈5–15 fps), or ÷1 for
  light programs. Register sliders and switches labelled from the TOML.
  - Option A: Python + PyQt6 with the harness as a shared library (ctypes).
  - Option B: pure C++ with SDL2.
- Sources:
  - Image file.
  - Video file (ffmpeg pipe).
  - Webcam / virtual camera, which covers apps exposing one.
  - **Syphon** (macOS inter-app GPU sharing) for VS2-style apps, if VS2 supports it.
  - Optionally NDI.
- Record the output to MP4 for sharing.

### Phase 4 (optional)
- Multi-threaded Verilator (`--threads`) and per-program `-O3` tuning.
- Run the same harness in CI (GitHub Actions) to render a preview GIF for each program in the README.

## Risks

| Risk | Mitigation |
|---|---|
| Verilator ≠ GHDL semantics (init values, X-states) | Phase 2 golden diff; GHDL stays the reference |
| Programs that instantiate `SB_*` iCE40 primitives directly | Yosys `ice40/cells_sim.v` models |
| Timing-ID-dependent programs | Harness writes register 8 like the firmware |
| VS2 has no Syphon/NDI/virtual-camera output | Screen-region capture via ScreenCaptureKit (macOS permission prompt) |
| Still not real-time at 1080p for heavy programs | Default ÷2 for preview; ÷1 for final stills |

## Effort

Roughly 5–7 focused days for phases 1–3. Phase 1 alone already replaces the
60 s wait with a ~1 s one.
