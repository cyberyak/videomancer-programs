---
paths:
  - "tools/fastsim/**"
---

# fastsim performance (measured on an Apple M2, Kaleido, ÷4 = 480×270)

- The Verilator model costs about 31 ms per frame: two `eval()` calls per clock, ~150k clocks.
  That's the floor. The live window reaches 28.5 fps because Python decodes and displays
  while the simulator runs (pipelined), and colour conversion happens in C++ (`--serve --rgb`).
- Things that did NOT help, so don't retry them:
  - Verilator `--threads 2/3/4`: 4–17× slower. Per-clock synchronisation dominates a design this small.
  - `-O3 -mcpu=native` instead of `-O2`: about 1% faster.
  - Extra Yosys passes before export (`opt -full; wreduce; share`): about 40% slower in Verilator.
  - The GPU: cycle-accurate RTL simulation doesn't map onto it.
- Multi-core helps only across independent jobs: separate simulations for sweeps, compare runs
  and GIF renders can run as parallel processes.
- The C++ BT.601 conversion in `harness.cpp` must stay bit-identical to the SDK tester's
  `image_converter.py`. It was verified at 0 differing pixels over 40 frames; recheck after
  touching it or the compiler flags (FMA contraction could change the rounding).

# Raster modes

- The default compact raster (SDK tester: +64 clocks, +8 lines, decimated picture, 1080p timing ID) is only
  right for programs that ignore the timing ID and the blanking time.
- `--real-timing` / "480p real" feeds CEA-861 totals (480p: 858×525, ~6 fps live).
- For a black or oddly scaled program, check `fastsim.uses_timing_id()` before suspecting fastsim.
