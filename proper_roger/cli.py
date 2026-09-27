"""The entry point: --stats, --export or a session."""
import os
import sys

from .limits import SAMPLE_RATE
from .audio import _silence, open_wav, pcm_bytes
from .player import PLAYER_CANDIDATES, Player, find_player
from .terminal import UI
from .rating import History, default_history_path
from .corpus import Corpus, CorpusError, Generator, load_corpus
from .settings import CLIError, EXPORT_COUNT, EXPORT_GAP_SECONDS, Settings, build_parser, resolve_settings
from .transmitter import Transmitter
from .stats import stats_report
from .session import Session


def export(settings: Settings, corpus: Corpus, out=None) -> int:
    """Write ``--count`` phrases to ``settings.export`` (WAV) and their text
    to the same name with ``.txt``, for listening away from the keyboard."""
    out = out or sys.stdout
    path = settings.export
    text_path = path.with_suffix(".txt")
    count = settings.count or EXPORT_COUNT
    generator = Generator(corpus, settings.difficulty, settings.mode, settings.seed,
                          settings.station)
    tx = Transmitter(settings)
    gap = _silence(int(SAMPLE_RATE * EXPORT_GAP_SECONDS))
    seconds = 0.0
    with open_wav(path) as w, text_path.open("w", encoding="utf-8") as text:
        for n in range(1, count + 1):
            phrase = generator.next()
            if generator.new_station:
                tx.new_station()
                if settings.station and n > 1:
                    text.write("\n")  # a blank line between stations
            samples, _ = tx.render(phrase, settings.wpm, settings.farnsworth)
            w.writeframes(pcm_bytes(samples + gap))
            text.write(f"{n:3}. {phrase}\n")
            seconds += (len(samples) + len(gap)) / SAMPLE_RATE
    minutes, secs = divmod(int(round(seconds)), 60)
    print(f"wrote {count} phrases ({minutes}:{secs:02d}) to {path}", file=out)
    print(f"text: {text_path}", file=out)
    return 0


def show_stats(settings: Settings, corpus: Corpus, out=None, today=None) -> int:
    """--stats: your Elo, streak and progress, from the history."""
    out = out or sys.stdout
    history = History(settings.history or default_history_path()).load()
    ui = UI(out)
    for line in stats_report(history, corpus.rating, ui, today):
        ui.line(line)
    return 0


def main(argv=None) -> int:
    if sys.platform == "win32":
        os.system("")  # enable ANSI escape processing in the Windows console
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        corpus = load_corpus(args.config)
        settings = resolve_settings(args, corpus.defaults)
        if settings.stats:
            return show_stats(settings, corpus)
        if settings.export:
            return export(settings, corpus)
        history = None
        if settings.history is not None and not settings.no_play:
            history = History(settings.history).load()
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
    except OSError as exc:  # --export to a path that cannot be written, unreadable history
        print(f"{parser.prog}: error: {exc.filename}: {exc.strerror or exc}", file=sys.stderr)
        return 2
    if history is not None and history.skipped:
        print(f"warning: {history.skipped} unreadable lines in {history.path} skipped",
              file=sys.stderr)
    player = Player(SAMPLE_RATE, backend, enabled=not settings.no_play)
    return Session(settings, corpus, player, history=history).run()
