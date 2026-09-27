"""Your CW RX rating (Elo), and the history it is computed from."""
import json
import math
import os
import sys
from dataclasses import asdict, dataclass, fields
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


@dataclass(frozen=True)
class Record:
    """One phrase you Rogered, as the history keeps it: how it was sent
    (enough to rate it again with any weights), what you had chosen, and how
    you copied it. Never a rating."""
    # how it was sent: everything phrase_rating() looks at
    wpm: int
    fw: int
    level: int = 1
    words: int = 1
    fist: int = 0
    vary: int = 0
    qsb: float = 0.0
    noise: float = 0.0
    qrm: float = 0.0
    filter: int = 400
    shape: str = "soft"
    # what you had chosen (a --vary station sends a little off it)
    mode: str = "mixed"
    set_wpm: int | None = None
    set_fw: int | None = None
    # how you copied it
    agn: int = 0
    qrs: int = 0
    peek: bool = False
    secs: float = 0.0
    t: str = ""         # local ISO time with its UTC offset
    text: str = ""

    @property
    def proper(self) -> bool:
        """A Proper Roger: no AGN, no QRS, no peek."""
        return not (self.agn or self.qrs or self.peek)

    @property
    def day(self) -> date:
        return datetime.fromisoformat(self.t).date()

    @property
    def setup(self):
        """``(level, wpm, farnsworth)`` you chose, not what a --vary station sent."""
        wpm = self.wpm if self.set_wpm is None else self.set_wpm
        return self.level, wpm, self.fw if self.set_fw is None else self.set_fw

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, line: str) -> "Record":
        """A record from one history line; ValueError if it cannot be one.
        Keys it does not know are ignored, so older and newer files load."""
        data = json.loads(line)
        if not isinstance(data, dict):
            raise ValueError("not an object")
        known = {f.name for f in fields(cls)}
        rec = cls(**{k: v for k, v in data.items() if k in known})
        datetime.fromisoformat(rec.t)
        if not (rec.wpm > 0 and rec.fw > 0):
            raise ValueError("speeds must be positive")
        return rec


def rank_for(model: RatingModel, value: float):
    """``(name, next_name, next_at)`` for a rating; the next rank is None at the top."""
    name, following = model.ranks[0][1], None
    for at, rank in model.ranks:
        if value >= at:
            name = rank
        elif following is None:
            following = (rank, at)
    return (name,) + (following or (None, None))


def phrase_rating(model: RatingModel, rec: Record) -> float:
    """D: how hard a phrase was to copy, sent the way ``rec`` says."""
    share = model.farnsworth_share
    blended = rec.wpm ** (1.0 - share) * rec.fw ** share
    return (model.base
            + model.speed * math.log2(blended / model.speed_ref)
            + model.level * (rec.level - 1)
            + model.words * math.log2(max(1, rec.words) / model.words_ref)
            + model.fist * rec.fist
            + model.qsb * rec.qsb
            + model.noise * rec.noise * math.sqrt(rec.filter / 400)
            + model.qrm * rec.qrm
            + model.vary * rec.vary)


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
        """``1412``: the rating as shown (provisional is said once, in words)."""
        return str(round(self.value))

    def play(self, rec: Record) -> Game:
        model = self.model
        d = phrase_rating(model, rec)
        s = copy_result(model, rec.agn, rec.qrs, rec.peek)
        e = self.expected(d)
        k = model.k_provisional if self.provisional else model.k
        before = self.value
        after = max(model.floor, before + k * (s - e))
        self.value = after
        self.count += 1
        new_best = (not self.provisional and self.best is not None
                    and round(after) > round(self.best))
        self.best = after if self.best is None else max(self.best, after)
        return Game(d, before, after, rec.proper, new_best,
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
                self.records.append(Record.from_json(line))
            except (ValueError, TypeError):
                self.skipped += 1
        return self

    def append(self, rec: Record) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(rec.to_json() + "\n")
        self.records.append(rec)


def speed_label(wpm, fw) -> str:
    return f"{wpm}" if wpm == fw else f"{wpm}/{fw}"


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


def longest_run(records) -> int:
    """The most Proper Rogers in a row."""
    best = run = 0
    for rec in records:
        run = run + 1 if rec.proper else 0
        best = max(best, run)
    return best
