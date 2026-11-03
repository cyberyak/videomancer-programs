-- Copyright (C) 2026 cyberyak
-- SPDX-License-Identifier: GPL-3.0-only
--
-- This file is part of Videomancer Community Programs.
-- See LICENSE file in the repository root for full license text.
--
-- Program Name:
--   Kaleido
--
-- Author:
--   cyberyak
--
-- Overview:
--   A two-dimensional kaleidoscope. Videomancer has no frame buffer, so a
--   window of the input is captured into a coarse tile store (128 x 128 luma,
--   64 x 32 chroma) and every output pixel reads the store through a
--   rotating, zooming coordinate that is folded by the square mirror group
--   p4m: mirror lines on the tile edges plus one diagonal. A kaleidoscope
--   shows one fundamental region many times, so the coarse store still
--   fills the screen.
--
--   Version 0.1: square mode only (see DESIGN.md for the radial, triangle
--   and hex modes).
--
-- Resources:
--   Luma store  16384 x 6 bit (inferred EBR, 24 blocks)
--   Chroma store 2048 x 8 bit (inferred EBR, 4 blocks): U4 & V4
--   1x sin_cos_full_lut_10x10, 3x interpolator_u, no hardware multipliers
--
-- Coordinates:
--   (u, v) are 24-bit signed, 1.0 tile = 2^16. Per frame a vblank sequencer
--   computes, with one serial shift-add multiplier (8 products x 14 clocks):
--     c = cos(a) * k / 512, s = sin(a) * k / 512   (k = tile step per pixel)
--     u0 = -c * W/2 - s * H/2,  v0 = s * W/2 - c * H/2   (screen centre = 0,0)
--   then per active pixel u += c, v -= s, and per line u_row += s,
--   v_row += c. Sizes scale by 2^shift from the measured active width
--   (>=1024: 0, >=512: 1, >=256: 2, else 3) so decimated simulations and SD
--   keep the HD composition.
--
-- Fold (p4m):
--   fraction f = u(15 downto 9) (7 bits = store column); Mirror mode inverts
--   f on odd tiles (u(16) = '1'); Diagonal swaps (fu, fv) when fv > fu.
--
-- Capture:
--   col = (x - xc) * r + 64 * 256, row likewise (r = store cells per pixel
--   x 256, capped at 256 so every cell is written), written every active
--   pixel inside the window (the last pixel of a cell wins: point sampling).
--   Y is stored rounded to 6 bits, U/V to 4.
--
-- Pipeline (data_in -> data_out):
--   P0  register (u, v) and input        P1  tile fraction + mirror
--   P2  diagonal swap, store address     P3  store read
--   P4  expand to 10 bit, seam glow      P5..P7  pad
--   interpolator (dry/wet mix)           4 clocks
--   Total C_PROCESSING_DELAY_CLKS = 12 (divisible by 4)
--
-- Limitations (v0.1):
--   Interlaced inputs are treated per field (the pattern is 2:1 stretched).
--   Single-bank store: fast motion inside the window can tear (use Freeze).
--
-- Register map:
--   Pot 1  (registers_in(0))    Angle      static rotation, 0..360 deg
--   Pot 2  (registers_in(1))    Zoom       tile size (up = larger tiles)
--   Pot 3  (registers_in(2))    Spin       rotation speed, bipolar
--   Pot 4  (registers_in(3))    Source X   capture window pan
--   Pot 5  (registers_in(4))    Source Y   capture window pan
--   Pot 6  (registers_in(5))    Window     capture window size
--   Tog 7  (registers_in(6)(0)) Mirror / Repeat
--   Tog 8  (registers_in(6)(1)) Diagonal on / off
--   Tog 9  (registers_in(6)(2)) Capture live / freeze
--   Tog 10 (registers_in(6)(3)) Seams off / glow
--   Fader  (registers_in(7))    Mix        dry/wet
--------------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

library work;
use work.all;
use work.core_pkg.all;
use work.video_timing_pkg.all;

architecture kaleido of program_top is

    constant C_PROCESSING_DELAY_CLKS : integer := 12;
    constant C_WET_CLKS              : integer := 8;   -- P0..P7
    constant C_MUL_STEPS             : integer := 14;  -- multiplier bits

    -- ------------------------------------------------------------------------
    -- Stores
    -- ------------------------------------------------------------------------
    type t_luma_ram is array (0 to 16383) of unsigned(5 downto 0);
    type t_chroma_ram is array (0 to 2047) of std_logic_vector(7 downto 0);
    signal ram_y : t_luma_ram := (others => to_unsigned(4, 6));         -- black
    signal ram_c : t_chroma_ram := (others => x"88");                   -- neutral

    -- ------------------------------------------------------------------------
    -- Controls
    -- ------------------------------------------------------------------------
    signal s_angle_knob : unsigned(9 downto 0);
    signal s_zoom       : unsigned(9 downto 0);
    signal s_spin       : unsigned(9 downto 0);
    signal s_pan_x      : unsigned(9 downto 0);
    signal s_pan_y      : unsigned(9 downto 0);
    signal s_window     : unsigned(9 downto 0);
    signal s_repeat     : std_logic;
    signal s_diag_off   : std_logic;
    signal s_freeze     : std_logic;
    signal s_seams      : std_logic;
    signal s_mix        : unsigned(9 downto 0);

    -- ------------------------------------------------------------------------
    -- Raster tracking
    -- ------------------------------------------------------------------------
    signal s_avid_prev  : std_logic := '0';
    signal s_vs_prev    : std_logic := '1';
    signal s_x_cnt      : unsigned(11 downto 0) := (others => '0');
    signal s_y_cnt      : unsigned(11 downto 0) := (others => '0');
    signal s_width      : unsigned(11 downto 0) := to_unsigned(1920, 12);
    signal s_height     : unsigned(11 downto 0) := to_unsigned(1080, 12);
    signal s_shift      : integer range 0 to 3 := 0;

    -- ------------------------------------------------------------------------
    -- Per-frame sequencer
    -- ------------------------------------------------------------------------
    signal s_seq_busy   : std_logic := '0';
    signal s_seq_idx    : integer range 0 to 7 := 0;
    signal s_seq_step   : integer range 0 to C_MUL_STEPS := 0;
    signal s_mul_a      : signed(39 downto 0) := (others => '0');
    signal s_mul_b      : unsigned(C_MUL_STEPS - 1 downto 0) := (others => '0');
    signal s_mul_acc    : signed(39 downto 0) := (others => '0');

    signal s_spin_acc   : unsigned(15 downto 0) := (others => '0');
    signal s_angle      : unsigned(9 downto 0) := (others => '0');
    signal s_sin        : signed(9 downto 0);
    signal s_cos        : signed(9 downto 0);
    signal s_k          : unsigned(C_MUL_STEPS - 1 downto 0) := (others => '0');
    signal s_r          : unsigned(C_MUL_STEPS - 1 downto 0) := (others => '0');
    signal s_xc         : signed(23 downto 0) := (others => '0');
    signal s_yc         : signed(23 downto 0) := (others => '0');
    signal s_c_inc      : signed(23 downto 0) := (others => '0');
    signal s_s_inc      : signed(23 downto 0) := (others => '0');
    signal s_t1, s_t2, s_t3, s_t4 : signed(23 downto 0) := (others => '0');
    signal s_xa         : signed(23 downto 0) := (others => '0');

    -- ------------------------------------------------------------------------
    -- Read-side DDA and capture-side counters
    -- ------------------------------------------------------------------------
    signal s_u, s_v         : signed(23 downto 0) := (others => '0');
    signal s_u_row, s_v_row : signed(23 downto 0) := (others => '0');
    signal s_col_acc        : signed(23 downto 0) := (others => '0');
    signal s_col0           : signed(23 downto 0) := (others => '0');
    signal s_row_acc        : signed(23 downto 0) := (others => '0');

    -- ------------------------------------------------------------------------
    -- Pipeline
    -- ------------------------------------------------------------------------
    signal p0_u, p0_v       : signed(23 downto 0) := (others => '0');
    signal p1_fu, p1_fv     : unsigned(6 downto 0) := (others => '0');
    signal p2_addr_y        : unsigned(13 downto 0) := (others => '0');
    signal p2_addr_c        : unsigned(10 downto 0) := (others => '0');
    signal p2_seam, p3_seam : std_logic := '0';
    signal p3_y             : unsigned(5 downto 0) := (others => '0');
    signal p3_c             : std_logic_vector(7 downto 0) := x"88";
    signal p4_y, p4_u, p4_v : unsigned(9 downto 0) := (others => '0');

    type t_pad is array (0 to 2) of unsigned(9 downto 0);
    signal s_pad_y, s_pad_u, s_pad_v : t_pad := (others => (others => '0'));

    signal s_wr_en          : std_logic := '0';
    signal s_wr_addr_y      : unsigned(13 downto 0) := (others => '0');
    signal s_wr_addr_c      : unsigned(10 downto 0) := (others => '0');
    signal s_wr_y           : unsigned(5 downto 0) := (others => '0');
    signal s_wr_c           : std_logic_vector(7 downto 0) := x"88";

    signal s_dry_y, s_dry_u, s_dry_v : std_logic_vector(9 downto 0);
    signal s_mix_y, s_mix_u, s_mix_v : unsigned(9 downto 0);
    signal s_mix_y_valid, s_mix_u_valid, s_mix_v_valid : std_logic;

    -- Round a 10-bit sample to its top n bits, saturating at the top code.
    function round_sat(x : std_logic_vector(9 downto 0); n : integer) return unsigned is
        variable v : unsigned(10 downto 0);
    begin
        v := resize(unsigned(x), 11) + to_unsigned(2 ** (9 - n), 11);
        if v(10) = '1' then
            return to_unsigned(2 ** n - 1, n);
        end if;
        return v(9 downto 10 - n);
    end function;

begin

    -- ========================================================================
    -- Register mapping
    -- ========================================================================
    s_angle_knob <= unsigned(registers_in(0));
    s_zoom       <= unsigned(registers_in(1));
    s_spin       <= unsigned(registers_in(2));
    s_pan_x      <= unsigned(registers_in(3));
    s_pan_y      <= unsigned(registers_in(4));
    s_window     <= unsigned(registers_in(5));
    s_repeat     <= registers_in(6)(0);
    s_diag_off   <= registers_in(6)(1);
    s_freeze     <= registers_in(6)(2);
    s_seams      <= registers_in(6)(3);
    s_mix        <= unsigned(registers_in(7));

    sin_cos_inst : entity work.sin_cos_full_lut_10x10
        port map(angle_in => std_logic_vector(s_angle), sin_out => s_sin, cos_out => s_cos);

    -- ========================================================================
    -- Raster tracking, per-frame sequencer, DDA and capture counters
    -- ========================================================================
    p_control : process(clk)
        variable v_avid_fall : boolean;
        variable v_vs_fall   : boolean;
        variable v_dz        : signed(10 downto 0);
        variable v_res       : signed(39 downto 0);
        variable v_r         : unsigned(C_MUL_STEPS - 1 downto 0);
    begin
        if rising_edge(clk) then
            v_avid_fall := data_in.avid = '0' and s_avid_prev = '1';
            v_vs_fall   := data_in.vsync_n = '0' and s_vs_prev = '1';
            s_avid_prev <= data_in.avid;
            s_vs_prev   <= data_in.vsync_n;

            -- Active pixel / line counters and measured frame size
            if data_in.avid = '1' then
                s_x_cnt <= s_x_cnt + 1;
            else
                s_x_cnt <= (others => '0');
            end if;
            if v_avid_fall then
                s_width <= s_x_cnt;
                s_y_cnt <= s_y_cnt + 1;
            end if;
            if v_vs_fall then
                s_height <= s_y_cnt;
                s_y_cnt  <= (others => '0');
                if s_width >= 1024 then
                    s_shift <= 0;
                elsif s_width >= 512 then
                    s_shift <= 1;
                elsif s_width >= 256 then
                    s_shift <= 2;
                else
                    s_shift <= 3;
                end if;
            end if;

            -- Read-side DDA: (u, v) is the coordinate of the pixel in data_in
            if data_in.avid = '1' then
                s_u <= s_u + s_c_inc;
                s_v <= s_v - s_s_inc;
                s_col_acc <= s_col_acc + signed(resize(s_r, 24));
            else
                s_u <= s_u_row;
                s_v <= s_v_row;
                s_col_acc <= s_col0;
            end if;
            if v_avid_fall then
                s_u_row   <= s_u_row + s_s_inc;
                s_v_row   <= s_v_row + s_c_inc;
                s_row_acc <= s_row_acc + signed(resize(s_r, 24));
            end if;

            -- Per-frame sequencer: latch controls, then 8 serial products
            if v_vs_fall then
                v_dz := signed(resize(s_spin, 11)) - 512;
                if v_dz > -8 and v_dz < 8 then
                    v_dz := (others => '0');
                end if;
                s_spin_acc <= unsigned(signed(s_spin_acc) + resize(v_dz, 16));
                s_angle    <= s_angle_knob + s_spin_acc(15 downto 6);
                -- k: tile step per pixel (2^16 = one tile); r: store cells
                -- per pixel x 256. Both scale with the measured width.
                s_k  <= shift_left(resize(to_unsigned(543, 10) - s_zoom(9 downto 1), C_MUL_STEPS), s_shift);
                -- r is capped at 256 (one cell per pixel) so no store cell is
                -- skipped; below 128 px the window simply stops shrinking.
                v_r := shift_left(resize(to_unsigned(287, 10) - s_window(9 downto 2), C_MUL_STEPS), s_shift);
                if v_r > 256 then
                    v_r := to_unsigned(256, C_MUL_STEPS);
                end if;
                s_r  <= v_r;
                s_xc <= resize(signed('0' & s_width(11 downto 1)), 24)
                        + shift_right(resize(signed(resize(s_pan_x, 11)) - 512, 24), s_shift);
                s_yc <= resize(signed('0' & s_y_cnt(11 downto 1)), 24)
                        + shift_right(resize(signed(resize(s_pan_y, 11)) - 512, 24), s_shift);
                s_seq_busy <= '1';
                s_seq_idx  <= 0;
                s_seq_step <= C_MUL_STEPS;   -- "load" step for product 0
            elsif s_seq_busy = '1' then
                if s_seq_step = C_MUL_STEPS then
                    -- Load the operands of product s_seq_idx
                    s_mul_acc  <= (others => '0');
                    s_seq_step <= 0;
                    case s_seq_idx is
                        when 0 => s_mul_a <= resize(s_cos, 40);   s_mul_b <= s_k;
                        when 1 => s_mul_a <= resize(s_sin, 40);   s_mul_b <= s_k;
                        when 2 => s_mul_a <= resize(s_c_inc, 40); s_mul_b <= resize(s_width(11 downto 1), C_MUL_STEPS);
                        when 3 => s_mul_a <= resize(s_s_inc, 40); s_mul_b <= resize(s_height(11 downto 1), C_MUL_STEPS);
                        when 4 => s_mul_a <= resize(s_s_inc, 40); s_mul_b <= resize(s_width(11 downto 1), C_MUL_STEPS);
                        when 5 => s_mul_a <= resize(s_c_inc, 40); s_mul_b <= resize(s_height(11 downto 1), C_MUL_STEPS);
                        when 6 => s_mul_a <= resize(s_xc, 40);    s_mul_b <= s_r;
                        when 7 => s_mul_a <= resize(s_yc, 40);    s_mul_b <= s_r;
                    end case;
                elsif s_seq_step < C_MUL_STEPS - 1 then
                    if s_mul_b(0) = '1' then
                        s_mul_acc <= s_mul_acc + s_mul_a;
                    end if;
                    s_mul_a    <= shift_left(s_mul_a, 1);
                    s_mul_b    <= shift_right(s_mul_b, 1);
                    s_seq_step <= s_seq_step + 1;
                else
                    -- Last bit: finish the product and store it
                    v_res := s_mul_acc;
                    if s_mul_b(0) = '1' then
                        v_res := v_res + s_mul_a;
                    end if;
                    case s_seq_idx is
                        when 0 => s_c_inc <= resize(shift_right(v_res, 9), 24);
                        when 1 => s_s_inc <= resize(shift_right(v_res, 9), 24);
                        when 2 => s_t1 <= resize(v_res, 24);
                        when 3 => s_t2 <= resize(v_res, 24);
                        when 4 => s_t3 <= resize(v_res, 24);
                        when 5 => s_t4 <= resize(v_res, 24);
                        when 6 => s_xa <= resize(v_res, 24);
                        when 7 =>
                            -- All products ready: start the frame
                            s_u_row   <= -(s_t1 + s_t2);
                            s_v_row   <= s_t3 - s_t4;
                            s_col0    <= to_signed(16384, 24) - s_xa;
                            s_row_acc <= to_signed(16384, 24) - resize(v_res, 24);
                            s_seq_busy <= '0';
                    end case;
                    if s_seq_idx < 7 then
                        s_seq_idx <= s_seq_idx + 1;
                    end if;
                    s_seq_step <= C_MUL_STEPS;
                end if;
            end if;
        end if;
    end process p_control;

    -- ========================================================================
    -- Capture: point-sample the window into the stores
    -- ========================================================================
    p_capture : process(clk)
    begin
        if rising_edge(clk) then
            s_wr_en <= '0';
            if data_in.avid = '1' and s_freeze = '0'
               and s_col_acc >= 0 and s_col_acc < 32768
               and s_row_acc >= 0 and s_row_acc < 32768 then
                s_wr_en <= '1';
            end if;
            s_wr_addr_y <= unsigned(s_row_acc(14 downto 8)) & unsigned(s_col_acc(14 downto 8));
            s_wr_addr_c <= unsigned(s_row_acc(14 downto 10)) & unsigned(s_col_acc(14 downto 9));
            s_wr_y      <= round_sat(data_in.y, 6);
            s_wr_c      <= std_logic_vector(round_sat(data_in.u, 4))
                         & std_logic_vector(round_sat(data_in.v, 4));
        end if;
    end process p_capture;

    p_ram_y : process(clk)
    begin
        if rising_edge(clk) then
            if s_wr_en = '1' then
                ram_y(to_integer(s_wr_addr_y)) <= s_wr_y;
            end if;
            p3_y <= ram_y(to_integer(p2_addr_y));
        end if;
    end process p_ram_y;

    p_ram_c : process(clk)
    begin
        if rising_edge(clk) then
            if s_wr_en = '1' then
                ram_c(to_integer(s_wr_addr_c)) <= s_wr_c;
            end if;
            p3_c <= ram_c(to_integer(p2_addr_c));
        end if;
    end process p_ram_c;

    -- ========================================================================
    -- Read pipeline: fold, address, expand
    -- ========================================================================
    p_fold : process(clk)
        variable v_fu, v_fv : unsigned(6 downto 0);
    begin
        if rising_edge(clk) then
            -- P0
            p0_u <= s_u;
            p0_v <= s_v;

            -- P1: tile fraction, mirrored on odd tiles
            v_fu := unsigned(p0_u(15 downto 9));
            v_fv := unsigned(p0_v(15 downto 9));
            if s_repeat = '0' and p0_u(16) = '1' then
                v_fu := not v_fu;
            end if;
            if s_repeat = '0' and p0_v(16) = '1' then
                v_fv := not v_fv;
            end if;
            p1_fu <= v_fu;
            p1_fv <= v_fv;

            -- P2: diagonal mirror, store addresses, seam flag
            if s_diag_off = '0' and p1_fv > p1_fu then
                v_fu := p1_fv;
                v_fv := p1_fu;
            else
                v_fu := p1_fu;
                v_fv := p1_fv;
            end if;
            p2_addr_y <= v_fv & v_fu;
            p2_addr_c <= v_fv(6 downto 2) & v_fu(6 downto 1);
            if v_fu = 0 or v_fu = 127 or v_fv = 0 or v_fv = 127
               or (s_diag_off = '0' and v_fu = v_fv) then
                p2_seam <= s_seams;
            else
                p2_seam <= '0';
            end if;

            -- P3: store read happens in p_ram_y / p_ram_c
            p3_seam <= p2_seam;

            -- P4: expand to 10 bits, seam glow
            if p3_seam = '1' then
                if p3_y >= 48 then
                    p4_y <= to_unsigned(940, 10);
                else
                    p4_y <= (p3_y & "0000") + 192;
                end if;
            else
                p4_y <= p3_y & "0000";
            end if;
            p4_u <= unsigned(p3_c(7 downto 4)) & "000000";
            p4_v <= unsigned(p3_c(3 downto 0)) & "000000";

            -- P5..P7: pad to C_WET_CLKS
            s_pad_y <= p4_y & s_pad_y(0 to 1);
            s_pad_u <= p4_u & s_pad_u(0 to 1);
            s_pad_v <= p4_v & s_pad_v(0 to 1);
        end if;
    end process p_fold;

    -- ========================================================================
    -- Dry/wet mix
    -- ========================================================================
    mix_y_inst : entity work.interpolator_u
        generic map(G_WIDTH => C_VIDEO_DATA_WIDTH, G_FRAC_BITS => C_VIDEO_DATA_WIDTH,
                    G_OUTPUT_MIN => 0, G_OUTPUT_MAX => 1023)
        port map(clk => clk, enable => '1',
                 a => unsigned(s_dry_y), b => s_pad_y(2),
                 t => s_mix, result => s_mix_y, valid => s_mix_y_valid);

    mix_u_inst : entity work.interpolator_u
        generic map(G_WIDTH => C_VIDEO_DATA_WIDTH, G_FRAC_BITS => C_VIDEO_DATA_WIDTH,
                    G_OUTPUT_MIN => 0, G_OUTPUT_MAX => 1023)
        port map(clk => clk, enable => '1',
                 a => unsigned(s_dry_u), b => s_pad_u(2),
                 t => s_mix, result => s_mix_u, valid => s_mix_u_valid);

    mix_v_inst : entity work.interpolator_u
        generic map(G_WIDTH => C_VIDEO_DATA_WIDTH, G_FRAC_BITS => C_VIDEO_DATA_WIDTH,
                    G_OUTPUT_MIN => 0, G_OUTPUT_MAX => 1023)
        port map(clk => clk, enable => '1',
                 a => unsigned(s_dry_v), b => s_pad_v(2),
                 t => s_mix, result => s_mix_v, valid => s_mix_v_valid);

    -- ========================================================================
    -- Sync and dry-data delay
    -- ========================================================================
    p_delay : process(clk)
        type t_sync_delay is array (0 to C_PROCESSING_DELAY_CLKS - 1) of std_logic;
        type t_data_delay is array (0 to C_WET_CLKS - 1) of std_logic_vector(9 downto 0);
        variable v_avid  : t_sync_delay := (others => '0');
        variable v_hsync : t_sync_delay := (others => '1');
        variable v_vsync : t_sync_delay := (others => '1');
        variable v_field : t_sync_delay := (others => '1');
        variable v_y     : t_data_delay := (others => (others => '0'));
        variable v_u     : t_data_delay := (others => (others => '0'));
        variable v_v     : t_data_delay := (others => (others => '0'));
    begin
        if rising_edge(clk) then
            v_avid  := data_in.avid    & v_avid(0 to C_PROCESSING_DELAY_CLKS - 2);
            v_hsync := data_in.hsync_n & v_hsync(0 to C_PROCESSING_DELAY_CLKS - 2);
            v_vsync := data_in.vsync_n & v_vsync(0 to C_PROCESSING_DELAY_CLKS - 2);
            v_field := data_in.field_n & v_field(0 to C_PROCESSING_DELAY_CLKS - 2);
            v_y     := data_in.y & v_y(0 to C_WET_CLKS - 2);
            v_u     := data_in.u & v_u(0 to C_WET_CLKS - 2);
            v_v     := data_in.v & v_v(0 to C_WET_CLKS - 2);

            data_out.avid    <= v_avid(C_PROCESSING_DELAY_CLKS - 1);
            data_out.hsync_n <= v_hsync(C_PROCESSING_DELAY_CLKS - 1);
            data_out.vsync_n <= v_vsync(C_PROCESSING_DELAY_CLKS - 1);
            data_out.field_n <= v_field(C_PROCESSING_DELAY_CLKS - 1);
            s_dry_y <= v_y(C_WET_CLKS - 1);
            s_dry_u <= v_u(C_WET_CLKS - 1);
            s_dry_v <= v_v(C_WET_CLKS - 1);
        end if;
    end process p_delay;

    data_out.y <= std_logic_vector(s_mix_y);
    data_out.u <= std_logic_vector(s_mix_u);
    data_out.v <= std_logic_vector(s_mix_v);

end architecture kaleido;
