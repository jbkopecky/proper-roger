"""The phrase on the air and what you did about it; the scoreboard that
rates and keeps every phrase you Roger. No sound, no text: the session does
that."""
import time
from dataclasses import dataclass, field, replace

from .rating import Game, History, Rating, RatingModel, Record, longest_run, phrase_rating


RUN_MILESTONES = (5, 10, 25, 50, 100)   # Proper Rogers in a row worth a cheer


@dataclass
class Attempt:
    """One phrase on the air: what you asked for, and the easiest way you
    heard it. Turning the speed down to copy a phrase is free, but it is
    then rated at that speed."""
    text: str
    agn: int = 0
    qrs: int = 0
    peek: bool = False
    heard: Record | None = None
    started: float = field(default_factory=time.monotonic)

    @property
    def proper(self) -> bool:
        return not (self.agn or self.qrs or self.peek)

    def hear(self, sent: Record, model: RatingModel) -> None:
        """The phrase was just sent as ``sent``: keep it if it was the easiest yet."""
        if self.heard is None or phrase_rating(model, sent) < phrase_rating(model, self.heard):
            self.heard = sent

    def record(self, when: str) -> Record:
        """What goes into the history when you Roger it at ``when``."""
        return replace(self.heard, agn=self.agn, qrs=self.qrs, peek=self.peek,
                       secs=round(time.monotonic() - self.started, 1), t=when, text=self.text)


class Scoreboard:
    """Your Elo, replayed from the history, and this session's phrases.

    Off the record (no history, or reading along with --show-text) it still
    counts your phrases, but rates and saves nothing.
    """

    def __init__(self, model: RatingModel, history: History | None = None,
                 rated: bool = True):
        self.history = history
        self.rated = rated and history is not None
        self.rating = Rating(model)
        for rec in history.records if history else ():
            self.rating.play(rec)
        self.opening = self.rating.value
        self.played: list[Record] = []
        self.in_a_row = 0            # Proper Rogers in a row, this session
        self._error: str | None = None
        self._error_reported = False

    def score(self, rec: Record) -> Game | None:
        """Count ``rec``; on the record, rate it and save it."""
        self.played.append(rec)
        self.in_a_row = self.in_a_row + 1 if rec.proper else 0
        if not self.rated:
            return None
        game = self.rating.play(rec)
        try:
            self.history.append(rec)
        except OSError as exc:
            self._error = self._error or (f"cannot save to {self.history.path}: "
                                          f"{exc.strerror or exc}")
        return game

    def take_error(self) -> str | None:
        """A message the first time saving fails, else None."""
        if self._error is None or self._error_reported:
            return None
        self._error_reported = True
        return self._error

    @property
    def milestone(self) -> int | None:
        """The run of Proper Rogers just reached, when it is worth a cheer."""
        return self.in_a_row if self.in_a_row in RUN_MILESTONES else None

    def total(self, what: str) -> int:
        """This session's ``"agn"``, ``"qrs"`` or ``"peek"`` count."""
        return sum(int(getattr(rec, what)) for rec in self.played)

    @property
    def proper(self) -> int:
        return sum(1 for rec in self.played if rec.proper)

    @property
    def best_run(self) -> int:
        return longest_run(self.played)
