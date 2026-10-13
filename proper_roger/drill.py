"""The --words drill: common words and Q codes, spaced like flash cards.

Every word sits in a box. Copied at first hearing, it climbs a box and comes
back later; copied after an AGN or a QRS, it goes back to box 1 and comes
back soon; peeked, it goes to box 0 and comes back almost at once. Time is
counted in words heard, not in days, so a word you know waits for you
across sessions. New words come in frequency order whenever nothing is due.
"""
import json
import os
from pathlib import Path


INTERVALS = (2, 5, 12, 30, 80, 200, 500)   # words heard before a word in box b comes back
NEW_BOX = 1                                 # a new word starts here: first hearing -> box 2
HELPED_BOX = 1                              # after an AGN or a QRS
MISSED_BOX = 0                              # after a peek


class WordList:
    """The words in order, most frequent first, and round again: for --export,
    where nobody grades them."""

    def __init__(self, words):
        if not words:
            raise ValueError("no [drill] words in the corpus")
        self.words = tuple(words)
        self.n = 0
        self.new_station = False

    def next(self) -> str:
        self.new_station = self.n == 0
        word = self.words[self.n % len(self.words)]
        self.n += 1
        return word


class WordDrill:
    """The words on a spaced schedule, kept in ``path`` (JSON) when given.

    ``next()`` sends the most overdue word, else the next new one, else the
    one due soonest; ``grade()`` files the word just sent. One station sends
    the whole drill, like a teacher.
    """

    def __init__(self, words, path=None):
        if not words:
            raise ValueError("no [drill] words in the corpus")
        self.words = tuple(words)
        self.rank = {w: i for i, w in enumerate(self.words)}
        self.path = Path(path) if path else None
        self.turn = 0                 # words heard, ever
        self.cards = {}               # word -> [box, due turn]
        self.current = None
        self.new_station = False
        self._started = False
        self._error = None
        self._error_reported = False
        self.load()

    def load(self) -> None:
        if self.path is None:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.turn = int(data.get("turn", 0))
            for word, (box, due) in data.get("cards", {}).items():
                if word in self.rank:   # a word cut from config.toml is simply forgotten
                    self.cards[word] = [max(0, min(int(box), len(INTERVALS) - 1)), int(due)]
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            self._error = f"cannot read {self.path}, starting afresh: {exc}"
            self.turn, self.cards = 0, {}

    def save(self) -> None:
        if self.path is None:
            return
        data = {"turn": self.turn, "cards": {w: self.cards[w] for w in self.words
                                             if w in self.cards}}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            self._error = self._error or f"cannot save to {self.path}: {exc.strerror or exc}"

    def take_error(self) -> str | None:
        """A message the first time reading or saving fails, else None."""
        if self._error is None or self._error_reported:
            return None
        self._error_reported = True
        return self._error

    @property
    def seen(self) -> int:
        return len(self.cards)

    @property
    def due(self) -> int:
        return sum(1 for _, due in self.cards.values() if due <= self.turn)

    def next(self) -> str:
        self.new_station = not self._started
        self._started = True
        others = [w for w in self.cards if w != self.current]
        due = [w for w in others if self.cards[w][1] <= self.turn]
        new = next((w for w in self.words if w not in self.cards), None)
        if due:
            word = min(due, key=lambda w: (self.cards[w][1], self.rank[w]))
        elif new is not None:
            word = new
        elif others:
            word = min(others, key=lambda w: (self.cards[w][1], self.rank[w]))
        else:
            word = self.current or self.words[0]
        self.current = word
        return word

    def grade(self, proper: bool, peek: bool) -> int:
        """File the word just sent; returns how many words until it comes back."""
        box = self.cards.get(self.current, [NEW_BOX, 0])[0]
        if peek:
            box = MISSED_BOX
        elif proper:
            box = min(box + 1, len(INTERVALS) - 1)
        else:
            box = HELPED_BOX
        self.turn += 1
        self.cards[self.current] = [box, self.turn + INTERVALS[box]]
        self.save()
        return INTERVALS[box]
