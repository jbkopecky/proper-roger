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
from .terminal import KEYS_HELP, Keyboard, UI, plural
from .rating import Game, History, Record, rank_for
from .scoreboard import Attempt, Scoreboard
from .corpus import Corpus, Generator
from .settings import Settings
from .transmitter import SIDETONE_GAP_SECONDS, Transmitter
from .meter import METER_FRAME_SECONDS, METER_LAG_SECONDS, meter_bar, meter_track
from .stats import heatmap


QRS_STEP_WPM = 3             # a QRS takes this off the Farnsworth (effective) speed...
QRQ_STEP_WPM = 1             # ...and every Proper Roger after it gives this back


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
        self.tx = Transmitter(settings, player.rate)
        self.board = Scoreboard(corpus.rating, history, rated=not settings.show_text)
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

    def speed_line(self) -> str:
        ui, st = self.ui, self.settings
        parts = [ui.field("", self.wpm, "WPM")]
        if self.qrs_drop:
            parts.append(ui.field("Farnsworth", self.sent_fw,
                                  f"(QRS, yours is {self.farnsworth})"))
        elif self.farnsworth != self.wpm:
            parts.append(ui.field("Farnsworth", self.farnsworth))
        parts += [ui.field("Difficulty", st.difficulty), ui.field("", st.mode.capitalize())]
        if not self.player.enabled:
            parts.append(ui.paint("no audio", "label"))
        return ui.joined(parts)

    def band_line(self) -> str:
        ui, st = self.ui, self.settings
        parts = []
        if st.fist:
            parts.append(ui.field("fist", f"{st.fist}%"))
        if st.vary:
            parts.append(ui.field("vary", f"{ui.sym['pm']}{st.vary}"))
        for name, value in (("QSB", st.qsb), ("noise", st.noise), ("QRM", st.qrm)):
            if value:
                parts.append(ui.field(name, f"{value:g}"))
        if st.noise or st.qrm:
            parts.append(ui.field("filter", st.filter, f"Hz {st.shape}"))
        return ui.joined(parts) if parts else ui.paint("perfect keyer, silent band", "label")

    def header(self) -> str:
        """Two short lines: how it is sent, then the band."""
        return self.speed_line() + "\n" + self.band_line()

    def rating_line(self) -> str:
        ui, rating = self.ui, self.rating
        if not self.board.rated:
            why = "--show-text" if self.settings.show_text else (
                "--no-play" if self.settings.no_play else "--no-history")
            return ui.paint(f"off the record ({why}): not rated", "label")
        rank, following, at = rank_for(rating.model, rating.value)
        parts = [ui.field("Elo", rating.label()), ui.paint(rank, "value")]
        if following:
            parts.append(ui.field("", math.ceil(at - rating.value), f"to {following}"))
        parts.append(ui.field("", rating.count, f"{plural(rating.count, 'phrase')} on record")
                     if rating.count else ui.paint("your first rated session", "label"))
        return ui.joined(parts)

    def status_text(self) -> str:
        state = self.player.state()
        sym = self.ui.sym
        icon = {"playing": f"{sym['play']} playing", "paused": f"{sym['pause']} paused",
                "done": f"{sym['stop']} done"}[state]
        keys = [("r", "roger"),
                ("space", {"playing": "pause", "paused": "resume", "done": "agn"}[state])]
        if state != "done":  # when done, space already asks AGN
            keys.append(("a", "agn"))
        keys += [("s", "qrs"), ("t", "hide" if self.revealed else "peek"),
                 ("q", "quit"), ("?", "help")]
        elo = self.ui.field("Elo", self.rating.label()) + "  " if self.board.rated else ""
        text = f"{icon:<9}  {elo}" + self.ui.keys(keys)
        if self.meter_on:
            text = self.meter_text() + "  " + text
        return text

    def reveal_text(self) -> str:
        if not self.settings.vary:
            return self.attempt.text
        wpm, farnsworth = self.tx.speeds(self.wpm, self.sent_fw)
        speed = f"{wpm} WPM" if wpm == farnsworth else f"{wpm}/{farnsworth} WPM"
        return f"{self.attempt.text}   ({speed})"

    def key_help(self) -> list:
        """KEYS_HELP with the keys bold and what they do grey."""
        lines = []
        for line in KEYS_HELP.split("\n")[1:]:
            key, what = line[:12], line[12:]
            lines.append(self.ui.paint(key, "value") + self.ui.paint(what, "label")
                         if key.strip() else line[:12] + self.ui.paint(what, "label"))
        return lines

    def verdict(self, game: Game | None) -> str:
        ui, sym, attempt = self.ui, self.ui.sym, self.attempt
        if attempt.peek:
            label = ui.paint(f"{sym['bad']} PEEKED", "bad")
        elif attempt.proper:
            label = ui.paint(f"{sym['ok']} PROPER ROGER", "good")
        else:
            asks = [f"{n} {what}" if n > 1 else what
                    for what, n in (("AGN", attempt.agn), ("QRS", attempt.qrs)) if n]
            label = ui.paint(f"{sym['ok']} ROGER", "ok") + f" after {' + '.join(asks)}"
        if game is None:
            return label
        delta = ui.paint(f"{game.delta:+d}",
                         "up" if game.delta > 0 else "down" if game.delta < 0 else "dim")
        return (f"{label}   {delta}  {ui.paint(sym['to'], 'label')} "
                f"{ui.field('Elo', self.rating.label())}   "
                f"{ui.paint(f'phrase {round(game.d)}', 'label')}")

    def notes(self, game: Game | None) -> list:
        """The crunchy bits: runs, personal bests, ranks."""
        if game is None:
            return []
        ui, sym = self.ui, self.ui.sym
        out = []
        if game.proper and self.board.milestone:
            out.append(ui.paint(f"  {sym['star']} {self.board.milestone} Proper Rogers in a row",
                                "good"))
        if game.rank_after != game.rank_before:
            ranks = [name for _, name in self.rating.model.ranks]
            if ranks.index(game.rank_after) > ranks.index(game.rank_before):
                out.append(ui.paint(f"  {sym['up']} RANK UP: {game.rank_after.upper()}", "good"))
            else:
                out.append(ui.paint(f"  {sym['down']} back to {game.rank_after}", "dim"))
        if game.new_best:
            out.append(ui.paint(f"  {sym['star']} new best: {round(game.after)}", "good"))
        return out

    def summary(self) -> list:
        """Two lines for the end of a rated session, else nothing."""
        ui, board, rating = self.ui, self.board, self.rating
        if not (board.rated and board.played):
            return []
        count, proper = len(board.played), board.proper
        parts = [ui.field("", count, plural(count, "phrase")),
                 ui.field("", proper, f"{plural(proper, 'Proper Roger')} ({proper / count:.0%})")]
        for what, name in (("agn", "AGN"), ("qrs", "QRS"), ("peek", "peek")):
            n = board.total(what)
            if n:
                parts.append(ui.field("", n, plural(n, name) if what == "peek" else name))
        if board.best_run > 1:
            parts.append(ui.field("best run", board.best_run))
        minutes = max(1, round((time.monotonic() - self._session_start) / 60))
        parts.append(ui.field("", minutes, "min"))
        change = round(rating.value) - round(board.opening)
        style = "up" if change > 0 else "down" if change < 0 else "dim"
        elo = [ui.field("Elo", round(board.opening)) + " " + ui.paint(ui.sym["to"], "label")
               + " " + ui.paint(rating.label(), "value") + " " + ui.paint(f"({change:+d})", style),
               ui.paint(rank_for(rating.model, rating.value)[0], "value")]
        if rating.best is not None:
            elo.append(ui.field("best", round(rating.best)))
        return [ui.joined(parts), ui.joined(elo)]

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
        self.ui.line(self.ui.paint(f"{self.ui.sym['send']} {call}", "dim"))
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
        self.ui.line(self.ui.paint(f"  QRS: Farnsworth {self.sent_fw}, back to "
                                   f"{self.farnsworth} as you copy", "dim"))

    def space(self) -> None:
        """Pause / resume; once the phrase is over, AGN?."""
        if self.player.state() == "done":
            self.ask_agn()
        else:
            self.player.toggle_pause()

    def toggle_text(self) -> None:
        """Reveal or hide the phrase; revealing it is a peek."""
        self.revealed = not self.revealed
        if self.revealed:
            self.attempt.peek = True
        self.ui.line(self.reveal_text() if self.revealed else "(text hidden)")

    def show_keys(self) -> None:
        for line in self.key_help():
            self.ui.line(line)

    def change_wpm(self, delta: int) -> None:
        new = max(WPM_MIN, min(WPM_MAX, self.wpm + delta))
        if new == self.wpm:
            return
        self.wpm = new
        self.farnsworth = new if self.farnsworth_locked else min(self._fw_wanted, new)
        self.ui.line(self.speed_line())
        self.start()

    def change_farnsworth(self, delta: int) -> None:
        """[ and ]: from what you hear now; it ends a QRS."""
        new = max(WPM_MIN, min(self.wpm, self.sent_fw + delta))
        if new == self.sent_fw:
            return
        self.qrs_drop = 0
        self.farnsworth = self._fw_wanted = new
        self.farnsworth_locked = new == self.wpm
        self.ui.line(self.speed_line())
        self.start()

    def roger(self) -> Game | None:
        """You copied it (or gave up): score it, say so, and QRQ after a
        Proper Roger."""
        ui = self.ui
        rec = self.attempt.record(self._now().isoformat(timespec="seconds"))
        game = self.board.score(rec)
        error = self.board.take_error()
        if error:
            ui.line(f"warning: {error}")
        if not self.revealed:
            ui.line(ui.paint(self.reveal_text(), "dim"))  # check your copy
        if not self.settings.show_text:  # reading along is not copying
            ui.line(self.verdict(game))
        for note in self.notes(game):
            ui.line(note)
        if rec.proper and self.qrs_drop:
            self.qrs_drop = max(0, self.qrs_drop - QRQ_STEP_WPM)
            qrq = (f"back to your Farnsworth {self.farnsworth}" if not self.qrs_drop
                   else f"Farnsworth {self.sent_fw}, {self.qrs_drop} to go")
            ui.line(ui.paint(f"  {ui.sym['up']} QRQ: {qrq}", "good"))
        return game

    def send_roger(self, keyboard: Keyboard) -> bool:
        """Your R R (or R after help) on the sidetone; any key cuts it
        short. False on quit."""
        if not (self.settings.sidetone and self.player.enabled):
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
        ui.line(ui.paint("CW Ragchew RX", "value"))
        ui.line(self.header())
        ui.line(self.rating_line())
        history = self.board.history
        if history is not None and history.records:
            ui.line()
            for line in heatmap(history.records, ui, self._now().date()):
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
        ui = self.ui
        phrase = self.generator.next()
        title = ui.field("Exercise", number)
        if self.generator.new_station:
            self.tx.new_station()
            if self.settings.station and number > 1:
                title = ui.joined([title, ui.paint("new station", "label")])
        ui.line()
        ui.line(title)
        self.revealed = self.settings.show_text
        self.start(phrase)
        if self.revealed:
            ui.line(self.reveal_text())
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
            if key in self.NEXT_KEYS:
                self.roger()
                return self.send_roger(keyboard)
            action = self._keys.get(key)
            if action:
                action()
                shown = None  # redraw the status line after any handled key
