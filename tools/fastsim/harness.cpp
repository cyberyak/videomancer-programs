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
//   IN.raw   W*H*3 uint16 (Y,U,V per pixel, row-major), 10-bit values
//   OUT.raw  CAPTURE*W*H*3 uint16 in the same layout (fields weaved)
//   REGS     32 comma-separated register values (0-1023)
#include "Vvm_fastsim_top.h"
#include "verilated.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

static const int H_BLANK = 64, V_BLANK = 8, HSYNC_CLKS = 8, VSYNC_LINES = 3, DRAIN_LINES = 32;

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

int main(int argc, char **argv) {
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
    s.top.r0 = regs[0];   s.top.r1 = regs[1];   s.top.r2 = regs[2];   s.top.r3 = regs[3];
    s.top.r4 = regs[4];   s.top.r5 = regs[5];   s.top.r6 = regs[6];   s.top.r7 = regs[7];
    s.top.r8 = regs[8];   s.top.r9 = regs[9];   s.top.r10 = regs[10]; s.top.r11 = regs[11];
    s.top.r12 = regs[12]; s.top.r13 = regs[13]; s.top.r14 = regs[14]; s.top.r15 = regs[15];
    s.top.r16 = regs[16]; s.top.r17 = regs[17]; s.top.r18 = regs[18]; s.top.r19 = regs[19];
    s.top.r20 = regs[20]; s.top.r21 = regs[21]; s.top.r22 = regs[22]; s.top.r23 = regs[23];
    s.top.r24 = regs[24]; s.top.r25 = regs[25]; s.top.r26 = regs[26]; s.top.r27 = regs[27];
    s.top.r28 = regs[28]; s.top.r29 = regs[29]; s.top.r30 = regs[30]; s.top.r31 = regs[31];

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
