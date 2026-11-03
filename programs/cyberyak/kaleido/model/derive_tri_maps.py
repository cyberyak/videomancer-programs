#!/usr/bin/env python3
# Copyright (C) 2026 cyberyak
# SPDX-License-Identifier: GPL-3.0-only
"""Derive the *333 (triangle kaleidoscope) fold as 6 affine maps.

Skewed coordinates: p = x*e1 + y*e2, e1 = (1, 0), e2 = (1/2, sqrt3/2). Mirror
lines are x = k, y = k, x + y = k (k integer). The fundamental triangle is
T = {x >= 0, y >= 0, x + y <= 1}. The group's translations form
L = {(a, b) : a - b = 0 mod 3}, so a point in rhombus (I, J) with fractions
(f, g) folds by a map that depends only on m = (I - J) mod 3 and on whether
f + g >= 1. Prints each map as (s, t) = A @ (f, g) + c and verifies it
against brute-force reflection folding.
"""
from fractions import Fraction as F
import itertools, random

def refl_x(p, k):  x, y = p; return (2 * k - x, y + x - k)
def refl_y(p, k):  x, y = p; return (x + y - k, 2 * k - y)
def refl_s(p, k):  x, y = p; return (k - y, k - x)

def in_T(p):
    x, y = p
    return x >= 0 and y >= 0 and x + y <= 1

def brute_fold(p):
    """Fold into T by reflecting across T's violated sides (terminates)."""
    for _ in range(10000):
        x, y = p
        if x < 0:   p = refl_x(p, 0)
        elif y < 0: p = refl_y(p, 0)
        elif x + y > 1: p = refl_s(p, 1)
        else: return p
    raise RuntimeError("no convergence")

def affine_from(fn):
    o = fn((F(0), F(0))); a = fn((F(1), F(0))); b = fn((F(0), F(1)))
    A = [[a[0] - o[0], b[0] - o[0]], [a[1] - o[1], b[1] - o[1]]]
    return A, o

maps = {}
reps = {0: (0, 0), 1: (1, 0), 2: (0, 1)}
for m, (I, J) in reps.items():
    for upper in (0, 1):
        # Representative interior point of this triangle
        f0, g0 = (F(1, 4), F(1, 4)) if not upper else (F(3, 4), F(3, 4))
        # Find the group element by folding a triangle of 3 interior points
        # and fitting the affine map from their images.
        pts = [(f0, g0), (f0 + F(1, 16), g0), (f0, g0 + F(1, 16))]
        imgs = [brute_fold((I + f, J + g)) for f, g in pts]
        d1 = ((imgs[1][0] - imgs[0][0]) * 16, (imgs[1][1] - imgs[0][1]) * 16)
        d2 = ((imgs[2][0] - imgs[0][0]) * 16, (imgs[2][1] - imgs[0][1]) * 16)
        A = [[d1[0], d2[0]], [d1[1], d2[1]]]
        c = (imgs[0][0] - A[0][0] * f0 - A[0][1] * g0, imgs[0][1] - A[1][0] * f0 - A[1][1] * g0)
        maps[(m, upper)] = (A, c)

def apply(m, upper, f, g):
    A, c = maps[(m, upper)]
    return (A[0][0] * f + A[0][1] * g + c[0], A[1][0] * f + A[1][1] * g + c[1])

# Verify on random points across many tiles
random.seed(1)
for _ in range(20000):
    X = F(random.randint(-4000, 4000), 97); Y = F(random.randint(-4000, 4000), 89)
    I, J = X.numerator // X.denominator, Y.numerator // Y.denominator
    f, g = X - I, Y - J
    m = (I - J) % 3; up = int(f + g >= 1)
    a = apply(m, up, f, g); b = brute_fold((X, Y))
    assert a == b or (in_T(a) and in_T(b) and abs(a[0]-b[0]) + abs(a[1]-b[1]) == 0), (X, Y, a, b)
print("verified 20000 points")
for (m, up), (A, c) in sorted(maps.items()):
    fs = lambda r: " + ".join(f"{int(k)}*{n}" for k, n in zip(r, "fg") if k) or "0"
    print(f"m={m} upper={up}:  s = {fs(A[0])} + {c[0]}   t = {fs(A[1])} + {c[1]}")
