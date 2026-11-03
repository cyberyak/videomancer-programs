# Kaleido: a true 2-D kaleidoscope for Videomancer

## Status (2026-11-03)

v0.3 implements all four modes in `kaleido.vhd`:

| Mode | Group | Switches |
|---|---|---|
| Square | p4m, or pmm with Diagonal off | S7 = Lattice, S11 = Square |
| Triangle | *333, the toy kaleidoscope | S7 = Lattice, S11 = Triangle, S8 Diagonal = Off |
| Hex | *632 / p6m | S7 = Lattice, S11 = Triangle, S8 Diagonal = On |
| Radial | D_n mandala, n = 2..16 even (knob 1) | S7 = Radial. S8 picks mirrored or repeated rings |

- **Simulation:** every mode is bit-identical in fastsim and GHDL, including the dry/wet mix.
- **Synthesis:**
  - HD: 6,771 / 7,680 LC (88%) and 31/32 EBR. Fmax is reported as 51.0 MHz, against 56–57 for passthru; the HD "FAIL" line also appears for passthru, so this needs confirming on hardware.
  - SD: 6,851 LC, passes 27 MHz.
- **Not yet tried on hardware.**
- **Previews:** the store settles over 3 frames, so use `--warmup 4`.

**Triangle fold:** this replaced the iterative reflections planned in §3. In skewed lattice coordinates the fold is one of six 0/±1 affine maps, chosen by `(I − J) mod 3` and the triangle half. `model/derive_tri_maps.py` derives the maps and verifies them on 20,000 points. Hex is the triangle fold plus a swap.

**Radial:** this differs from §3. A full CORDIC there and back did not fit, so radial reads the store in **polar form**:
- column = folded angle
- row = radius, as rings one tile apart

Each wedge shows the window bent into a sector (a mandala), not an undistorted slice. The CORDIC uses two iterations per stage, so it fits the existing pipeline with no extra latency.

**Area savings that made room for radial:**
- The SDK's combinational sin/cos LUT (571 LC) became a quarter-wave ROM in 1 EBR, read twice per frame during vblank.
- The 12-clock dry-video delay (~360 flip-flops) became a 256-deep circular buffer in 2 EBR.

## 1. What the hardware allows

- iCE40 HX4K: 7,680 logic cells, 32 EBR × 4 Kbit = 128 Kbit RAM, **no DSP
  multipliers, no frame buffer**. The stock core uses ≈1,600 LC and 0 EBR, so a
  program gets ≈6,000 LC and all 32 EBR.
- Pixel clock 74.25 MHz (HD) / 13.5 MHz (SD). Every pixel is one clock, so
  everything must be a fixed-depth pipeline.
- Video arrives in raster order. A pixel can only use the current line, a few
  previous lines (line buffers), or whatever was saved in EBR earlier.

## 2. How existing programs do "mirror / kaleidoscope"

| Program | Technique | Limitation |
|---|---|---|
| Reflector, Hatchglass, Vitrail, Hallway, Panemix, Foldither | Fold the **x** coordinate with an up/down counter; read from a line buffer of the previous line | Symmetry is horizontal only. Panes are vertical strips, never wedges or triangles. |
| Damier, Hypnos | Synthesis: compute the folded coordinate, then *generate* a pattern from it | Doesn't show the input video |
| Feedback (25 EBR), Afterimage (16 EBR) | **Coarse frame store**: 128×64 Y4 + 64×32 UV, or 256×128 at 1 bit | Low resolution, but genuinely 2-D random access |

**The gap:** nothing folds the input video in 2-D (wedges, triangles, squares).
The coarse frame store trick from Feedback and Afterimage makes that possible.

## 3. Architecture

```
input ─► capture window ─► box average ─► TILE STORE (28 EBR) ◄─ read addr ─┐
                                                                            │
screen x,y ─► rotate/zoom DDA ─► symmetry fold ─► store addr ───────────────┘
                                                         │
                     store Y/U/V ─► (seam glow, tint) ─► mix with dry ─► out
```

### Tile store (the key budget decision)
- Y 6-bit at 128 × 112 = 86,016 bits → 21 EBR (seven 2048×2 groups of three).
- U/V 4-bit each at 64 × 56 (4:2:0) = 28,672 bits → 7 EBR (512×8).
- **Total 28 EBR**, leaving 4 for a sin/cos ROM, etc.
- A kaleidoscope only ever shows *one fundamental region* of the source,
  repeated. So 128×112 source pixels become a full-screen pattern, and the coarse
  store looks far sharper here than it does in Feedback. The model output in
  `model/out` uses exactly this quantisation.
- Single bank: the store is written while it's read, which can tear on fast
  motion through the window. Mitigations: S9 *Freeze*, and capture on alternate
  frames. Double-buffering would halve the resolution (≈90×80), so I'd avoid it.

### Rotate / zoom: adders only
`u = u0 + x·(c/z) − y·(s/z)` is linear, so per pixel it's `u += du_dx`, and per
line `u_row += du_dy` (the classic rotozoomer DDA). The per-frame increments come
from a sin/cos ROM and one serial multiplier during vblank (same pattern as
Reflector and Hallway). For the lattice modes, the DDA steps directly in
**lattice coordinates** (the skew is folded into the increments), so the
"mod lattice" step is just dropping integer bits.

### Symmetry folds

| Mode | Group | Hardware | Est. LC |
|---|---|---|---|
| Square | *442 / p4m | Two triangle waves (invert the fraction when the integer LSB is set) + compare/swap | ~120 |
| Triangle (toy kaleidoscope) | *333 / p3m1 | Lattice reduce (free) + fixed chain of 3–6 conditional reflections; constants ½ and √3/2 as shift-adds | ~500 |
| Hex | *632 / p6m | Triangle + one extra mirror | ~560 |
| Radial mandala | N-wedge dihedral | CORDIC vectoring (atan2, r) → `a' = tri(a·N/π)` → sin/cos LUT + two soft multipliers back to x,y | ~1,100 |

Rough total: DDA ~250, capture/averager ~300, folds as above, mix/output ~250.
That's **≈2,500–3,000 LC** with every mode in one program, about half the budget.
Timing at 74.25 MHz needs deep pipelining (Howler runs 16 stages). Plan on 1–2
adds per stage.

## 4. Proposed controls

| Control | Function |
|---|---|
| K1 Segments | Radial: N = 2…16. Lattice modes: tile size. |
| K2 Zoom | Pattern scale (DDA step) |
| K3 Spin | Rotation speed, bipolar (centre = still) |
| K4 Source X / K5 Source Y | Where the capture window sits on the input |
| K6 Window | Capture window size (zoom into the source) |
| S7+S8 Mode | Radial / Square / Triangle / Hex |
| S9 Freeze | Stop capturing: hold the tile, keep spinning |
| S10 Mirror/Rotate | Reflections (p6m…) vs rotations only (p6…): a "turning" look with no mirror seams |
| S11 Seams | Glow lines along the mirror edges |
| P12 Mix | Dry/wet crossfade (SDK `interpolator`) |

Later ideas: *Twist* (`angle += k·log r`, spiral arms), *Drift* (the source window
orbits on its own), and an LFSR shimmer on the seams.

## 5. Build plan

1. **Square mode only** (p4m): capture + store + DDA + fold. Simulate with the
   VHDL image tester against `model/input.png`. The fold is ~10 lines of VHDL, so
   this proves the store and timing.
2. Add spin/zoom per-frame math, then the triangle/hex folds.
3. Add radial (CORDIC). If LC gets tight, ship it as a separate program.
4. Synthesize for SD and HD, check LC/EBR/Fmax, then sign and load onto the unit.

Bit-accurate model first: extend `kaleido_model.py` to integer arithmetic before
writing each VHDL stage, then diff the image tester output against it.
