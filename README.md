# proper-roger

A tiny command-line **CW ragchew RX trainer**: it plays realistic fragments of
conversational amateur-radio Morse, you listen, replay, reveal the text, and
move on to the next one.

```text
$ python3 pr.py -w 18 -f 15 -d 3

CW Ragchew RX
18 WPM · Farnsworth 15 · Difficulty 3 · Mixed

Exercise 1
▶ playing...   [space] pause  [r] replay  [t] reveal  [n] next  [q] quit
```

Press `t`:

```text
BEEN HAM FER 25 YRS AND USUALLY RUN QRP
■ done         [space] replay  [r] replay  [t] hide  [n] next  [q] quit
```

It is meant for operators who already copy callsigns and short POTA/SOTA
exchanges and want to move from `TU 599 73` to comfortably following
`NAME HANS QTH BERLIN RIG HR K2 PWR 5W WX COLD TODAY BUT SUNNY`.

No scoring, no typing, no QSO simulation, no browser, no network, no account.

## Requirements

- Python 3.8 or newer. **No third-party packages**: `pr.py` uses only the
  standard library, so it runs anywhere Python runs.
- A way to play a WAV file, detected automatically:
  - macOS: `afplay` (built in).
  - Linux: `paplay`, `pw-play`, `aplay`, `play` (SoX), `ffplay` or `mpv`,
    whichever is found first.
  - Windows: the built-in `winsound` module.
  - Anything else: `--player "some-command"` (the WAV path is appended).

## Quick start

```bash
git clone <this repo> && cd proper-roger
python3 pr.py                       # 18 WPM, difficulty 2, mixed, endless
python3 pr.py -w 18 -f 15 -d 3      # 18 WPM characters, 15 WPM effective
python3 pr.py -d 1 -m blocks -w 25  # fast but very predictable chunks
python3 pr.py --count 20 --seed 42  # 20 exercises, reproducible sequence
uv run pr.py -w 20                  # works too, still no dependencies
```

Sessions are endless by default; `q` quits.

## Keys during a session

| key       | action                                                 |
|-----------|--------------------------------------------------------|
| `space`   | pause / resume (replays once the phrase has finished)  |
| `r`       | replay the phrase from the beginning                   |
| `t`       | reveal / hide the text                                 |
| `n`, Enter| next phrase                                            |
| `+` / `-` | character speed +1 / -1 WPM (re-plays the phrase)      |
| `]` / `[` | Farnsworth effective speed +1 / -1 WPM                 |
| `h`, `?`  | show this key list                                     |
| `q`, Esc  | quit                                                   |

No Enter is needed after a key on macOS, Linux and Windows terminals. When
stdin is not a terminal (a pipe), one command per line is read instead.

## Options

```text
-w, --wpm N          character speed in WPM (5-60, default 18)
-f, --farnsworth N   effective speed in WPM, must be <= --wpm (default: same as --wpm)
-d, --difficulty N   language difficulty 1-5, independent from speed (default 2)
-m, --mode MODE      blocks | phrases | mixed (default mixed)
    --tone HZ        sidetone frequency (default 650)
    --volume X       0.0-1.0 (default 0.5)
-c, --count N        stop after N exercises (default: endless)
    --show-text      show the phrase before it plays (read-along practice)
    --hide-text      hide until t is pressed (the default)
    --seed N         reproducible phrase sequence
    --no-play        generate without audio (debugging, tests)
    --player CMD     force the WAV player command
    --config PATH    another corpus / defaults file (default: config.yaml next to pr.py)
```

Invalid combinations are refused with a clear message, for example
`--farnsworth 20 --wpm 15`, `--wpm 0`, `--difficulty 7`, `--volume 2`.

## Difficulty is about language, not speed

You can practise difficulty 4 at 15 WPM or difficulty 1 at 25 WPM.

| level | feel                          | example                                                   |
|-------|-------------------------------|-----------------------------------------------------------|
| 1     | familiar QSO fields           | `NAME HANS HANS`, `UR RST 579`                            |
| 2     | compact QSO sentences         | `RIG HR K2 PWR 5W`, `WX SUNNY TEMP 18C`                   |
| 3     | normal ragchew                | `BEEN HAM FER 25 YRS`, `UR SIG FB WITH SOME QSB`          |
| 4     | natural conversational CW     | `JUST CAME BACK FROM A WALK WITH THE DOG HI`              |
| 5     | messy real-world ragchew      | `BEEN HAM SINCE 1987 BUT ONLY STARTED CW AGAIN LAST YR`   |

Modes: `blocks` are short chunks to be heard as one unit (`TNX FER CALL`),
`phrases` are longer fragments, `mixed` draws from both.

## Farnsworth

`--wpm` is the character speed, `--farnsworth` the effective speed. With
`-w 18 -f 15` every dit and dah is sent at 18 WPM; only the gaps between
characters and words are stretched so that a standard word takes as long as
at 15 WPM (ARRL method). The elements themselves are never slowed down.

## Configuration and corpus

Everything the trainer says comes from `config.yaml`:

- `defaults:` the command-line defaults (`wpm`, `farnsworth`, `difficulty`,
  `mode`, `tone`, `volume`, optional `player`).
- `vocab:` word lists used by placeholders such as `{name}`, `{qth}`, `{rig}`.
- `ranges:` integer ranges such as `temp: [8, 24]` for `{temp}`.
- `blocks:` and `phrases:` templates grouped by difficulty 1 to 5.

Template rules:

- `{name}` picks a value; the same placeholder twice repeats the same value
  (`NAME {name} {name}` gives `NAME HANS HANS`, like real operators do).
- A digit suffix asks for a different value from the same list:
  `HAVE {rig} ES {rig2}`.
- Only letters, digits, spaces and `. , ? / = + - @` are allowed: anything
  without a Morse code is refused at start-up, with the offending template.

The file is standard YAML, read by a small built-in parser, so keep to plain
mappings, `- item` lists, `[a, b]` lists and quoted or plain strings. See
[DOC.md](DOC.md) for the exact subset.

## Tips

- Listen for meaning, not letters. Missing a word is normal; keep listening.
- Replay is one key away, but so is `n`. Move on more often than you replay.
- Start with `-d 1 -m blocks` at a speed slightly above your comfort zone,
  then raise the difficulty before you raise the speed.
- Use `--show-text` for read-along sessions when a level is new.

## Troubleshooting

- **`no audio player found`**: install one of the players above, or pass
  `--player "ffplay -nodisp -autoexit -loglevel quiet"` (any command that
  plays the WAV path given as last argument works).
- **Odd symbols instead of `▶ · ■`**: the terminal encoding cannot show them;
  the trainer falls back to ASCII automatically when it can detect that.
- **Terminal left in a strange state** after a crash: type `reset`.
- **See what the generator produces without listening**:
  `yes n | python3 pr.py --no-play --show-text --count 10 --seed 1 -d 5`

## Tests

```bash
python3 -m unittest -v
```

Covers the YAML subset, Morse timing and Farnsworth, audio synthesis, corpus
validation, seeded phrase generation, CLI validation and a scripted session.

## Documentation

Design notes, the YAML subset, timing maths, playback and keyboard internals:
[DOC.md](DOC.md).

## License

MIT, see [LICENSE](LICENSE).

73
