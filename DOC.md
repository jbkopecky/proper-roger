# proper-roger: technical documentation

This document describes how `pr.py` works and why it is built the way it is.
The user-facing guide is [README.md](README.md); the original specification
is the *CW Ragchew RX Trainer* spec this MVP was written from.

## 1. Goals and constraints

- **RX comprehension only.** Play a realistic ragchew fragment, let the
  operator replay, pause, reveal and move on. No scoring, no typing, no QSO
  simulation, no gamification.
- **Ultra portable.** Standard library only. One script (`pr.py`), one data
  file (`config.yaml`). No `pip install`, no virtualenv, no network. It runs
  with any Python 3.8+ on macOS, Linux and Windows.
- **Small.** No terminal UI framework, no plugin system, no generic
  abstractions. Roughly a thousand lines including docstrings.

The stdlib-only constraint drove three design decisions that would otherwise
have been solved by a dependency:

| need              | usual dependency        | what pr.py does instead                          |
|-------------------|-------------------------|--------------------------------------------------|
| YAML corpus       | PyYAML                  | a 150-line reader for the subset config.yaml uses |
| audio synthesis   | numpy                   | `array('h')` + `math.sin`, tones cached per length |
| audio playback    | sounddevice / pyaudio   | WAV to a temp file, played by the OS player        |

## 2. Files

```text
pr.py          the whole program (see section 4 for its internal sections)
config.yaml    corpus (vocabulary, ranges, templates) and CLI defaults
test_pr.py     unittest suite, python3 -m unittest -v
README.md      user guide
DOC.md         this file
```

## 3. Runtime flow

```text
main()
  parse argv                          argparse, build_parser()
  load_corpus(--config)               mini YAML -> Corpus, validated
  resolve_settings(args, defaults)    CLI > config.yaml defaults > built-ins, validated
  find_player(--player)               unless --no-play
  Session(settings, corpus, Player).run()
     print header and key help
     with Keyboard():                 cbreak mode on a TTY
        for each exercise:
           phrase = Generator.next()
           samples = synthesize(phrase, timing_for(wpm, farnsworth), tone, volume)
           Player.load(samples); Player.play(0)
           loop: redraw status line when state changes, read one key, act
```

Exit code is 0 on a normal quit (`q`, Esc, Ctrl-C, EOF) and 2 on an
invalid option or a broken corpus, with the message on stderr.

## 4. Inside pr.py

### 4.1 Mini YAML reader (`load_yaml`)

`config.yaml` is ordinary YAML so any YAML tool can validate or edit it, but
`pr.py` parses it itself. Supported subset:

- block mappings `key: value`, nested by indentation (spaces only; a tab in
  the indentation is an error);
- block lists `- item` whose items are scalars or nested blocks;
- flow lists `[a, b, c]` of scalars, which may continue over several lines
  until the closing `]` (bracket depth is tracked outside quotes);
- scalars: `"double"` (with `\" \\ \n \t` escapes), `'single'` (`''` for a
  quote) and plain. Plain scalars are typed: `null`/`~`/empty gives `None`,
  `true`/`false` booleans, integers, floats, else a string;
- `#` comments, when at line start or preceded by a space, outside quotes;
- a `---` document start line is ignored.

Not supported, and rejected with a line number: flow mappings `{...}`,
anchors and tags, multi-line scalars, lists of mappings, multiple documents,
duplicate keys. Mapping keys are typed like scalars, so `1:` is the integer
`1`; the corpus loader accepts both `1:` and `"1":` for difficulty levels.

### 4.2 Morse table

`MORSE` maps `A-Z`, `0-9` and `. , ? / = + - @` to dit/dah strings.
`unsupported_chars(text)` lists characters of a phrase that have no code;
it is used to validate the corpus at load time and each generated phrase.

### 4.3 Timing and Farnsworth (`timing_for`, `timeline`)

Standard PARIS timing with `unit = 1.2 / wpm` seconds:

```text
dit 1 unit   dah 3 units   gap inside a character 1   between characters 3   between words 7
```

`PARIS` is 31 character units plus 19 spacing units (4 x 3 + 7) = 50, so
one word per `60 / wpm` seconds.

Farnsworth keeps the character unit for dits, dahs and intra-character gaps
and stretches only the inter-character and inter-word gaps. The stretched
spacing unit is chosen so that `PARIS` + word gap lasts `60 / farnsworth`:

```text
spacing_unit = (60 / farnsworth - 37.2 / wpm) / 19
gap_char = 3 * spacing_unit        gap_word = 7 * spacing_unit
```

This is the ARRL method. When `farnsworth == wpm` it reduces exactly to the
standard unit. `timeline(text, timing)` turns a phrase into a list of
`(tone_on, seconds)` segments with no leading or trailing silence;
`duration()` sums it.

### 4.4 Audio synthesis (`synthesize`)

- 16 kHz, 16-bit signed mono, built in an `array('h')`.
- Every tone segment is a sine at `--tone` Hz with a 5 ms raised-cosine
  attack and release to avoid clicks. Tones are cached by sample length, so
  a phrase costs two sine computations (dit and dah) plus concatenation.
- Amplitude is `32767 * volume`; volume is baked into the samples so every
  player backend behaves the same.
- 300 ms of silence is prepended (system players take a moment to start,
  the first dit must not be clipped) and 200 ms appended.
- `write_wav(path, samples, rate)` writes the file with the `wave` module.

On a 10-second phrase this takes a few tens of milliseconds in pure Python.

### 4.5 Playback (`find_player`, `Player`)

Playback goes through whatever the operating system already has:

| platform | candidates, first found wins                                          |
|----------|------------------------------------------------------------------------|
| macOS    | `afplay`                                                               |
| Linux    | `paplay`, `pw-play`, `aplay -q`, `play -q`, `ffplay -nodisp -autoexit -loglevel quiet`, `mpv --really-quiet --no-video` |
| Windows  | `winsound.PlaySound` (standard library, asynchronous)                  |
| any      | `--player CMD` or `defaults.player` in config.yaml                     |

`Player` keeps the current phrase's samples and one temporary WAV file
(`cw-ragchew-*.wav` in the system temp directory), rewritten on every play
and removed in `close()`.

- `play(start_sample)` writes `samples[start:]` to the WAV and launches the
  player as a subprocess with all standard streams detached. It also records
  a monotonic start time.
- `state()` is `playing` while the subprocess is alive, `paused`, or `done`.
  With `--no-play` (or `winsound`, which has no process) the state is derived
  from the clock: `playing` until the chunk's duration has elapsed. This is
  why `--no-play` still shows a realistic playing / done status.
- `pause()` reads the estimated position (start sample + elapsed time),
  terminates the player and remembers `position - 0.25 s`. `resume()` simply
  plays from that sample: the remaining audio is rendered to a fresh WAV.
  The quarter-second rewind compensates for player start-up latency and
  makes the resume land slightly before the cut, never after it.
- `toggle_pause()` implements the space bar: pause when playing, resume when
  paused, replay from the start when done.
- `stop()` terminates the subprocess, waits up to one second, then kills it.

The temp-file approach was preferred to `SIGSTOP`/`SIGCONT` on the player
because it behaves identically on all three platforms and also works with
`winsound`, which cannot be suspended.

### 4.6 Keyboard (`Keyboard`)

- POSIX TTY: `termios` saves the terminal state, `tty.setcbreak` turns off
  line buffering and echo while keeping signal handling (Ctrl-C still raises
  `KeyboardInterrupt`). `select.select` waits for a key with a timeout, so the
  session loop can poll the player state every 100 ms. An escape byte drains
  the rest of the escape sequence and returns `esc`.
- Windows TTY: `msvcrt.kbhit()` / `getwch()` polled every 10 ms until the
  timeout. Two-code function and arrow keys are swallowed.
- Not a TTY (stdin is a pipe or file): one command per line via `readline()`.
  An empty line is `enter` (next), end of input is `eof` (quit). This is what
  makes `yes n | python3 pr.py --no-play --show-text --count 10` a handy
  generator dump, and what the scripted session tests use.

The terminal state is always restored in `__exit__`, including after an
exception.

### 4.7 Corpus model (`load_corpus`, `corpus_from_data`)

```yaml
defaults:  { wpm, farnsworth, difficulty, mode, tone, volume, player }
vocab:     placeholder -> list of strings   (upper-cased, whitespace normalised)
ranges:    placeholder -> [low, high]       (integers, low <= high)
blocks:    difficulty 1..5 -> list of templates
phrases:   difficulty 1..5 -> list of templates
```

Validation happens once at start-up and fails with a precise message:

- every vocabulary value and every literal part of every template must be
  Morse-encodable (`unsupported_chars`), so a stray `'`, `é` or `{` is caught
  before a session starts;
- every `{placeholder}` must exist in `vocab` or `ranges`;
- both `blocks` and `phrases` must define all five difficulty levels with
  at least one template each;
- unknown keys under `defaults:` and wrongly typed defaults are refused.

### 4.8 Phrase generation (`Generator`)

`Generator(corpus, difficulty, mode, seed)` owns a `random.Random(seed)`
instance so a seeded session is reproducible regardless of anything else
that uses the global `random` module.

`next()`:

1. choose the pool: `blocks[d]`, `phrases[d]`, or for `mixed` one of the two
   with probability 0.5;
2. choose a template; if it is the same as the previous one and the pool has
   more than one entry, choose again among the others;
3. `fill()` the template.

`fill()` walks `{placeholder}` occurrences (regex `\{([A-Za-z_]+)(\d*)\}`):

- the first occurrence of `{name}` picks a value; later occurrences of the
  same placeholder in the same template reuse it (`NAME {name} {name}`);
- a digit suffix (`{rig2}`) draws from the same pool but avoids values
  already used by `{rig}`, `{rig3}`... in this template;
- `vocab` entries are chosen with `rng.choice`, `ranges` with `rng.randint`
  (re-drawn a few times if the value was already used by a suffixed twin);
- the result is upper-cased, whitespace-normalised and checked once more
  against the Morse table. It is the exact string that is synthesised and
  revealed.

### 4.9 Settings (`build_parser`, `resolve_settings`)

Resolution order for every option: command line, then `defaults:` in
`config.yaml`, then built-in defaults (`BUILTIN_DEFAULTS`). `--farnsworth`
falls back to the resolved `--wpm`. `--show-text` / `--hide-text` share one
destination; hidden is the default.

Validation ranges: WPM 5-60 for both speeds, `farnsworth <= wpm`,
difficulty 1-5, tone 100-3000 Hz, volume 0.0-1.0, count >= 1. Errors raise
`CLIError`; `main()` prints them as `pr.py: error: ...` and exits 2.
`settings_from_argv(argv, defaults)` exposes the same path to tests without
touching the file system.

### 4.10 Session and UI (`Session`, `UI`)

- The header line is `18 WPM · Farnsworth 15 · Difficulty 3 · Mixed`;
  `Farnsworth` is omitted when it equals the character speed, `no audio` is
  appended under `--no-play`.
- One status line (`▶ playing...`, `⏸ paused`, `■ done` plus the key hints)
  is rewritten in place with `\r` and `ESC[K` on a TTY, and only when its text
  changes. Off a TTY each change is printed as a normal line. Symbols fall
  back to ASCII when the output encoding cannot represent them; on Windows
  `os.system("")` enables ANSI processing in the console.
- `+`/`-` change the character speed by 1 WPM and re-synthesise and replay
  the current phrase at once so the change is heard. If Farnsworth was off
  (equal speeds) it stays off; otherwise the effective speed is clamped to
  stay `<= wpm`. `]`/`[` change the effective speed within `[5, wpm]`.
- `t` toggles the reveal and prints the phrase (or `(text hidden)`) as a
  permanent line, so revealed text stays in the scrollback.
- Quitting (`q`, Esc, Ctrl-C, Ctrl-D, EOF) always goes through `finally`:
  the player is stopped, the temp file removed, the terminal restored.

## 5. config.yaml reference

```yaml
defaults:
  wpm: 18            # 5-60
  farnsworth: null   # null = same as wpm
  difficulty: 2      # 1-5
  mode: mixed        # blocks | phrases | mixed
  tone: 650          # 100-3000 Hz
  volume: 0.5        # 0.0-1.0
  # player: "ffplay -nodisp -autoexit -loglevel quiet"

vocab:
  name: [HANS, PETER]          # {name}
  rig: [K2, KX3]               # {rig}, {rig2}
  ...

ranges:
  temp: [8, 24]                # {temp} -> integer 8..24
  ...

blocks:
  1: ["NAME {name}", ...]
  ...
  5: [...]

phrases:
  1: [...]
  ...
  5: [...]
```

Shipped vocabulary keys: `name`, `qth`, `rig`, `antenna`, `power`, `weather`,
`band`, `rst`, `key`, `job`, `hobby`, `activity`, `when`. Shipped ranges:
`temp`, `temp_low`, `temp_high`, `height`, `years`, `year`, `age`,
`licence_age`. Add a key to `vocab` or `ranges` and it is immediately
available as a placeholder.

Content guidelines from the spec, worth keeping when editing:

- difficulty is linguistic complexity, never speed and never malformed Morse;
- prefer small vocabularies and composable templates over long lists of
  fixed sentences;
- avoid nonsense combinations by construction (`temp_low` next to `COLD`,
  `temp_high` next to `HOT`, `when` only for past moments).

## 6. Tests

```bash
python3 -m unittest -v
```

| area          | what is checked                                                        |
|---------------|------------------------------------------------------------------------|
| YAML subset   | mappings, lists, multi-line flow lists, quotes, comments, typed scalars, error cases, the real config.yaml |
| timing        | 1.2 / WPM unit, dah = 3 dits, PARIS + word gap = 60 / WPM, Farnsworth stretches only gaps and hits the effective speed, invalid speeds |
| audio         | sample count, silent lead-in, envelope starts at zero, peak below `32767 * volume`, WAV header |
| corpus        | unknown placeholder, unbalanced brace, non-Morse character, missing level, bad range, missing file |
| generator     | difficulty selects the right pool in all modes, braces never remain, `{rig}`/`{rig2}` differ, repeated placeholder repeats, seeded determinism, no immediate template repeat, 3000 phrases from the shipped corpus are all Morse-clean |
| CLI           | defaults, config defaults and overrides, Farnsworth fallback, every rejected combination from the spec, show/hide text |
| player        | state machine of the silent player: playing, paused, resumed, done      |
| session       | scripted line-mode sessions: count is honoured, reveal prints the seeded phrase, speed keys update the header, EOF quits |

Real audio and single-key input cannot run under `unittest` without a
terminal and a sound device. They were checked by hand on macOS with
`afplay`, and the raw-key path through a pseudo-terminal:

```bash
(sleep 1; printf 't'; sleep 0.5; printf 'n'; sleep 0.5; printf 'q') \
  | script -q /dev/null python3 pr.py --no-play --seed 3
```

## 7. Portability notes

- Python 3.8+: only `from __future__ import annotations` style hints,
  dataclasses, f-strings. No walrus, no `match`, no `removeprefix`.
- macOS: `afplay` ships with the system; `termios` path.
- Linux: needs one of the listed players; PipeWire desktops usually provide
  `paplay` through pipewire-pulse. Headless machines can use `--no-play`.
- Windows: `winsound` for audio, `msvcrt` for keys, ANSI enabled via
  `os.system("")`. Pause/resume works the same way (render from offset).
- Big-endian hosts: `write_wav` byte-swaps the samples.
- `uv run pr.py` works because there are no dependencies to resolve. A
  `pyproject.toml` with a `cw-ragchew` script entry could be added later if
  the `uv run cw-ragchew` form from the spec is wanted.

## 8. Known limitations

- Pause position is estimated from wall-clock time, not read back from the
  player, so a resume is accurate to a couple of hundred milliseconds. It is
  deliberately biased to resume slightly *before* the cut.
- Audio is rendered per phrase, not streamed; a speed change re-renders and
  restarts the current phrase rather than changing speed mid-phrase.
- Off a terminal (piped stdin) the status line cannot update on its own,
  since input is blocking; it refreshes after each command.
- The YAML reader covers the documented subset only. A corpus that uses
  anchors or lists of mappings must be simplified first.
- No `--category` filter yet; templates are grouped by difficulty only.

## 9. Extending

Ideas that fall out of the current structure with little code:

- `--category station|weather|personal|operating`: add a `categories:`
  mapping of template prefixes or a third nesting level in the corpus, then
  filter the pool in `Generator._pool()`.
- Export a session to WAV: `write_wav()` already exists; concatenate the
  samples of N phrases with a word gap between them.
- `--duration 10m`: stop the session loop when `time.monotonic()` passes the
  deadline instead of counting exercises.
- Mild QSB or QRN: multiply the sample array by a slow envelope or add
  filtered `random.gauss` noise in `synthesize()`; keep it off by default,
  comprehension comes first.
- Region-specific names and QTHs: a second `config-xx.yaml` and `--config`.

Things the spec asks to keep out: sending practice, keyer input, two-way
QSO simulation, pileups, scoring, typing answers, statistics, propagation,
contest logic, browser UI, LLM or network calls.

## 10. Abbreviations used in the corpus

| abbr.   | meaning                     | abbr.   | meaning                          |
|---------|-----------------------------|---------|----------------------------------|
| ABT     | about                       | NR      | near / number                    |
| AGN     | again                       | NW      | now                              |
| ANT     | antenna                     | OM      | old man (fellow ham)             |
| BK      | break, back to you          | OP      | operator                         |
| BURO    | QSL bureau                  | PCT     | percent                          |
| CONDX   | conditions                  | PSE     | please                           |
| CPY     | copy                        | PWR     | power                            |
| CUAGN   | see you again               | QRM     | man-made interference            |
| DR      | dear                        | QRP     | low power                        |
| DX      | long distance               | QRS     | send slower                      |
| ES      | and                         | QRT     | stop transmitting                |
| FB      | fine business, great        | QSB     | fading                           |
| FER     | for                         | QSL     | confirmation                     |
| GA/GE/GM| good afternoon/evening/morning | QTH  | location                         |
| GL      | good luck                   | R       | roger, received                  |
| HI      | laughter                    | RIG     | radio                            |
| HPE     | hope                        | RPRT    | report                           |
| HR      | here                        | RPT     | repeat                           |
| HW      | how                         | RST     | readability strength tone report |
| MNI     | many                        | SIG     | signal                           |
| TNX/TU  | thanks / thank you          | SRI     | sorry                            |
| U/UR    | you / your                  | VY      | very                             |
| WX      | weather                     | XYL     | wife                             |
| YR(S)   | year(s)                     | 73      | best regards                     |
