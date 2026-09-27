"""Playback through the operating system's own player, with pause and resume."""
import array
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from .audio import LEAD_IN_SECONDS, _silence, write_wav


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

    Pause stops the player and remembers where to restart: the start of the
    word that was playing (or a little before the cut when no word marks are
    known). Resume renders the rest of the samples, after a short silence for
    the player to start, to a fresh WAV and plays that. With
    ``enabled=False`` nothing is played but the playback clock still runs
    (for --no-play).
    """

    REWIND_SECONDS = 0.25  # the clock runs ahead of the audio: back up a little

    def __init__(self, rate: int, backend, enabled: bool = True, clock=time.monotonic):
        self.rate = rate
        self.backend = backend
        self.enabled = enabled and backend is not None
        self._clock = clock
        self._samples: array.array | None = None
        self._word_starts: list = []
        self._proc: subprocess.Popen | None = None
        self._path: str | None = None
        self._start_sample = 0
        self._started_at: float | None = None
        self._duration = 0.0
        self._paused_at: int | None = None
        self._error: str | None = None
        self._error_reported = False

    @property
    def name(self) -> str:
        if not self.enabled:
            return "none"
        return self.backend[0]

    def load(self, samples: array.array, word_starts=()) -> None:
        self.stop()
        self._samples = samples
        self._word_starts = sorted(word_starts)
        self._paused_at = None

    def play(self, start_sample: int = 0) -> None:
        self.stop()
        if self._samples is None:
            return
        start = max(0, min(start_sample, len(self._samples)))
        chunk = self._samples[start:]
        lead = 0
        if start and chunk:
            # Mid-phrase restart: the phrase's own lead-in is gone, add one so
            # the player's start-up latency does not clip the first element.
            lead = int(self.rate * LEAD_IN_SECONDS)
            chunk = _silence(lead) + chunk
        self._start_sample = start - lead
        self._paused_at = None
        self._duration = len(chunk) / self.rate
        self._started_at = self._clock()
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
            else:
                self._check_exit()
            self._proc = None
        elif self.enabled and self.backend[0] == "winsound" and self._started_at is not None:
            import winsound
            winsound.PlaySound(None, 0)  # None stops any sound being played
        self._started_at = None
        self._paused_at = None

    def state(self) -> str:
        """``'playing'``, ``'paused'`` or ``'done'``."""
        if self._paused_at is not None:
            return "paused"
        if self._started_at is None:
            return "done"
        if self._proc is not None:
            if self._proc.poll() is None:
                return "playing"
            self._check_exit()
            return "done"
        return "playing" if self._clock() - self._started_at < self._duration else "done"

    def take_error(self) -> str | None:
        """A message the first time the player exits with an error, else None."""
        if self._error is None or self._error_reported:
            return None
        self._error_reported = True
        return self._error

    def _check_exit(self) -> None:
        """Record a player that ended on its own with a non-zero exit code."""
        code = self._proc.returncode
        if code and self._error is None:
            self._error = (f"audio player {self.backend[0]!r} failed (exit code {code}); "
                           "check your sound setup, or try --player CMD or --no-play")

    def position(self) -> int:
        """Approximate current sample index."""
        if self._paused_at is not None:
            return self._paused_at
        if self._started_at is None or self._samples is None:
            return 0
        elapsed = self._clock() - self._started_at
        pos = self._start_sample + int(round(elapsed * self.rate))
        return max(0, min(len(self._samples), pos))

    def resume_point(self, pos: int) -> int:
        """Where to restart after a pause at sample ``pos``: the start of the
        word being heard, so a resume never begins in the middle of a tone."""
        heard = max(0, pos - int(self.REWIND_SECONDS * self.rate))
        if not self._word_starts:
            return heard
        before = [s for s in self._word_starts if s <= heard]
        return before[-1] if before else 0

    def pause(self) -> bool:
        if self.state() != "playing":
            return False
        pos = self.position()
        self.stop()
        self._paused_at = self.resume_point(pos)
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
