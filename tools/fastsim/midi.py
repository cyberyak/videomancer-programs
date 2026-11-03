# Copyright (C) 2026 cyberyak
# SPDX-License-Identifier: GPL-3.0-only
"""MIDI control for the live preview: mappings, learn mode, presets.

Targets a MIDI control can drive:
  <parameter_id>           a knob/fader (CC 0-127 -> 0-1023) or a switch
                           (CC >= 64 on; a note-on flips it)
  lfo_depth:<parameter_id> that knob's LFO depth, bipolar (CC 64 = none)
  lfo_rate:sel             LFO rate of the panel's selected parameter (log)
  lfo_shape:sel            LFO shape of the selected parameter

Mappings are stored per input port in ~/.config/fastsim-live/midi.json, so a
controller keeps its mapping across programs and runs.
"""
from __future__ import annotations

import json
import queue
from pathlib import Path

STORE = Path.home() / ".config/fastsim-live/midi.json"

# Faderfox UC4, factory Setup 1 (assumed: encoders CC 8-15, faders CC 32-39 on
# channel 1, from Spectrasonics' UC4 guide; verify with Learn if a control
# doesn't respond). Encoders drive the six knobs and the selected LFO, faders
# the per-knob LFO depths and the fader.
PRESETS = {
    "Faderfox UC4 (Setup 1)": {
        **{f"cc:0:{8 + i}": f"rotary_potentiometer_{i + 1}" for i in range(6)},
        "cc:0:14": "lfo_rate:sel",
        "cc:0:15": "lfo_shape:sel",
        **{f"cc:0:{32 + i}": f"lfo_depth:rotary_potentiometer_{i + 1}" for i in range(6)},
        "cc:0:38": "lfo_depth:linear_potentiometer_12",
        "cc:0:39": "linear_potentiometer_12",
    },
}


def key_of(msg) -> str | None:
    """Mapping key for a MIDI message, or None for messages we don't map."""
    if msg.type == "control_change":
        return f"cc:{msg.channel}:{msg.control}"
    if msg.type in ("note_on", "note_off"):
        return f"note:{msg.channel}:{msg.note}"
    return None


def describe(key: str) -> str:
    kind, ch, num = key.split(":")
    return f"{'CC' if kind == 'cc' else 'Note'} {num} (ch {int(ch) + 1})"


class MidiIn:
    """One open input port; messages are queued by the MIDI thread and
    drained on the Qt thread with poll()."""

    def __init__(self):
        import mido
        self.mido = mido
        self.port = None
        self.name = ""
        self.q: queue.Queue = queue.Queue()
        self.maps: dict[str, dict[str, str]] = self._load()

    @property
    def mapping(self) -> dict[str, str]:
        return self.maps.setdefault(self.name, {})

    def ports(self) -> list[str]:
        try:
            return self.mido.get_input_names()
        except Exception:
            return []

    def open(self, name: str):
        self.close()
        if name:
            self.port = self.mido.open_input(name, callback=self.q.put)
        self.name = name

    def close(self):
        if self.port is not None:
            self.port.close()
            self.port = None

    def poll(self) -> list:
        out = []
        while True:
            try:
                out.append(self.q.get_nowait())
            except queue.Empty:
                return out

    def bind(self, key: str, target: str):
        # one control per target: drop older bindings of the same target
        for k in [k for k, t in self.mapping.items() if t == target]:
            del self.mapping[k]
        self.mapping[key] = target
        self.save()

    def apply_preset(self, name: str):
        self.mapping.update(PRESETS[name])
        self.save()

    def clear(self):
        self.mapping.clear()
        self.save()

    def _load(self) -> dict:
        try:
            return json.loads(STORE.read_text())
        except Exception:
            return {}

    def save(self):
        STORE.parent.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(self.maps, indent=1))
