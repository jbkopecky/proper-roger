"""One practice session: phrases, your requests, your Rogers, your Elo."""
import array
import math
import random
import time
from datetime import datetime

from .limits import WPM_MAX, WPM_MIN
from .audio import _silence
from .player import Player
from .terminal import KEYS_HELP, Keyboard, UI, plural
from .rating import Game, History, Rating, _proper, longest_run, phrase_rating, rank_for
from .corpus import Corpus, Generator
from .settings import Settings
from .transmitter import SIDETONE_GAP_SECONDS, Transmitter
from .meter import METER_FRAME_SECONDS, METER_LAG_SECONDS, meter_bar, meter_track
from .stats import heatmap


QRS_STEP_WPM = 3             # a QRS takes this off the Farnsworth (effective) speed...
QRQ_STEP_WPM = 1             # ...and every Proper Roger after it gives this back

RUN_MILESTONES = (5, 10, 25, 50, 100)


class Session:
    """One practice session: phrases, your requests, your Rogers, your Elo.

    With a ``history``, every phrase you Roger with r is rated and saved;
    without one (--no-history, --no-play), or with --show-text, the session
    is off the record.
    """

    QUIT_KEYS = ("q", "esc", "eof", "ctrl-c")
    NEXT_KEYS = ("r", "n", "enter")

    def __init__(self, settings: Settings, corpus: Corpus, player: Player, out=None,
                 history: History | None = None, now=None):
        self.settings = settings
        self.corpus = corpus
        self.player = player
        self.ui = UI(out)
        self.generator = Generator(corpus, settings.difficulty, settings.mode, settings.seed,
                                   settings.station)
        # Only on a live terminal: off it, every change would print a line.
        self.meter_on = settings.meter and self.ui.tty and player.enabled
        self._track = []
        self.tx = Transmitter(settings, player.rate)
        self.wpm = settings.wpm
        self.farnsworth = settings.farnsworth
        # Farnsworth "off" follows the character speed; "on" keeps its own
        # value. Only [ and ] switch it, so - then + restores the offset.
        self.farnsworth_locked = settings.farnsworth == settings.wpm
        self._fw_wanted = settings.farnsworth  # last value chosen, before clamping
        self.qrs_drop = 0   # WPM a QRS took off the Farnsworth speed, given back by QRQ
        self.phrase = ""
        self._audio = (array.array("h"), [])  # the phrase as the station sends it now
        seed = settings.seed
        self._calls = random.Random(None if seed is None else f"{seed}:sidetone")
        self._now = now or (lambda: datetime.now().astimezone())
        self.history = history
        self.rated = history is not None and not settings.show_text
        self.rating = Rating(corpus.rating)
        for rec in history.records if history else ():
            self.rating.play(rec)
        self.opening = self.rating.value
        self.played: list = []   # this session's phrases, as history records
        self.in_a_row = 0        # Proper Rogers in a row, this session
        self._save_error = None
        self._session_start = time.monotonic()
        self._begin("")

    @property
    def sent_fw(self) -> int:
        """The Farnsworth speed the station sends at: yours, minus any QRS."""
        return max(WPM_MIN, self.farnsworth - self.qrs_drop)

    # -- output -------------------------------------------------------------

    def header(self) -> str:
        ui, st = self.ui, self.settings
        parts = [ui.field("", self.wpm, "WPM")]
        if self.qrs_drop:
            parts.append(ui.field("Farnsworth", self.sent_fw,
                                  f"(QRS, yours is {self.farnsworth})"))
        elif self.farnsworth != self.wpm:
            parts.append(ui.field("Farnsworth", self.farnsworth))
        parts += [ui.field("Difficulty", st.difficulty), ui.field("", st.mode.capitalize())]
        if st.fist:
            parts.append(ui.field("fist", f"{st.fist}%"))
        if st.vary:
            parts.append(ui.field("vary", f"{ui.sym['pm']}{st.vary}"))
        for name, value in (("QSB", st.qsb), ("noise", st.noise), ("QRM", st.qrm)):
            if value:
                parts.append(ui.field(name, f"{value:g}"))
        if st.noise or st.qrm:
            parts.append(ui.field("filter", st.filter, f"Hz {st.shape}"))
        if not self.player.enabled:
            parts.append(ui.paint("no audio", "label"))
        return ui.joined(parts)

    def rating_line(self) -> str:
        ui = self.ui
        if not self.rated:
            why = "--show-text" if self.settings.show_text else (
                "--no-play" if self.settings.no_play else "--no-history")
            return ui.paint(f"off the record ({why}): not rated", "label")
        rank, following, at = rank_for(self.rating.model, self.rating.value)
        parts = [ui.field("Elo", self.rating.label()), ui.paint(rank, "value")]
        if following:
            parts.append(ui.field("", math.ceil(at - self.rating.value), f"to {following}"))
        count = self.rating.count
        parts.append(ui.field("", count, f"{plural(count, 'phrase')} on record")
                     if count else ui.paint("your first rated session", "label"))
        return ui.joined(parts)

    def status_text(self, revealed: bool) -> str:
        state = self.player.state()
        sym = self.ui.sym
        icon = {"playing": f"{sym['play']} playing", "paused": f"{sym['pause']} paused",
                "done": f"{sym['stop']} done"}[state]
        keys = [("r", "roger"),
                ("space", {"playing": "pause", "paused": "resume", "done": "agn"}[state])]
        if state != "done":  # when done, space already asks AGN
            keys.append(("a", "agn"))
        keys += [("s", "qrs"), ("t", "hide" if revealed else "peek"), ("q", "quit")]
        elo = self.ui.field("Elo", self.rating.label()) + "   " if self.rated else ""
        text = f"{icon:<9}   {elo}" + self.ui.keys(keys)
        if self.meter_on:
            text = self.meter_text() + "  " + text
        return text

    def reveal_text(self) -> str:
        if not self.settings.vary:
            return self.phrase
        wpm, farnsworth = self.tx.speeds(self.wpm, self.sent_fw)
        speed = f"{wpm} WPM" if wpm == farnsworth else f"{wpm}/{farnsworth} WPM"
        return f"{self.phrase}   ({speed})"

    # -- playback -----------------------------------------------------------

    def _begin(self, phrase: str) -> None:
        """A new phrase: nothing asked, nothing heard yet."""
        self.phrase = phrase
        self.agn = self.qrs = 0
        self.peek = False
        self._heard = None      # the easiest way this phrase was heard: a record
        self._started = time.monotonic()

    def start(self, phrase: str | None = None) -> None:
        """Play ``phrase`` as a new one, or the current one again (after a
        speed change)."""
        if phrase is not None:
            self._begin(phrase)
        self._audio = self.tx.render(self.phrase, self.wpm, self.sent_fw)
        self._note_heard()
        self.replay()

    def _note_heard(self) -> None:
        """Keep the easiest speeds this phrase was heard at: turning the
        speed down to copy it is free, but it is rated at the slowest."""
        wpm, fw = self.tx.speeds(self.wpm, self.sent_fw)
        st = self.settings
        rec = {"wpm": wpm, "fw": fw, "set_wpm": self.wpm, "set_fw": self.farnsworth,
               "level": st.difficulty, "mode": st.mode,
               "words": len(self.phrase.split()), "fist": st.fist, "vary": st.vary,
               "qsb": st.qsb, "noise": st.noise, "qrm": st.qrm, "filter": st.filter,
               "shape": st.shape}
        model = self.rating.model
        if self._heard is None or phrase_rating(model, rec) < phrase_rating(model, self._heard):
            self._heard = rec

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
        self.ui.line(self.ui.paint(f"{self.ui.sym['send']} {call}", "dim"))
        if not self.settings.sidetone:
            self._play(samples, starts)
            return
        side, side_starts = self.tx.sidetone(call, self.wpm)
        gap = _silence(int(self.player.rate * SIDETONE_GAP_SECONDS))
        offset = len(side) + len(gap)
        self._play(side + gap + samples, side_starts + [offset + i for i in starts],
                   tx_samples=len(side))

    def ask_agn(self) -> None:
        """AGN?: the phrase again, from the top."""
        self.agn += 1
        self._send_then(self._call("agn"), *self._audio)

    def ask_qrs(self) -> None:
        """QRS: the station sends again with more space between characters
        (Farnsworth), the characters themselves still at your --wpm, and
        stays that slow. Each Proper Roger after that brings it back up."""
        self.qrs += 1
        drop = min(self.qrs_drop + QRS_STEP_WPM, self.farnsworth - WPM_MIN)
        if drop > self.qrs_drop:
            self.qrs_drop = drop
            self._audio = self.tx.render(self.phrase, self.wpm, self.sent_fw)
            self._note_heard()
        self._send_then(self._call("qrs"), *self._audio)
        self.ui.line(self.ui.paint(f"  QRS: Farnsworth {self.sent_fw}, back to "
                                   f"{self.farnsworth} as you copy", "dim"))

    def change_wpm(self, delta: int) -> None:
        new = max(WPM_MIN, min(WPM_MAX, self.wpm + delta))
        if new == self.wpm:
            return
        self.wpm = new
        self.farnsworth = new if self.farnsworth_locked else min(self._fw_wanted, new)
        self.ui.line(self.header())
        self.start()

    def change_farnsworth(self, delta: int) -> None:
        """[ and ]: from what you hear now; it ends a QRS."""
        new = max(WPM_MIN, min(self.wpm, self.sent_fw + delta))
        if new == self.sent_fw:
            return
        self.qrs_drop = 0
        self.farnsworth = self._fw_wanted = new
        self.farnsworth_locked = new == self.wpm
        self.ui.line(self.header())
        self.start()

    # -- Roger ----------------------------------------------------------------

    def roger(self, revealed: bool) -> Game | None:
        """You copied it (or gave up): score it, save it, say so."""
        ui = self.ui
        rec = {"t": self._now().isoformat(timespec="seconds"), **self._heard,
               "agn": self.agn, "qrs": self.qrs, "peek": self.peek,
               "secs": round(time.monotonic() - self._started, 1), "text": self.phrase}
        self.played.append(rec)
        proper = _proper(rec)
        self.in_a_row = self.in_a_row + 1 if proper else 0
        qrq = None
        if proper and self.qrs_drop:
            self.qrs_drop = max(0, self.qrs_drop - QRQ_STEP_WPM)
            qrq = (f"back to your Farnsworth {self.farnsworth}" if not self.qrs_drop
                   else f"Farnsworth {self.sent_fw}, {self.qrs_drop} to go")
        if not revealed:
            ui.line(ui.paint(self.reveal_text(), "dim"))  # check your copy
        game = None
        if self.rated:
            game = self.rating.play(rec)
            try:
                self.history.append(rec)
            except OSError as exc:
                if self._save_error is None:
                    self._save_error = f"cannot save to {self.history.path}: {exc.strerror or exc}"
                    ui.line(f"warning: {self._save_error}")
        if not self.settings.show_text:  # reading along is not copying
            ui.line(self.verdict(proper, game))
        for note in self.notes(game):
            ui.line(note)
        if qrq:
            ui.line(ui.paint(f"  {ui.sym['up']} QRQ: {qrq}", "good"))
        return game

    def verdict(self, proper: bool, game: Game | None) -> str:
        ui, sym = self.ui, self.ui.sym
        if self.peek:
            label = ui.paint(f"{sym['bad']} PEEKED", "bad")
        elif proper:
            label = ui.paint(f"{sym['ok']} PROPER ROGER", "good")
        else:
            asks = [f"{n} {what}" if n > 1 else what
                    for what, n in (("AGN", self.agn), ("QRS", self.qrs)) if n]
            label = ui.paint(f"{sym['ok']} ROGER", "ok") + f" after {' + '.join(asks)}"
        if game is None:
            return label
        delta = f"{game.delta:+d}"
        delta = ui.paint(delta, "up" if game.delta > 0 else "down" if game.delta < 0 else "dim")
        return (f"{label}   {delta}  {ui.paint(sym['to'], 'label')} "
                f"{ui.field('Elo', self.rating.label())}   "
                f"{ui.paint(f'phrase {round(game.d)}', 'label')}")

    def notes(self, game: Game | None) -> list:
        """The crunchy bits: runs, personal bests, ranks."""
        if game is None:
            return []
        ui, sym = self.ui, self.ui.sym
        out = []
        if game.proper and self.in_a_row in RUN_MILESTONES:
            out.append(ui.paint(f"  {sym['star']} {self.in_a_row} Proper Rogers in a row", "good"))
        if game.rank_after != game.rank_before:
            ranks = [name for _, name in self.rating.model.ranks]
            if ranks.index(game.rank_after) > ranks.index(game.rank_before):
                out.append(ui.paint(f"  {sym['up']} RANK UP: {game.rank_after.upper()}", "good"))
            else:
                out.append(ui.paint(f"  {sym['down']} back to {game.rank_after}", "dim"))
        if game.new_best:
            out.append(ui.paint(f"  {sym['star']} new best: {round(game.after)}", "good"))
        return out

    def send_roger(self, keyboard: Keyboard) -> bool:
        """Your R R (or R after help) on the sidetone; any key cuts it
        short. False on quit."""
        if not (self.settings.sidetone and self.player.enabled):
            return True
        kind = "roger" if _proper(self.played[-1]) else "ack"
        side, starts = self.tx.sidetone(self._call(kind), self.wpm)
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

    def summary(self) -> list:
        """Two lines for the end of a rated session, else nothing."""
        ui = self.ui
        if not (self.rated and self.played):
            return []
        count = len(self.played)
        proper = sum(1 for rec in self.played if _proper(rec))
        seconds = time.monotonic() - self._session_start
        parts = [ui.field("", count, plural(count, "phrase")),
                 ui.field("", proper, f"{plural(proper, 'Proper Roger')} ({proper / count:.0%})")]
        for key, name in (("agn", "AGN"), ("qrs", "QRS"), ("peek", "peek")):
            n = sum(int(rec[key]) for rec in self.played)
            if n:
                parts.append(ui.field("", n, plural(n, name) if name == "peek" else name))
        best_run = longest_run(self.played)
        if best_run > 1:
            parts.append(ui.field("best run", best_run))
        parts.append(ui.field("", max(1, round(seconds / 60)), "min"))
        change = round(self.rating.value) - round(self.opening)
        style = "up" if change > 0 else "down" if change < 0 else "dim"
        elo = [ui.field("Elo", round(self.opening)) + " " + ui.paint(ui.sym["to"], "label") + " "
               + ui.paint(self.rating.label(), "value") + " " + ui.paint(f"({change:+d})", style),
               ui.paint(rank_for(self.rating.model, self.rating.value)[0], "value")]
        if self.rating.best is not None:
            elo.append(ui.field("best", round(self.rating.best)))
        return [ui.joined(parts), ui.joined(elo)]

    # -- main loop ----------------------------------------------------------

    def key_help(self) -> list:
        """KEYS_HELP with the keys bold and what they do grey."""
        lines = []
        for line in KEYS_HELP.split("\n")[1:]:
            key, what = line[:12], line[12:]
            lines.append(self.ui.paint(key, "value") + self.ui.paint(what, "label")
                         if key.strip() else line[:12] + self.ui.paint(what, "label"))
        return lines

    def run(self) -> int:
        ui = self.ui
        self._session_start = time.monotonic()
        ui.line(ui.paint("CW Ragchew RX", "value"))
        ui.line(self.header())
        ui.line(self.rating_line())
        if self.history is not None and self.history.records:
            ui.line()
            for line in heatmap(self.history.records, ui, self._now().date()):
                ui.line(line)
        ui.line()
        for line in self.key_help():
            ui.line(line)
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
            lines = self.summary()
            ui.line(ui.joined([ui.paint("73", "value")] + lines[:1]))
            for line in lines[1:]:
                ui.line(line)
            ui.close()
        return 0

    def exercise(self, number: int, keyboard: Keyboard) -> bool:
        """Run one exercise; return False when the user quits."""
        phrase = self.generator.next()
        title = self.ui.field("Exercise", number)
        if self.generator.new_station:
            self.tx.new_station()
            if self.settings.station and number > 1:
                title = self.ui.joined([title, self.ui.paint("new station", "label")])
        revealed = self.settings.show_text
        ui = self.ui
        ui.line()
        ui.line(title)
        self.start(phrase)
        if revealed:
            ui.line(self.reveal_text())
        shown = None
        while True:
            text = self.status_text(revealed)
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
            if key in self.NEXT_KEYS:
                self.roger(revealed)
                return self.send_roger(keyboard)
            if key == "a":
                self.ask_agn()
            elif key == "s":
                self.ask_qrs()
            elif key == "space":
                if self.player.state() == "done":
                    self.ask_agn()
                else:
                    self.player.toggle_pause()
            elif key == "t":
                revealed = not revealed
                if revealed:
                    self.peek = True
                ui.line(self.reveal_text() if revealed else "(text hidden)")
            elif key in ("+", "="):
                self.change_wpm(+1)
            elif key == "-":
                self.change_wpm(-1)
            elif key == "]":
                self.change_farnsworth(+1)
            elif key == "[":
                self.change_farnsworth(-1)
            elif key in ("h", "?"):
                for line in self.key_help():
                    ui.line(line)
            shown = None  # redraw the status line after any handled key
