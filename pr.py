#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""proper-roger: a tiny CW ragchew RX trainer.

Plays realistic fragments of conversational amateur-radio CW so you can
practise *listening*: ask for AGN? or QRS, Roger what you copied, watch your
Elo grow. The code lives in the proper_roger package next to this file.

Standard library only (Python 3.11+). Audio goes through the operating
system's own player (afplay on macOS, paplay/aplay/... on Linux, winsound on
Windows).

    python3 pr.py -d 3
"""
import sys

if sys.version_info < (3, 11):
    sys.exit("pr.py needs Python 3.11 or newer (it reads config.toml with tomllib); "
             "try: uv run pr.py")

import os  # noqa: E402  (after the version check)

from proper_roger.cli import main  # noqa: E402

if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # The reader went away (python3 pr.py --stats | head): stop quietly,
        # and keep Python from failing again when it flushes stdout at exit.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(1)
