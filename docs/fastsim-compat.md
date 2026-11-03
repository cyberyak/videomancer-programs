# fastsim compatibility (Verilator vs GHDL reference)

## Build-and-run sweep (2026-11-03)

`tools/fastsim/sweep.py` builds every program in the workspace and runs 3 frames
at ÷4 on `test-images/shapes.png`, four programs at a time. Results go to
`build/fastsim/sweep/results.md`.

- **All 133 YUV-core programs build and run**: 11 LZX examples, all 121
  community programs, and Kaleido.
- **The 3 RGB-core programs are unsupported**: `colorbars_rgb` and `passthru_rgb`
  (gbr444) and `passthru_gbr422`. The harness wraps the YUV core.
  `colorbars_rgb` fails to build; the passthrus run, but their colours are
  interpreted as YUV.
- **Build** (one-off per program, cached): median 9.9 s, max 46 s.
- **Run**, 3 frames at 480×270: median 0.42 s, max 3.5 s (czartroniks/pulp).
- The sweep only shows a program builds and produces frames. Pixel accuracy
  against GHDL is in the table below; extending it to every program is VM-2.

## Pixel comparison with GHDL

`tools/fastsim/compare.py` runs the SDK's GHDL image tester and fastsim with
identical settings (1080p29.97 timing, ÷8, `test-images/shapes.png`, TOML
defaults) and diffs the RGB output.

Last run: 2026-11-03.

| Program | Result | Notes |
|---|---|---|
| sdk/yuv_amplifier | identical | |
| sdk/colorbars | identical | synthesis |
| sdk/howler | identical | 3 BRAM line buffers, feedback |
| sdk/kintsugi | identical | |
| sdk/stic | identical | |
| czartroniks/feedback | identical | 25 EBR coarse frame store |
| sdk/sabattier | **differs** | 91% of pixels, mean 12/255, mostly along edges (Mackie lines). Not a spatial shift. |
| czartroniks/reflector | **differs** | best match is a 6 px horizontal shift, plus other differences |

## Open question: which side is right for the two mismatches

fastsim simulates the design **after** GHDL synthesis + Yosys. That's the same
front end that builds the real bitstream, so a mismatch is one of:
1. a VHDL simulation/synthesis mismatch in the program. Here fastsim would match the hardware and GHDL would not.
2. a fastsim export issue: initial values, memory read-during-write, X handling.
3. a harness difference.

What's been tried:
- Yosys export logs show no warnings for these programs.
- Simulating the `ghdl --synth` VHDL netlist in GHDL produced a blank frame, so
  this route needs more work to act as a tie-breaker.

Next steps are tracked in Jira (phase 2):
- Bisect by tapping intermediate signals.
- Try Verilator `--x-initial unique` with several seeds.
- Check on hardware: capture Videomancer's output for the same still and
  register values.

Until this is settled, use `--engine ghdl` to confirm subtle results for
sabattier/reflector-style programs.
