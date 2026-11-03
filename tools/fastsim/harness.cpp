// Copyright (C) 2026 cyberyak
// SPDX-License-Identifier: GPL-3.0-only
//
// Verilator harness for one Videomancer program (vm_fastsim_top wrapper).
//
// Drives the same raster as the SDK's VHDL image tester (image_converter.py
// build_stimulus + testbench_gen.py capture) so the two can be diffed:
//   - progressive: per frame h + 8 lines (vsync low on lines 0-2, active
//     from line 4), per line w + 64 clocks (hsync low on clocks 0-7, active
//     from clock 32); blanking pixels are Y=64 U=V=512
//   - interlaced: two fields per frame (even rows, field_n=1; odd rows,
//     field_n=0), each with its own vsync
//   - 32 drain lines after the last frame
// Capture follows the testbench exactly: count OUTPUT vsync/hsync falls and
// take the active window of every field after the warm-up ones. Unlike the
// testbench, it can capture several consecutive frames.
//
// Usage: harness IN.raw OUT.raw W H INTERLACED WARMUP CAPTURE REGS
//        harness --serve W H [--rgb M0..M8]   (see serve() below)
//        either form also takes --timing HB VB HS VS
//   IN.raw   W*H*3 uint16 (Y,U,V per pixel, row-major), 10-bit values
//   OUT.raw  CAPTURE*W*H*3 uint16 in the same layout (fields weaved)
//   REGS     32 comma-separated register values (0-1023)
#include "Vvm_fastsim_top.h"
#include "verilated.h"

#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

// Raster geometry. The defaults match the SDK image tester's compact raster;
// --timing HB VB HS VS gives a real video standard's blanking (e.g. 480p:
// 138 45 62 6) for programs that need real line lengths.
static int H_BLANK = 64, V_BLANK = 8, HSYNC_CLKS = 8, VSYNC_LINES = 3;
static const int DRAIN_LINES = 32;

struct Sim {
    Vvm_fastsim_top top;
    int w, field_h, capture_fields, warmup_fields;
    std::vector<uint16_t> captured;
    // Output-side capture state (mirrors tb_vit.vhd p_sim)
    bool hs_prev = true, vs_prev = true, capturing = false;
    int out_field = 0, line = 0, col = 0;
    size_t target;

    void clock(uint16_t y, uint16_t u, uint16_t v, bool avid, bool hs, bool vs, bool fd) {
        top.yi = y; top.ui = u; top.vi = v;
        top.avid_i = avid; top.hsync_n_i = hs; top.vsync_n_i = vs; top.field_n_i = fd;
        top.clk = 0; top.eval();
        top.clk = 1; top.eval();

        bool hs_o = top.hsync_n_o, vs_o = top.vsync_n_o;
        bool hs_fall = hs_prev && !hs_o, vs_fall = vs_prev && !vs_o;
        hs_prev = hs_o; vs_prev = vs_o;
        if (vs_fall) {
            out_field++; line = 0; col = 0;
            if (out_field > warmup_fields) capturing = true;
        }
        if (hs_fall) { line++; col = 0; } else { col++; }
        if (capturing && captured.size() < target
            && line >= V_BLANK / 2 + 1 && line <= V_BLANK / 2 + field_h
            && col >= H_BLANK / 2 && col < H_BLANK / 2 + w) {
            captured.push_back(top.yo); captured.push_back(top.uo); captured.push_back(top.vo);
        }
    }

    // One field (or progressive frame) of raster; rows = pointer to the
    // first source row, stride = rows to step per field line.
    void field(const uint16_t *img, int first_row, int row_step, bool fd) {
        for (int ln = 0; ln < field_h + V_BLANK; ln++) {
            bool vs = ln >= VSYNC_LINES;
            bool act_line = ln >= V_BLANK / 2 && ln < V_BLANK / 2 + field_h;
            const uint16_t *row = act_line
                ? img + (size_t)(first_row + (ln - V_BLANK / 2) * row_step) * w * 3 : nullptr;
            for (int c = 0; c < w + H_BLANK; c++) {
                bool hs = c >= HSYNC_CLKS;
                bool act = act_line && c >= H_BLANK / 2 && c < H_BLANK / 2 + w;
                if (act) {
                    const uint16_t *p = row + (size_t)(c - H_BLANK / 2) * 3;
                    clock(p[0], p[1], p[2], true, hs, vs, fd);
                } else {
                    clock(64, 512, 512, false, hs, vs, fd);
                }
            }
        }
    }
};

static void set_regs(Vvm_fastsim_top &top, const int *regs) {
    top.r0 = regs[0];   top.r1 = regs[1];   top.r2 = regs[2];   top.r3 = regs[3];
    top.r4 = regs[4];   top.r5 = regs[5];   top.r6 = regs[6];   top.r7 = regs[7];
    top.r8 = regs[8];   top.r9 = regs[9];   top.r10 = regs[10]; top.r11 = regs[11];
    top.r12 = regs[12]; top.r13 = regs[13]; top.r14 = regs[14]; top.r15 = regs[15];
    top.r16 = regs[16]; top.r17 = regs[17]; top.r18 = regs[18]; top.r19 = regs[19];
    top.r20 = regs[20]; top.r21 = regs[21]; top.r22 = regs[22]; top.r23 = regs[23];
    top.r24 = regs[24]; top.r25 = regs[25]; top.r26 = regs[26]; top.r27 = regs[27];
    top.r28 = regs[28]; top.r29 = regs[29]; top.r30 = regs[30]; top.r31 = regs[31];
}

// BT.601 limited-range conversions, matching the SDK tester's
// image_converter.py (same matrices, numpy-style round-half-even, clipping)
// so RGB serve mode gives bit-identical results to converting in Python.
// The inverse matrix is passed in from Python (np.linalg.inv) for the same
// floating-point values.
static const double M_RGB2YUV[3][3] = {
    {0.299, 0.587, 0.114},
    {-0.168736, -0.331264, 0.500000},
    {0.500000, -0.418688, -0.081312},
};
static double clip(double x, double lo, double hi) { return x < lo ? lo : (x > hi ? hi : x); }

static void rgb_to_yuv10(const uint8_t *rgb, uint16_t *yuv, size_t n) {
    for (size_t i = 0; i < n; i++, rgb += 3, yuv += 3) {
        double r = rgb[0] / 255.0, g = rgb[1] / 255.0, b = rgb[2] / 255.0;
        double y = M_RGB2YUV[0][0] * r + M_RGB2YUV[0][1] * g + M_RGB2YUV[0][2] * b;
        double u = M_RGB2YUV[1][0] * r + M_RGB2YUV[1][1] * g + M_RGB2YUV[1][2] * b;
        double v = M_RGB2YUV[2][0] * r + M_RGB2YUV[2][1] * g + M_RGB2YUV[2][2] * b;
        yuv[0] = (uint16_t)std::nearbyint(clip(y * (940 - 64) + 64, 64, 940));
        yuv[1] = (uint16_t)std::nearbyint(clip(u * (960 - 64) + 512, 64, 960));
        yuv[2] = (uint16_t)std::nearbyint(clip(v * (960 - 64) + 512, 64, 960));
    }
}

static void yuv10_to_rgb(const uint16_t *yuv, uint8_t *rgb, size_t n, const double inv[9]) {
    for (size_t i = 0; i < n; i++, rgb += 3, yuv += 3) {
        double y = (yuv[0] - 64.0) / (940 - 64);
        double u = (yuv[1] - 512.0) / (960 - 64);
        double v = (yuv[2] - 512.0) / (960 - 64);
        for (int c = 0; c < 3; c++)
            rgb[c] = (uint8_t)std::nearbyint(clip((inv[3 * c] * y + inv[3 * c + 1] * u + inv[3 * c + 2] * v) * 255.0, 0, 255));
    }
}

// Serve mode: keep one simulation alive across frames (state such as spin
// and the tile store carries over). Per frame, stdin carries one text line
// of 32 comma-separated registers followed by the input frame; stdout
// returns the captured output of that frame. Frames are W*H*3 uint16 YUV
// (progressive), or with --rgb M0..M8 (the YUV->RGB inverse matrix)
// W*H*3 uint8 RGB in both directions.
static int serve(int w, int h, const double *inv) {
    Sim s;
    s.w = w; s.field_h = h; s.warmup_fields = 0; s.capture_fields = 1;
    s.target = (size_t)w * h * 3;
    size_t px = (size_t)w * h;
    std::vector<uint16_t> img(s.target), out(s.target);
    std::vector<uint8_t> rgb(inv ? s.target : 0);
    char line[4096];
    int regs[32];
    while (fgets(line, sizeof line, stdin)) {
        memset(regs, 0, sizeof regs);
        char *tok = strtok(line, ",\n");
        for (int i = 0; i < 32 && tok; i++, tok = strtok(nullptr, ",\n")) regs[i] = atoi(tok);
        if (inv) {
            if (fread(rgb.data(), 1, rgb.size(), stdin) != rgb.size()) return 1;
            rgb_to_yuv10(rgb.data(), img.data(), px);
        } else if (fread(img.data(), 2, img.size(), stdin) != img.size()) {
            return 1;
        }
        set_regs(s.top, regs);
        s.captured.clear(); s.capturing = false; s.out_field = 0;
        s.field(img.data(), 0, 1, true);
        size_t got = s.captured.size(), pad = s.target - got;
        for (size_t i = 0; i < pad; i += 3) { out[i] = 64; out[i + 1] = 512; out[i + 2] = 512; }
        memcpy(out.data() + pad, s.captured.data(), got * 2);
        if (inv) {
            yuv10_to_rgb(out.data(), rgb.data(), px, inv);
            fwrite(rgb.data(), 1, rgb.size(), stdout);
        } else {
            fwrite(out.data(), 2, out.size(), stdout);
        }
        fflush(stdout);
    }
    return 0;
}

int main(int argc, char **argv) {
    // Strip an optional "--timing HB VB HS VS" from anywhere in the arguments
    for (int i = 1; i + 4 < argc; i++) {
        if (strcmp(argv[i], "--timing") == 0) {
            H_BLANK = atoi(argv[i + 1]); V_BLANK = atoi(argv[i + 2]);
            HSYNC_CLKS = atoi(argv[i + 3]); VSYNC_LINES = atoi(argv[i + 4]);
            for (int j = i; j + 5 < argc; j++) argv[j] = argv[j + 5];
            argc -= 5;
            break;
        }
    }
    if (argc >= 4 && strcmp(argv[1], "--serve") == 0) {
        if (argc == 4) return serve(atoi(argv[2]), atoi(argv[3]), nullptr);
        if (argc == 14 && strcmp(argv[4], "--rgb") == 0) {
            double inv[9];
            for (int i = 0; i < 9; i++) inv[i] = strtod(argv[5 + i], nullptr);
            return serve(atoi(argv[2]), atoi(argv[3]), inv);
        }
        fprintf(stderr, "usage: %s --serve W H [--rgb M0 .. M8]\n", argv[0]);
        return 2;
    }
    if (argc != 9) {
        fprintf(stderr, "usage: %s IN.raw OUT.raw W H INTERLACED WARMUP CAPTURE REGS\n", argv[0]);
        return 2;
    }
    int w = atoi(argv[3]), h = atoi(argv[4]), interlaced = atoi(argv[5]);
    int warmup = atoi(argv[6]), capture = atoi(argv[7]);

    std::vector<uint16_t> img((size_t)w * h * 3);
    FILE *f = fopen(argv[1], "rb");
    if (!f || fread(img.data(), 2, img.size(), f) != img.size()) {
        fprintf(stderr, "cannot read %s\n", argv[1]);
        return 1;
    }
    fclose(f);

    Sim s;
    int regs[32] = {0};
    char *tok = strtok(argv[8], ",");
    for (int i = 0; i < 32 && tok; i++, tok = strtok(nullptr, ",")) regs[i] = atoi(tok);
    set_regs(s.top, regs);

    int fields_per_frame = interlaced ? 2 : 1;
    s.w = w;
    s.field_h = interlaced ? h / 2 : h;
    s.warmup_fields = warmup * fields_per_frame;
    s.capture_fields = capture * fields_per_frame;
    s.target = (size_t)capture * w * h * 3;
    s.captured.reserve(s.target);

    auto t0 = std::chrono::steady_clock::now();
    for (int fr = 0; fr < warmup + capture; fr++) {
        if (interlaced) {
            s.field(img.data(), 0, 2, true);
            s.field(img.data(), 1, 2, false);
        } else {
            s.field(img.data(), 0, 1, true);
        }
    }
    for (int ln = 0; ln < DRAIN_LINES; ln++)
        for (int c = 0; c < w + H_BLANK; c++)
            s.clock(64, 512, 512, false, c >= HSYNC_CLKS, true, true);
    double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();

    // Pad short captures with black, like read_output_file does.
    std::vector<uint16_t> out(s.target);
    size_t got = s.captured.size(), pad = s.target - got;
    for (size_t i = 0; i < pad; i += 3) { out[i] = 64; out[i + 1] = 512; out[i + 2] = 512; }
    std::memcpy(out.data() + pad, s.captured.data(), got * 2);

    // Weave interlaced fields back into frames.
    if (interlaced) {
        std::vector<uint16_t> woven(out.size());
        size_t fh = s.field_h, row = (size_t)w * 3;
        for (int fr = 0; fr < capture; fr++) {
            const uint16_t *src = out.data() + (size_t)fr * h * row;
            uint16_t *dst = woven.data() + (size_t)fr * h * row;
            for (size_t r = 0; r < fh; r++) {
                std::memcpy(dst + (2 * r) * row, src + r * row, row * 2);
                std::memcpy(dst + (2 * r + 1) * row, src + (fh + r) * row, row * 2);
            }
        }
        out.swap(woven);
    }

    f = fopen(argv[2], "wb");
    if (!f || fwrite(out.data(), 2, out.size(), f) != out.size()) {
        fprintf(stderr, "cannot write %s\n", argv[2]);
        return 1;
    }
    fclose(f);
    fprintf(stderr, "fastsim: %d frame(s) %dx%d, captured %zu/%zu px, %.2fs\n",
            warmup + capture, w, h, got / 3, s.target / 3, secs);
    return 0;
}
