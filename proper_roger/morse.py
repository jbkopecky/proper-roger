"""Morse code, prosigns and PARIS / Farnsworth timing."""
import re
from dataclasses import dataclass


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

# A prosign is written <AR>, <KN>, <SK>...: its letters are sent run
# together, without the gap between characters.
PROSIGN_RE = re.compile(r"<([A-Z0-9]+)>")


def unsupported_chars(text: str):
    """Characters of ``text`` (other than spaces) that cannot be sent: no
    Morse code, or a ``<`` / ``>`` that is not part of a ``<PROSIGN>``."""
    rest = PROSIGN_RE.sub(" ", text.upper())
    return sorted({c for c in rest if c != " " and c not in MORSE})


def word_codes(word: str):
    """Dit/dah strings of the characters of one word; a ``<PROSIGN>`` is a
    single character made of its letters' codes."""
    word = word.upper()
    codes = []
    i = 0
    while i < len(word):
        match = PROSIGN_RE.match(word, i)
        if match:
            codes.append("".join(MORSE[c] for c in match.group(1)))
            i = match.end()
            continue
        code = MORSE.get(word[i])
        if code is None:
            raise ValueError(f"character {word[i]!r} has no Morse code")
        codes.append(code)
        i += 1
    return codes


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
    for wi, word in enumerate(text.split()):
        if wi:
            out.append((False, t.gap_word))
        for ci, code in enumerate(word_codes(word)):
            if ci:
                out.append((False, t.gap_char))
            for ei, element in enumerate(code):
                if ei:
                    out.append((False, t.gap_element))
                out.append((True, t.dah if element == "-" else t.dit))
    return out


def duration(text: str, t: Timing) -> float:
    return sum(seconds for _, seconds in timeline(text, t))
