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
  Syphon… / NDI…  live video from another app; pick a source when asked

LFOs: every knob and the fader has an LFO (sine, triangle, saw, square,
sample & hold) with a rate in Hz and a bipolar depth (attenuverter, centre =
none) that offsets the knob each frame. LFO time advances per simulated
frame at 59.94 fps, so modulation matches the device frame-for-frame; the
window itself runs slower than real time.

  python3 tools/fastsim/live.py [program_dir] [--mode "480p real"] [--video FILE]

Resolution modes: "÷4 fast" and "÷2" use the SDK tester's compact 1080p
raster (fast; fine for most programs). "480p/720p/1080p real" feed the
standard's true resolution and blanking, needed by programs that size things
from the video timing ID or work during blanking (e.g. czartroniks/bishop).
Programs that read the timing ID start in "480p real" automatically.

The Program menu switches between every program in the workspace (ours, the
SDK examples, the community library); each builds once (~4-10 s), then is
cached. Programs that failed tools/fastsim/sweep.py are marked with a warning.

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
from PyQt6.QtCore import QRectF, QSettings, Qt, QTimer
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtMultimedia import QMediaDevices
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                             QGridLayout, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton, QScrollArea, QSlider,
                             QSizePolicy, QTabWidget, QVBoxLayout, QWidget)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastsim  # noqa: E402
from midi import MidiIn, describe, key_of  # noqa: E402
from vpanel import VPanel  # noqa: E402
import vpanel  # noqa: E402

VPANEL_HELP = (vpanel.__doc__.split("\n\n", 1)[1]).strip()
from vhdl_image_tester.core.config import ABI_REG_VIDEO_TIMING, resolve_video_settings  # noqa: E402
from vhdl_image_tester.core.image_converter import _M_YUV_TO_RGB, prepare_image  # noqa: E402

CIRCLES = "Moving circles"
DEFAULT_PROGRAM = fastsim.ROOT / "programs/cyberyak/kaleido"
# name -> (video mode, decimation, real timing)
MODES = {
    "÷4 fast": ("1080p2997", 4, False),
    "÷2": ("1080p2997", 2, False),
    "480p real": ("480p", 1, True),
    "720p real": ("720p60", 1, True),
    "1080p real": ("1080p2997", 1, True),
}


def auto_mode(prog_dir: Path) -> str:
    return "480p real" if fastsim.uses_timing_id(prog_dir) else "÷4 fast"


_windows: list = []                     # keep switched-to windows alive


def program_list() -> list[tuple[str, Path]]:
    """(label, dir) for every program: ours, SDK examples, community."""
    ok = {}
    results = fastsim.ROOT / "build/fastsim/sweep/results.json"
    if results.exists():
        import json
        ok = {r["program"]: r["status"] == "ok" for r in json.loads(results.read_text())}
    groups = [(sorted((fastsim.ROOT / "programs/cyberyak").iterdir()), "cyberyak"),
              (sorted((fastsim.ROOT / "videomancer-sdk/programs").iterdir()), "lzx"),
              (sorted((fastsim.ROOT / "videomancer-community-programs/programs").glob("*/*")), None)]
    out = []
    for dirs, vendor in groups:
        for d in dirs:
            if not (d / f"{d.name}.toml").exists():
                continue
            label = f"{vendor or d.parent.name}/{d.name}"
            if ok.get(str(d.relative_to(fastsim.ROOT))) is False:
                label = "⚠ " + label
            out.append((label, d))
    return out
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

    CAMERA_HELP = ("If macOS blocked it: System Settings > Privacy & Security > Camera, "
                   "allow the app this runs in (Claude or Terminal), then reopen the window. "
                   "A virtual camera (VS2/OBS) is listed by name once its app is running; "
                   "reopen the window to refresh the list.")

    def __init__(self, spec: str | int, w: int, h: int):
        import cv2
        self.cv2, self.w, self.h = cv2, w, h
        self.camera = isinstance(spec, int)
        self.cap = (cv2.VideoCapture(spec, cv2.CAP_AVFOUNDATION) if self.camera
                    else cv2.VideoCapture(spec))
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open {'camera' if self.camera else 'video'} {spec!r}."
                               + (" " + self.CAMERA_HELP if self.camera else ""))
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)) if not self.camera else 0
        self.pos = -1           # index of the frame in self.last
        self.last: Image.Image | None = None
        self.misses = 0
        if self.camera:
            # Cameras deliver nothing for a moment after opening: wait up to
            # ~3 s for the first frame instead of failing on the first read.
            for _ in range(30):
                ok, bgr = self.cap.read()
                if ok:
                    self.last = self._convert(bgr)
                    break
                time.sleep(0.1)
            else:
                self.cap.release()
                raise RuntimeError(f"camera {spec} opened but sent no frames in 3 s. "
                                   + self.CAMERA_HELP)
        log.info("%s %s: %.2f fps, %d frames", "camera" if self.camera else "video",
                 spec, self.fps, self.count)

    def _convert(self, bgr) -> Image.Image:
        rgb = self.cv2.cvtColor(bgr, self.cv2.COLOR_BGR2RGB)
        return prepare_image(Image.fromarray(rgb), self.w, self.h)

    def frame(self, t: float) -> Image.Image:
        """Frame shown at device time t (seconds since the source started)."""
        if self.camera:
            ok, bgr = self.cap.read()
            if ok:
                self.last, self.misses = self._convert(bgr), 0
            else:
                # Ride out short dropouts on the last good frame (~2 s).
                self.misses += 1
                if self.misses > 120:
                    raise RuntimeError("camera stopped sending frames. " + self.CAMERA_HELP)
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


class SyphonSource:
    """A Syphon server on this Mac (VS2, Resolume, VDMX, ...)."""

    directory = None                    # kept alive: it tracks servers appearing

    @classmethod
    def servers(cls) -> list:
        from syphon import SyphonServerDirectory
        if cls.directory is None:
            cls.directory = SyphonServerDirectory()
        for _ in range(20):             # announcements arrive on the event loop
            QApplication.processEvents()
            time.sleep(0.05)
        return list(cls.directory.servers)

    @staticmethod
    def label(server) -> str:
        return " - ".join(x for x in (server.app_name, server.name) if x)

    def __init__(self, label: str, w: int, h: int):
        from syphon import SyphonMetalClient
        from syphon.utils.numpy import copy_mtl_texture_to_image
        self.copy = copy_mtl_texture_to_image
        self.w, self.h = w, h
        server = next((x for x in self.servers() if self.label(x) == label), None)
        if server is None:
            raise RuntimeError(f"Syphon server not found: {label}. Is the app running?")
        self.client = SyphonMetalClient(server)
        self.last = Image.new("RGB", (w, h))
        log.info("syphon %s", label)

    def frame(self, t: float) -> Image.Image:
        if self.client.has_new_frame:
            bgra = self.copy(self.client.new_frame_image)
            self.last = prepare_image(Image.fromarray(bgra[..., [2, 1, 0]]), self.w, self.h)
        return self.last

    def close(self):
        self.client.stop()


class NDISource:
    """An NDI source on the network or this Mac."""

    @staticmethod
    def names() -> list[str]:
        from cyndilib.finder import Finder
        f = Finder()
        f.open()
        for _ in range(4):              # discovery takes a moment
            f.wait_for_sources(0.5)
            QApplication.processEvents()
        names = list(f.get_source_names())
        f.close()
        return names

    def __init__(self, name: str, w: int, h: int):
        from cyndilib.finder import Finder
        from cyndilib.receiver import Receiver
        from cyndilib.video_frame import VideoFrameSync
        from cyndilib.wrapper import RecvBandwidth, RecvColorFormat
        self.w, self.h = w, h
        self.finder = Finder()
        self.finder.open()
        src = None
        for _ in range(10):
            self.finder.wait_for_sources(0.3)
            if name in self.finder.get_source_names():
                src = self.finder.get_source(name)
                break
        if src is None:
            self.finder.close()
            raise RuntimeError(f"NDI source not found: {name}")
        self.vf = VideoFrameSync()
        self.rx = Receiver(color_format=RecvColorFormat.RGBX_RGBA, bandwidth=RecvBandwidth.highest)
        self.rx.frame_sync.set_video_frame(self.vf)
        self.rx.set_source(src)
        self.last = Image.new("RGB", (w, h))
        log.info("ndi %s", name)

    def frame(self, t: float) -> Image.Image:
        self.rx.frame_sync.capture_video()
        if self.vf.xres:
            a = np.asarray(self.vf.get_array())
            if a.size == self.vf.xres * self.vf.yres * 4:
                rgba = a.reshape(self.vf.yres, self.vf.xres, 4)
                self.last = prepare_image(Image.fromarray(rgba[..., :3].copy()), self.w, self.h)
        return self.last

    def close(self):
        self.rx.disconnect()
        self.finder.close()


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
    def __init__(self, prog_dir: Path, mode: str | None = None):
        super().__init__()
        self.prog_dir = prog_dir.resolve()
        self.needs_real = fastsim.uses_timing_id(self.prog_dir)
        self.mode = mode or auto_mode(self.prog_dir)
        if self.needs_real and not MODES[self.mode][2]:
            log.info("%s needs real timing; using 480p real instead of %s", self.prog_dir.name, self.mode)
            self.mode = "480p real"
        video_mode, decimation, self.real_timing = MODES[self.mode]
        self._opened: str | None = None     # file chosen with "Open image/video…"
        self.program = fastsim.load_program(self.prog_dir.name, self.prog_dir.parent)
        self.vs = resolve_video_settings(video_mode, decimation)
        if self.vs.is_interlaced:
            raise SystemExit("live preview supports progressive modes only")
        self.w, self.h = self.vs.sim_width, self.vs.sim_height
        exe = fastsim.build(self.prog_dir, self.vs.fpga_config)
        log.info("program %s, %s %dx%d (%s), simulator %s", self.prog_dir, self.mode,
                 self.w, self.h, self.vs.fpga_config, exe)
        self._harness_err = open(HARNESS_LOG, "ab")
        # RGB in and out: the simulator converts with the SDK tester's BT.601
        # maths (inverse matrix passed as exact doubles), saving ~10 ms of
        # Python per frame.
        self.proc = subprocess.Popen([str(exe), "--serve", str(self.w), str(self.h), "--rgb",
                                      *(repr(float(c)) for c in _M_YUV_TO_RGB.flatten()),
                                      *fastsim.timing_args(video_mode, self.real_timing)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self._harness_err)
        self.inflight: Image.Image | None = None    # input of the frame being simulated
        self.circles = Circles(self.w, self.h)
        self.still: Image.Image | None = None
        self.video: VideoSource | None = None
        self.video_start = 0
        self.values: dict[str, int] = {}
        self.lfos: dict[str, LFO] = {}
        self.readouts: dict[str, QLabel] = {}
        self.controls: dict[str, QWidget] = {}          # slider or checkbox per parameter
        self.lfo_widgets: dict[str, tuple] = {}         # (shape, rate, depth) per knob
        self.sim_frame = 0
        self.shown_values: dict[str, int] = {}          # values after LFOs (panel LEDs)
        self.modulation_enabled = True                  # panel MOTION button
        self.last_target: str | None = None             # last control touched (MIDI learn)
        self.learning = False
        try:
            self.midi = MidiIn()
        except Exception:                               # mido/rtmidi missing
            log.exception("MIDI unavailable")
            self.midi = None
        self.frames, self.t_last = 0, time.time()
        self._build_ui()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(0)
        self.midi_timer = QTimer(self)                  # MIDI works while paused too
        self.midi_timer.timeout.connect(self._midi_poll)
        self.midi_timer.start(10)

    # -- UI -----------------------------------------------------------------
    def _build_ui(self):
        self.setWindowTitle(f"fastsim live: {self.program.display_name}")
        root = QHBoxLayout(self)

        view = QVBoxLayout()
        # Grows with the window, keeping the picture's display aspect
        # (SD standards are 4:3 on screen: non-square pixels)
        self.out_lbl = AspectView(4 / 3 if self.w == 720 else 16 / 9)
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

        head = QGridLayout()
        r = 0
        self.program_box = QComboBox()
        self.program_box.setMaxVisibleItems(30)
        for label, d in program_list():
            self.program_box.addItem(label, str(d))
            if d.resolve() == self.prog_dir:
                self.program_box.setCurrentIndex(self.program_box.count() - 1)
        self.program_box.currentIndexChanged.connect(self._switch_program)
        head.addWidget(QLabel("<b>Program</b>"), r, 0)
        head.addWidget(self.program_box, r, 1, 1, 2)
        r += 1
        self.mode_box = QComboBox()
        self.mode_box.addItems(list(MODES))
        self.mode_box.setCurrentText(self.mode)
        self.mode_box.setToolTip("÷4/÷2: compact raster, fast. 'real': the standard's true resolution\n"
                                 "and blanking, for programs that use the timing ID or blanking time.")
        if self.needs_real:
            # Compact modes can't work for these programs: grey them out
            reason = ("Not available: this program sizes its picture from the video\n"
                      "standard, so it needs real timing (480p real is the fastest).")
            for j, (name, (_, _, real)) in enumerate(MODES.items()):
                if not real:
                    self.mode_box.model().item(j).setEnabled(False)
                    self.mode_box.setItemData(j, reason, Qt.ItemDataRole.ToolTipRole)
        self.mode_box.currentIndexChanged.connect(self._switch_mode)
        head.addWidget(QLabel("Resolution"), r, 0)
        head.addWidget(self.mode_box, r, 1, 1, 2)
        r += 1
        self.source = QComboBox()
        self.source.addItem(CIRCLES)
        for p in sorted((fastsim.ROOT / "test-images").glob("*.png")):
            self.source.addItem(p.name, str(p))
        for p in sorted(q for q in (fastsim.ROOT / "test-images").iterdir()
                        if q.suffix.lower() in VIDEO_EXTS):
            self.source.addItem(p.name, str(p))
        self.source.addItem("Open image…")
        self.source.addItem("Open video…")
        self.source.addItem("Syphon…")
        self.source.addItem("NDI…")
        # Cameras that exist, by name. OpenCV's AVFoundation backend numbers
        # devices in the same discovery order as Qt's list.
        for cam, dev in enumerate(QMediaDevices.videoInputs()):
            self.source.addItem(f"Camera: {dev.description()}", cam)
        self.source.currentIndexChanged.connect(self._source_changed)
        head.addWidget(QLabel("Source"), r, 0)
        head.addWidget(self.source, r, 1)
        r += 1
        self.play = QPushButton("Pause")
        self.play.clicked.connect(self._toggle_play)
        head.addWidget(self.play, r, 0, 1, 2)
        r += 1
        # MIDI: input port, learn mode, presets
        midi_row = QHBoxLayout()
        self.midi_box = QComboBox()
        self.midi_box.addItem("(no MIDI)", "")
        for name in (self.midi.ports() if self.midi else []):
            self.midi_box.addItem(name, name)
        self.midi_box.currentIndexChanged.connect(lambda i: self.open_midi(self.midi_box.itemData(i)))
        self.learn_btn = QPushButton("Learn")
        self.learn_btn.setCheckable(True)
        self.learn_btn.setToolTip("Touch a control on the panel or in Controls, then move a\n"
                                  "knob/fader/button on your MIDI controller to bind it.\n"
                                  "Stays on for more bindings; click again to finish.")
        self.learn_btn.toggled.connect(self._set_learning)
        preset = QPushButton("UC4 preset")
        preset.setToolTip("Faderfox UC4, factory Setup 1 (assumed CCs: encoders 8-15, faders 32-39):\n"
                          "encoders 1-6 = knobs 1-6, 7 = LFO rate, 8 = LFO shape (selected knob);\n"
                          "faders 1-6 = LFO depth of knobs 1-6, 7 = fader LFO depth, 8 = fader.\n"
                          "If something doesn't respond, fix it with Learn.")
        preset.clicked.connect(lambda: self._midi_preset("Faderfox UC4 (Setup 1)"))
        clear = QPushButton("Clear")
        clear.clicked.connect(self._midi_clear)
        for wdg in (self.midi_box, self.learn_btn, preset, clear):
            midi_row.addWidget(wdg)
        head.addWidget(QLabel("MIDI"), r, 0)
        head.addLayout(midi_row, r, 1, 1, 2)
        r += 1

        ctl = QGridLayout()
        r = 0
        for p in self.program.parameters:
            if p.is_toggle:
                labels = p.value_labels or ["Off", "On"]
                box = QCheckBox(f"{p.name_label}: {labels[0]} / {labels[1]}")
                box.setChecked(p.initial_toggle_state)
                box.toggled.connect(lambda on, pid=p.parameter_id: self._set(pid, int(on)))
                self.controls[p.parameter_id] = box
                self.values[p.parameter_id] = int(p.initial_toggle_state)
                ctl.addWidget(box, r, 0, 1, 2)
            else:
                pid = p.parameter_id
                s = QSlider(Qt.Orientation.Horizontal)
                s.setRange(0, 1023)
                s.setValue(p.initial_pot_value)
                s.valueChanged.connect(lambda v, pid=pid: self._set(pid, v))
                self.controls[pid] = s
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
                self.lfo_widgets[pid] = (shape, rate, depth)
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
        r += 1
        ctl.setRowStretch(r, 1)
        side = QWidget()
        side.setLayout(ctl)
        scroll = QScrollArea()
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)

        # Right column: shared header, then the panel and the control list
        self.panel = VPanel(self)
        self.panel.setToolTip(VPANEL_HELP)
        tabs = QTabWidget()
        tabs.addTab(self.panel, "Panel")
        tabs.addTab(scroll, "Controls")
        right = QVBoxLayout()
        right.addLayout(head)
        right.addWidget(tabs, 1)
        holder = QWidget()
        holder.setLayout(right)
        holder.setFixedWidth(580)
        root.addWidget(holder)

    def _set(self, pid, v):
        self.values[pid] = v
        self.last_target = pid

    # -- MIDI ------------------------------------------------------------------
    def open_midi(self, name: str):
        if self.midi is None:
            return
        try:
            self.midi.open(name)
        except Exception as e:
            log.exception("cannot open MIDI port %r", name)
            self.panel.say(f"MIDI: cannot open {name}")
            return
        QSettings("cyberyak", "fastsim-live").setValue("midi_port", name)
        self.midi_box.blockSignals(True)
        j = self.midi_box.findData(name)
        if j < 0 and name:
            self.midi_box.addItem(name, name)
            j = self.midi_box.count() - 1
        self.midi_box.setCurrentIndex(max(j, 0))
        self.midi_box.blockSignals(False)
        if name:
            self.panel.say(f"MIDI: {name} ({len(self.midi.mapping)} mappings)")

    def _set_learning(self, on: bool):
        self.learning = on
        self.panel.say("Learn: touch a control, then move a MIDI control" if on else "Learn off")

    def _midi_preset(self, name: str):
        if self.midi is None or not self.midi.name:
            self.panel.say("Pick a MIDI input first")
            return
        self.midi.apply_preset(name)
        self.panel.say(f"{name}: {len(self.midi.mapping)} mappings")

    def _midi_clear(self):
        if self.midi is not None and self.midi.name:
            self.midi.clear()
            self.panel.say("MIDI mappings cleared")

    def _midi_poll(self):
        if self.midi is None or self.midi.port is None:
            return
        for msg in self.midi.poll():
            key = key_of(msg)
            if key is None:
                continue
            active = msg.type == "control_change" or (msg.type == "note_on" and msg.velocity > 0)
            if self.learning and active:
                if self.last_target is None:
                    self.panel.say("Learn: touch a control first")
                    continue
                if self.midi.mapping.get(key) != self.last_target:
                    self.midi.bind(key, self.last_target)
                    self.panel.say(f"{describe(key)} -> {self._target_name(self.last_target)}")
            target = self.midi.mapping.get(key)
            if target and active:
                self._midi_apply(target, msg)

    def _target_name(self, target: str) -> str:
        kind, _, pid = target.partition(":")
        if kind == "lfo_rate":
            return "selected LFO rate"
        if kind == "lfo_shape":
            return "selected LFO shape"
        p = self.panel.param(pid if kind == "lfo_depth" else target)
        name = p.name_label if p else (pid or target)
        return f"{name} LFO depth" if kind == "lfo_depth" else name

    def _midi_apply(self, target: str, msg):
        v = msg.value if msg.type == "control_change" else msg.velocity
        kind, _, rest = target.partition(":")
        if kind == "lfo_depth":
            depth = max(-1.0, min(1.0, (v - 64) / 63))
            self.set_lfo(rest, depth=depth)
            if self.lfos.get(rest) and self.lfos[rest].shape == "Off" and depth != 0:
                self.set_lfo(rest, shape="Sine")
        elif kind == "lfo_rate":
            self.panel.apply_lfo_control("time", v / 127)
        elif kind == "lfo_shape":
            self.panel.apply_lfo_control("slope", v / 127)
        elif target in self.values:
            p = self.panel.param(target)
            if p is not None and p.is_toggle:
                on = (not self.values[target]) if msg.type == "note_on" else v >= 64
                self.apply_value(target, int(on), touched=False)
            else:
                self.apply_value(target, round(v / 127 * 1023), touched=False)

    def _switch_program(self, i):
        target = Path(self.program_box.itemData(i))
        if target.resolve() != self.prog_dir:
            self._reopen(target, None)          # new program: automatic mode

    def _switch_mode(self, i):
        mode = self.mode_box.itemText(i)
        if mode != self.mode:
            self._reopen(self.prog_dir, mode)

    def _reopen(self, target: Path, mode: str | None):
        """Replace this window with one for (target, mode), keeping the source."""
        log.info("opening %s (%s)", target, mode or "auto")
        self.timer.stop()
        self.status.setText(f"building {target.name}… (first time only)")
        QApplication.processEvents()
        try:
            new = Live(target, mode)
        except BaseException as e:      # build errors, SystemExit for interlaced
            log.exception("could not open %s", target)
            QMessageBox.critical(self, "Program failed to load",
                                 f"{target.name}: {e}\n\nDetails in {LOG}")
            for box, current in ((self.program_box, None), (self.mode_box, self.mode)):
                box.blockSignals(True)
                if current is None:
                    box.setCurrentIndex(next(
                        j for j in range(box.count())
                        if Path(box.itemData(j)).resolve() == self.prog_dir))
                else:
                    box.setCurrentText(current)
                box.blockSignals(False)
            self.timer.start(0)
            return
        new.setGeometry(self.geometry())        # keep size and position
        new.show()
        _windows.append(new)
        text, data = self.source.currentText(), self.source.currentData()
        if text.startswith("Open"):
            text, data = (Path(self._opened).name, self._opened) if self._opened else ("", None)
        port, learning = (self.midi.name if self.midi else ""), self.learning
        self.close()                    # releases the camera / video / MIDI port first
        new.select_source(text, data)
        new.open_midi(port)
        new.learn_btn.setChecked(learning)

    # -- panel API --------------------------------------------------------------
    def apply_value(self, pid: str, v: int, touched: bool = True):
        """Set a parameter and move its classic widget to match. touched=False
        for MIDI-driven changes, which must not become the learn target."""
        self.values[pid] = v
        if touched:
            self.last_target = pid
        w = self.controls.get(pid)
        if w is not None:
            w.blockSignals(True)
            if isinstance(w, QCheckBox):
                w.setChecked(bool(v))
            else:
                w.setValue(v)
                self.readouts[pid].setText(str(v))
            w.blockSignals(False)
        self.panel.update()

    def set_lfo(self, pid: str, shape=None, rate=None, depth=None):
        lfo = self.lfos.get(pid)
        if lfo is None:
            return
        if shape is not None:
            lfo.shape = shape
        if rate is not None:
            lfo.rate = rate
        if depth is not None:
            lfo.depth = depth
        sw, rw, dw = self.lfo_widgets[pid]
        for w in (sw, rw, dw):
            w.blockSignals(True)
        sw.setCurrentText(lfo.shape)
        rw.setValue(lfo.rate)
        dw.setValue(int(round(lfo.depth * 100)))
        for w in (sw, rw, dw):
            w.blockSignals(False)
        self.panel.update()

    def step_program(self, delta: int):
        n = self.program_box.count()
        self.program_box.setCurrentIndex((self.program_box.currentIndex() + delta) % n)

    def set_playing(self, on: bool):
        if on != self.timer.isActive():
            self._toggle_play()
        self.panel.update()

    def _lfos_off(self):
        for shape, _, _ in self.lfo_widgets.values():
            shape.setCurrentText("Off")
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

    def _pick_stream(self, kind: str):
        """Discover Syphon/NDI sources, let the user pick, add it to the menu."""
        self.status.setText(f"looking for {kind[:-1]} sources…")
        QApplication.processEvents()
        if kind == "Syphon…":
            found = [SyphonSource.label(x) for x in SyphonSource.servers()]
            prefix = "syphon:"
        else:
            found = NDISource.names()
            prefix = "ndi:"
        if not found:
            QMessageBox.information(self, kind[:-1], f"No {kind[:-1]} sources found. "
                                    "Start the sending app (and enable its output), then try again.")
            self.source.setCurrentIndex(0)
            return
        name, ok = QInputDialog.getItem(self, kind[:-1], "Source:", found, 0, False)
        if ok and name:
            self.select_source(f"{kind[:-1]}: {name}", prefix + name)
        else:
            self.source.setCurrentIndex(0)

    def select_source(self, text: str, data) -> None:
        """Select a source by what it is (menu text + data), adding a file
        that isn't in this window's menu (e.g. one given with --video)."""
        if not text:
            return
        for j in range(self.source.count()):
            if self.source.itemText(j) == text and self.source.itemData(j) == data:
                self.source.setCurrentIndex(j)
                return
        if isinstance(data, str) and (data.startswith(("syphon:", "ndi:")) or Path(data).exists()):
            label = text if data.startswith(("syphon:", "ndi:")) else Path(data).name
            self.source.addItem(label, data)
            self.source.setCurrentIndex(self.source.count() - 1)
        else:
            log.info("source %r not available here; keeping the default", text)

    def _source_changed(self, i):
        if i < 0:
            return
        text = self.source.itemText(i)
        log.info("source: %s", text)
        if self.video is not None:
            self.video.close()
            self.video = None
        self.still = None
        data = self.source.itemData(i)
        if text == CIRCLES:
            pass
        elif text.startswith("Camera: "):
            self._start_video(int(data))
        elif text in ("Syphon…", "NDI…"):
            self._pick_stream(text)
        elif isinstance(data, str) and data.startswith("syphon:"):
            self.video = SyphonSource(data[7:], self.w, self.h)
            self.video_start = self.sim_frame
        elif isinstance(data, str) and data.startswith("ndi:"):
            self.video = NDISource(data[4:], self.w, self.h)
            self.video_start = self.sim_frame
        elif text == "Open video…":
            path, _ = QFileDialog.getOpenFileName(
                self, "Video", str(fastsim.ROOT),
                "Video (" + " ".join("*" + e for e in VIDEO_EXTS) + ")")
            if path:
                self._start_video(path)
                self._opened = path
        elif data and Path(data).suffix.lower() in VIDEO_EXTS:
            self._start_video(data)
        elif text == "Open image…":
            path, _ = QFileDialog.getOpenFileName(self, "Image", str(fastsim.ROOT),
                                                  "Images (*.png *.jpg *.jpeg *.bmp *.tif)")
            if path:
                self.still = prepare_image(Image.open(path), self.w, self.h)
                self._opened = path
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
        # Pipelined: the simulator works on frame N while Python decodes
        # frame N+1 and then shows frame N, so the two run on separate cores.
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
            if self.modulation_enabled:
                values[pid] = lfo.apply(self.values[pid], t)
            self.readouts[pid].setText(str(values[pid]))
        self.shown_values = values
        self.panel.update()
        regs = self.program.build_register_array(values)
        regs[ABI_REG_VIDEO_TIMING] = self.vs.timing_id
        if self.inflight is not None:
            n = self.w * self.h * 3
            buf = self.proc.stdout.read(n)
            if len(buf) != n:
                raise RuntimeError(f"simulator returned {len(buf)} of {n} bytes "
                                   f"(exit code {self.proc.poll()})")
            out = np.frombuffer(buf, np.uint8).reshape(self.h, self.w, 3)
            shown = self.inflight
        else:
            out = shown = None
        self.proc.stdin.write((",".join(map(str, regs)) + "\n").encode())
        self.proc.stdin.write(np.asarray(src.convert("RGB"), np.uint8).tobytes())
        self.proc.stdin.flush()
        self.inflight = src
        if out is None:
            return
        self.out_lbl.setPixmap(_pix_rgb(out))
        self.in_lbl.setPixmap(_pix(shown))
        self.frames += 1
        now = time.time()
        if now - self.t_last >= 1:
            self.status.setText(f"{self.frames / (now - self.t_last):.1f} fps  "
                                f"{self.w}x{self.h} ({self.mode})")
            self.frames, self.t_last = 0, now

    def closeEvent(self, e):
        QSettings("cyberyak", "fastsim-live").setValue("geometry", self.saveGeometry())
        self.timer.stop()
        self.proc.kill()
        if self.video is not None:
            self.video.close()
        if self.midi is not None:
            self.midi.close()
        self.midi_timer.stop()
        super().closeEvent(e)


class AspectView(QWidget):
    """Shows a pixmap scaled to fit, letterboxed to a fixed display aspect."""

    def __init__(self, aspect: float):
        super().__init__()
        self.aspect, self._pm = aspect, None
        self.setMinimumSize(480, 270)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def setPixmap(self, pm: QPixmap):
        self._pm = pm
        self.update()

    def pixmap(self) -> QPixmap | None:
        return self._pm

    def paintEvent(self, _):
        qp = QPainter(self)
        qp.fillRect(self.rect(), QColor(0, 0, 0))
        if self._pm is None:
            return
        w = min(self.width(), self.height() * self.aspect)
        h = w / self.aspect
        qp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        qp.drawPixmap(QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h).toRect(), self._pm)


def _pix(im: Image.Image) -> QPixmap:
    return _pix_rgb(np.ascontiguousarray(np.asarray(im.convert("RGB"))))


def _pix_rgb(a: np.ndarray) -> QPixmap:
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
    ap.add_argument("program_dir", type=Path, nargs="?", default=DEFAULT_PROGRAM)
    ap.add_argument("--mode", choices=list(MODES), help="resolution mode (default: automatic)")
    ap.add_argument("--video", help="start with this video file as the source")
    a = ap.parse_args()
    _setup_logging()
    log.info("---- live preview start ----")
    app = QApplication(sys.argv)
    w = Live(a.program_dir, a.mode)
    settings = QSettings("cyberyak", "fastsim-live")
    geom = settings.value("geometry")
    if geom is not None:
        w.restoreGeometry(geom)                 # size and place from last time
    port = settings.value("midi_port") or ""
    if port and w.midi and port in w.midi.ports():
        w.open_midi(port)
    if a.video:
        w.select_source(Path(a.video).name, str(Path(a.video).resolve()))
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
