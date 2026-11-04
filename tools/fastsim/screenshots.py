#!/usr/bin/env python3
# Copyright (C) 2026 cyberyak
# SPDX-License-Identifier: GPL-3.0-only
"""Render README media of the live window without showing it on screen.

  screenshots.py [--out docs/media]

Opens live.py windows with WA_DontShowOnScreen (real macOS fonts, nothing
pops up), drives them frame by frame and grabs the window:
  app_kaleido.png / app_kaleido.gif   Kaleido radial mode, LFOs, moving clip
  app_panel.png                       the virtual front panel tab
  app_controls.png                    the Controls tab: TOML labels and LFOs
  app_feedback.png                    a community program via the picker
Only the project's own test media is used as input.
Run with the bundled Python: `source tools/env.sh` first.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QTabWidget

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live  # noqa: E402

ROOT = live.fastsim.ROOT


def grab(w) -> Image.Image:
    QApplication.processEvents()
    img = w.grab().toImage().convertToFormat(QImage.Format.Format_RGB888)
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    return Image.frombuffer("RGB", (img.width(), img.height()), bytes(ptr), "raw", "RGB",
                            img.bytesPerLine(), 1).copy()


def window(prog: Path, source: str, values: dict, lfos: dict):
    w = live.Live(prog, "÷4 fast")
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    w.timer.stop()
    w.show()
    w.source.setCurrentIndex([w.source.itemText(i) for i in range(w.source.count())].index(source))
    # Set the values, then move the widgets so the screenshot shows them
    w.values.update(values)
    _sync_widgets(w)
    for pid, (shape, rate, depth) in lfos.items():
        lfo = w.lfos[pid]
        lfo.shape, lfo.rate, lfo.depth = shape, rate, depth
    _sync_lfo_widgets(w)
    return w


def _sync_widgets(w):
    """Move sliders and checkboxes to w.values without triggering callbacks."""
    for pid, widget in w.controls.items():
        widget.blockSignals(True)
        if isinstance(widget, live.QCheckBox):
            widget.setChecked(bool(w.values[pid]))
        else:
            widget.setValue(w.values[pid])
            w.readouts[pid].setText(str(w.values[pid]))
        widget.blockSignals(False)


def _sync_lfo_widgets(w):
    for pid, (shape, rate, depth) in w.lfo_widgets.items():
        lfo = w.lfos[pid]
        for widget in (shape, rate, depth):
            widget.blockSignals(True)
        shape.setCurrentText(lfo.shape)
        rate.setValue(lfo.rate)
        depth.setValue(int(round(lfo.depth * 100)))
        for widget in (shape, rate, depth):
            widget.blockSignals(False)


def save_png(im: Image.Image, path: Path, width: int = 1400):
    """Retina grabs are 2x; store at a README-friendly width."""
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    im.save(path, optimize=True)


def save_gif(frames: list[Image.Image], path: Path, width: int, ms: int):
    h = round(frames[0].height * width / frames[0].width)
    small = [f.resize((width, h), Image.LANCZOS).quantize(colors=96, method=Image.Quantize.MEDIANCUT)
             for f in frames]
    small[0].save(path, save_all=True, append_images=small[1:], duration=ms, loop=0, optimize=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "docs/media")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)

    # Kaleido: radial mandala on the moving test clip, with LFOs
    w = window(ROOT / "programs/cyberyak/kaleido", "clip_moving.mp4",
               {"rotary_potentiometer_1": 300, "rotary_potentiometer_2": 520,
                "rotary_potentiometer_3": 600, "toggle_switch_7": 1},
               {"rotary_potentiometer_2": ("Sine", 0.5, 0.35),
                "rotary_potentiometer_4": ("Triangle", 0.3, 0.2)})
    frames = []
    for i in range(150):
        w.tick()
        if i >= 30 and i % 3 == 0:
            frames.append(grab(w))
    save_png(frames[-1], a.out / "app_kaleido.png")
    save_gif(frames, a.out / "app_kaleido.gif", 760, 100)
    save_png(grab(w), a.out / "app_panel.png")
    w.findChild(QTabWidget).setCurrentIndex(1)
    save_png(grab(w), a.out / "app_controls.png")
    w.proc.kill()
    w.video.close()

    # A community program, to show the picker at work
    w = window(ROOT / "videomancer-community-programs/programs/czartroniks/feedback", "Moving circles", {}, {})
    for _ in range(40):
        w.tick()
    save_png(grab(w), a.out / "app_feedback.png")
    w.proc.kill()
    for p in sorted(a.out.glob("app_*")):
        print(p, f"{p.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
