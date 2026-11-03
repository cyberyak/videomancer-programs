# Trying a program on the Videomancer

## Build the package

```bash
tools/vm_package.sh programs/cyberyak/kaleido      # ~5 min, all 6 input configs
# -> out/rev_b/kaleido.vmprog
```

The package is **unsigned**. The firmware trusts only LZX's built-in keys and
rejects invalid signatures, so a self-signed package would be no better and
might be refused. Unsigned packages with valid hashes are expected to need
**Developer Mode** on the device. That's an assumption from LZX's notes, so
confirm it on first load.

## Load it

1. Copy `out/rev_b/kaleido.vmprog` into the `programs/` folder on the
   Videomancer's microSD card (FAT32), next to the other `.vmprog` files.
2. Eject, put the card back, and power-cycle.
3. Turn on Developer Mode if the program doesn't appear or is refused.
4. Pick Kaleido in the program browser.

## How it runs

The firmware loads the bitstream that matches the active input: HD or SD,
and HDMI, analog or dual. Every frame:
1. Kaleido captures a window of that frame (Source X/Y, Window) into its
   tile store.
2. Every output pixel reads the store through the folded, rotating coordinate.

So whatever moves inside the window moves in the pattern, live. The program
needs an input signal. With no input there is no video timing, so it shows
nothing; see "No input" below.

## Test checklist (Kaleido v0.3)

- [ ] It loads; note whether Developer Mode was needed.
- [ ] HDMI 1080p from a computer (e.g. VS2): all four modes:
  - Layout Lattice + Shape Square
  - Layout Lattice + Shape Triangle, with Diagonal on and off
  - Layout Radial, sweeping knob 1 through all segment counts
- [ ] HDMI 720p and SD (480p / 576p): same composition, nothing cut off.
- [ ] Analog input (if available).
- [ ] Interlaced SD input: expected to look 2:1 stretched (known v0.3 limit).
- [ ] Spin both ways; Zoom across its range; Window and Source X/Y.
- [ ] Freeze holds the tile; Seams glow; Mix fades from input to effect.
- [ ] Fast motion in the window: look for a tear line in the pattern.
- [ ] Any shimmer or wrong pixels at HD. nextpnr reports 51 MHz against the
      74.25 MHz HD clock, a "FAIL" that also appears for passthru, so this
      is the main thing to confirm on hardware.

## No input

Kaleido processes incoming video, so it needs a source. Ways to feed it:
- a computer over HDMI (VS2, a video player, a test pattern), or
- a later Kaleido feature: an internal generator of moving shapes, used when
  no input is detected. It depends on whether the firmware's standalone
  timing configs run processing programs without input, which needs checking
  on hardware. Space is limited: about 900 LC and 1 EBR left.

For simulation, the live preview has a built-in "Moving circles" source:

```bash
python3 tools/fastsim/live.py programs/cyberyak/kaleido
```
