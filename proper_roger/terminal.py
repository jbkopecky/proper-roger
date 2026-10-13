"""The terminal: single keys in, text, colours and a status line out."""
import os
import re
import shutil
import sys
import time


# Colours: only the 16 standard ANSI colours, so they follow your terminal's
# own scheme. heat0-4 colour the practice heatmap; meter/over/off the
# S-meter segments (up to S9, over S9, unlit); tx the meter while you send.
STYLES = {"good": "1;32", "ok": "33", "bad": "31", "bold": "1", "dim": "2",
          "label": "90", "value": "1",
          "up": "32", "down": "31", "heat0": "90", "heat1": "32", "heat2": "32",
          "heat3": "32", "heat4": "92", "meter": "32", "over": "91", "off": "90",
          "tx": "1;31"}

KEYS_HELP = """\
keys during a session:
  r         Roger: you copied it, next phrase (n and Enter work too)
  a         AGN?: the phrase again
  s         QRS: more space between characters; the station stays slower
            and speeds up again as you copy
  space     pause / resume (AGN? once the phrase has finished)
  t         peek: reveal / hide the text (the phrase then scores nothing)
  + / -     character speed +/- 1 WPM
  ] / [     Farnsworth effective speed +/- 1 WPM
  h / ?     show this key list
  q / Esc   quit"""


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
            # A lone ESC is the Esc key. Arrow and function keys send ESC
            # followed at once by more bytes: swallow the sequence, ignore it.
            sequence = False
            while select.select([self._fd], [], [], 0.02)[0]:
                if not os.read(self._fd, 1):
                    break
                sequence = True
            return None if sequence else "esc"
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


def plural(n: int, word: str) -> str:
    """``word`` for 1, ``words`` otherwise."""
    return word if n == 1 else word + "s"


def _symbols(out):
    unicode = {"play": "▶", "pause": "⏸", "stop": "■", "dot": "·", "pm": "±",
               "ok": "✓", "bad": "✗", "star": "★", "up": "▲", "down": "▼", "to": "→",
               "full": "█", "empty": "░", "blocks": "▁▂▃▄▅▆▇█",
               "heat": ("··", "░░", "▒▒", "▓▓", "██"), "lit": "▮", "unlit": "▯"}
    ascii_ = {"play": ">", "pause": "||", "stop": "#", "dot": "-", "pm": "+/-",
              "ok": "+", "bad": "x", "star": "*", "up": "^", "down": "v", "to": "->",
              "full": "#", "empty": ".", "blocks": "_.-~=+*#",
              "heat": ("..", "--", "==", "##", "@@"), "lit": "|", "unlit": "."}
    try:
        encoding = getattr(out, "encoding", None) or "ascii"
        "".join("".join(v) for v in unicode.values()).encode(encoding)
        return unicode
    except (UnicodeEncodeError, LookupError):
        return ascii_


ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def visible_len(text: str) -> int:
    """Length on screen: colour codes take no room."""
    return len(ANSI_RE.sub("", text))


def clip(text: str, width: int) -> str:
    """``text`` cut to ``width`` visible characters; colour codes do not
    count and are never cut in half."""
    out, seen, pos = [], 0, 0
    for match in ANSI_RE.finditer(text):
        plain = text[pos:match.start()]
        if seen + len(plain) > width:
            out.append(plain[:width - seen])
            return "".join(out) + "\033[0m"
        out.append(plain)
        seen += len(plain)
        out.append(match.group())
        pos = match.end()
    rest = text[pos:]
    if seen + len(rest) > width:
        return "".join(out) + rest[:width - seen] + "\033[0m"
    return "".join(out) + rest


class UI:
    """Plain text output with one status line rewritten in place on a TTY,
    in colour on a TTY unless NO_COLOR is set.

    After ``pin()`` on a TTY, the screen is cleared and the status line
    stays put under the lines written so far (the header); later lines
    scroll in a region below it, which ``release()`` gives back."""

    def __init__(self, out=None):
        self.out = out or sys.stdout
        try:
            self.tty = self.out.isatty()
        except (AttributeError, ValueError):
            self.tty = False
        self.color = self.tty and not os.environ.get("NO_COLOR")
        self.sym = _symbols(self.out)
        self._status_open = False
        self._pinned = False
        self._rows = 0          # rows written since pin(): the header, then the log
        self._status_row = 0    # 0 until the first status line
        self._status_col = 1

    def field(self, label: str, value, unit: str = "") -> str:
        """``label value unit``, the label and unit grey, the value bold:
        ``field("Farnsworth", 18)``, ``field("", 25, "WPM")``."""
        return " ".join(part for part in (self.paint(label, "label"),
                                          self.paint(str(value), "value"),
                                          self.paint(unit, "label")) if part)

    def joined(self, parts) -> str:
        """Parts separated by a grey dot."""
        return self.paint(f" {self.sym['dot']} ", "label").join(parts)

    def keys(self, pairs) -> str:
        """Key hints: the key bold, what it does grey."""
        return "  ".join(f"{self.paint(key, 'value')} {self.paint(what, 'label')}"
                         for key, what in pairs)

    def paint(self, text: str, style: str) -> str:
        code = STYLES[style]
        if not self.color or not text or not code:
            return text
        return f"\033[{code}m{text}\033[0m"

    def pin(self) -> None:
        """Clear the screen; the status line will stay under what comes next."""
        if not self.tty:
            return
        self.out.write("\033[H\033[2J")
        self.out.flush()
        self._pinned, self._rows, self._status_row = True, 0, 0

    def line(self, text: str = "") -> None:
        if self._pinned:
            self._log(text)
            return
        if self._status_open:
            self.out.write("\r\033[K")
            self._status_open = False
        self.out.write(text + "\n")
        self.out.flush()

    def _log(self, text: str) -> None:
        """Pinned: each line on the next free row, the log region scrolling
        once full. Cut to the width, so that a row is a line."""
        columns, height = _size()
        for piece in text.split("\n"):
            row = self._rows + 1
            if self._status_row and row > height:
                self.out.write(f"\033[{height};1H\n\033[K")
            else:
                self.out.write(f"\033[{row};1H\033[K")
            self.out.write(clip(piece, columns - 1))
            self._rows = min(row, height) if self._status_row else row
        if self._status_row:   # the cursor back at the end of the status line
            self.out.write(f"\033[{self._status_row};{self._status_col}H")
        self.out.flush()

    def status(self, text: str) -> None:
        if not self.tty:
            self.out.write(text + "\n")
            self.out.flush()
            return
        # A status line that wraps can no longer be rewritten in place.
        columns, height = _size()
        text = clip(text, columns - 1)
        if self._pinned and not self._status_row:
            if self._rows + 3 > height:   # no room for a log under it
                self.release()
            else:
                self._status_row = self._rows + 1
                self._rows = self._status_row + 1
                self.out.write(f"\033[{self._status_row + 2};{height}r")
        if self._pinned:
            self._status_col = visible_len(text) + 1
            self.out.write(f"\033[{self._status_row};1H\033[K{text}")
        else:
            self.out.write("\r\033[K" + text)
            self._status_open = True
        self.out.flush()

    def release(self) -> None:
        """Back to plain lines: the status line goes, the cursor goes under
        the log."""
        if not self._pinned:
            return
        self._pinned = False
        if self._status_row:
            self.out.write(f"\033[r\033[{self._status_row};1H\033[K")
            self.out.write(f"\033[{self._rows};1H\n")
        self.out.flush()

    def close(self) -> None:
        self.release()
        if self._status_open:
            self.out.write("\n")
            self._status_open = False
            self.out.flush()


def _size():
    size = shutil.get_terminal_size((100, 24))
    return size.columns, size.lines
