# Copyright (C) 2026 cyberyak
# SPDX-License-Identifier: GPL-3.0-only
"""Virtual front panel for the live preview, laid out like the Videomancer.

Same control positions and numbering as the hardware panel: knobs 1-6,
toggles 7-11 and the fader (12), with Time/Space/Slope, an LCD, the 1-11
button grid, Tap/Stop/Play and the Select encoder. The modulation mapping is
this simulator's own (it drives live.py's LFOs), not LZX firmware behaviour:

  buttons 1-6, ★   select knob 1-6 / the fader for modulation
  Time             LFO rate (0.01-20 Hz, log)        drag up/down
  Space            LFO depth, bipolar (centre = 0)   drag up/down
  Slope            LFO shape (Off, Sine, Tri, ...)   drag up/down
  Tap              tap twice: LFO rate = 1 / interval
  Motion           modulation on/off for all knobs
  ▶ / ■            play / pause the simulation
  Select ◁ ▷       previous / next program
  double-click     a knob resets it (Space: to 0)
"""
from __future__ import annotations

import math
import time

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

W, H = 800, 600                     # design units; painted scaled to the widget
KNOB_R, BTN_R = 25, 14
PANEL = QColor(28, 28, 31)
INK = QColor(214, 214, 210)
DIM = QColor(120, 120, 118)
LED_ON = QColor(255, 80, 60)

MOD_KNOBS = {"time": (100, 140), "space": (200, 140), "slope": (300, 140)}
KNOBS = {i + 1: (100 + 100 * (i % 3), 245 + 100 * (i // 3)) for i in range(6)}
TOGGLES = {7 + i: (80 + 50 * i, 470) for i in range(5)}
BUTTONS = {}
for row, labels in enumerate([["1", "2", "3", "MOTION", "SYSTEM"],
                              ["4", "5", "6", "STATE", "★"],
                              ["7", "8", "9", "10", "11"]]):
    for col, lab in enumerate(labels):
        BUTTONS[lab] = (400 + 50 * col, 245 + 55 * row)
TRANSPORT = {"TAP": (420, 470), "STOP": (520, 470), "PLAY": (620, 470)}
SELECT = (705, 135)
FADER = (705, 230, 460)                 # x, top, bottom
LCD = QRectF(385, 105, 255, 70)
SHAPES = ["Off", "Sine", "Triangle", "Saw", "Square", "S&H"]


class VPanel(QWidget):
    """Paints the panel from the live window's state; edits go back through it."""

    def __init__(self, live):
        super().__init__()
        self.live = live
        self.sel = "rotary_potentiometer_1"          # parameter being modulated
        self.motion = True
        self.msg, self.msg_t = "", 0.0
        self.drag = None                              # (target, start y, start value)
        self.taps: list[float] = []
        self.setMinimumSize(560, 420)
        self.setMouseTracking(False)

    # -- mapping -------------------------------------------------------------
    @staticmethod
    def pid_for(n: int) -> str:
        if n <= 6:
            return f"rotary_potentiometer_{n}"
        if n <= 11:
            return f"toggle_switch_{n}"
        return "linear_potentiometer_12"

    def param(self, pid):
        return next((p for p in self.live.program.parameters if p.parameter_id == pid), None)

    def say(self, text: str):
        self.msg, self.msg_t = text, time.time()
        self.update()

    # -- geometry ------------------------------------------------------------
    def _xf(self):
        s = min(self.width() / W, self.height() / H)
        return s, (self.width() - W * s) / 2, (self.height() - H * s) / 2

    def _to_design(self, pos) -> QPointF:
        s, ox, oy = self._xf()
        return QPointF((pos.x() - ox) / s, (pos.y() - oy) / s)

    def _hit(self, p: QPointF):
        def near(c, r):
            return (p.x() - c[0]) ** 2 + (p.y() - c[1]) ** 2 <= r * r
        for name, c in MOD_KNOBS.items():
            if near(c, KNOB_R + 6):
                return ("mod", name)
        for n, c in KNOBS.items():
            if near(c, KNOB_R + 6):
                return ("knob", n)
        for n, c in TOGGLES.items():
            if abs(p.x() - c[0]) < 16 and abs(p.y() - c[1]) < 24:
                return ("toggle", n)
        for lab, c in BUTTONS.items():
            if near(c, BTN_R + 4):
                return ("button", lab)
        for lab, c in TRANSPORT.items():
            if near(c, 20):
                return ("transport", lab)
        if near(SELECT, 30):
            return ("select", -1 if p.x() < SELECT[0] else 1)
        x, top, bot = FADER
        if abs(p.x() - x) < 22 and top - 10 <= p.y() <= bot + 10:
            return ("fader", 12)
        return None

    # -- values ----------------------------------------------------------------
    def _lfo_value(self, which: str) -> float:
        lfo = self.live.lfos.get(self.sel)
        if lfo is None:
            return 0.0
        if which == "time":
            return (math.log10(lfo.rate) + 2) / (math.log10(20) + 2)
        if which == "space":
            return (lfo.depth + 1) / 2
        return SHAPES.index(lfo.shape) / (len(SHAPES) - 1)

    def apply_lfo_control(self, which: str, v: float):
        """Time/Space/Slope from MIDI: same as turning the panel knob."""
        self._set_lfo_from(which, v, touched=False)

    def _set_lfo_from(self, which: str, v: float, touched: bool = True):
        v = min(1.0, max(0.0, v))
        pid = self.sel
        if touched:
            self.live.last_target = {"time": "lfo_rate:sel", "space": f"lfo_depth:{pid}",
                                     "slope": "lfo_shape:sel"}[which]
        if pid not in self.live.lfos:
            return
        name = self.param(pid).name_label if self.param(pid) else pid
        if which == "time":
            rate = round(10 ** (v * (math.log10(20) + 2) - 2), 2)
            self.live.set_lfo(pid, rate=rate)
            self.say(f"{name} LFO rate {rate:.2f} Hz")
        elif which == "space":
            depth = round((v * 2 - 1) * 100)
            self.live.set_lfo(pid, depth=depth / 100)
            self.say(f"{name} LFO depth {depth:+d}%")
        else:
            shape = SHAPES[round(v * (len(SHAPES) - 1))]
            self.live.set_lfo(pid, shape=shape)
            self.say(f"{name} LFO {shape}")

    # -- mouse -------------------------------------------------------------------
    def mousePressEvent(self, e):
        hit = self._hit(self._to_design(e.position()))
        if hit is None:
            return
        kind, key = hit
        if kind == "mod":
            self.drag = (hit, e.position().y(), self._lfo_value(key))
        elif kind in ("knob", "fader"):
            pid = self.pid_for(key)
            if self.param(pid) is None:
                self.say(f"{key}: not used by this program")
                return
            if kind == "fader":
                self._fader_to(e.position())
            self.drag = (hit, e.position().y(), self.live.values[pid] / 1023)
            self._announce(pid)
        elif kind == "toggle":
            pid = self.pid_for(key)
            p = self.param(pid)
            if p is None:
                self.say(f"{key}: not used by this program")
                return
            on = 0 if self.live.values[pid] else 1
            self.live.apply_value(pid, on)
            labels = p.value_labels or ["Off", "On"]
            self.say(f"{key} {p.name_label}: {labels[on]}")
        elif kind == "button":
            self._button(key)
        elif kind == "transport":
            self._transport(key)
        elif kind == "select":
            self.live.step_program(key)

    def mouseMoveEvent(self, e):
        if self.drag is None:
            return
        (kind, key), y0, v0 = self.drag
        if kind == "fader":
            self._fader_to(e.position())
            return
        v = v0 + (y0 - e.position().y()) / 200          # 200 px = full travel
        if kind == "mod":
            self._set_lfo_from(key, v)
        else:
            pid = self.pid_for(key)
            self.live.apply_value(pid, int(round(min(1, max(0, v)) * 1023)))
            self._announce(pid)

    def mouseReleaseEvent(self, e):
        self.drag = None

    def mouseDoubleClickEvent(self, e):
        hit = self._hit(self._to_design(e.position()))
        if not hit:
            return
        kind, key = hit
        if kind == "knob" and self.param(self.pid_for(key)):
            pid = self.pid_for(key)
            self.live.apply_value(pid, self.param(pid).initial_pot_value)
            self._announce(pid)
        elif kind == "mod" and key == "space":
            self._set_lfo_from("space", 0.5)

    def wheelEvent(self, e):
        hit = self._hit(self._to_design(e.position()))
        if hit and hit[0] == "select":
            self.live.step_program(1 if e.angleDelta().y() < 0 else -1)

    def _fader_to(self, pos):
        x, top, bot = FADER
        y = self._to_design(pos).y()
        v = (bot - min(bot, max(top, y))) / (bot - top)
        self.live.apply_value("linear_potentiometer_12", int(round(v * 1023)))
        self._announce("linear_potentiometer_12")

    def _announce(self, pid):
        p = self.param(pid)
        if p:
            self.say(f"{p.name_label}: {self.live.values[pid]}")

    def _button(self, lab):
        if lab in ("1", "2", "3", "4", "5", "6", "★"):
            pid = self.pid_for(12 if lab == "★" else int(lab))
            if self.param(pid) is None or pid not in self.live.lfos:
                self.say(f"{lab}: not used by this program")
                return
            self.sel = pid
            lfo = self.live.lfos[pid]
            self.say(f"Modulate {self.param(pid).name_label}: {lfo.shape} "
                     f"{lfo.rate:.2f} Hz {round(lfo.depth * 100):+d}%")
        elif lab == "MOTION":
            self.motion = not self.motion
            self.live.modulation_enabled = self.motion
            self.say("Modulation " + ("on" if self.motion else "off (LFOs held)"))
        elif lab == "SYSTEM":
            self.say(f"{self.live.mode}  {self.live.w}x{self.live.h}")
        elif lab == "STATE":
            active = [self.param(pid).name_label for pid, l in self.live.lfos.items()
                      if l.shape != "Off" and l.depth != 0 and self.param(pid)]
            self.say("LFOs: " + (", ".join(active) if active else "none"))
        else:
            self.say(f"{lab}: switch {lab}" if lab in ("7", "8", "9", "10", "11") else lab)

    def _transport(self, lab):
        if lab == "PLAY":
            self.live.set_playing(True)
            self.say("Play")
        elif lab == "STOP":
            self.live.set_playing(False)
            self.say("Paused")
        else:
            now = time.time()
            self.taps = [t for t in self.taps if now - t < 4] + [now]
            if len(self.taps) >= 2:
                period = (self.taps[-1] - self.taps[0]) / (len(self.taps) - 1)
                rate = round(min(20, max(0.01, 1 / period)), 2)
                self.live.set_lfo(self.sel, rate=rate)
                if self.live.lfos[self.sel].shape == "Off":
                    self.live.set_lfo(self.sel, shape="Sine")
                self.say(f"Tap: {self.param(self.sel).name_label} LFO {rate:.2f} Hz")
            else:
                self.say("Tap again to set the LFO rate")

    # -- painting ------------------------------------------------------------------
    def paintEvent(self, _):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        s, ox, oy = self._xf()
        qp.translate(ox, oy)
        qp.scale(s, s)
        lv = self.live

        qp.setPen(Qt.PenStyle.NoPen)
        qp.setBrush(PANEL)
        qp.drawRoundedRect(QRectF(10, 10, W - 20, H - 20), 14, 14)
        for x, y in ((40, 40), (W - 40, 40), (40, H - 40), (W - 40, H - 40)):
            self._screw(qp, x, y)
        qp.setPen(INK)
        qp.setFont(QFont("Helvetica", 30, QFont.Weight.Light))
        qp.drawText(QRectF(0, 30, W, 50), Qt.AlignmentFlag.AlignCenter, "· v i r t u a l   p a n e l ·")

        # modulation knobs show the selected parameter's LFO
        lfo = lv.lfos.get(self.sel)
        for name, (x, y) in MOD_KNOBS.items():
            active = lfo is not None and lfo.shape != "Off" and lfo.depth != 0
            led = 0.5 + 0.5 * lfo.value(lv.sim_frame / lv.frame_rate) if active and self.motion else 0
            self._knob(qp, x, y, self._lfo_value(name), name.upper(), led, ring=True)
        for n, (x, y) in KNOBS.items():
            pid = self.pid_for(n)
            used = self.param(pid) is not None
            val = lv.values.get(pid, 0) / 1023
            shown = lv.shown_values.get(pid, lv.values.get(pid, 0)) / 1023 if used else 0
            self._knob(qp, x, y, val if used else 0, str(n), shown if used else 0,
                       dim=not used, selected=(pid == self.sel))
        for n, (x, y) in TOGGLES.items():
            pid = self.pid_for(n)
            used = self.param(pid) is not None
            self._toggle(qp, x, y, bool(lv.values.get(pid, 0)), str(n), dim=not used)

        self._lcd(qp)
        for lab, (x, y) in BUTTONS.items():
            lit = (lab == "MOTION" and self.motion) or \
                  (lab in ("1", "2", "3", "4", "5", "6") and self.pid_for(int(lab)) == self.sel) or \
                  (lab == "★" and self.sel == "linear_potentiometer_12")
            self._button_shape(qp, x, y, BTN_R, lab, lit, small_label=len(lab) > 2)
        playing = lv.timer.isActive()
        for lab, (x, y) in TRANSPORT.items():
            glyph = {"TAP": "", "STOP": "■", "PLAY": "▶"}[lab]
            lit = (lab == "PLAY" and playing) or (lab == "STOP" and not playing)
            self._button_shape(qp, x, y, 18, glyph, lit)
            if lab == "TAP":
                self._label(qp, x, y + 34, "TAP")
        self._encoder(qp)
        self._fader(qp)
        qp.end()

    def _screw(self, qp, x, y):
        qp.setPen(QPen(QColor(70, 70, 72), 2))
        qp.setBrush(QColor(45, 45, 48))
        qp.drawEllipse(QPointF(x, y), 8, 8)
        qp.drawLine(QPointF(x - 5, y), QPointF(x + 5, y))
        qp.drawLine(QPointF(x, y - 5), QPointF(x, y + 5))

    def _label(self, qp, x, y, text, color=INK, size=11):
        qp.setPen(color)
        qp.setFont(QFont("Helvetica", size, QFont.Weight.DemiBold))
        qp.drawText(QRectF(x - 50, y - 9, 100, 18), Qt.AlignmentFlag.AlignCenter, text)

    def _led(self, qp, x, y, level):
        c = QColor(LED_ON)
        c.setAlphaF(0.15 + 0.85 * max(0.0, min(1.0, level)))
        qp.setPen(Qt.PenStyle.NoPen)
        qp.setBrush(QColor(60, 30, 28))
        qp.drawEllipse(QPointF(x, y), 4, 4)
        qp.setBrush(c)
        qp.drawEllipse(QPointF(x, y), 4, 4)

    def _knob(self, qp, x, y, v, label, led, dim=False, ring=False, selected=False):
        self._led(qp, x, y - KNOB_R - 14, led)
        if selected:
            qp.setPen(QPen(QColor(255, 255, 255, 90), 3))
            qp.setBrush(Qt.BrushStyle.NoBrush)
            qp.drawEllipse(QPointF(x, y), KNOB_R + 7, KNOB_R + 7)
        qp.setPen(QPen(QColor(10, 10, 10), 2))
        qp.setBrush(QColor(55, 55, 58) if not dim else QColor(40, 40, 42))
        qp.drawEllipse(QPointF(x, y), KNOB_R, KNOB_R)
        qp.setBrush(QColor(22, 22, 24))
        qp.drawEllipse(QPointF(x, y), KNOB_R - 6, KNOB_R - 6)
        # pointer: 270 degree sweep, white triangle like the hardware
        a = math.radians(225 - 270 * max(0.0, min(1.0, v)))
        tip = QPointF(x + math.cos(a) * (KNOB_R - 7), y - math.sin(a) * (KNOB_R - 7))
        b1 = QPointF(x + math.cos(a + 2.7) * 6, y - math.sin(a + 2.7) * 6)
        b2 = QPointF(x + math.cos(a - 2.7) * 6, y - math.sin(a - 2.7) * 6)
        path = QPainterPath(tip)
        path.lineTo(b1)
        path.lineTo(b2)
        path.closeSubpath()
        qp.setPen(Qt.PenStyle.NoPen)
        qp.setBrush(DIM if dim else INK)
        qp.drawPath(path)
        self._label(qp, x, y + KNOB_R + 14, label, DIM if dim else INK, 11 if ring else 12)

    def _toggle(self, qp, x, y, on, label, dim=False):
        self._led(qp, x, y - 36, 1.0 if on and not dim else 0.0)
        qp.setPen(QPen(QColor(10, 10, 10), 2))
        qp.setBrush(QColor(70, 70, 72))
        qp.drawEllipse(QPointF(x, y), 11, 11)
        tip_y = y - 18 if on else y + 18
        qp.setPen(QPen(DIM if dim else QColor(200, 200, 198), 6, cap=Qt.PenCapStyle.RoundCap))
        qp.drawLine(QPointF(x, y), QPointF(x, tip_y))
        self._label(qp, x, y + 36, label, DIM if dim else INK, 12)

    def _button_shape(self, qp, x, y, r, label, lit, small_label=False):
        qp.setPen(QPen(QColor(8, 8, 8), 2))
        qp.setBrush(QColor(240, 240, 236) if lit else QColor(48, 48, 52))
        qp.drawEllipse(QPointF(x, y), r, r)
        if small_label:
            self._label(qp, x, y + r + 11, label, INK, 9)
        elif label:
            qp.setPen(QColor(30, 30, 30) if lit else INK)
            qp.setFont(QFont("Helvetica", 12 if r > 15 else 11, QFont.Weight.Bold))
            qp.drawText(QRectF(x - r, y - r, 2 * r, 2 * r), Qt.AlignmentFlag.AlignCenter, label)

    def _encoder(self, qp):
        x, y = SELECT
        qp.setPen(QPen(QColor(8, 8, 8), 2))
        qp.setBrush(QColor(45, 45, 48))
        qp.drawEllipse(QPointF(x, y), 20, 20)
        qp.setBrush(QColor(25, 25, 27))
        qp.drawEllipse(QPointF(x, y), 13, 13)
        self._label(qp, x - 36, y, "◁")
        self._label(qp, x + 36, y, "▷")
        self._label(qp, x, y + 36, "SELECT")

    def _fader(self, qp):
        x, top, bot = FADER
        pid = "linear_potentiometer_12"
        used = self.param(pid) is not None
        self._led(qp, x, top - 22, self.live.shown_values.get(pid, 0) / 1023 if used else 0)
        qp.setPen(QPen(QColor(8, 8, 8), 6, cap=Qt.PenCapStyle.RoundCap))
        qp.drawLine(QPointF(x, top), QPointF(x, bot))
        qp.setPen(QPen(DIM, 1))
        for i in range(11):
            ty = top + (bot - top) * i / 10
            qp.drawLine(QPointF(x - 22, ty), QPointF(x - 12, ty))
            qp.drawLine(QPointF(x + 12, ty), QPointF(x + 22, ty))
        v = self.live.values.get(pid, 0) / 1023 if used else 0
        cy = bot - (bot - top) * v
        qp.setPen(QPen(QColor(8, 8, 8), 2))
        qp.setBrush(QColor(200, 200, 198) if used else QColor(90, 90, 90))
        qp.drawRoundedRect(QRectF(x - 20, cy - 9, 40, 18), 3, 3)
        qp.setPen(QPen(QColor(60, 60, 60), 2))
        qp.drawLine(QPointF(x - 16, cy), QPointF(x + 16, cy))
        self._label(qp, x, bot + 28, "★" if used else "")

    def _lcd(self, qp):
        qp.setPen(QPen(QColor(10, 10, 10), 3))
        qp.setBrush(QColor(62, 66, 84))
        qp.drawRoundedRect(LCD, 4, 4)
        qp.setPen(QColor(210, 220, 255))
        qp.setFont(QFont("Menlo", 13, QFont.Weight.Bold))
        name = self.live.program.display_name[:24]
        qp.drawText(LCD.adjusted(10, 8, -10, -36), Qt.AlignmentFlag.AlignLeft, name)
        qp.setFont(QFont("Menlo", 11))
        line = self.msg if time.time() - self.msg_t < 4 else \
            f"{self.live.mode}  {'▶' if self.live.timer.isActive() else '■'}"
        qp.drawText(LCD.adjusted(10, 36, -10, -6), Qt.AlignmentFlag.AlignLeft, line[:32])
