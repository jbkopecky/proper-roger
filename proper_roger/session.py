"""One practice session: the station sends, you ask or Roger, the scoreboard
keeps count. This module is the sound and the text; the scoring itself is in
scoreboard."""
import array
import math
import random
import time
from datetime import datetime

from .limits import WPM_MAX, WPM_MIN
from .audio import _silence
from .player import Player
from .terminal import KEYS_HELP, Keyboard, UI, plural, visible_len
from .rating import Game, History, Record, rank_for
from .scoreboard import Attempt, Scoreboard
from .corpus import Corpus, Generator
from .drill import WordDrill, WordList
from .settings import OPTIONS, Settings
from .transmitter import SIDETONE_GAP_SECONDS, Transmitter
from .meter import METER_FRAME_SECONDS, METER_LAG_SECONDS, meter_bar, meter_track
from .stats import habit_line


QRS_STEP_WPM = 3             # a QRS takes this off the Farnsworth (effective) speed...
QRQ_STEP_WPM = 1             # ...and every Proper Roger after it gives this back
PHRASE_WIDTH = 32            # the text column of a phrase line
VERDICT_WIDTH = 22           # "+ Roger after AGN+QRS" and friends
HEADER_WIDTH = 80
OFF_SPEED_WPM = 2            # show what a --vary station sent when this far off yours
BAND = ("fist", "vary", "qsb", "noise", "qrm", "filter")   # filter covers its shape too


def phrase_source(corpus: Corpus, settings: Settings, history: History | None = None,
                  graded: bool = True):
    """What the station sends: generated phrases, or the --words drill:
    spaced and kept next to the ``history`` when you grade it, in plain
    order when nobody does (--export)."""
    if settings.words and not graded:
        return WordList(corpus.words)
    if settings.words:
        return WordDrill(corpus.words, history and history.path.with_name("drill.json"))
    return Generator(corpus, settings.difficulty, settings.mode, settings.seed, settings.station)


class Session:
    """One practice session: phrases, your requests, your Rogers, your Elo.

    With a ``history``, every phrase you Roger with r is rated and saved;
    without one (--no-history, --no-play), with --show-text or --words, the
    session is off the record.
    """

    QUIT_KEYS = ("q", "esc", "eof", "ctrl-c")
    NEXT_KEYS = ("r", "n", "enter")

    def __init__(self, settings: Settings, corpus: Corpus, player: Player, out=None,
                 history: History | None = None, now=None):
        self.settings = settings
        self.corpus = corpus
        self.player = player
        self.ui = UI(out)
        self.generator = phrase_source(corpus, settings, history)
        self.tx = Transmitter(settings, player.rate)
        self.board = Scoreboard(corpus.rating, history,
                                rated=not (settings.show_text or settings.words))
        # Speeds. Farnsworth "off" follows the character speed; "on" keeps its
        # own value. Only [ and ] switch it, so - then + restores the offset.
        self.wpm = settings.wpm
        self.farnsworth = settings.farnsworth
        self.farnsworth_locked = settings.farnsworth == settings.wpm
        self._fw_wanted = settings.farnsworth  # last value chosen, before clamping
        self.qrs_drop = 0   # WPM a QRS took off the Farnsworth speed, given back by QRQ
        # The phrase on the air.
        self.attempt = Attempt("")
        self.revealed = settings.show_text
        self.checking = False   # you Rogered: the text is up, copied all of it or not?
        self._audio = (array.array("h"), [])  # the phrase as the station sends it now
        # Only on a live terminal: off it, every change would print a line.
        self.meter_on = settings.meter and self.ui.tty and player.enabled
        self._track = []
        seed = settings.seed
        self._calls = random.Random(None if seed is None else f"{seed}:sidetone")
        self._now = now or (lambda: datetime.now().astimezone())
        self._session_start = time.monotonic()
        self._keys = {
            "a": self.ask_agn, "s": self.ask_qrs, "space": self.space, "t": self.toggle_text,
            "+": lambda: self.change_wpm(+1), "=": lambda: self.change_wpm(+1),
            "-": lambda: self.change_wpm(-1),
            "]": lambda: self.change_farnsworth(+1), "[": lambda: self.change_farnsworth(-1),
            "h": self.show_keys, "?": self.show_keys,
        }

    @property
    def rating(self):
        return self.board.rating

    @property
    def sent_fw(self) -> int:
        """The Farnsworth speed the station sends at: yours, minus any QRS."""
        return max(WPM_MIN, self.farnsworth - self.qrs_drop)

    # -- text ---------------------------------------------------------------

    def title_line(self) -> str:
        """``CW Ragchew RX   Elo 1081 · Novice · 20 to Pencil copier``"""
        ui, rating = self.ui, self.rating
        title = ui.paint("CW Ragchew RX", "value") + "   "
        if self.settings.words:
            return title + ui.paint("word drill, no Elo", "label")
        if not self.board.rated:
            st = self.settings
            why = ("--show-text" if st.show_text
                   else "--no-play" if st.no_play else "--no-history")
            return title + ui.paint(f"off the record ({why})", "label")
        rank, following, at = rank_for(rating.model, rating.value)
        parts = [ui.field("Elo", rating.label()), ui.paint(rank, "value")]
        if following:
            parts.append(ui.field("", math.ceil(at - rating.value), f"to {following}"))
        if rating.provisional:
            parts.append(ui.paint("provisional", "label"))
        return title + ui.joined(parts)

    def setup_line(self) -> str:
        """``25/18 WPM · level 2 · calm band``: only what you changed is
        spelled out. The band goes to a line of its own if it does not fit."""
        ui, st = self.ui, self.settings
        speed = f"{self.wpm}" if self.sent_fw == self.wpm else f"{self.wpm}/{self.sent_fw}"
        unit = f"WPM (QRS, yours is {self.farnsworth})" if self.qrs_drop else "WPM"
        parts = [ui.field("", speed, unit), self.drill_text() if st.words
                 else ui.field("level", st.difficulty)]
        if st.mode != OPTIONS["mode"].default and not st.words:
            parts.append(ui.paint(st.mode, "value"))
        if not self.player.enabled:
            parts.append(ui.paint("no audio", "label"))
        line = ui.joined(parts[:2] + [self.band_text()] + parts[2:])
        if visible_len(line) <= HEADER_WIDTH:
            return line
        return ui.joined(parts) + "\n" + self.band_text()

    def drill_text(self) -> str:
        """``common words · 23/127 seen · 4 due``"""
        ui, drill = self.ui, self.generator
        parts = [ui.paint("common words", "value")]
        if drill.seen:
            parts.append(ui.field("", f"{drill.seen}/{len(drill.words)}", "seen"))
            parts.append(ui.field("", drill.due, "due"))
        return ui.joined(parts)

    def band_text(self) -> str:
        """``calm band`` (the defaults), ``clean band`` (--clean), or the
        calm band and what differs from it."""
        ui, st = self.ui, self.settings
        if not (st.fist or st.vary or st.qsb or st.noise or st.qrm):
            return ui.paint("clean band", "value")
        changed = [name for name in BAND if getattr(st, name) != OPTIONS[name].default]
        if st.shape != OPTIONS["shape"].default and "filter" not in changed:
            changed.append("filter")
        if not (st.noise or st.qrm):   # no noise, no QRM: the filter changes nothing
            changed = [name for name in changed if name != "filter"]
        shape = "" if st.shape == OPTIONS["shape"].default else f" {st.shape}"
        shown = {"fist": lambda: ui.field("fist", f"{st.fist}%"),
                 "vary": lambda: ui.field("vary", f"{ui.sym['pm']}{st.vary}"),
                 "qsb": lambda: ui.field("QSB", f"{st.qsb:g}"),
                 "noise": lambda: ui.field("noise", f"{st.noise:g}"),
                 "qrm": lambda: ui.field("QRM", f"{st.qrm:g}"),
                 "filter": lambda: ui.field("filter", st.filter, f"Hz{shape}")}
        extras = [shown[name]() for name in changed]
        if len(extras) > 2:            # mostly your own band: no need to say "calm"
            return ui.paint(", ", "label").join(extras)
        return ui.paint(", ", "label").join([ui.paint("calm band", "value")] + extras)

    def header(self) -> str:
        """Two short lines: who you are, then how the station is sent."""
        return self.title_line() + "\n" + self.setup_line()

    def status_text(self) -> str:
        """``S7 ▮▮▮▮▮▮▮▯▯▯▯▯  ▶  r roger  a agn  s qrs  t peek  ? help``, or
        once you Roger, the text to check your copy against."""
        ui = self.ui
        state = self.player.state()
        parts = [self.meter_text()] if self.meter_on else []
        parts.append({"playing": ui.sym["play"], "paused": ui.sym["pause"],
                      "done": ui.sym["stop"]}[state])
        if self.checking:
            parts.append(ui.paint(self.reveal_text(), "value"))
            parts.append(ui.keys([("r", "all there"), ("x", "missed some")]))
            return "  ".join(parts)
        if self.qrs_drop:
            parts.append(ui.paint(f"QRS {self.sent_fw}", "ok"))
        if self.revealed:
            parts.append(ui.paint(self.reveal_text(), "value"))
        parts.append(ui.keys([("r", "roger"), ("a", "agn"), ("s", "qrs"),
                              ("t", "hide" if self.revealed else "peek"), ("?", "help")]))
        return "  ".join(parts)

    def reveal_text(self) -> str:
        """The phrase, and the speed the station sent it at when a --vary
        station was clearly off yours."""
        sent = self.tx.speeds(self.wpm, self.sent_fw)
        off = max(abs(sent[0] - self.wpm), abs(sent[1] - self.sent_fw))
        if off < OFF_SPEED_WPM:
            return self.attempt.text
        return f"{self.attempt.text}   ({sent[0]}/{sent[1]} WPM)"

    def key_help(self) -> list:
        """KEYS_HELP with the keys bold and what they do grey."""
        lines = []
        for line in KEYS_HELP.split("\n")[1:]:
            key, what = line[:12], line[12:]
            lines.append(self.ui.paint(key, "value") + self.ui.paint(what, "label")
                         if key.strip() else line[:12] + self.ui.paint(what, "label"))
        return lines

    def verdict(self, game: Game | None, back: int | None = None) -> str:
        """``+ Roger after AGN   +16  1097``, the label padded to one column."""
        ui, sym, attempt = self.ui, self.ui.sym, self.attempt
        if attempt.peek:
            label, style = f"{sym['bad']} peeked", "bad"
        elif attempt.missed:
            label, style = f"{sym['bad']} missed some", "bad"
        elif attempt.proper:
            label, style = f"{sym['ok']} PROPER ROGER", "good"
        else:
            asks = [f"{n} {what}" if n > 1 else what
                    for what, n in (("AGN", attempt.agn), ("QRS", attempt.qrs)) if n]
            label, style = f"{sym['ok']} Roger after {'+'.join(asks)}", "ok"
        text = ui.paint(label, style)
        if back is not None:          # the --words drill: when the word comes back
            pad = " " * max(0, VERDICT_WIDTH - len(label))
            return f"{text}{pad}  {ui.paint(f'again in {back}', 'label')}"
        if game is None:
            return text
        delta = ui.paint(f"{game.delta:+d}".rjust(4),
                         "up" if game.delta > 0 else "down" if game.delta < 0 else "dim")
        pad = " " * max(0, VERDICT_WIDTH - len(label))
        return f"{text}{pad}  {delta}  {ui.paint(self.rating.label(), 'value')}"

    def phrase_line(self, number: int, game: Game | None, back: int | None = None) -> str:
        """`` 1  BK TO U        + Roger after AGN   +16  1097``: the whole
        exercise on one line (two when the phrase is long)."""
        lead = f"{number:>2}  "
        text = self.reveal_text()
        if self.settings.show_text:   # reading along is not copying: no verdict
            return lead + text
        verdict = self.verdict(game, back)
        if len(text) > PHRASE_WIDTH:
            return f"{lead}{text}\n{' ' * (len(lead) + PHRASE_WIDTH + 2)}{verdict}"
        return f"{lead}{text:<{PHRASE_WIDTH}}  {verdict}"

    def notes(self, game: Game | None) -> list:
        """The crunchy bits: runs, personal bests, ranks."""
        if game is None:
            return []
        ui, sym = self.ui, self.ui.sym
        out = []
        if game.proper and self.board.milestone:
            out.append(ui.paint(f"    {sym['star']} {self.board.milestone} Proper Rogers in a row",
                                "good"))
        if game.rank_after != game.rank_before:
            ranks = [name for _, name in self.rating.model.ranks]
            if ranks.index(game.rank_after) > ranks.index(game.rank_before):
                out.append(ui.paint(f"    {sym['up']} RANK UP: {game.rank_after.upper()}", "good"))
            else:
                out.append(ui.paint(f"    {sym['down']} back to {game.rank_after}", "dim"))
        if game.new_best:
            out.append(ui.paint(f"    {sym['star']} new best: {round(game.after)}", "good"))
        return out

    def summary(self) -> str:
        """``73 · 5 phrases, 3 Proper Rogers · 7 min · Elo 1081 → 1118 (+37)``"""
        ui, board, rating = self.ui, self.board, self.rating
        parts = [ui.paint("73", "value")]
        if self.settings.words and board.played:
            count, proper = len(board.played), board.proper
            parts.append(ui.field("", count, plural(count, "word")) + ui.paint(", ", "label")
                         + ui.field("", proper, "at first hearing"))
        elif board.rated and board.played:
            count, proper = len(board.played), board.proper
            parts.append(ui.field("", count, plural(count, "phrase")) + ui.paint(", ", "label")
                         + ui.field("", proper, plural(proper, "Proper Roger")))
            minutes = max(1, round((time.monotonic() - self._session_start) / 60))
            parts.append(ui.field("", minutes, "min"))
            change = round(rating.value) - round(board.opening)
            style = "up" if change > 0 else "down" if change < 0 else "dim"
            parts.append(ui.field("Elo", round(board.opening)) + " "
                         + ui.paint(ui.sym["to"], "label") + " "
                         + ui.paint(rating.label(), "value") + " "
                         + ui.paint(f"({change:+d})", style))
        return ui.joined(parts)

    # -- sound --------------------------------------------------------------

    def start(self, phrase: str | None = None) -> None:
        """Send ``phrase`` as a new one, or the current one again (after a
        speed change)."""
        if phrase is not None:
            self.attempt = Attempt(phrase)
        self._send_phrase()
        self.replay()

    def _send_phrase(self) -> None:
        """Render the phrase as the station sends it now, and note how it was heard."""
        self._audio = self.tx.render(self.attempt.text, self.wpm, self.sent_fw)
        wpm, fw = self.tx.speeds(self.wpm, self.sent_fw)
        st = self.settings
        sent = Record(wpm=wpm, fw=fw, level=st.difficulty, words=len(self.attempt.text.split()),
                      fist=st.fist, vary=st.vary, qsb=st.qsb, noise=st.noise, qrm=st.qrm,
                      filter=st.filter, shape=st.shape, mode=st.mode,
                      set_wpm=self.wpm, set_fw=self.farnsworth)
        self.attempt.hear(sent, self.rating.model)

    def replay(self) -> None:
        """Play the phrase from the top as the station sends it now."""
        self._play(*self._audio)

    def _play(self, samples, starts=(), tx_samples: int = 0) -> None:
        """Load and play one buffer, the first ``tx_samples`` being you."""
        self.player.load(samples, starts)
        self.player.play(0)
        self._track = (meter_track(samples, self.player.rate, self.settings.volume,
                                   self.tx.strength, tx_samples) if self.meter_on else [])

    def meter_text(self) -> str:
        state = self.player.state()
        entry = None
        if state != "done" and self._track:
            lag = 0 if state == "paused" else int(METER_LAG_SECONDS * self.player.rate)
            pos = max(0, self.player.position() - lag)
            frame = pos // max(1, int(self.player.rate * METER_FRAME_SECONDS))
            entry = self._track[min(frame, len(self._track) - 1)]
        return meter_bar(entry, self.ui)

    def _call(self, kind: str) -> str:
        return self._calls.choice(self.corpus.sidetone[kind])

    def _send_then(self, call: str, samples, starts) -> None:
        """Your ``call`` on the sidetone, a pause, then ``samples``: the
        other station comes back."""
        if not self.settings.sidetone:
            self._play(samples, starts)
            return
        side, side_starts = self.tx.sidetone(call, self.wpm)
        gap = _silence(int(self.player.rate * SIDETONE_GAP_SECONDS))
        offset = len(side) + len(gap)
        self._play(side + gap + samples, side_starts + [offset + i for i in starts],
                   tx_samples=len(side))

    # -- keys ---------------------------------------------------------------

    def ask_agn(self) -> None:
        """AGN?: the phrase again, from the top."""
        self.attempt.agn += 1
        self._send_then(self._call("agn"), *self._audio)

    def ask_qrs(self) -> None:
        """QRS: the station sends again with more space between characters
        (Farnsworth), the characters themselves still at your --wpm, and
        stays that slow. Each Proper Roger after that brings it back up."""
        self.attempt.qrs += 1
        drop = min(self.qrs_drop + QRS_STEP_WPM, self.farnsworth - WPM_MIN)
        if drop > self.qrs_drop:
            self.qrs_drop = drop
            self._send_phrase()
        self._send_then(self._call("qrs"), *self._audio)

    def space(self) -> None:
        """Pause / resume; once the phrase is over, AGN?."""
        if self.player.state() == "done":
            self.ask_agn()
        else:
            self.player.toggle_pause()

    def toggle_text(self) -> None:
        """Reveal or hide the phrase on the status line; revealing it is a peek."""
        self.revealed = not self.revealed
        if self.revealed:
            self.attempt.peek = True

    def show_keys(self) -> None:
        for line in self.key_help():
            self.ui.line(line)

    def change_wpm(self, delta: int) -> None:
        new = max(WPM_MIN, min(WPM_MAX, self.wpm + delta))
        if new == self.wpm:
            return
        self.wpm = new
        self.farnsworth = new if self.farnsworth_locked else min(self._fw_wanted, new)
        self.ui.line("    " + self.setup_line())
        self.start()

    def change_farnsworth(self, delta: int) -> None:
        """[ and ]: from what you hear now; it ends a QRS."""
        new = max(WPM_MIN, min(self.wpm, self.sent_fw + delta))
        if new == self.sent_fw:
            return
        self.qrs_drop = 0
        self.farnsworth = self._fw_wanted = new
        self.farnsworth_locked = new == self.wpm
        self.ui.line("    " + self.setup_line())
        self.start()

    def roger(self, number: int = 0) -> Game | None:
        """You copied it (or gave up): score it, write its line, and QRQ
        one step after a Proper Roger."""
        ui = self.ui
        rec = self.attempt.record(self._now().isoformat(timespec="seconds"))
        game = self.board.score(rec)
        back = None
        if self.settings.words and not self.settings.show_text:   # reading along files nothing
            back = self.generator.grade(rec.proper, rec.peek or rec.missed)
        for error in (self.board.take_error(),
                      self.generator.take_error() if self.settings.words else None):
            if error:
                ui.line(f"warning: {error}")
        ui.line(self.phrase_line(number, game, back))
        for note in self.notes(game):
            ui.line(note)
        if rec.proper and self.qrs_drop:
            self.qrs_drop = max(0, self.qrs_drop - QRQ_STEP_WPM)
        return game

    def send_roger(self, keyboard: Keyboard) -> bool:
        """Your R R (or R after help) on the sidetone; any key cuts it
        short. Nothing when you missed some. False on quit."""
        if self.attempt.missed or not (self.settings.sidetone and self.player.enabled):
            return True
        side, starts = self.tx.sidetone(self._call("roger" if self.attempt.proper else "ack"),
                                        self.wpm)
        self._play(side, starts, tx_samples=len(side))
        while self.player.state() == "playing":
            if not keyboard.raw:  # line mode: the next line is the next command
                time.sleep(0.05)
                continue
            key = keyboard.read_key(0.05)
            if key in self.QUIT_KEYS:
                return False
            if key is not None:
                break
        return True

    # -- main loop ----------------------------------------------------------

    def run(self) -> int:
        ui = self.ui
        self._session_start = time.monotonic()
        ui.pin()
        ui.line(self.header())
        history = self.board.history
        if history is not None and history.records:
            ui.line(habit_line(history.records, ui, self._now().date()))
        ui.line()
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
            ui.release()
            ui.line()
            ui.line(self.summary())
            ui.close()
        return 0

    def exercise(self, number: int, keyboard: Keyboard) -> bool:
        """Run one exercise; return False when the user quits."""
        ui = self.ui
        phrase = self.generator.next()
        if self.generator.new_station:
            self.tx.new_station()
            if self.settings.station and number > 1:
                ui.line(ui.paint("    new station", "label"))
        self.revealed = self.settings.show_text
        self.checking = False
        self.start(phrase)
        shown = None
        while True:
            text = self.status_text()
            error = self.player.take_error()
            if error:
                ui.line(f"warning: {error}")
                shown = None
            if text != shown:
                ui.status(text)
                shown = text
            key = keyboard.read_key(0.05 if self.meter_on else 0.1)
            if key is None:
                continue
            if key in self.QUIT_KEYS:
                return False
            if key in self.NEXT_KEYS and not (self.checking or self.attempt.peek
                                                or self.settings.show_text):
                self.checking = True    # first the text, to check your copy against
                shown = None
                continue
            if key in self.NEXT_KEYS or (self.checking and key == "x"):
                self.attempt.missed = key == "x"
                self.roger(number)
                return self.send_roger(keyboard)
            if self.checking:           # nothing else until you say how it went
                continue
            action = self._keys.get(key)
            if action:
                action()
                shown = None  # redraw the status line after any handled key
