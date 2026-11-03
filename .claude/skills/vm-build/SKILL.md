---
name: vm-build
description: Synthesize a Videomancer program to FPGA bitstreams and report logic cells, EBR and Fmax. Use after editing a program's VHDL, or to check whether a design fits the iCE40 HX4K.
argument-hint: "<program_dir> [sd|hd|both]"
allowed-tools: Bash(tools/vm_build.sh:*)
---

Run from the workspace root:

```bash
tools/vm_build.sh $ARGUMENTS
```

The program dir holds `<name>.vhd` + `<name>.toml`, for example `programs/cyberyak/kaleido` or
`videomancer-sdk/programs/sabattier`. Each config takes about 35 s.
Logs go to `build/<name>/<config>.log`.

Report back:
- LC and EBR for each config, as used/available.
- Program cost: LC minus ~1,600 for the core.
- Post-route Fmax. The `ADV7611_CLK … FAIL at 74.25 MHz` line also appears for passthru,
  so flag it only if it gets clearly worse than passthru (~56–57 MHz).
- On a failure, the first GHDL/Yosys error with file:line. Read the log for context first;
  only then propose a fix.

A full signed `.vmprog` comes from `videomancer-sdk/build_programs.sh`, which needs Ed25519 keys
(`videomancer-sdk/docs/package-signing-guide.md`).
