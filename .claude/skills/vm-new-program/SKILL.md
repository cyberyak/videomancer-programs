---
name: vm-new-program
description: Scaffold a new Videomancer FPGA program in programs/cyberyak/<name>/ (VHDL architecture + TOML) following SDK conventions, then build and preview it. Use when starting a new effect.
argument-hint: "<name> <one-line idea>"
disable-model-invocation: true
---

Create `programs/cyberyak/<name>/` for: $ARGUMENTS

1. Read the conventions in `.claude/rules/vhdl-conventions.md`.
2. Pick the closest reference and copy its structure:
   - Simple pixel processing: `videomancer-sdk/programs/sabattier`.
   - Line buffers or feedback: `videomancer-sdk/programs/howler`.
   - Coarse frame store: `videomancer-community-programs/programs/czartroniks/feedback`.
   - Synthesis: `videomancer-sdk/programs/perlin`.
3. Write `<name>.toml`:
   - `program_id = "com.cyberyak.<name>"`, `program_version = "0.1.0"`.
   - Valid categories.
   - Every parameter labelled.
   - Validate it with the toml-validator.
4. Write `<name>.vhd`:
   - Start with the smallest version of the effect that works.
   - Document latency and EBR in the header.
   - Use a fader dry/wet mix through `interpolator`, unless that makes no sense for the effect.
5. If the effect has non-trivial maths, first prototype it in Python (NumPy) under `programs/cyberyak/<name>/model/`,
   with the hardware quantisation applied. `programs/cyberyak/kaleido/model/kaleido_model.py` is the pattern.
6. Run `/vm-build programs/cyberyak/<name> sd`, fix errors, then `/vm-preview programs/cyberyak/<name>`.
   Iterate until the output matches the model, then build `both`.
