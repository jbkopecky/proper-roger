# proper-roger: technical documentation

This document describes how `pr.py` works and why it is built the way it is.
The user-facing guide is [README.md](README.md); the original specification
is the *CW Ragchew RX Trainer* spec this MVP was written from.

## 1. Goals and constraints

- **RX comprehension only.** Play a realistic ragchew fragment, let the
  operator replay, pause, reveal and move on. No scoring, no typing, no QSO
  simulation, no gamification.
- **Ultra portable.** Standard library only. One script (`pr.py`), one data
  file (`config.toml`). No `pip install`, no virtualenv, no network. It runs
  with any Python 3.11+ on macOS, Linux and Windows.
- **Small.** No terminal UI framework, no plugin system, no generic
  abstractions. Roughly 1400 lines including docstrings.
- **Real but peaceful by default.** Out of the box you hear a calm band: a
  discreet human fist (5 %), stations +/- 1 WPM apart, gentle fading (0.2)
  and a soft noise floor (0.15) through a 400 Hz soft filter, at a 600 Hz
  pitch, with no QRM. `--clean` gives the perfect keyer on a silent band.

The stdlib-only constraint drove these design decisions, each of which would
usually be solved by a dependency:

| need              | usual dependency        | what pr.py does instead                          |
|-------------------|-------------------------|--------------------------------------------------|
| corpus file       | PyYAML                  | TOML, read by the standard `tomllib` (3.11+)     |
| audio synthesis   | numpy                   | `array('h')` + `math.sin`, tones cached per length |
| noise, fading     | numpy / scipy           | `random.gauss` through a biquad band-pass, per-sample loops |
| audio playback    | sounddevice / pyaudio   | WAV to a temp file, played by the OS player        |

Version 0.1 had its own 200-line YAML reader. Raising the minimum to Python
3.11 replaced it with `tomllib` and removed a whole class of parsing bugs.

## 2. Files

```text
pr.py          the whole program (see section 4 for its internal sections)
config.toml    corpus (vocabulary, ranges, templates, station) and CLI defaults
test_pr.py     unittest suite, python3 -m unittest -v
README.md      user guide
DOC.md         this file
```

## 3. Runtime flow

```text
main()
  parse argv                          argparse, build_parser()
  load_corpus(--config)               tomllib -> Corpus, validated
  resolve_settings(args, defaults)    CLI > config.toml defaults > built-ins, validated
  --export?  export(settings, corpus) phrases -> one WAV + a .txt, then exit
  find_player(--player)               unless --no-play
  Session(settings, corpus, Player).run()
     print header and key help
     with Keyboard():                 cbreak mode on a TTY
        for each exercise:
           phrase = Generator.next()            new station? -> Transmitter.new_station()
           samples, word_starts = Transmitter.render(phrase, wpm, farnsworth)
                                     -> render(text, timing, tone, volume, conditions, rng)
           Player.load(samples, word_starts); Player.play(0)
           loop: redraw status line when state changes, read one key, act
```

Exit code is 0 on a normal quit (`q`, Esc, Ctrl-C, EOF) or a finished
export, and 2 on an invalid option, a broken corpus or an export file that
cannot be written, with the message on stderr. On Python older than 3.11 the
script stops at once with a message suggesting `uv run pr.py`.

## 4. Inside pr.py

### 4.1 Config file (`tomllib`)

`config.toml` is standard TOML, read with `tomllib.load()`. TOML fits the
corpus well: strings are always quoted (no `NO` turning into `false`, no
`O'BRIEN` quoting puzzles), lists may span lines with a trailing comma, and
syntax errors come with a line and column. Difficulty levels are written as
bare keys `1 = [...]` (TOML keys are strings; the loader converts them).

TOML has no `null`, so an absent key means "use the built-in default". This
is how `farnsworth` is turned off. A `--config` ending in `.yaml`/`.yml` is
refused with a hint, for users upgrading from 0.1.

### 4.2 Morse table and prosigns

`MORSE` maps `A-Z`, `0-9` and `. , ? / = + - @` to dit/dah strings. `=` is
BT and `+` is AR.

A prosign is written in angle brackets, `<KN>`, `<SK>`, `<AR>`, `<BK>`...
(regex `<([A-Z0-9]+)>`). `word_codes(word)` turns a word into one code per
character, and a prosign becomes a *single* character: the codes of its
letters concatenated, so they are sent with only element gaps between them
(`<KN>` is `-.--.`, not `-.-` + `-.`). `<AR>` therefore sounds exactly like `+`.

`unsupported_chars(text)` removes well-formed prosigns first, then lists
what has no code, including a stray `<` or `>`. It validates the corpus at
load time and, as a last safety net, each generated phrase.

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

### 4.4 Audio synthesis (`render`, `synthesize`)

- 16 kHz, 16-bit signed mono, built in an `array('h')`.
- Every tone segment is a sine at `--tone` Hz with a 5 ms raised-cosine
  attack and release to avoid clicks. Tones are cached by sample length, so
  a phrase costs two sine computations (dit and dah) plus concatenation.
- Amplitude is `32767 * volume`; volume is baked into the samples so every
  player backend behaves the same.
- 300 ms of silence is prepended (system players take a moment to start,
  the first dit must not be clipped) and 200 ms appended.
- `render()` also returns the sample index where each word starts (its
  first tone), used by pause/resume; `synthesize()` returns the samples only.
- `write_wav(path, samples, rate)` writes the file with the `wave` module.

On a 10-second phrase this takes a few milliseconds in pure Python.

### 4.4b Band conditions (`Conditions`)

`render(..., conditions, rng)` can make the CW sound like it came off the
air. Everything is drawn from `rng`, so a seeded session sounds the same
every time. With `Conditions()` (all zero) the output is bit-identical to
the clean rendering.

| knob    | what it does                                                             |
|---------|--------------------------------------------------------------------------|
| `fist`  | every element and gap is multiplied by `gauss(1, fist/100)`, floored at 0.5: a human hand, not a keyer. Word starts are recorded after jitter, so pause/resume still land on words |
| `qsb`   | the wanted signal (only) is multiplied by a slow raised-cosine envelope, 0.15-0.5 Hz with a random phase, between 1 and `1 - qsb` |
| `qrm`   | another station, `CQ CQ CQ DE <call> <call> K` with a random plausible call, is rendered by `render()` itself 80-600 Hz off the receive pitch and +/- 6 WPM, joins up to 1 s late, repeats, and is mixed in at `qrm` times the signal level *before* the receiver filter |
| `noise` | white `random.gauss` noise, dense enough that a 500 Hz filter would pass `noise x NOISE_GAIN` times the signal RMS. `NOISE_GAIN = 0.6` (-4.4 dB) was tuned by ear so that 0.2-0.4 sounds like a quiet-to-busy band. The noise goes through the receiver filter like everything else, so a 250 Hz filter really is 3 dB quieter than a 500 Hz one |
| `filter_hz`, `filter_shape` | the receiver's CW filter (see below), applied whenever `noise` or `qrm` is on |

#### The receiver

The model is a receiver tuned so that a zero-beat station sounds at
`--tone`: that pitch is the centre of its CW filter (`rx_hz`). With `--vary`
a station is up to 60 Hz off, as when you are not exactly on frequency. The
wanted signal, the QRM and the noise are mixed, then **all** go through the
filter, as in a real receiver.

The filter (`rx_filter`) is what makes the noise sound right. A plain audio
band-pass biquad is symmetric on a log scale and its skirts fall only
6 dB/octave, so it lets through a wide, harsh hiss. A receiver's crystal or
DSP CW filter is symmetric in Hz around the pitch. `rx_filter` does the same
thing a receiver does:

1. shift the audio down so the pitch sits at 0 Hz: multiply by a rotating
   complex phasor, which gives I/Q samples;
2. low-pass the I/Q samples, cut at half the bandwidth, with one of two
   shapes (below);
3. shift back up and take twice the real part.

Both shapes are exactly -3 dB at `pitch +/- bandwidth/2`:

| shape | low-pass | at +400 Hz (400 Hz filter) | at 3 kHz | overshoot | ringing |
|-------|----------|----------------------------|----------|-----------|---------|
| `soft` (default) | 6 identical one-pole sections (`y += a (x - y)`), close to a Gaussian; `a` by bisection | -10 dB | -73 dB | 0.3 % | none |
| `sharp` | 8th-order Butterworth, four RBJ biquads with the Butterworth Qs | -48 dB | -176 dB | 16 % | a tail of a few % for 10 ms or more |

The first version shipped only `sharp`. It measures well, but by ear the
ringing sounds like a reverb on every element and a hollow, "yogurt pot"
noise, which is tiring over a session. Real poles only (soft) mean no
overshoot and no tail: the round, flat sound of a rig's SOFT filter setting.
`sharp` stays available for crowded bands, where cutting a nearby station
matters more than comfort. `soft` is also about 30 % faster.
`filter_gain(center, bandwidth, freq, shape=...)` gives the response for
tests.

The mix is done in floats and clipped to 16 bits. With everything on, a
30-second phrase takes about 0.8 s to render (a typical 5-10 s phrase takes
0.1-0.3 s). That is acceptable when a phrase starts, and it is why
conditions are applied once per phrase rather than while playing.

### 4.4c Per-station sending (`Transmitter`)

`Transmitter` sits between the session and `render()`. It holds its own
`random.Random` (seeded from `--seed`, separate from the phrase generator so
turning conditions on does not change the phrases), and for `--vary N` a
per-station offset: up to +/- N WPM on both speeds (clamped to 5-60 and
`farnsworth <= wpm`) and up to +/- 60 Hz on the tone. `new_station()`
redraws the offsets. `render(phrase, wpm, farnsworth, slow=False)` applies
them. `slow=True` also lowers the effective speed to two thirds, for the `s`
key.

### 4.5 Playback (`find_player`, `Player`)

Playback goes through whatever the operating system already has:

| platform | candidates, first found wins                                          |
|----------|------------------------------------------------------------------------|
| macOS    | `afplay`                                                               |
| Linux    | `paplay`, `pw-play`, `aplay -q`, `play -q`, `ffplay -nodisp -autoexit -loglevel quiet`, `mpv --really-quiet --no-video` |
| Windows  | `winsound.PlaySound` (standard library, asynchronous)                  |
| any      | `--player CMD` or `player` under `[defaults]` in config.toml           |

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
  backs up 0.25 s (the clock starts before the player produces sound), and
  snaps back to the start of that word. `render()` returns the sample index
  of every word start for this. `resume()` plays from there: 300 ms of
  silence, then the rest of the samples, written to a fresh WAV. So a
  resume always begins on a tone's ramp, with no click, a complete word and
  no clipping from the player's start-up.
- A player that exits on its own with a non-zero code (a `paplay` without a
  sound server, for example) is reported once as a `warning:` line in the
  session. Exits caused by `stop()` are not errors.
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
  session loop can poll the player state every 100 ms. A lone escape byte is
  `esc` (quit); an escape followed at once by more bytes is an arrow or
  function key: the sequence is drained and ignored.
- Windows TTY: `msvcrt.kbhit()` / `getwch()` polled every 10 ms until the
  timeout. Two-code function and arrow keys are swallowed.
- Not a TTY (stdin is a pipe or file): one command per line via `readline()`.
  An empty line is `enter` (next), end of input is `eof` (quit). This is what
  makes `yes n | python3 pr.py --no-play --show-text --count 10` a handy
  generator dump, and what the scripted session tests use.

The terminal state is always restored in `__exit__`, including after an
exception.

### 4.7 Corpus model (`load_corpus`, `corpus_from_data`)

```text
[defaults]  wpm, farnsworth, difficulty, mode, tone, volume, player,
            station, fist, vary, qsb, noise, qrm, filter, shape
[station]   keys = placeholders that stay fixed for one station
[vocab]     placeholder -> list of strings   (upper-cased, whitespace normalised)
[ranges]    placeholder -> [low, high]       (integers, low <= high)
[blocks]    difficulty "1".."5" -> list of templates
[phrases]   difficulty "1".."5" -> list of templates
```

Validation happens once at start-up and fails with a precise message:

- every vocabulary value and every literal part of every template must be
  Morse-encodable (`unsupported_chars`), so a stray `'`, `é` or `{` is caught
  before a session starts;
- every `{placeholder}` must exist in `vocab` or `ranges`, and so must every
  `station.keys` entry;
- prosigns must be well formed (`<KN>`, not `<KN`);
- empty, `null` or `true`/`false` entries in a vocabulary list or template
  list are refused (they would otherwise be sent as `NONE` or `TRUE`);
- both `blocks` and `phrases` must define all five difficulty levels with
  at least one template each;
- unknown keys under `[defaults]` and wrongly typed defaults are refused.

### 4.8 Phrase generation (`Generator`)

`Generator(corpus, difficulty, mode, seed, station)` owns a
`random.Random(seed)` instance so a seeded session is reproducible regardless
of anything else that uses the global `random` module.

`next()`:

0. with `station=N`, every N phrases forget the current station's values and
   set `new_station` (with `station=0`, `new_station` is always true);
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
- a station placeholder without suffix (`{name}`, `{qth}`, `{rig}`... from
  `station.keys`) takes the station's value, drawn once and kept until the
  next station. A suffixed one (`{rig2}`, `{qth2}`) avoids the station's value
  as well, which is how templates speak about the *listener's* rig or another
  place (`UR {rig2} SOUNDS FB`, `WORKED {qth2} LAST NIGHT`);
- the result is upper-cased and whitespace-normalised, and checked against
  the Morse table once more (only a `Corpus` built by hand, without
  `corpus_from_data()`, could fail here). It is the exact string that is
  synthesised and revealed.

### 4.9 Settings (`build_parser`, `resolve_settings`)

Resolution order for every option: command line, then `[defaults]` in
`config.toml`, then built-in defaults (`BUILTIN_DEFAULTS`). `--farnsworth`
falls back to the resolved `--wpm`. A `farnsworth` default from the config
that is above a `-w` given on the command line follows it down instead of
being an error (`farnsworth = 15` then `-w 12` gives 12/12). `--show-text` /
`--hide-text` share one destination; hidden is the default.

Validation ranges: WPM 5-60 for both speeds, `farnsworth <= wpm`,
difficulty 1-5, tone 100-3000 Hz, volume 0.0-1.0, count >= 1, station
>= 0, fist 0-50, vary 0-10, qsb/noise/qrm 0.0-1.0, filter 100-1000 Hz, shape soft/sharp,
`--export` must end in
`.wav`. Errors raise
`CLIError` and name where the bad value came from (`--wpm` or
`config defaults.wpm`); `main()` prints them as `pr.py: error: ...` and
exits 2.
`settings_from_argv(argv, defaults)` exposes the same path to tests without
touching the file system.

### 4.10 Session and UI (`Session`, `UI`)

- The header line is `18 WPM · Farnsworth 15 · Difficulty 3 · Mixed`;
  `Farnsworth` is omitted when it equals the character speed, active band
  conditions are appended (`fist 10% · vary ±2 · QSB 0.5`), and `no audio`
  under `--no-play`.
- Each exercise title is `Exercise N`, or `Exercise N · new station` when
  the generator starts a new station (never for the first exercise).
- One status line (`▶ playing...`, `⏸ paused`, `■ done` plus the key hints)
  is rewritten in place with `\r` and `ESC[K` on a TTY, and only when its text
  changes. Off a TTY each change is printed as a normal line. Symbols fall
  back to ASCII when the output encoding cannot represent them; on Windows
  `os.system("")` enables ANSI processing in the console.
- `+`/`-` change the character speed by 1 WPM and re-synthesise and replay
  the current phrase at once so the change is heard. If Farnsworth is off
  (equal speeds) it stays off and follows the character speed. If it is on,
  the chosen effective speed is kept and only clamped to `<= wpm` while the
  character speed is lower, so `-` then `+` from 16/15 gives back 16/15.
  `]`/`[` change the effective speed within `[5, wpm]`; reaching `wpm`
  turns Farnsworth off.
- The status line hides `[r] replay` once a phrase is done, since space
  replays then.
- A player failure is printed once as a `warning:` line.
- `t` toggles the reveal and prints the phrase (or `(text hidden)`) as a
  permanent line, so revealed text stays in the scrollback. With `--vary` the
  speed the station actually sent is appended: `... HI   (17 WPM)`.
- `r` reloads the normal-speed rendering and plays it from the top. `s`
  plays the slow rendering (spacing at two thirds of the effective speed),
  one second of silence, then the normal rendering, as one buffer with word
  marks for both parts, so pause/resume still work inside it.
- Quitting (`q`, Esc, Ctrl-C, Ctrl-D, EOF) always goes through `finally`:
  the player is stopped, the temp file removed, the terminal restored.

### 4.11 Export (`export`)

`--export FILE.wav` skips the player and the keyboard entirely. It builds the
same `Generator` and `Transmitter` as a session, renders `--count` phrases
(20 by default), and writes each one followed by 3 s of silence straight
into the WAV with `wave.writeframes`, so memory stays at one phrase. The
text goes to `FILE.txt`, one numbered line per phrase, with a blank line
before each new station. A seeded export gives the same phrases as a seeded
session.

## 5. config.toml reference

```toml
[defaults]
wpm = 18            # 5-60
# farnsworth = 15   # leave out = same as wpm
difficulty = 2      # 1-5
mode = "mixed"      # "blocks" | "phrases" | "mixed"
tone = 650          # 100-3000 Hz
volume = 0.5        # 0.0-1.0
station = 8         # phrases per station, 0 = off
fist = 0            # 0-50 %
vary = 0            # 0-10 WPM
qsb = 0.0           # 0.0-1.0
noise = 0.0         # 0.0-1.0, in a 500 Hz filter
qrm = 0.0           # 0.0-1.0
filter = 400        # 100-1000 Hz, receiver CW filter with noise or qrm
shape = "soft"      # "soft" | "sharp"
# player = "ffplay -nodisp -autoexit -loglevel quiet"

[station]
keys = ["name", "qth", "rig", ...]

[vocab]
name = ["HANS", "PETER"]       # {name}
rig = ["K2", "KX3"]            # {rig}, {rig2}

[ranges]
temp = [8, 24]                 # {temp} -> integer 8..24

[blocks]
1 = ["NAME {name}", "73 <SK>"]
# ... up to 5

[phrases]
1 = ["NAME {name} {name}"]
# ... up to 5
```

Shipped vocabulary keys: `name`, `qth`, `rig`, `antenna`, `power`, `weather`,
`band`, `rst`, `key`, `job`, `hobby`, `activity`, `when`. Shipped ranges:
`temp`, `temp_low`, `frost`, `temp_high`, `height`, `years`, `year`, `age`,
`age_retired`, `licence_age`. Add a key to `vocab` or `ranges` and it is immediately
available as a placeholder.

Content guidelines from the spec, worth keeping when editing:

- difficulty is linguistic complexity, never speed and never malformed Morse;
- prefer small vocabularies and composable templates over long lists of
  fixed sentences;
- avoid nonsense combinations by construction (`temp_low` next to `COLD`,
  `temp_high` next to `HOT`, `age_retired` next to `RETIRED`, `when` only for
  past moments);
- keep ranges non-negative and write below-zero temperatures as
  `MINUS {frost}C`, as operators do, instead of sending a `-` sign;
- a template about the listener's things uses a suffixed placeholder
  (`UR {rig2}`), since the unsuffixed one is the station's own;
- use `=` between topics and `<KN>` / `<AR>` / `<SK>` at the end of an
  over, as on the air.

## 6. Tests

```bash
python3 -m unittest -v
```

| area          | what is checked                                                        |
|---------------|------------------------------------------------------------------------|
| config file   | the shipped config.toml loads and its defaults are all valid settings, string level keys and trailing commas, TOML syntax errors name file and line, bad `station.keys` |
| timing        | 1.2 / WPM unit, dah = 3 dits, PARIS + word gap = 60 / WPM, Farnsworth stretches only gaps and hits the effective speed, invalid speeds |
| prosigns      | letters run together, `<AR>` = `+`, broken prosigns refused, the shipped corpus uses them |
| audio         | sample count, silent lead-in, envelope starts at zero, peak below `32767 * volume`, word-start marks, WAV header |
| conditions    | none = bit-identical clean audio; fist is reproducible and keeps the length close; QSB fades the signal only; noise fills the silences at the requested RMS; QRM adds another station; a narrower filter gives quieter noise (by the square root of the bandwidth) and less QRM; an off-frequency station keeps only its key clicks; everything at once stays within 16 bits |
| receiver filter | both shapes: exact -3 dB bandwidth, symmetric in Hz, unity gain at the pitch; sharp: brick-wall skirts; soft: monotonic skirts, -10 dB at twice the half-width; soft neither overshoots nor rings while sharp does (measured on a tone burst) |
| corpus        | unknown placeholder, unbalanced brace, non-Morse character, missing level, bad range, missing file, empty/null entries, the last-resort check in `fill()` |
| generator     | difficulty selects the right pool in all modes, braces never remain, `{rig}`/`{rig2}` differ, repeated placeholder repeats, seeded determinism, no immediate template repeat, 3000 phrases from the shipped corpus are all Morse-clean |
| station       | values fixed for N phrases then renewed, `new_station` flags, suffixed placeholders never equal the station's value, `station=0`, non-station placeholders still vary |
| transmitter   | `--vary` offsets stay in bounds and keep Farnsworth consistent, no vary = exact speed, slow render is longer with the same tone |
| CLI           | defaults, config defaults and overrides, Farnsworth fallback, config Farnsworth following a lower `-w`, error messages naming their source, band-condition options and their limits, every rejected combination from the spec, show/hide text |
| player        | state machine of the silent player; with a fake clock, pause snaps to the word start and resume adds a lead-in; a real subprocess that fails is reported once, one that succeeds or is stopped is not |
| keyboard      | through an `os.pipe()`: lone Esc, arrow/function sequences ignored, plain keys, timeout (POSIX) |
| session       | scripted line-mode sessions: count is honoured, reveal prints the seeded phrase, speed keys update the header, EOF quits; Farnsworth kept across `-`/`+`, status line, player warning, key help, slow then normal replay, new-station titles, header and reveal with conditions |
| export        | WAV format and length, numbered text with blank lines between stations, same first phrase as a seeded generator, 20 phrases by default, unwritable path exits 2 |

Real audio and single-key input cannot run under `unittest` without a
terminal and a sound device. They were checked by hand on macOS with
`afplay`, and the raw-key path through a pseudo-terminal:

```bash
(sleep 1; printf 't'; sleep 0.5; printf 'n'; sleep 0.5; printf 'q') \
  | script -q /dev/null python3 pr.py --no-play --seed 3
```

## 7. Portability notes

- Python 3.11+, for `tomllib`. The script checks the version before
  importing anything else and suggests `uv run pr.py`, which fetches a
  suitable interpreter: the PEP 723 block at the top of `pr.py` declares
  `requires-python = ">=3.11"` and no dependencies.
- macOS: `afplay` ships with the system; `termios` path.
- Linux: needs one of the listed players; PipeWire desktops usually provide
  `paplay` through pipewire-pulse. Headless machines can use `--no-play`.
- Windows: `winsound` for audio, `msvcrt` for keys, ANSI enabled via
  `os.system("")`. Pause/resume works the same way (render from offset).
- Big-endian hosts: `pcm_bytes()` byte-swaps the samples for WAV files.
- `uv run pr.py` works because there are no dependencies to resolve. A
  `pyproject.toml` with a `cw-ragchew` script entry could be added later if
  the `uv run cw-ragchew` form from the spec is wanted.

## 8. Known limitations

- Pause position is estimated from wall-clock time, not read back from the
  player. Snapping the resume to a word start hides that imprecision.
  Stopping the player mid-tone can still click, since the player process is
  simply terminated.
- Audio is rendered per phrase, not streamed; a speed change re-renders and
  restarts the current phrase rather than changing speed mid-phrase.
- Off a terminal (piped stdin) the status line cannot update on its own,
  since input is blocking; it refreshes after each command.
- Band conditions are simple models: one fading rate per phrase, one QRM
  station, white noise through the receiver filter. No QRN crashes, no chirp, no
  multipath. They are enough to break the "perfect keyer" habit, not a
  propagation simulator.
- A station is a set of fixed placeholder values, not a story: it never
  contradicts itself on name, QTH or rig, but weather and temperatures are
  drawn per phrase.
- No `--category` filter yet; templates are grouped by difficulty only.

## 9. Extending

Ideas that fall out of the current structure with little code:

- `--category station|weather|personal|operating`: add a `categories:`
  mapping of template prefixes or a third nesting level in the corpus, then
  filter the pool in `Generator._pool()`.
- `--duration 10m`: stop the session loop when `time.monotonic()` passes the
  deadline instead of counting exercises.
- QRN crashes: short bursts of unfiltered noise at random times, added in
  `_apply_conditions()` like the QRM.
- Export with each phrase played twice (slow, then normal), reusing the same
  concatenation as `Session.slow_replay()`.
- Region-specific names and QTHs: a second `config-xx.toml` and `--config`.

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
| <AR>    | end of message (also `+`)   | <KN>    | over, only you answer            |
| <SK>    | end of contact              | =       | BT, pause / new topic            |
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
