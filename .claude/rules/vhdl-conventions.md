---
paths:
  - "**/*.vhd"
  - "**/*.toml"
---

# Videomancer program conventions (from the SDK docs and examples)

- Implement `architecture <name> of program_top`. Never write a custom entity.
- Use `ieee.std_logic_1164`, `ieee.numeric_std`, `work.core_pkg`, `work.video_timing_pkg`.
- Every process is `if rising_edge(clk)` only. No resets, `wait` or `after`.
- Registers:
  - `registers_in(0..5)` are knobs 1–6, 10-bit.
  - `registers_in(6)` bits 0–4 are toggles 7–11.
  - `registers_in(7)` is fader 12.
  - `registers_in(8)(3 downto 0)` is the video timing ID.
- Delay `hsync_n`, `vsync_n`, `field_n` and `avid` by the exact data latency.
- `C_PROCESSING_DELAY_CLKS` = total `data_in`→`data_out` latency, **divisible by 4** (pad with stages).
- SDK DSP modules (`interpolator`, `multiplier_s`, `variable_delay_u`, `sin_cos_full_lut_10x10`, `lfsr16`, …):
  - Instantiate as `entity work.<name>`.
  - Their valid signal leads data by 1 cycle.
- Header comment documents: overview, pipeline stages, total latency, EBR use, register map.
- Scale spatial sizes per resolution from the timing ID. Community programs treat sizes as fractions of 1080p.
- TOML limits:
  - `program_name` ≤ 31 characters, `description` ≤ 127.
  - `categories` from `videomancer-sdk/docs/program-categories.md`.
  - `program_type` is `processing` or `synthesis`.
  - `core = "yuv444_30b"`, `hardware_compatibility = ["rev_b"]`.
- Validate TOML: `python3 videomancer-sdk/tools/toml-validator/toml_schema_validator.py <toml>`.
- Coarse frame stores are the only 2-D memory. See `feedback.vhd` and `afterimage.vhd` in the community programs for patterns.
