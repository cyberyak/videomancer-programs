#!/usr/bin/env python3
# Copyright (C) 2026 cyberyak
# SPDX-License-Identifier: GPL-3.0-only
"""Live fastsim preview: a Videomancer program running continuously.

The program's VHDL runs in Verilator (tools/fastsim) frame after frame, so
state carries over: spin animates, stores settle, feedback evolves. Knobs,
switches and the fader come from the program's TOML and act immediately.

Sources:
  Moving circles  bouncing circles generated on the fly (the input moves)
  any image       still images from test-images/ or a file you open
  video file      .mp4/.mov/... from test-images/ or a file you open; it
                  plays on the device clock (one source frame per 59.94 Hz
                  frame, like HDMI input) and loops
  Camera          a webcam, or a virtual camera that VS2/OBS can feed
                  (macOS asks for camera permission the first time)

LFOs: every knob and the fader has an LFO (sine, triangle, saw, square,
sample & hold) with a rate in Hz and a bipolar depth (attenuverter, centre =
none) that offsets the knob each frame. LFO time advances per simulated
frame at 59.94 fps, so modulation matches the device frame-for-frame; the
window itself runs slower than real time.

  python3 tools/fastsim/live.py <program_dir> [--decimation 4] [--video FILE]

Errors never close the window: they are logged with a traceback to
build/live.log and shown in a dialog. Native crashes (segfaults) are also
written there by faulthandler; the simulator process logs to
build/live_harness.log.

Run with the bundled Python: `source tools/env.sh` first.
"""
from __future__ import annotations

import argparse
import faulthandler
import logging
import subprocess
import sys
import time
import traceback
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                             QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QSlider,
                             QVBoxLayout, QWidget)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastsim  # noqa: E402
from vhdl_image_tester.core.config import ABI_REG_VIDEO_TIMING, resolve_video_settings  # noqa: E402
from vhdl_image_tester.core.image_converter import prepare_image, rgb_to_yuv10, yuv10_to_rgb  # noqa: E402

CIRCLES = "Moving circles"
LOG = fastsim.ROOT / "build/live.log"
HARNESS_LOG = fastsim.ROOT / "build/live_harness.log"
log = logging.getLogger("live")
FRAME_RATE = 59.94                      # device frame rate for LFO time
SHAPES = ["Off", "Sine", "Triangle", "Saw", "Square", "S&H"]


class LFO:
    """One modulation source; value(t) is in -1..1."""

    def __init__(self, seed: int):
        self.shape, self.rate, self.depth = "Off", 0.25, 0.0
        self._rng = np.random.default_rng(seed)
        self._cycle, self._held = -1, 0.0

    def value(self, t: float) -> float:
        p = t * self.rate
        ph = p - np.floor(p)
        if self.shape == "Sine":
            return float(np.sin(2 * np.pi * ph))
        if self.shape == "Triangle":
            return float(4 * abs(ph - 0.5) - 1) * -1
        if self.shape == "Saw":
            return float(2 * ph - 1)
        if self.shape == "Square":
            return 1.0 if ph < 0.5 else -1.0
        if self.shape == "S&H":
            if int(np.floor(p)) != self._cycle:
                self._cycle, self._held = int(np.floor(p)), float(self._rng.uniform(-1, 1))
            return self._held
        return 0.0

    def apply(self, base: int, t: float) -> int:
        if self.shape == "Off" or self.depth == 0:
            return base
        return int(np.clip(round(base + self.depth * 512 * self.value(t)), 0, 1023))


VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm")


class VideoSource:
    """A video file (frame-accurate on the device clock, looping) or a camera."""

    def __init__(self, spec: str | int, w: int, h: int):
        import cv2
        self.cv2, self.w, self.h = cv2, w, h
        self.cap = cv2.VideoCapture(spec)
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open video source {spec!r}")
        self.camera = isinstance(spec, int)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)) if not self.camera else 0
        self.pos = -1           # index of the frame in self.last
        self.last: Image.Image | None = None
        log.info("video %s: %.2f fps, %d frames", spec, self.fps, self.count)

    def _read(self) -> Image.Image:
        ok, bgr = self.cap.read()
        if not ok:
            raise RuntimeError("video read failed")
        rgb = self.cv2.cvtColor(bgr, self.cv2.COLOR_BGR2RGB)
        return prepare_image(Image.fromarray(rgb), self.w, self.h)

    def frame(self, t: float) -> Image.Image:
        """Frame shown at device time t (seconds since the source started)."""
        if self.camera:
            self.last = self._read()
            return self.last
        want = int(t * self.fps) % self.count if self.count > 0 else int(t * self.fps)
        if want < self.pos or self.last is None:          # looped or first frame
            self._rewind()
        while self.pos < want:                            # skip ahead
            ok = self.cap.grab()
            if ok and self.pos == want - 1:
                ok, bgr = self.cap.retrieve()
                if ok:
                    rgb = self.cv2.cvtColor(bgr, self.cv2.COLOR_BGR2RGB)
                    self.last = prepare_image(Image.fromarray(rgb), self.w, self.h)
            if not ok:
                # Container metadata often overstates the length (screen
                # recordings are variable frame rate): learn the real length
                # and loop instead of failing.
                if self.pos < 0:
                    raise RuntimeError("video has no decodable frames")
                log.info("video ends at frame %d (metadata said %d); looping",
                         self.pos + 1, self.count)
                self.count = self.pos + 1
                want %= self.count
                self._rewind()
                continue
            self.pos += 1
        return self.last

    def _rewind(self):
        self.cap.set(self.cv2.CAP_PROP_POS_FRAMES, 0)
        self.pos = -1

    def close(self):
        self.cap.release()


class Circles:
    """Bouncing, overlapping circles over a slow gradient."""

    def __init__(self, w: int, h: int, n: int = 22, seed: int = 3):
        rng = np.random.default_rng(seed)
        self.w, self.h = w, h
        self.r = rng.uniform(0.04, 0.14, n) * h
        self.pos = np.stack([rng.uniform(0, w, n), rng.uniform(0, h, n)], 1)
        self.vel = rng.normal(0, 1, (n, 2)) * h / 140
        self.col = [tuple(int(c) for c in rng.integers(40, 256, 3)) for _ in range(n)]
        self.t = 0

    def frame(self) -> Image.Image:
        self.t += 1
        self.pos += self.vel
        for k, lim in ((0, self.w), (1, self.h)):
            lo, hi = self.pos[:, k] < 0, self.pos[:, k] > lim
            self.vel[lo | hi, k] *= -1
            self.pos[:, k] = np.clip(self.pos[:, k], 0, lim)
        y = np.linspace(0, 1, self.h)[:, None]
        x = np.linspace(0, 1, self.w)[None, :]
        ph = self.t / 90
        shape = (self.h, self.w)
        bg = np.stack([np.broadcast_to(40 + 50 * np.sin(3 * y + ph), shape),
                       np.broadcast_to(20 + 30 * np.cos(2 * x + ph), shape),
                       90 + 60 * np.sin(2 * (x + y) - ph)], -1)
        im = Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8))
        d = ImageDraw.Draw(im)
        lw = max(1, self.h // 120)
        for (cx, cy), r, c in zip(self.pos, self.r, self.col):
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c, outline=(0, 0, 0), width=lw)
        return im


class Live(QWidget):
    def __init__(self, prog_dir: Path, decimation: int):
        super().__init__()
        self.prog_dir = prog_dir.resolve()
        self.program = fastsim.load_program(self.prog_dir.name, self.prog_dir.parent)
        self.vs = resolve_video_settings("1080p2997", decimation)
        if self.vs.is_interlaced:
            raise SystemExit("live preview supports progressive modes only")
        self.w, self.h = self.vs.sim_width, self.vs.sim_height
        exe = fastsim.build(self.prog_dir, self.vs.fpga_config)
        log.info("program %s, %dx%d (%s), simulator %s", self.prog_dir, self.w, self.h,
                 self.vs.fpga_config, exe)
        self._harness_err = open(HARNESS_LOG, "ab")
        self.proc = subprocess.Popen([str(exe), "--serve", str(self.w), str(self.h)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self._harness_err)
        self.circles = Circles(self.w, self.h)
        self.still: Image.Image | None = None
        self.video: VideoSource | None = None
        self.video_start = 0
        self.values: dict[str, int] = {}
        self.lfos: dict[str, LFO] = {}
        self.readouts: dict[str, QLabel] = {}
        self.sim_frame = 0
        self.frames, self.t_last = 0, time.time()
        self._build_ui()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(0)

    # -- UI -----------------------------------------------------------------
    def _build_ui(self):
        self.setWindowTitle(f"fastsim live: {self.program.display_name}")
        root = QHBoxLayout(self)

        view = QVBoxLayout()
        self.out_lbl = QLabel()
        self.out_lbl.setFixedSize(960, 540)
        self.out_lbl.setScaledContents(True)
        self.in_lbl = QLabel()
        self.in_lbl.setFixedSize(320, 180)
        self.in_lbl.setScaledContents(True)
        self.status = QLabel()
        view.addWidget(self.out_lbl)
        row = QHBoxLayout()
        row.addWidget(QLabel("input:"))
        row.addWidget(self.in_lbl)
        row.addWidget(self.status, 1)
        view.addLayout(row)
        root.addLayout(view)

        ctl = QGridLayout()
        r = 0
        self.source = QComboBox()
        self.source.addItem(CIRCLES)
        for p in sorted((fastsim.ROOT / "test-images").glob("*.png")):
            self.source.addItem(p.name, str(p))
        for p in sorted(q for q in (fastsim.ROOT / "test-images").iterdir()
                        if q.suffix.lower() in VIDEO_EXTS):
            self.source.addItem(p.name, str(p))
        self.source.addItem("Open image…")
        self.source.addItem("Open video…")
        self.source.addItem("Camera")
        self.source.currentIndexChanged.connect(self._source_changed)
        ctl.addWidget(QLabel("Source"), r, 0)
        ctl.addWidget(self.source, r, 1)
        r += 1
        self.play = QPushButton("Pause")
        self.play.clicked.connect(self._toggle_play)
        ctl.addWidget(self.play, r, 0, 1, 2)
        r += 1

        for p in self.program.parameters:
            if p.is_toggle:
                labels = p.value_labels or ["Off", "On"]
                box = QCheckBox(f"{p.name_label}: {labels[0]} / {labels[1]}")
                box.setChecked(p.initial_toggle_state)
                box.toggled.connect(lambda on, pid=p.parameter_id: self._set(pid, int(on)))
                self.values[p.parameter_id] = int(p.initial_toggle_state)
                ctl.addWidget(box, r, 0, 1, 2)
            else:
                pid = p.parameter_id
                s = QSlider(Qt.Orientation.Horizontal)
                s.setRange(0, 1023)
                s.setValue(p.initial_pot_value)
                s.valueChanged.connect(lambda v, pid=pid: self._set(pid, v))
                self.values[pid] = p.initial_pot_value
                readout = QLabel(str(p.initial_pot_value))
                readout.setFixedWidth(36)
                self.readouts[pid] = readout
                ctl.addWidget(QLabel(f"<b>{p.name_label}</b>"), r, 0)
                ctl.addWidget(s, r, 1)
                ctl.addWidget(readout, r, 2)
                r += 1
                # LFO row: shape, rate (Hz), depth (bipolar attenuverter)
                lfo = self.lfos[pid] = LFO(seed=len(self.lfos) + 1)
                shape = QComboBox()
                shape.addItems(SHAPES)
                shape.currentTextChanged.connect(lambda t, l=lfo: setattr(l, "shape", t))
                rate = QDoubleSpinBox()
                rate.setRange(0.01, 20.0)
                rate.setDecimals(2)
                rate.setSingleStep(0.05)
                rate.setValue(lfo.rate)
                rate.setSuffix(" Hz")
                rate.valueChanged.connect(lambda v, l=lfo: setattr(l, "rate", v))
                depth = QSlider(Qt.Orientation.Horizontal)
                depth.setRange(-100, 100)
                depth.setValue(0)
                depth.setToolTip("LFO depth, -100..100 % (centre = none)")
                depth.valueChanged.connect(lambda v, l=lfo: setattr(l, "depth", v / 100))
                row = QHBoxLayout()
                row.addWidget(QLabel("  LFO"))
                row.addWidget(shape)
                row.addWidget(rate)
                row.addWidget(depth, 1)
                ctl.addLayout(row, r, 0, 1, 3)
            r += 1
        reset = QPushButton("All LFOs off")
        reset.clicked.connect(self._lfos_off)
        ctl.addWidget(reset, r, 0, 1, 3)
        self._shape_boxes = [w for w in self.findChildren(QComboBox) if w is not self.source]
        r += 1
        ctl.setRowStretch(r, 1)
        side = QWidget()
        side.setLayout(ctl)
        scroll = QScrollArea()
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(470)
        root.addWidget(scroll)

    def _set(self, pid, v):
        self.values[pid] = v

    def _lfos_off(self):
        for box in self._shape_boxes:
            box.setCurrentText("Off")
        for lfo in self.lfos.values():
            lfo.shape = "Off"

    def _toggle_play(self):
        if self.timer.isActive():
            self.timer.stop()
            self.play.setText("Play")
        else:
            self.timer.start(0)
            self.play.setText("Pause")

    def _start_video(self, spec):
        self.video = VideoSource(spec, self.w, self.h)
        self.video_start = self.sim_frame

    def _source_changed(self, i):
        text = self.source.itemText(i)
        log.info("source: %s", text)
        if self.video is not None:
            self.video.close()
            self.video = None
        self.still = None
        data = self.source.itemData(i)
        if text == CIRCLES:
            pass
        elif text == "Camera":
            self._start_video(0)
        elif text == "Open video…":
            path, _ = QFileDialog.getOpenFileName(
                self, "Video", str(fastsim.ROOT),
                "Video (" + " ".join("*" + e for e in VIDEO_EXTS) + ")")
            if path:
                self._start_video(path)
        elif data and Path(data).suffix.lower() in VIDEO_EXTS:
            self._start_video(data)
        elif text == "Open image…":
            path, _ = QFileDialog.getOpenFileName(self, "Image", str(fastsim.ROOT),
                                                  "Images (*.png *.jpg *.jpeg *.bmp *.tif)")
            if path:
                self.still = prepare_image(Image.open(path), self.w, self.h)
        else:
            self.still = prepare_image(Image.open(self.source.itemData(i)), self.w, self.h)

    # -- simulation ---------------------------------------------------------
    def tick(self):
        # Any failure stops playback once (one dialog, not one per frame).
        try:
            if self.proc.poll() is not None:
                raise RuntimeError(f"simulator process exited (code {self.proc.returncode})")
            self._tick()
        except Exception as e:
            self.timer.stop()
            self.play.setText("Play")
            log.exception("frame %d failed; playback stopped", self.sim_frame)
            self.status.setText("stopped on error, see build/live.log")
            QMessageBox.critical(self, "Live preview stopped",
                                 f"{type(e).__name__}: {e}\n\nFull traceback in {LOG}\n"
                                 f"Simulator output in {HARNESS_LOG}\n\n"
                                 + ("Close and reopen the window to restart the simulator."
                                    if self.proc.poll() is not None else "Press Play to retry."))

    def _tick(self):
        if self.video is not None:
            src = self.video.frame((self.sim_frame - self.video_start) / FRAME_RATE)
        elif self.still is not None:
            src = self.still
        else:
            src = self.circles.frame()
        t = self.sim_frame / FRAME_RATE
        self.sim_frame += 1
        values = dict(self.values)
        for pid, lfo in self.lfos.items():
            values[pid] = lfo.apply(self.values[pid], t)
            self.readouts[pid].setText(str(values[pid]))
        regs = self.program.build_register_array(values)
        regs[ABI_REG_VIDEO_TIMING] = self.vs.timing_id
        self.proc.stdin.write((",".join(map(str, regs)) + "\n").encode())
        self.proc.stdin.write(rgb_to_yuv10(src).astype("<u2").tobytes())
        self.proc.stdin.flush()
        n = self.w * self.h * 3 * 2
        buf = self.proc.stdout.read(n)
        if len(buf) != n:
            raise RuntimeError(f"simulator returned {len(buf)} of {n} bytes "
                               f"(exit code {self.proc.poll()})")
        out = yuv10_to_rgb(np.frombuffer(buf, "<u2").reshape(self.h, self.w, 3))
        self.out_lbl.setPixmap(_pix(out))
        self.in_lbl.setPixmap(_pix(src))
        self.frames += 1
        now = time.time()
        if now - self.t_last >= 1:
            self.status.setText(f"{self.frames / (now - self.t_last):.1f} fps  "
                                f"{self.w}x{self.h} (÷{1920 // self.w})")
            self.frames, self.t_last = 0, now

    def closeEvent(self, e):
        self.timer.stop()
        self.proc.kill()
        if self.video is not None:
            self.video.close()
        super().closeEvent(e)


def _pix(im: Image.Image) -> QPixmap:
    a = np.ascontiguousarray(np.asarray(im.convert("RGB")))
    q = QImage(a.data, a.shape[1], a.shape[0], 3 * a.shape[1], QImage.Format.Format_RGB888)
    return QPixmap.fromImage(q.copy())


def _setup_logging():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(LOG), logging.StreamHandler()])
    # Native crashes (segfault, abort) write their Python stack here too.
    faulthandler.enable(open(LOG, "a"), all_threads=True)

    def excepthook(kind, value, tb):
        # PyQt6 aborts on an unhandled exception in a callback unless a hook
        # is set; log it and keep the window alive instead.
        text = "".join(traceback.format_exception(kind, value, tb))
        log.error("unhandled exception:\n%s", text)
        if QApplication.instance() is not None:
            QMessageBox.critical(None, "Live preview error",
                                 f"{kind.__name__}: {value}\n\nFull traceback in {LOG}")

    sys.excepthook = excepthook


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("program_dir", type=Path)
    ap.add_argument("--decimation", type=int, default=4, choices=[2, 4, 8])
    ap.add_argument("--video", help="start with this video file as the source")
    a = ap.parse_args()
    _setup_logging()
    log.info("---- live preview start ----")
    app = QApplication(sys.argv)
    w = Live(a.program_dir, a.decimation)
    if a.video:
        w.source.addItem(Path(a.video).name, str(Path(a.video).resolve()))
        w.source.setCurrentIndex(w.source.count() - 1)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
