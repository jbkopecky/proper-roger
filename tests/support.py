"""Shared test helpers: the shipped config, a tiny corpus, a record."""
from pathlib import Path

from proper_roger.audio import render
from proper_roger.corpus import corpus_from_data
from proper_roger.limits import SAMPLE_RATE
from proper_roger.rating import Record


HERE = Path(__file__).resolve().parent.parent   # the repository
CONFIG = HERE / "config.toml"


def synth(text, t, tone_hz, volume, rate=SAMPLE_RATE):
    return render(text, t, tone_hz, volume, rate)[0]


def tiny_corpus(**overrides):
    data = {
        "vocab": {"name": ["HANS", "PETER"], "rig": ["K2", "KX3", "QCX"]},
        "ranges": {"temp": [10, 12]},
        "blocks": {d: [f"B{d} NAME {{name}}"] for d in range(1, 6)},
        "phrases": {d: [f"P{d} RIG {{rig}} ES {{rig2}} TEMP {{temp}}C"] for d in range(1, 6)},
    }
    data.update(overrides)
    return corpus_from_data(data)


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def calm(**kw):
    """A record for -w 20 -f 15 -d 3, 6 words, on the default calm band."""
    fields = dict(t="2026-09-27T20:00:00+02:00", wpm=20, fw=15, level=3, words=6,
                  fist=5, vary=1, qsb=0.2, noise=0.15, qrm=0.0, filter=400)
    return Record(**{**fields, **kw})
