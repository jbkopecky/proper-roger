"""Command-line options, config.toml defaults and their validation."""
import argparse
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .limits import FILTER_MAX, FILTER_MIN, FIST_MAX, TONE_MAX, TONE_MIN, VARY_MAX, WPM_MAX, WPM_MIN
from .receiver import FILTER_SHAPES
from .audio import Conditions
from .terminal import KEYS_HELP
from .rating import default_history_path
from .corpus import MODES


DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"  # next to pr.py

BUILTIN_DEFAULTS = {
    "wpm": 25,           # learn the sound of each character at speed...
    "farnsworth": 18,    # ...with room between them; None = same as wpm
    "difficulty": 2,
    "mode": "mixed",
    "tone": 600,
    "volume": 0.5,
    "player": None,
    "station": 8,        # phrases from one station before the next, 0 = none
    # Band conditions: a calm, real band by default (--clean turns them off).
    "fist": 5,           # a discreet human hand
    "vary": 1,           # each station a little faster or slower, a little off
    "qsb": 0.2,          # slow, gentle fading
    "noise": 0.15,       # the soft rush of a quiet band
    "qrm": 0.0,
    "filter": 400,       # receiver CW filter, Hz
    "shape": "soft",     # receiver filter shape: soft | sharp
    "sidetone": True,    # hear yourself send AGN?, QRS PSE, R R on a perfect keyer
    "meter": True,       # a live S-meter on the status line (on a terminal)
    "history": None,     # where your rated phrases go, None = the default place
}

EXPORT_COUNT = 20       # phrases in an --export file without --count
EXPORT_GAP_SECONDS = 3.0


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
    station: int = 8
    fist: int = 0
    vary: int = 0
    qsb: float = 0.0
    noise: float = 0.0
    qrm: float = 0.0
    filter: int = 400
    shape: str = "soft"
    export: Path | None = None
    sidetone: bool = True
    meter: bool = True
    history: Path | None = None   # None: off the record, nothing rated
    stats: bool = False

    @property
    def conditions(self) -> Conditions:
        return Conditions(self.fist, self.qsb, self.noise, self.qrm, self.filter, self.shape)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pr.py",
        description="CW Ragchew RX: listen to generated conversational CW, "
                    "replay it, reveal the text, move on.",
        epilog=KEYS_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-w", "--wpm", type=int, metavar="N",
                   help="character speed in WPM (default 25)")
    p.add_argument("-f", "--farnsworth", type=int, metavar="N",
                   help="effective speed in WPM, <= --wpm (default 18, or --wpm when lower)")
    p.add_argument("-d", "--difficulty", type=int, metavar="1-5",
                   help="language difficulty, independent from speed (default 2)")
    p.add_argument("-m", "--mode", choices=MODES,
                   help="blocks = short chunks, phrases = longer sentences (default mixed)")
    p.add_argument("--tone", type=int, metavar="HZ",
                   help="sidetone / receive pitch (default 600)")
    p.add_argument("--volume", type=float, metavar="0-1", help="playback volume (default 0.5)")
    p.add_argument("-c", "--count", type=int, metavar="N",
                   help="number of exercises (default: endless, quit with q)")
    p.add_argument("--show-text", dest="show_text", action="store_true", default=None,
                   help="show the phrase before it plays (read-along practice)")
    p.add_argument("--hide-text", dest="show_text", action="store_false",
                   help="hide the phrase until t is pressed (the default)")
    p.add_argument("--station", type=int, metavar="N",
                   help="phrases from the same station (name, QTH, rig...) before a new "
                        "one (default 8, 0 = new values every phrase)")
    g = p.add_argument_group("band conditions (default: a calm band, see --clean)")
    g.add_argument("--clean", action="store_true",
                   help="perfect keyer on a silent band: no fist, vary, QSB, noise or QRM")
    g.add_argument("--fist", type=int, metavar="PCT",
                   help=f"human timing variation, %% of each element (0-{FIST_MAX}, default 5)")
    g.add_argument("--vary", type=int, metavar="WPM",
                   help=f"each station sends up to +/- WPM off --wpm, with its own tone "
                        f"(0-{VARY_MAX}, default 1)")
    g.add_argument("--qsb", type=float, metavar="0-1", help="fading depth (default 0.2)")
    g.add_argument("--noise", type=float, metavar="0-1",
                   help="band noise (default 0.15; 0.3 busy, 0.5 noisy)")
    g.add_argument("--qrm", type=float, metavar="0-1",
                   help="level of another station calling CQ nearby (default 0, try 0.3)")
    g.add_argument("--filter", type=int, metavar="HZ",
                   help=f"receiver CW filter bandwidth with --noise or --qrm "
                        f"({FILTER_MIN}-{FILTER_MAX}, default 400; try 250 or 500)")
    g.add_argument("--shape", choices=FILTER_SHAPES,
                   help="receiver filter shape: soft = round, no ringing (default); "
                        "sharp = steep skirts against QRM, but rings")
    r = p.add_argument_group("your rating")
    r.add_argument("--stats", action="store_true",
                   help="show your Elo, streak, progress and what to try next, then exit")
    r.add_argument("--sidetone", dest="sidetone", action="store_true", default=None,
                   help="hear yourself send AGN?, QRS PSE and R R on a perfect keyer (default)")
    r.add_argument("--no-sidetone", dest="sidetone", action="store_false",
                   help="requests are silent, the phrase just plays again")
    r.add_argument("--meter", dest="meter", action="store_true", default=None,
                   help="a live S-meter on the status line (default)")
    r.add_argument("--no-meter", dest="meter", action="store_false", help="no S-meter")
    r.add_argument("--history", type=Path, metavar="FILE",
                   help=f"where rated phrases are kept (default {default_history_path()})")
    r.add_argument("--no-history", action="store_true",
                   help="off the record: nothing rated, nothing saved")
    p.add_argument("--export", type=Path, metavar="FILE.wav",
                   help=f"write --count phrases (default {EXPORT_COUNT}) to a WAV file and "
                        "their text to FILE.txt, then exit; for listening on the go")
    p.add_argument("--seed", type=int, help="random seed for a reproducible phrase sequence")
    p.add_argument("--no-play", action="store_true", help="do not play audio")
    p.add_argument("--player", metavar="CMD",
                   help="command used to play a WAV file, e.g. "
                        "'ffplay -nodisp -autoexit -loglevel quiet'")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG, metavar="PATH",
                   help="corpus / defaults file (default: config.toml next to pr.py)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def resolve_settings(args: argparse.Namespace, defaults: dict | None = None) -> Settings:
    """Merge CLI arguments, config.toml defaults and built-in defaults; validate."""
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
        if kind is bool and not isinstance(value, bool):
            raise CLIError(f"config defaults.{name}: expected true or false, got {value!r}")
        if kind is str and not isinstance(value, str):
            raise CLIError(f"config defaults.{name}: expected text, got {value!r}")
        return kind(value)

    def label(name):
        """Where a value came from, for error messages."""
        if getattr(args, name) is not None:
            return f"--{name}"
        if name in (defaults or {}):
            return f"config defaults.{name}"
        return f"--{name}"

    wpm = pick("wpm", int, args.wpm)
    farnsworth = pick("farnsworth", int, args.farnsworth)
    if farnsworth is None:
        farnsworth = wpm
    elif farnsworth > wpm and args.farnsworth is None and args.wpm is not None:
        # A Farnsworth default from config.toml follows a slower -w given on
        # the command line instead of making it an error.
        farnsworth = wpm
    difficulty = pick("difficulty", int, args.difficulty)
    mode = pick("mode", str, args.mode)
    tone = pick("tone", int, args.tone)
    volume = pick("volume", float, args.volume)
    player = pick("player", str, args.player)
    station = pick("station", int, args.station)
    fist = pick("fist", int, args.fist)
    vary = pick("vary", int, args.vary)
    qsb = pick("qsb", float, args.qsb)
    noise = pick("noise", float, args.noise)
    qrm = pick("qrm", float, args.qrm)
    if args.clean:
        fist = vary = 0
        qsb = noise = qrm = 0.0
    rx_filter_hz = pick("filter", int, args.filter)
    shape = pick("shape", str, args.shape)
    sidetone = pick("sidetone", bool, args.sidetone)
    meter = pick("meter", bool, args.meter)
    history = pick("history", str, None if args.history is None else str(args.history))
    history = None if args.no_history else (
        Path(history).expanduser() if history else default_history_path())

    if not WPM_MIN <= wpm <= WPM_MAX:
        raise CLIError(f"{label('wpm')} must be between {WPM_MIN} and {WPM_MAX} (got {wpm})")
    if not WPM_MIN <= farnsworth <= WPM_MAX:
        raise CLIError(f"{label('farnsworth')} must be between {WPM_MIN} and {WPM_MAX} "
                       f"(got {farnsworth})")
    if farnsworth > wpm:
        raise CLIError(f"{label('farnsworth')} {farnsworth} must be <= {label('wpm')} {wpm}: "
                       "Farnsworth is the effective speed, characters are always sent at "
                       "the character speed")
    if not 1 <= difficulty <= 5:
        raise CLIError(f"{label('difficulty')} must be between 1 and 5 (got {difficulty})")
    if mode not in MODES:
        raise CLIError(f"{label('mode')} must be one of {', '.join(MODES)} (got {mode!r})")
    if not TONE_MIN <= tone <= TONE_MAX:
        raise CLIError(f"{label('tone')} must be between {TONE_MIN} and {TONE_MAX} Hz "
                       f"(got {tone})")
    if not 0.0 <= volume <= 1.0:
        raise CLIError(f"{label('volume')} must be between 0.0 and 1.0 (got {volume})")
    for name, value, high in (("fist", fist, FIST_MAX), ("vary", vary, VARY_MAX),
                              ("qsb", qsb, 1.0), ("noise", noise, 1.0), ("qrm", qrm, 1.0)):
        if not 0 <= value <= high:
            raise CLIError(f"{label(name)} must be between 0 and {high} (got {value})")
    if not FILTER_MIN <= rx_filter_hz <= FILTER_MAX:
        raise CLIError(f"{label('filter')} must be between {FILTER_MIN} and {FILTER_MAX} Hz "
                       f"(got {rx_filter_hz})")
    if shape not in FILTER_SHAPES:
        raise CLIError(f"{label('shape')} must be one of {', '.join(FILTER_SHAPES)} "
                       f"(got {shape!r})")
    if station < 0:
        raise CLIError(f"{label('station')} must be >= 0 (got {station})")
    if args.export is not None and args.export.suffix.lower() != ".wav":
        raise CLIError(f"--export needs a .wav file name (got {args.export})")
    if args.count is not None and args.count < 1:
        raise CLIError(f"--count must be >= 1 (got {args.count})")

    return Settings(wpm=wpm, farnsworth=farnsworth, difficulty=difficulty, mode=mode,
                    tone=tone, volume=volume, count=args.count,
                    show_text=bool(args.show_text), seed=args.seed, no_play=args.no_play,
                    config=Path(args.config), player=player, station=station, fist=fist,
                    vary=vary, qsb=qsb, noise=noise, qrm=qrm, filter=rx_filter_hz,
                    shape=shape, export=args.export, sidetone=sidetone, history=history,
                    stats=args.stats, meter=meter)


def settings_from_argv(argv, defaults: dict | None = None) -> Settings:
    """Parse ``argv`` without touching the file system (used by tests)."""
    return resolve_settings(build_parser().parse_args(argv), defaults)
