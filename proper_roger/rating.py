"""Your CW RX rating (Elo), and the history it is computed from."""
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path


# Every phrase is an opponent with its own rating D, from how it was sent:
# speed, Farnsworth, language level, length and band conditions. You are
# rated R. How you copied it is the result of the game: 1 for a Proper Roger,
# less after an AGN or a QRS, 0 after a peek. Then plain Elo:
#
#     E = 1 / (1 + 10^((D - R) / 400))      R <- R + K (S - E)
#
# The history keeps what was heard and how it was copied, never a rating, so
# the whole curve is recomputed at start-up: change a weight in config.toml
# and every phrase you ever copied is priced again.

DEFAULT_RANKS = (
    (0, "SWL"), (900, "Novice"), (1100, "Pencil copier"), (1300, "Ragchewer"),
    (1500, "Head copier"), (1700, "Brass pounder"), (1900, "Old timer"), (2100, "Legend"),
)


@dataclass(frozen=True)
class RatingModel:
    """The Elo weights; every field can be set in config.toml's [rating]."""
    start: float = 1000.0         # your rating before your first phrase
    base: float = 1000.0          # a phrase at speed_ref, level 1, words_ref words, clean band
    speed_ref: float = 15.0       # WPM
    speed: float = 500.0          # points per doubling of the blended speed
    farnsworth_share: float = 0.7  # blended speed = wpm^(1 - share) x farnsworth^share
    level: float = 120.0          # points per language level above 1
    words_ref: float = 3.0
    words: float = 40.0           # points per doubling of the number of words
    fist: float = 4.0             # points per % of --fist
    qsb: float = 100.0            # at --qsb 1
    noise: float = 200.0          # at --noise 1 through a 400 Hz filter
    qrm: float = 150.0            # at --qrm 1
    vary: float = 10.0            # per WPM of --vary
    scale: float = 400.0          # Elo: 400 points apart = 10 to 1 odds
    k: float = 24.0
    k_provisional: float = 48.0
    provisional: int = 50         # your first phrases move the rating faster
    floor: float = 100.0
    agn: float = 0.5              # result of a Roger after one AGN: a draw
    qrs: float = 0.5              # after one QRS: a repeat too, and rated at the slower speed
    extra: float = 0.15           # taken off for every further AGN or QRS
    ranks: tuple = DEFAULT_RANKS


def rank_for(model: RatingModel, value: float):
    """``(name, next_name, next_at)`` for a rating; the next rank is None at the top."""
    name, following = model.ranks[0][1], None
    for at, rank in model.ranks:
        if value >= at:
            name = rank
        elif following is None:
            following = (rank, at)
    return (name,) + (following or (None, None))


def phrase_rating(model: RatingModel, rec: dict) -> float:
    """D: how hard a phrase was to copy, sent the way ``rec`` says."""
    wpm = rec["wpm"]
    fw = rec.get("fw", wpm)
    share = model.farnsworth_share
    blended = wpm ** (1.0 - share) * fw ** share
    words = max(1, rec.get("words", 1))
    return (model.base
            + model.speed * math.log2(blended / model.speed_ref)
            + model.level * (rec.get("level", 1) - 1)
            + model.words * math.log2(words / model.words_ref)
            + model.fist * rec.get("fist", 0)
            + model.qsb * rec.get("qsb", 0.0)
            + model.noise * rec.get("noise", 0.0) * math.sqrt(rec.get("filter", 400) / 400)
            + model.qrm * rec.get("qrm", 0.0)
            + model.vary * rec.get("vary", 0))


def copy_result(model: RatingModel, agn: int = 0, qrs: int = 0, peek: bool = False) -> float:
    """S: 1 for a Proper Roger, less for every AGN or QRS, 0 after a peek."""
    if peek:
        return 0.0
    asks = agn + qrs
    if not asks:
        return 1.0
    first = model.qrs if qrs else model.agn
    return max(0.0, first - model.extra * (asks - 1))


@dataclass(frozen=True)
class Game:
    """One rated phrase."""
    d: float          # the phrase's rating
    before: float
    after: float
    proper: bool      # a Proper Roger: no AGN, no QRS, no peek
    new_best: bool
    rank_before: str
    rank_after: str

    @property
    def delta(self) -> int:
        return round(self.after) - round(self.before)


class Rating:
    """Your Elo, played phrase by phrase."""

    def __init__(self, model: RatingModel):
        self.model = model
        self.value = model.start
        self.count = 0
        self.best = None

    @property
    def provisional(self) -> bool:
        return self.count < self.model.provisional

    def expected(self, d: float) -> float:
        return 1.0 / (1.0 + 10.0 ** ((d - self.value) / self.model.scale))

    def label(self) -> str:
        """``1412``, or ``1043?`` while provisional."""
        return f"{round(self.value)}{'?' if self.provisional else ''}"

    def play(self, rec: dict) -> Game:
        model = self.model
        d = phrase_rating(model, rec)
        agn, qrs, peek = rec.get("agn", 0), rec.get("qrs", 0), bool(rec.get("peek"))
        s = copy_result(model, agn, qrs, peek)
        e = self.expected(d)
        k = model.k_provisional if self.provisional else model.k
        before = self.value
        after = max(model.floor, before + k * (s - e))
        self.value = after
        self.count += 1
        new_best = (not self.provisional and self.best is not None
                    and round(after) > round(self.best))
        self.best = after if self.best is None else max(self.best, after)
        return Game(d, before, after, _proper(rec), new_best,
                    rank_for(model, before)[0], rank_for(model, after)[0])


def default_history_path() -> Path:
    """Where the history lives unless --history says otherwise."""
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or home / ".local" / "state")
    return base / "proper-roger" / "history.jsonl"


class History:
    """Your rated phrases, one JSON object per line, oldest first."""

    def __init__(self, path):
        self.path = Path(path)
        self.records: list = []
        self.skipped = 0   # lines that could not be read

    def load(self) -> "History":
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return self
        for line in filter(str.strip, text.splitlines()):
            try:
                rec = json.loads(line)
                datetime.fromisoformat(rec["t"])       # a date...
                if not (rec["wpm"] > 0 and rec.get("fw", 1) > 0):   # ...and speeds
                    raise ValueError
            except (ValueError, TypeError, KeyError):
                self.skipped += 1
                continue
            self.records.append(rec)
        return self

    def append(self, rec: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        self.records.append(rec)


def speed_label(wpm, fw) -> str:
    return f"{wpm}" if wpm == fw else f"{wpm}/{fw}"


def _day(rec: dict) -> date:
    return datetime.fromisoformat(rec["t"]).date()


def streaks(days, today: date):
    """``(current, best)`` runs of consecutive days with practice. Today
    not played yet does not break the current streak."""
    days = set(days)
    best = run = 0
    for day in sorted(days):
        run = run + 1 if day - timedelta(days=1) in days else 1
        best = max(best, run)
    current = 0
    day = today if today in days else today - timedelta(days=1)
    while day in days:
        current += 1
        day -= timedelta(days=1)
    return current, best


def setup_of(rec: dict):
    """``(level, wpm, farnsworth)`` you chose, not what a --vary station sent."""
    wpm = rec.get("set_wpm", rec["wpm"])
    return rec.get("level", 1), wpm, rec.get("set_fw", rec.get("fw", wpm))


def _proper(rec: dict) -> bool:
    return not (rec.get("agn") or rec.get("qrs") or rec.get("peek"))


def longest_run(records) -> int:
    """The most Proper Rogers in a row."""
    best = run = 0
    for rec in records:
        run = run + 1 if _proper(rec) else 0
        best = max(best, run)
    return best
