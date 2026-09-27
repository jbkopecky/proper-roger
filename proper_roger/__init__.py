"""proper-roger: a tiny CW ragchew RX trainer, standard library only.

Modules, in dependency order (each only imports the ones before it):

    limits       speed, tone and filter limits
    morse        Morse code, prosigns, PARIS / Farnsworth timing
    receiver     the receiver's CW filter
    audio        synthesis, band conditions, WAV files
    player       playback through the system player, pause / resume
    terminal     keyboard, text, colours, status line
    rating       the Elo, the history
    corpus       config.toml: templates, vocabulary, sidetone, Elo weights
    settings     command-line options and config defaults
    transmitter  each station's speed, pitch and strength; your sidetone
    meter        the S-meter
    stats        --stats and the practice heatmap
    session      one practice session
    cli          main(): --stats, --export or a session
"""

__version__ = "0.3.0"
