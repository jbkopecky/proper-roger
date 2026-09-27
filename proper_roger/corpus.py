"""The corpus in config.toml (templates, vocabulary, sidetone calls, Elo weights) and the phrase generator."""
import random
import re
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path

from .morse import unsupported_chars
from .rating import RatingModel


MODES = ("blocks", "phrases", "mixed")

PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_]+)(\d*)\}")


class CorpusError(ValueError):
    """The corpus file is missing, malformed or inconsistent."""


@dataclass
class Corpus:
    vocab: dict          # placeholder -> list of values
    ranges: dict         # placeholder -> (low, high) integers
    blocks: dict         # difficulty -> list of templates
    phrases: dict        # difficulty -> list of templates
    defaults: dict       # CLI defaults from the file
    path: Path | None = None
    station_keys: tuple = ()  # placeholders that stay fixed for one station
    sidetone: dict | None = None        # what you send: agn, qrs, roger, ack
    rating: "RatingModel | None" = None  # the Elo weights

    def __post_init__(self):
        if self.sidetone is None:
            self.sidetone = {k: list(v) for k, v in SIDETONE_DEFAULTS.items()}
        if self.rating is None:
            self.rating = RatingModel()


def load_corpus(path) -> Corpus:
    path = Path(path)
    if path.suffix.lower() in (".yaml", ".yml"):
        raise CorpusError(f"{path}: the corpus is a TOML file since version 0.2; "
                          "see config.toml for the format")
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except OSError as exc:
        raise CorpusError(f"cannot read corpus {path}: {exc.strerror or exc}")
    except tomllib.TOMLDecodeError as exc:
        raise CorpusError(f"{path}: {exc}")
    return corpus_from_data(data, path)


def corpus_from_data(data, path=None) -> Corpus:
    if not isinstance(data, dict):
        raise CorpusError("corpus: the top level must be a mapping")
    vocab = {}
    for key, values in _section(data, "vocab").items():
        if not isinstance(values, list) or not values:
            raise CorpusError(f"vocab.{key}: expected a non-empty list")
        if any(v is None or isinstance(v, bool) or not str(v).strip() for v in values):
            raise CorpusError(f"vocab.{key}: empty, null or true/false entry in {values!r}")
        vocab[str(key).lower()] = [" ".join(str(v).upper().split()) for v in values]
    ranges = {}
    for key, bounds in _section(data, "ranges").items():
        ok = (isinstance(bounds, list) and len(bounds) == 2
              and all(isinstance(b, int) and not isinstance(b, bool) for b in bounds)
              and bounds[0] <= bounds[1])
        if not ok:
            raise CorpusError(f"ranges.{key}: expected [low, high] integers with low <= high")
        ranges[str(key).lower()] = (bounds[0], bounds[1])
    blocks = _templates(_section(data, "blocks"), "blocks")
    phrases = _templates(_section(data, "phrases"), "phrases")
    defaults = data.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise CorpusError("defaults: expected a mapping")
    keys = _section(data, "station").get("keys", [])
    if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
        raise CorpusError("station.keys: expected a list of placeholder names")
    station_keys = tuple(k.lower() for k in keys)
    sidetone = _sidetone_calls(_section(data, "sidetone"))
    rating = rating_model_from(_section(data, "rating"))
    corpus = Corpus(vocab, ranges, blocks, phrases, defaults, path, station_keys,
                    sidetone, rating)
    _validate_corpus(corpus)
    return corpus


def _section(data: dict, name: str) -> dict:
    value = data.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CorpusError(f"{name}: expected a mapping")
    return value


def _sidetone_calls(section: dict) -> dict:
    calls = {k: list(v) for k, v in SIDETONE_DEFAULTS.items()}
    for key, values in section.items():
        if key not in calls:
            raise CorpusError(f"sidetone: unknown entry {key!r} "
                              f"(expected {', '.join(SIDETONE_DEFAULTS)})")
        if (not isinstance(values, list) or not values
                or not all(isinstance(v, str) and v.strip() for v in values)):
            raise CorpusError(f"sidetone.{key}: expected a non-empty list of texts")
        values = [" ".join(v.upper().split()) for v in values]
        for value in values:
            bad = unsupported_chars(value)
            if bad:
                raise CorpusError(f"sidetone.{key}: {value!r} uses characters "
                                  f"without Morse code: {''.join(bad)!r}")
        calls[key] = values
    return calls


def _templates(section: dict, name: str) -> dict:
    result = {}
    for key, items in section.items():
        try:
            level = int(key)
        except (TypeError, ValueError):
            raise CorpusError(f"{name}: difficulty keys must be integers 1-5, got {key!r}")
        if not 1 <= level <= 5:
            raise CorpusError(f"{name}: difficulty {level} is outside 1-5")
        if not isinstance(items, list) or not items:
            raise CorpusError(f"{name}.{level}: expected a non-empty list of templates")
        if any(item is None or isinstance(item, bool) or not str(item).strip() for item in items):
            raise CorpusError(f"{name}.{level}: empty, null or true/false template")
        result[level] = [" ".join(str(item).split()) for item in items]
    missing = [d for d in range(1, 6) if d not in result]
    if missing:
        raise CorpusError(f"{name}: missing difficulty levels {missing}")
    return result


def _validate_corpus(corpus: Corpus) -> None:
    for key in corpus.station_keys:
        if key not in corpus.vocab and key not in corpus.ranges:
            raise CorpusError(f"station.keys: {key!r} is not in vocab or ranges")
    for key, values in corpus.vocab.items():
        for value in values:
            bad = unsupported_chars(value)
            if bad:
                raise CorpusError(f"vocab.{key}: {value!r} uses characters "
                                  f"without Morse code: {''.join(bad)!r}")
    for name, pools in (("blocks", corpus.blocks), ("phrases", corpus.phrases)):
        for level, templates in pools.items():
            for tpl in templates:
                for m in PLACEHOLDER_RE.finditer(tpl):
                    base = m.group(1).lower()
                    if base not in corpus.vocab and base not in corpus.ranges:
                        raise CorpusError(f"{name}.{level}: unknown placeholder "
                                          f"{m.group(0)} in {tpl!r}")
                literal = PLACEHOLDER_RE.sub(" ", tpl).upper()
                bad = unsupported_chars(literal)
                if bad:
                    raise CorpusError(f"{name}.{level}: {tpl!r} uses characters "
                                      f"without Morse code: {''.join(bad)!r}")


class Generator:
    """Draws templates for one difficulty/mode and fills their placeholders.

    * ``{name}`` picks from ``vocab.name``; ``{temp}`` picks an integer from
      ``ranges.temp``.
    * The same placeholder twice in a template repeats the same value
      (``NAME {name} {name}`` -> ``NAME HANS HANS``).
    * A digit suffix asks for a *different* value from the same pool
      (``{rig}`` / ``{rig2}``).
    * The same template is never drawn twice in a row.
    * With ``station=N``, the corpus' station placeholders (name, QTH, rig...)
      keep their value for N phrases in a row: one operator talking. After
      each ``next()``, ``new_station`` tells whether a new one just started.
    """

    def __init__(self, corpus: Corpus, difficulty: int, mode: str = "mixed",
                 seed: int | None = None, station: int = 0):
        if difficulty not in corpus.blocks or difficulty not in corpus.phrases:
            raise ValueError(f"difficulty must be 1-5, got {difficulty}")
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        self.corpus = corpus
        self.difficulty = difficulty
        self.mode = mode
        self.station = station
        self.rng = random.Random(seed)
        self._last_template = None
        self._persona = {}       # station placeholder -> its value for this station
        self._phrases_left = 0   # before the next station
        self.new_station = False

    def next(self) -> str:
        self.new_station = self._phrases_left == 0
        if self.new_station:
            self._persona = {}
            self._phrases_left = self.station
        self._phrases_left = max(0, self._phrases_left - 1)
        pool = self._pool()
        template = self.rng.choice(pool)
        if len(pool) > 1 and template == self._last_template:
            template = self.rng.choice([t for t in pool if t != template])
        self._last_template = template
        return self.fill(template)

    def _pool(self):
        if self.mode == "blocks":
            return self.corpus.blocks[self.difficulty]
        if self.mode == "phrases":
            return self.corpus.phrases[self.difficulty]
        if self.rng.random() < 0.5:
            return self.corpus.blocks[self.difficulty]
        return self.corpus.phrases[self.difficulty]

    def fill(self, template: str) -> str:
        picks = {}

        def replace(match):
            base, suffix = match.group(1).lower(), match.group(2)
            key = base + suffix
            if key not in picks:
                used = {v for k, v in picks.items() if k.rstrip("0123456789") == base}
                if self.station and base in self.corpus.station_keys:
                    if base not in self._persona:
                        self._persona[base] = self._pick(base, set())
                    if not suffix:
                        picks[key] = self._persona[base]
                        return picks[key]
                    used.add(self._persona[base])  # {rig2} differs from the station's rig
                picks[key] = self._pick(base, used)
            return picks[key]

        text = " ".join(PLACEHOLDER_RE.sub(replace, template).upper().split())
        bad = unsupported_chars(text)
        if bad:  # only possible with a Corpus built without corpus_from_data()
            raise CorpusError(f"{text!r} uses characters without Morse code: {''.join(bad)!r}")
        return text

    def _pick(self, base: str, used: set) -> str:
        if base in self.corpus.vocab:
            values = self.corpus.vocab[base]
            candidates = [v for v in values if v not in used] or values
            return self.rng.choice(candidates)
        if base in self.corpus.ranges:
            low, high = self.corpus.ranges[base]
            value = str(self.rng.randint(low, high))
            for _ in range(8):
                if value not in used or low == high:
                    break
                value = str(self.rng.randint(low, high))
            return value
        raise CorpusError(f"unknown placeholder {{{base}}}")


SIDETONE_DEFAULTS = {
    "agn": ("AGN?", "PSE AGN"),     # r: a repeat, please
    "qrs": ("QRS PSE", "PSE QRS"),  # s: slower, please
    "roger": ("R R", "R FB", "FB"),  # n after a Proper Roger
    "ack": ("R", "OK"),             # n after an AGN, a QRS or a peek
}


def rating_model_from(section: dict) -> RatingModel:
    """A RatingModel from config.toml's [rating] section."""
    known = {f.name: f for f in fields(RatingModel)}
    changes = {}
    for key, value in section.items():
        if key not in known:
            raise CorpusError(f"rating: unknown setting {key!r}")
        if key == "ranks":
            changes[key] = _ranks(value)
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CorpusError(f"rating.{key}: expected a number, got {value!r}")
        if key == "provisional":
            if not isinstance(value, int) or value < 0:
                raise CorpusError(f"rating.provisional: expected an integer >= 0, got {value!r}")
        elif value < 0 or (key in ("speed_ref", "words_ref", "scale") and value == 0):
            raise CorpusError(f"rating.{key}: must be positive, got {value!r}")
        changes[key] = value if key == "provisional" else float(value)
    if not 0 <= changes.get("farnsworth_share", 0) <= 1:
        raise CorpusError("rating.farnsworth_share: must be between 0 and 1")
    return replace(RatingModel(), **changes)


def _ranks(value) -> tuple:
    ok = isinstance(value, list) and value and all(
        isinstance(r, dict) and set(r) == {"at", "name"} and isinstance(r["name"], str)
        and isinstance(r["at"], (int, float)) and not isinstance(r["at"], bool)
        for r in value)
    if not ok:
        raise CorpusError('rating.ranks: expected a list of { at = 1300, name = "Ragchewer" }')
    return tuple(sorted((r["at"], r["name"]) for r in value))
