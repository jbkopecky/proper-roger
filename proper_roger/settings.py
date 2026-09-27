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

@dataclass(frozen=True)
class Option:
    """A setting shared by the command line and config.toml's [defaults]:
    its type, its default and what values it takes."""
    kind: type
    default: object = None
    low: float | None = None     # inclusive limits...
    high: float | None = None
    unit: str = ""               # ...shown in the message, as in "3000 Hz"
    choices: tuple = ()          # or a fixed set of values


OPTIONS = {
    "wpm": Option(int, 25, WPM_MIN, WPM_MAX),       # the sound of each character at speed...
    "farnsworth": Option(int, 18, WPM_MIN, WPM_MAX),  # ...with room between; None = same as wpm
    "difficulty": Option(int, 2, 1, 5),
    "mode": Option(str, "mixed", choices=MODES),
    "tone": Option(int, 600, TONE_MIN, TONE_MAX, " Hz"),
    "volume": Option(float, 0.5, 0.0, 1.0),
    "player": Option(str),
    "station": Option(int, 8, 0),       # phrases from one station before the next, 0 = none
    # Band conditions: a calm, real band by default (--clean turns them off).
    "fist": Option(int, 5, 0, FIST_MAX),     # a discreet human hand
    "vary": Option(int, 1, 0, VARY_MAX),     # each station a little faster or slower, a little off
    "qsb": Option(float, 0.2, 0, 1.0),       # slow, gentle fading
    "noise": Option(float, 0.15, 0, 1.0),    # the soft rush of a quiet band
    "qrm": Option(float, 0.0, 0, 1.0),
    "filter": Option(int, 400, FILTER_MIN, FILTER_MAX, " Hz"),   # receiver CW filter
    "shape": Option(str, "soft", choices=FILTER_SHAPES),
    "sidetone": Option(bool, True),    # hear yourself send AGN?, QRS PSE, R R
    "meter": Option(bool, True),       # a live S-meter on the status line (on a terminal)
    "history": Option(str),            # where your rated phrases go, None = the default place
}
BUILTIN_DEFAULTS = {name: option.default for name, option in OPTIONS.items()}
CLEAN = {"fist": 0, "vary": 0, "qsb": 0.0, "noise": 0.0, "qrm": 0.0}   # --clean
TYPE_NAMES = {int: "an integer", float: "a number", bool: "true or false", str: "text"}


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


def _check(name: str, value, where: str):
    """``value`` for option ``name`` with its type, limits and choices
    checked; ``where`` says where it came from, for the message."""
    option = OPTIONS[name]
    if value is None:
        return None
    kind = option.kind
    ok = isinstance(value, (int, float) if kind is float else kind)
    if not ok or (kind is not bool and isinstance(value, bool)):
        raise CLIError(f"config defaults.{name}: expected {TYPE_NAMES[kind]}, got {value!r}")
    value = kind(value)
    if option.choices and value not in option.choices:
        raise CLIError(f"{where} must be one of {', '.join(option.choices)} (got {value!r})")
    if option.low is not None and option.high is None and value < option.low:
        raise CLIError(f"{where} must be >= {option.low} (got {value})")
    if option.high is not None and not option.low <= value <= option.high:
        raise CLIError(f"{where} must be between {option.low} and {option.high}{option.unit} "
                       f"(got {value})")
    return value


def resolve_settings(args: argparse.Namespace, defaults: dict | None = None) -> Settings:
    """Merge CLI arguments, config.toml defaults and built-in defaults; validate."""
    defaults = defaults or {}
    for key in defaults:
        if key not in OPTIONS:
            raise CLIError(f"config defaults: unknown setting {key!r}")

    def where(name):
        """Where a value came from, for error messages."""
        if getattr(args, name) is None and name in defaults:
            return f"config defaults.{name}"
        return f"--{name}"

    values = {}
    for name, option in OPTIONS.items():
        cli = getattr(args, name)
        cli = str(cli) if isinstance(cli, Path) else cli
        value = cli if cli is not None else defaults.get(name, option.default)
        if args.clean and name in CLEAN:
            value = CLEAN[name]   # before the checks: --clean wins over anything
        values[name] = _check(name, value, where(name))

    wpm, farnsworth = values["wpm"], values["farnsworth"]
    if farnsworth is None or (farnsworth > wpm and args.farnsworth is None
                              and args.wpm is not None):
        # No Farnsworth, or a default one that follows a slower -w given on
        # the command line instead of making it an error.
        values["farnsworth"] = farnsworth = wpm
    if farnsworth > wpm:
        raise CLIError(f"{where('farnsworth')} {farnsworth} must be <= {where('wpm')} {wpm}: "
                       "Farnsworth is the effective speed, characters are always sent at "
                       "the character speed")
    history = values["history"]
    values["history"] = None if args.no_history else (
        Path(history).expanduser() if history else default_history_path())
    if args.export is not None and args.export.suffix.lower() != ".wav":
        raise CLIError(f"--export needs a .wav file name (got {args.export})")
    if args.count is not None and args.count < 1:
        raise CLIError(f"--count must be >= 1 (got {args.count})")

    return Settings(**values, count=args.count, show_text=bool(args.show_text), seed=args.seed,
                    no_play=args.no_play, config=Path(args.config), export=args.export,
                    stats=args.stats)


def settings_from_argv(argv, defaults: dict | None = None) -> Settings:
    """Parse ``argv`` without touching the file system (used by tests)."""
    return resolve_settings(build_parser().parse_args(argv), defaults)
