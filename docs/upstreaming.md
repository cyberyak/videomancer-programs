# Sharing a program with the community library

Community programs live in
[lzxindustries/videomancer-community-programs](https://github.com/lzxindustries/videomancer-community-programs)
(also hosted on LZX's Forgejo at git.lzxindustries.net). Our
`programs/cyberyak/<name>/` folders use the same layout, so a program upstreams
by copying its folder.

## Ready-to-share checklist (from the community CONTRIBUTING.md)

- [ ] `architecture <name> of program_top`. Sync delayed to match latency.
      `C_PROCESSING_DELAY_CLKS` divisible by 4.
- [ ] Builds for every config with no errors or unintended latches:
      `tools/vm_build.sh programs/cyberyak/<name> both`
- [ ] Meets timing: 74.25 MHz HD and 27 MHz SD. Compare against the passthru baseline.
- [ ] Complete TOML that passes the toml-validator: name, author, description, categories, every parameter labelled.
- [ ] GPL-3.0 header in every source file:
      ```vhdl
      -- Copyright (C) 2026 cyberyak
      -- SPDX-License-Identifier: GPL-3.0-only
      --
      -- This file is part of Videomancer Community Programs.
      -- See LICENSE file in the repository root for full license text.
      ```
- [ ] VHDL header documents the architecture, pipeline stages, latency, EBR and register map.
- [ ] Tested on real hardware: all parameter ranges, rapid changes, HD/SD, analog/HDMI.
- [ ] Optional `README.md` in the program folder for complex programs. Preview images
      go in our repo only; keep the upstream PR to the files they expect.

## Submitting

```bash
gh repo fork lzxindustries/videomancer-community-programs --clone=false   # once
git -C videomancer-community-programs remote add fork https://github.com/cyberyak/videomancer-community-programs.git
git -C videomancer-community-programs switch -c add-cyberyak-<name>
cp -R programs/cyberyak/<name> videomancer-community-programs/programs/cyberyak/
# remove model/ and preview images unless wanted upstream, then:
git -C videomancer-community-programs add programs/cyberyak/<name>
git -C videomancer-community-programs commit -m "Add cyberyak/<name>"
git -C videomancer-community-programs push fork add-cyberyak-<name>
gh pr create --repo lzxindustries/videomancer-community-programs --head cyberyak:add-cyberyak-<name>
```

Opening the PR is a public action; confirm the target and content before running it.
Check the community README for whether LZX currently prefers PRs on GitHub or on Forgejo.

In this repo, tag the shared version (`git tag <name>-v1.0.0`) and add the PR link
to the program's row in the main README.
