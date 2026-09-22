#!/usr/bin/env python3
"""proper-roger: a tiny CW ragchew RX trainer.

Plays realistic fragments of conversational amateur-radio CW so you can
practise *listening*: replay, pause, reveal the text, move on.

Standard library only. Audio goes through the operating system's own player
(afplay on macOS, paplay/aplay/... on Linux, winsound on Windows).

    python3 pr.py --wpm 18 --farnsworth 15 --difficulty 3
"""
from __future__ import annotations

import argparse
import array
import math
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path

__version__ = "0.1.0"

DEFAULT_CONFIG = Path(__file__).resolve().with_name("config.yaml")

BUILTIN_DEFAULTS = {
    "wpm": 18,
    "farnsworth": None,  # None = same as wpm
    "difficulty": 2,
    "mode": "mixed",
    "tone": 650,
    "volume": 0.5,
    "player": None,
}
WPM_MIN, WPM_MAX = 5, 60
TONE_MIN, TONE_MAX = 100, 3000
MODES = ("blocks", "phrases", "mixed")
SAMPLE_RATE = 16000

KEYS_HELP = """\
keys during a session:
  space   pause / resume (replay once the phrase has finished)
  r       replay the phrase from the beginning
  t       reveal / hide the text
  n       next phrase (Enter works too)
  + / -   character speed +/- 1 WPM
  ] / [   Farnsworth effective speed +/- 1 WPM
  q       quit"""


# ----------------------------------------------------------------------------
# Minimal YAML reader (the subset used by config.yaml)
# ----------------------------------------------------------------------------

class YAMLError(ValueError):
    """config.yaml uses something outside the supported YAML subset."""


def load_yaml(text: str):
    """Parse a small YAML subset: block mappings, block lists of scalars,
    flow lists ``[a, b]`` of scalars (which may span several lines), plain /
    'single' / "double" quoted scalars, comments, ``null`` / booleans / ints /
    floats.

    Not supported: anchors, tags, multi-line scalars, flow mappings ``{}``,
    lists of mappings, multiple documents.
    """
    lines = []
    pending = None  # (no, indent, content) of a [flow list] continued on later lines
    for no, raw in enumerate(text.splitlines(), 1):
        if raw.strip() == "---":
            continue
        code = _strip_comment(raw)
        if not code.strip():
            continue
        if pending is not None:
            p_no, p_indent, p_content = pending
            p_content = p_content + " " + code.strip()
            if _bracket_depth(p_content) > 0:
                pending = (p_no, p_indent, p_content)
                continue
            lines.append((p_no, p_indent, p_content))
            pending = None
            continue
        leading = code[: len(code) - len(code.lstrip())]
        if "\t" in leading:
            raise YAMLError(f"line {no}: tabs are not allowed for indentation")
        content = code.strip()
        if _bracket_depth(content) > 0:
            pending = (no, len(leading), content)
            continue
        lines.append((no, len(leading), content))
    if pending is not None:
        raise YAMLError(f"line {pending[0]}: [flow list] is never closed")
    if not lines:
        return {}
    value, idx = _parse_block(lines, 0, lines[0][1])
    if idx != len(lines):
        raise YAMLError(f"line {lines[idx][0]}: unexpected indentation")
    return value


def _scan_quotes(text: str, stop: str, need_space_before: bool = False):
    """Index of the first unquoted ``stop`` character, or -1."""
    in_single = in_double = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_double:
            if ch == "\\":
                i += 1
            elif ch == '"':
                in_double = False
        elif in_single:
            if ch == "'":
                in_single = False
        elif ch == '"':
            in_double = True
        elif ch == "'":
            in_single = True
        elif ch == stop:
            before_ok = not need_space_before or i == 0 or text[i - 1] in " \t"
            after_ok = stop != ":" or i + 1 == len(text) or text[i + 1] in " \t"
            if before_ok and after_ok:
                return i
        i += 1
    return -1


def _bracket_depth(text: str) -> int:
    """Net count of unquoted ``[`` minus ``]`` in ``text``."""
    depth = 0
    in_single = in_double = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_double:
            if ch == "\\":
                i += 1
            elif ch == '"':
                in_double = False
        elif in_single:
            if ch == "'":
                in_single = False
        elif ch == '"':
            in_double = True
        elif ch == "'":
            in_single = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        i += 1
    return depth


def _strip_comment(line: str) -> str:
    i = _scan_quotes(line, "#", need_space_before=True)
    return line if i < 0 else line[:i]


def _is_item(content: str) -> bool:
    return content == "-" or content.startswith("- ")


def _parse_block(lines, idx, indent):
    if _is_item(lines[idx][2]):
        return _parse_sequence(lines, idx, indent)
    return _parse_mapping(lines, idx, indent)


def _parse_sequence(lines, idx, indent):
    items = []
    while idx < len(lines):
        no, ind, content = lines[idx]
        if ind < indent:
            break
        if ind > indent:
            raise YAMLError(f"line {no}: unexpected indentation")
        if not _is_item(content):
            raise YAMLError(f"line {no}: expected a '- item' in this list")
        rest = content[1:].strip()
        idx += 1
        if rest:
            items.append(_parse_inline(rest, no))
        elif idx < len(lines) and lines[idx][1] > indent:
            value, idx = _parse_block(lines, idx, lines[idx][1])
            items.append(value)
        else:
            items.append(None)
    return items, idx


def _parse_mapping(lines, idx, indent):
    result = {}
    while idx < len(lines):
        no, ind, content = lines[idx]
        if ind < indent:
            break
        if ind > indent:
            raise YAMLError(f"line {no}: unexpected indentation")
        if _is_item(content):
            raise YAMLError(f"line {no}: list item where 'key: value' was expected")
        colon = _scan_quotes(content, ":")
        if colon < 0:
            raise YAMLError(f"line {no}: expected 'key: value'")
        key = _parse_scalar(content[:colon].strip(), no)
        rest = content[colon + 1:].strip()
        if key in result:
            raise YAMLError(f"line {no}: duplicate key {key!r}")
        idx += 1
        if rest:
            result[key] = _parse_inline(rest, no)
        elif idx < len(lines) and lines[idx][1] > indent:
            result[key], idx = _parse_block(lines, idx, lines[idx][1])
        else:
            result[key] = None
    return result, idx


def _parse_inline(text: str, no: int):
    if text.startswith("["):
        if not text.endswith("]"):
            raise YAMLError(f"line {no}: a [flow list] must end with ']'")
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part.strip(), no) for part in _split_commas(inner)]
    if text.startswith("{"):
        raise YAMLError(f"line {no}: flow mappings {{...}} are not supported; "
                        "use indented 'key: value' lines")
    return _parse_scalar(text, no)


def _split_commas(text: str):
    parts = []
    while True:
        i = _scan_quotes(text, ",")
        if i < 0:
            parts.append(text)
            return parts
        parts.append(text[:i])
        text = text[i + 1:]


_INT_RE = re.compile(r"^[-+]?\d+$")
_FLOAT_RE = re.compile(r"^[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?$")


def _parse_scalar(text: str, no: int):
    if text in ("", "~", "null", "Null", "NULL"):
        return None
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return _unescape(text[1:-1], no)
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1].replace("''", "'")
    if text[0] in "\"'":
        raise YAMLError(f"line {no}: unterminated quoted string")
    if text in ("true", "True", "TRUE"):
        return True
    if text in ("false", "False", "FALSE"):
        return False
    if _INT_RE.match(text):
        return int(text)
    if _FLOAT_RE.match(text):
        return float(text)
    return text


def _unescape(text: str, no: int) -> str:
    out, i = [], 0
    escapes = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/"}
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 1
            if i >= len(text):
                raise YAMLError(f"line {no}: dangling backslash in string")
            out.append(escapes.get(text[i], "\\" + text[i]))
        else:
            out.append(ch)
        i += 1
    return "".join(out)


# ----------------------------------------------------------------------------
# Morse code and timing
# ----------------------------------------------------------------------------

MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
    "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.",
    ".": ".-.-.-", ",": "--..--", "?": "..--..", "/": "-..-.", "=": "-...-",
    "+": ".-.-.", "-": "-....-", "@": ".--.-.",
}


def unsupported_chars(text: str):
    """Characters of ``text`` (other than spaces) with no Morse code."""
    return sorted({c for c in text if c != " " and c not in MORSE})


@dataclass(frozen=True)
class Timing:
    """Element and gap durations in seconds."""
    wpm: int
    farnsworth: int
    dit: float
    dah: float
    gap_element: float   # between dits/dahs of one character
    gap_char: float      # between characters of one word
    gap_word: float      # between words


def timing_for(wpm: int, farnsworth: int | None = None) -> Timing:
    """Standard PARIS timing: dit = 1.2 / wpm seconds; dah 3 units; gaps 1/3/7.

    With Farnsworth (effective speed < character speed) dits, dahs and the
    gaps inside a character keep the character-speed unit; only the gaps
    between characters and words are stretched so that PARIS + word gap
    takes 60 / farnsworth seconds (ARRL method).
    """
    if farnsworth is None:
        farnsworth = wpm
    if wpm <= 0 or farnsworth <= 0:
        raise ValueError("speeds must be positive")
    if farnsworth > wpm:
        raise ValueError("farnsworth speed must be <= character speed")
    unit = 1.2 / wpm
    if farnsworth == wpm:
        spacing = unit
    else:
        # PARIS = 31 character units + 19 spacing units (4 x 3 + 7).
        spacing = (60.0 / farnsworth - 37.2 / wpm) / 19.0
    return Timing(wpm, farnsworth, unit, 3 * unit, unit, 3 * spacing, 7 * spacing)


def timeline(text: str, t: Timing):
    """``[(tone_on, seconds), ...]`` for ``text``; no leading/trailing silence."""
    out = []
    for wi, word in enumerate(text.upper().split()):
        if wi:
            out.append((False, t.gap_word))
        for ci, ch in enumerate(word):
            if ci:
                out.append((False, t.gap_char))
            code = MORSE.get(ch)
            if code is None:
                raise ValueError(f"character {ch!r} has no Morse code")
            for ei, element in enumerate(code):
                if ei:
                    out.append((False, t.gap_element))
                out.append((True, t.dah if element == "-" else t.dit))
    return out


def duration(text: str, t: Timing) -> float:
    return sum(seconds for _, seconds in timeline(text, t))


# ----------------------------------------------------------------------------
# Audio synthesis (16-bit mono PCM in an array('h'))
# ----------------------------------------------------------------------------

RAMP_SECONDS = 0.005     # attack / release to avoid clicks
LEAD_IN_SECONDS = 0.30   # silence before the first element (player start-up)
TAIL_SECONDS = 0.20


def synthesize(text: str, t: Timing, tone_hz: int, volume: float,
               rate: int = SAMPLE_RATE) -> array.array:
    amp = int(round(32767 * max(0.0, min(1.0, volume))))
    ramp = int(rate * RAMP_SECONDS)
    out = array.array("h")
    out.extend(_silence(int(rate * LEAD_IN_SECONDS)))
    tones = {}
    for on, seconds in timeline(text, t):
        n = int(round(seconds * rate))
        if on:
            if n not in tones:
                tones[n] = _tone(n, tone_hz, amp, rate, ramp)
            out.extend(tones[n])
        else:
            out.extend(_silence(n))
    out.extend(_silence(int(rate * TAIL_SECONDS)))
    return out


def _silence(n: int) -> array.array:
    return array.array("h", bytes(2 * max(0, n)))


def _tone(n: int, freq: float, amp: int, rate: int, ramp: int) -> array.array:
    ramp = min(ramp, n // 2)
    step = 2.0 * math.pi * freq / rate
    buf = array.array("h", bytes(2 * n))
    for i in range(n):
        env = 1.0
        if ramp:
            if i < ramp:
                env = 0.5 - 0.5 * math.cos(math.pi * i / ramp)
            elif i >= n - ramp:
                env = 0.5 - 0.5 * math.cos(math.pi * (n - 1 - i) / ramp)
        buf[i] = int(amp * env * math.sin(step * i))
    return buf


def write_wav(path: str, samples: array.array, rate: int = SAMPLE_RATE) -> None:
    data = samples
    if sys.byteorder == "big":
        data = array.array("h", samples)
        data.byteswap()
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())


# ----------------------------------------------------------------------------
# Playback through the system player
# ----------------------------------------------------------------------------

PLAYER_CANDIDATES = (
    ("afplay", ["afplay"]),                                      # macOS
    ("paplay", ["paplay"]),                                      # PulseAudio / PipeWire
    ("pw-play", ["pw-play"]),                                    # PipeWire
    ("aplay", ["aplay", "-q"]),                                  # ALSA
    ("play", ["play", "-q"]),                                    # SoX
    ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]),
    ("mpv", ["mpv", "--really-quiet", "--no-video"]),
)


def find_player(override: str | None = None):
    """``(name, argv_prefix)`` of the player to use, or ``None`` if none found.

    On Windows the pseudo player ``winsound`` (standard library) is used.
    """
    if override:
        argv = shlex.split(override)
        if not argv or shutil.which(argv[0]) is None:
            raise RuntimeError(f"player command not found: {override}")
        return argv[0], argv
    if sys.platform == "win32":
        return "winsound", []
    for name, argv in PLAYER_CANDIDATES:
        if shutil.which(argv[0]):
            return name, argv
    return None


class Player:
    """Plays an array of samples via the system player, with pause/resume.

    Pause stops the player and remembers the position; resume renders the
    remaining samples to a fresh WAV and plays that. With ``enabled=False``
    nothing is played but the playback clock still runs (for --no-play).
    """

    REWIND_SECONDS = 0.25  # back up a little when resuming

    def __init__(self, rate: int, backend, enabled: bool = True):
        self.rate = rate
        self.backend = backend
        self.enabled = enabled and backend is not None
        self._samples: array.array | None = None
        self._proc: subprocess.Popen | None = None
        self._path: str | None = None
        self._start_sample = 0
        self._started_at: float | None = None
        self._duration = 0.0
        self._paused_at: int | None = None

    @property
    def name(self) -> str:
        if not self.enabled:
            return "none"
        return self.backend[0]

    def load(self, samples: array.array) -> None:
        self.stop()
        self._samples = samples
        self._paused_at = None

    def play(self, start_sample: int = 0) -> None:
        self.stop()
        if self._samples is None:
            return
        start = max(0, min(start_sample, len(self._samples)))
        chunk = self._samples[start:]
        self._start_sample = start
        self._paused_at = None
        self._duration = len(chunk) / self.rate
        self._started_at = time.monotonic()
        if not self.enabled or not chunk:
            return
        path = self._tempfile()
        write_wav(path, chunk, self.rate)
        name, argv = self.backend
        if name == "winsound":
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            self._proc = subprocess.Popen(
                argv + [path], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self) -> None:
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait()
            self._proc = None
        elif self.enabled and self.backend[0] == "winsound" and self._started_at is not None:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        self._started_at = None
        self._paused_at = None

    def state(self) -> str:
        """``'playing'``, ``'paused'`` or ``'done'``."""
        if self._paused_at is not None:
            return "paused"
        if self._started_at is None:
            return "done"
        if self._proc is not None:
            return "playing" if self._proc.poll() is None else "done"
        return "playing" if time.monotonic() - self._started_at < self._duration else "done"

    def position(self) -> int:
        """Approximate current sample index."""
        if self._paused_at is not None:
            return self._paused_at
        if self._started_at is None or self._samples is None:
            return 0
        elapsed = time.monotonic() - self._started_at
        return min(len(self._samples), self._start_sample + int(elapsed * self.rate))

    def pause(self) -> bool:
        if self.state() != "playing":
            return False
        pos = self.position()
        self.stop()
        self._paused_at = max(0, pos - int(self.REWIND_SECONDS * self.rate))
        return True

    def resume(self) -> bool:
        if self._paused_at is None:
            return False
        self.play(self._paused_at)
        return True

    def toggle_pause(self) -> None:
        state = self.state()
        if state == "paused":
            self.resume()
        elif state == "playing":
            self.pause()
        else:
            self.play(0)

    def close(self) -> None:
        self.stop()
        if self._path:
            try:
                os.remove(self._path)
            except OSError:
                pass
            self._path = None

    def _tempfile(self) -> str:
        if self._path is None:
            fd, self._path = tempfile.mkstemp(prefix="cw-ragchew-", suffix=".wav")
            os.close(fd)
        return self._path


# ----------------------------------------------------------------------------
# Keyboard: single keys on a terminal, lines otherwise
# ----------------------------------------------------------------------------

class Keyboard:
    """Reads one key at a time (no Enter needed) on POSIX and Windows
    terminals. When stdin is not a terminal, reads a line per command instead.
    """

    def __init__(self, stream=None):
        self.stream = stream or sys.stdin
        self.raw = False
        self._fd = None
        self._saved = None

    def __enter__(self):
        try:
            is_tty = self.stream.isatty()
        except (AttributeError, ValueError):
            is_tty = False
        if not is_tty:
            return self
        if sys.platform == "win32":
            self.raw = True
            return self
        try:
            import termios
            import tty
            self._fd = self.stream.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
            self.raw = True
        except Exception:  # not a real terminal after all
            self.raw = False
        return self

    def __exit__(self, *exc):
        if self._saved is not None:
            import termios
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
            self._saved = None
        return False

    def read_key(self, timeout: float):
        """A key name (``'r'``, ``'space'``, ``'enter'``, ``'esc'``, ``'eof'``...)
        or ``None`` when nothing was pressed within ``timeout`` seconds."""
        if not self.raw:
            return self._read_line()
        if sys.platform == "win32":
            return self._read_windows(timeout)
        return self._read_posix(timeout)

    def _read_posix(self, timeout: float):
        import select
        ready, _, _ = select.select([self._fd], [], [], timeout)
        if not ready:
            return None
        data = os.read(self._fd, 1)
        if not data:
            return "eof"
        if data == b"\x1b":
            while select.select([self._fd], [], [], 0.02)[0]:
                if not os.read(self._fd, 1):
                    break
            return "esc"
        return _key_name(data.decode("utf-8", "ignore") or "?")

    def _read_windows(self, timeout: float):
        import msvcrt
        deadline = time.monotonic() + timeout
        while True:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):  # function / arrow keys: two codes
                    msvcrt.getwch()
                    return None
                return _key_name(ch)
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.01)

    def _read_line(self):
        line = self.stream.readline()
        if line == "":
            return "eof"
        line = line.strip()
        return "enter" if not line else _key_name(line[0])


def _key_name(ch: str) -> str:
    return {"\r": "enter", "\n": "enter", " ": "space", "\x03": "ctrl-c",
            "\x04": "eof", "\x1b": "esc"}.get(ch, ch.lower())


# ----------------------------------------------------------------------------
# Corpus and phrase generation
# ----------------------------------------------------------------------------

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


def load_corpus(path) -> Corpus:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CorpusError(f"cannot read corpus {path}: {exc.strerror or exc}")
    try:
        data = load_yaml(text)
    except YAMLError as exc:
        raise CorpusError(f"{path}: {exc}")
    return corpus_from_data(data, path)


def corpus_from_data(data, path=None) -> Corpus:
    if not isinstance(data, dict):
        raise CorpusError("corpus: the top level must be a mapping")
    vocab = {}
    for key, values in _section(data, "vocab").items():
        if not isinstance(values, list) or not values:
            raise CorpusError(f"vocab.{key}: expected a non-empty list")
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
    corpus = Corpus(vocab, ranges, blocks, phrases, defaults, path)
    _validate_corpus(corpus)
    return corpus


def _section(data: dict, name: str) -> dict:
    value = data.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CorpusError(f"{name}: expected a mapping")
    return value


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
        result[level] = [" ".join(str(item).split()) for item in items]
    missing = [d for d in range(1, 6) if d not in result]
    if missing:
        raise CorpusError(f"{name}: missing difficulty levels {missing}")
    return result


def _validate_corpus(corpus: Corpus) -> None:
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
    """

    def __init__(self, corpus: Corpus, difficulty: int, mode: str = "mixed",
                 seed: int | None = None):
        if difficulty not in corpus.blocks or difficulty not in corpus.phrases:
            raise ValueError(f"difficulty must be 1-5, got {difficulty}")
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        self.corpus = corpus
        self.difficulty = difficulty
        self.mode = mode
        self.rng = random.Random(seed)
        self._last_template = None

    def next(self) -> str:
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
                picks[key] = self._pick(base, used)
            return picks[key]

        text = " ".join(PLACEHOLDER_RE.sub(replace, template).upper().split())
        bad = unsupported_chars(text)
        if bad:
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


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

class CLIError(Exception):
    """Invalid option value or combination."""


@dataclass
class Settings:
    wpm: int
    farnsworth: int
    difficulty: int
    mode: str
    tone: int
    volume: float
    count: int | None
    show_text: bool
    seed: int | None
    no_play: bool
    config: Path
    player: str | None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pr.py",
        description="CW Ragchew RX: listen to generated conversational CW, "
                    "replay it, reveal the text, move on.",
        epilog=KEYS_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-w", "--wpm", type=int, metavar="N",
                   help="character speed in WPM (default 18)")
    p.add_argument("-f", "--farnsworth", type=int, metavar="N",
                   help="effective speed in WPM, must be <= --wpm (default: same as --wpm)")
    p.add_argument("-d", "--difficulty", type=int, metavar="1-5",
                   help="language difficulty, independent from speed (default 2)")
    p.add_argument("-m", "--mode", choices=MODES,
                   help="blocks = short chunks, phrases = longer sentences (default mixed)")
    p.add_argument("--tone", type=int, metavar="HZ", help="sidetone frequency (default 650)")
    p.add_argument("--volume", type=float, metavar="0-1", help="playback volume (default 0.5)")
    p.add_argument("-c", "--count", type=int, metavar="N",
                   help="number of exercises (default: endless, quit with q)")
    p.add_argument("--show-text", dest="show_text", action="store_true", default=None,
                   help="show the phrase before it plays (read-along practice)")
    p.add_argument("--hide-text", dest="show_text", action="store_false",
                   help="hide the phrase until t is pressed (the default)")
    p.add_argument("--seed", type=int, help="random seed for a reproducible phrase sequence")
    p.add_argument("--no-play", action="store_true", help="do not play audio")
    p.add_argument("--player", metavar="CMD",
                   help="command used to play a WAV file, e.g. "
                        "'ffplay -nodisp -autoexit -loglevel quiet'")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG, metavar="PATH",
                   help="corpus / defaults file (default: config.yaml next to pr.py)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def resolve_settings(args: argparse.Namespace, defaults: dict | None = None) -> Settings:
    """Merge CLI arguments, config.yaml defaults and built-in defaults; validate."""
    cfg = dict(BUILTIN_DEFAULTS)
    for key, value in (defaults or {}).items():
        if key in cfg:
            cfg[key] = value
        else:
            raise CLIError(f"config defaults: unknown setting {key!r}")

    def pick(name, kind, cli_value):
        value = cli_value if cli_value is not None else cfg[name]
        if value is None:
            return None
        if kind is int and (not isinstance(value, int) or isinstance(value, bool)):
            raise CLIError(f"config defaults.{name}: expected an integer, got {value!r}")
        if kind is float and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise CLIError(f"config defaults.{name}: expected a number, got {value!r}")
        if kind is str and not isinstance(value, str):
            raise CLIError(f"config defaults.{name}: expected text, got {value!r}")
        return kind(value)

    wpm = pick("wpm", int, args.wpm)
    farnsworth = pick("farnsworth", int, args.farnsworth)
    if farnsworth is None:
        farnsworth = wpm
    difficulty = pick("difficulty", int, args.difficulty)
    mode = pick("mode", str, args.mode)
    tone = pick("tone", int, args.tone)
    volume = pick("volume", float, args.volume)
    player = pick("player", str, args.player)

    if not WPM_MIN <= wpm <= WPM_MAX:
        raise CLIError(f"--wpm must be between {WPM_MIN} and {WPM_MAX} (got {wpm})")
    if not WPM_MIN <= farnsworth <= WPM_MAX:
        raise CLIError(f"--farnsworth must be between {WPM_MIN} and {WPM_MAX} (got {farnsworth})")
    if farnsworth > wpm:
        raise CLIError(f"--farnsworth {farnsworth} must be <= --wpm {wpm}: Farnsworth is the "
                       "effective speed, characters are always sent at --wpm")
    if not 1 <= difficulty <= 5:
        raise CLIError(f"--difficulty must be between 1 and 5 (got {difficulty})")
    if mode not in MODES:
        raise CLIError(f"--mode must be one of {', '.join(MODES)} (got {mode!r})")
    if not TONE_MIN <= tone <= TONE_MAX:
        raise CLIError(f"--tone must be between {TONE_MIN} and {TONE_MAX} Hz (got {tone})")
    if not 0.0 <= volume <= 1.0:
        raise CLIError(f"--volume must be between 0.0 and 1.0 (got {volume})")
    if args.count is not None and args.count < 1:
        raise CLIError(f"--count must be >= 1 (got {args.count})")

    return Settings(wpm=wpm, farnsworth=farnsworth, difficulty=difficulty, mode=mode,
                    tone=tone, volume=volume, count=args.count,
                    show_text=bool(args.show_text), seed=args.seed, no_play=args.no_play,
                    config=Path(args.config), player=player)


def settings_from_argv(argv, defaults: dict | None = None) -> Settings:
    """Parse ``argv`` without touching the file system (used by tests)."""
    return resolve_settings(build_parser().parse_args(argv), defaults)


# ----------------------------------------------------------------------------
# Terminal session
# ----------------------------------------------------------------------------

def _symbols(out):
    unicode = {"play": "▶", "pause": "⏸", "stop": "■", "dot": "·"}
    ascii_ = {"play": ">", "pause": "||", "stop": "#", "dot": "-"}
    try:
        "".join(unicode.values()).encode(getattr(out, "encoding", None) or "ascii")
        return unicode
    except (UnicodeEncodeError, LookupError):
        return ascii_


class UI:
    """Plain text output with one status line rewritten in place on a TTY."""

    def __init__(self, out=None):
        self.out = out or sys.stdout
        try:
            self.tty = self.out.isatty()
        except (AttributeError, ValueError):
            self.tty = False
        self.sym = _symbols(self.out)
        self._status_open = False

    def line(self, text: str = "") -> None:
        if self._status_open:
            self.out.write("\r\033[K")
            self._status_open = False
        self.out.write(text + "\n")
        self.out.flush()

    def status(self, text: str) -> None:
        if self.tty:
            self.out.write("\r\033[K" + text)
            self._status_open = True
        else:
            self.out.write(text + "\n")
        self.out.flush()

    def close(self) -> None:
        if self._status_open:
            self.out.write("\n")
            self._status_open = False
            self.out.flush()


class Session:
    QUIT_KEYS = ("q", "esc", "eof", "ctrl-c")
    NEXT_KEYS = ("n", "enter")

    def __init__(self, settings: Settings, corpus: Corpus, player: Player, out=None):
        self.settings = settings
        self.corpus = corpus
        self.player = player
        self.ui = UI(out)
        self.generator = Generator(corpus, settings.difficulty, settings.mode, settings.seed)
        self.wpm = settings.wpm
        self.farnsworth = settings.farnsworth
        self.phrase = ""

    # -- output -------------------------------------------------------------

    def header(self) -> str:
        dot = f" {self.ui.sym['dot']} "
        parts = [f"{self.wpm} WPM"]
        if self.farnsworth != self.wpm:
            parts.append(f"Farnsworth {self.farnsworth}")
        parts += [f"Difficulty {self.settings.difficulty}", self.settings.mode.capitalize()]
        if not self.player.enabled:
            parts.append("no audio")
        return dot.join(parts)

    def status_text(self, revealed: bool) -> str:
        state = self.player.state()
        sym = self.ui.sym
        icon = {"playing": f"{sym['play']} playing...", "paused": f"{sym['pause']} paused",
                "done": f"{sym['stop']} done"}[state]
        space = {"playing": "[space] pause", "paused": "[space] resume",
                 "done": "[space] replay"}[state]
        reveal = "[t] hide" if revealed else "[t] reveal"
        return f"{icon:<14} {space}  [r] replay  {reveal}  [n] next  [q] quit"

    # -- playback -----------------------------------------------------------

    def start(self, phrase: str) -> None:
        self.phrase = phrase
        timing = timing_for(self.wpm, self.farnsworth)
        samples = synthesize(phrase, timing, self.settings.tone, self.settings.volume,
                             self.player.rate)
        self.player.load(samples)
        self.player.play(0)

    def change_wpm(self, delta: int) -> None:
        locked = self.farnsworth == self.wpm
        new = max(WPM_MIN, min(WPM_MAX, self.wpm + delta))
        if new == self.wpm:
            return
        self.wpm = new
        self.farnsworth = new if locked else min(self.farnsworth, new)
        self.ui.line(self.header())
        self.start(self.phrase)

    def change_farnsworth(self, delta: int) -> None:
        new = max(WPM_MIN, min(self.wpm, self.farnsworth + delta))
        if new == self.farnsworth:
            return
        self.farnsworth = new
        self.ui.line(self.header())
        self.start(self.phrase)

    # -- main loop ----------------------------------------------------------

    def run(self) -> int:
        ui = self.ui
        ui.line("CW Ragchew RX")
        ui.line(self.header())
        ui.line()
        ui.line(KEYS_HELP.split("\n", 1)[1])
        n = 0
        try:
            with Keyboard() as keyboard:
                while self.settings.count is None or n < self.settings.count:
                    n += 1
                    if not self.exercise(n, keyboard):
                        break
        except KeyboardInterrupt:
            pass
        finally:
            self.player.close()
            ui.line()
            ui.line("73")
            ui.close()
        return 0

    def exercise(self, number: int, keyboard: Keyboard) -> bool:
        """Run one exercise; return False when the user quits."""
        phrase = self.generator.next()
        revealed = self.settings.show_text
        ui = self.ui
        ui.line()
        ui.line(f"Exercise {number}")
        if revealed:
            ui.line(phrase)
        self.start(phrase)
        shown = None
        while True:
            text = self.status_text(revealed)
            if text != shown:
                ui.status(text)
                shown = text
            key = keyboard.read_key(0.1)
            if key is None:
                continue
            if key in self.QUIT_KEYS:
                return False
            if key in self.NEXT_KEYS:
                return True
            if key == "r":
                self.player.play(0)
            elif key == "space":
                self.player.toggle_pause()
            elif key == "t":
                revealed = not revealed
                ui.line(phrase if revealed else "(text hidden)")
            elif key in ("+", "="):
                self.change_wpm(+1)
            elif key == "-":
                self.change_wpm(-1)
            elif key == "]":
                self.change_farnsworth(+1)
            elif key == "[":
                self.change_farnsworth(-1)
            elif key in ("h", "?"):
                ui.line(KEYS_HELP.split("\n", 1)[1])
            shown = None  # redraw the status line after any handled key


def main(argv=None) -> int:
    if sys.platform == "win32":
        os.system("")  # enable ANSI escape processing in the Windows console
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        corpus = load_corpus(args.config)
        settings = resolve_settings(args, corpus.defaults)
        backend = None
        if not settings.no_play:
            backend = find_player(settings.player)
            if backend is None:
                names = ", ".join(name for name, _ in PLAYER_CANDIDATES)
                raise CLIError(f"no audio player found (looked for {names}). Install one, "
                               "pass --player CMD, or run with --no-play.")
    except (CorpusError, CLIError, RuntimeError) as exc:
        print(f"{parser.prog}: error: {exc}", file=sys.stderr)
        return 2
    player = Player(SAMPLE_RATE, backend, enabled=not settings.no_play)
    return Session(settings, corpus, player).run()


if __name__ == "__main__":
    sys.exit(main())
