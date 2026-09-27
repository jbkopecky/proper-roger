# proper-roger: technical documentation

This document describes how proper-roger works and why it is built the way it is.
The user-facing guide is [README.md](README.md); the original specification
is the *CW Ragchew RX Trainer* spec this MVP was written from.

## 1. Goals and constraints

- **RX comprehension only.** Play a realistic ragchew fragment, let the
  operator ask for it again (`AGN?`), slower (`QRS`), peek, or Roger it and
  move on. No typing and no sending practice: your sidetone only plays
  what the keys ask for.
- **Progress you can trust.** Since 0.3, every Roger is a game in an Elo
  rating against the phrase's own difficulty, with ranks, runs, streaks and
  `--stats`. It is crunchy but honest. The rating is replayed from a plain
  history file, so a change of weights re-rates everything.
- **Ultra portable.** Standard library only. A launcher (`pr.py`), one
  package of plain modules (`proper_roger/`) and one data file
  (`config.toml`). No `pip install`, no virtualenv, no network. It runs with
  any Python 3.11+ on macOS, Linux and Windows. Copy the folder and go.
- **Small.** No terminal UI framework, no plugin system, no generic
  abstractions. Roughly 2400 lines including docstrings, in 14 modules of
  10 to 450 lines, each one readable on its own.
- **Real but peaceful by default.** Out of the box you hear a calm band: a
  discreet human fist (5 %), stations +/- 1 WPM apart, gentle fading (0.2)
  and a soft noise floor (0.15) through a 400 Hz soft filter, at a 600 Hz
  pitch, with no QRM. `--clean` gives the perfect keyer on a silent band.

The stdlib-only constraint drove these design decisions, each of which would
usually be solved by a dependency:

| need              | usual dependency        | what proper-roger does instead                   |
|-------------------|-------------------------|--------------------------------------------------|
| corpus file       | PyYAML                  | TOML, read by the standard `tomllib` (3.11+)     |
| audio synthesis   | numpy                   | `array('h')` + `math.sin`, tones cached per length |
| noise, fading     | numpy / scipy           | `random.gauss` through a biquad band-pass, per-sample loops |
| audio playback    | sounddevice / pyaudio   | WAV to a temp file, played by the OS player        |

Version 0.1 had its own 200-line YAML reader. Raising the minimum to Python
3.11 replaced it with `tomllib` and removed a whole class of parsing bugs.

## 2. Files

```text
pr.py            launcher: checks for Python 3.11+, then proper_roger.cli.main()
proper_roger/    the program, one module per concern (below)
config.toml      corpus (vocabulary, ranges, templates, station), CLI defaults,
                 your sidetone calls and the Elo weights
tests/           unittest suite, one test_<module>.py per module, plus support.py
                 (python3 -m unittest -v)
history.jsonl    your rated phrases, outside the repo (see 4.12)
README.md        user guide
DOC.md           this file
```

The modules, in dependency order. Each one imports only modules above it,
so there are no import cycles and any module can be read (or tested)
knowing only what comes before it:

| module           | holds                                                        | section |
|------------------|--------------------------------------------------------------|---------|
| `limits`         | speed, tone and filter limits, the sample rate               |         |
| `morse`          | the Morse table, prosigns, PARIS / Farnsworth timing         | 4.2-4.3 |
| `receiver`       | the receiver's I/Q CW filter                                 | 4.4b    |
| `audio`          | `render()`, band conditions, QRM, WAV files                  | 4.4-4.4b |
| `player`         | the system player, pause / resume                            | 4.5     |
| `terminal`       | `Keyboard`, `UI`, colours, `clip()`, `KEYS_HELP`             | 4.6, 4.10 |
| `rating`         | `RatingModel`, `Rating`, `Record`, `History`                 | 4.12    |
| `scoreboard`     | `Attempt` (the phrase on the air), `Scoreboard` (the Elo, this session) | 4.12 |
| `corpus`         | loading and checking config.toml, `Generator`                | 4.1, 4.7-4.8 |
| `settings`       | argparse, config defaults, `Settings`                        | 4.9     |
| `transmitter`    | per-station speed, pitch and strength; your sidetone         | 4.4c    |
| `meter`          | the S-meter                                                  | 4.14    |
| `stats`          | `--stats`, the heatmap, next step                            | 4.13    |
| `session`        | `Session`: speeds, QRS, sound, text and the key loop         | 4.10    |
| `cli`            | `main()`, `--export`, `--stats`                              | 3, 4.11 |

`pr.py` stays at the top so that `python3 pr.py` and `uv run pr.py` work
from anywhere. Python puts the script's folder on the import path, so the
package next to it is found without installing anything.

## 3. Runtime flow

```text
main()
  parse argv                          argparse, build_parser()
  load_corpus(--config)               tomllib -> Corpus, validated
  resolve_settings(args, defaults)    CLI > config.toml defaults > built-ins, validated
  --stats?   show_stats()             replay the history, print the report, exit
  --export?  export(settings, corpus) phrases -> one WAV + a .txt, then exit
  History(--history).load()           unless --no-history / --no-play
  find_player(--player)               unless --no-play
  Session(settings, corpus, Player, history).run()
     Scoreboard: replay the history into a Rating
     print the two header lines, the Elo line and the practice heatmap
     with Keyboard():                 cbreak mode on a TTY
        for each exercise:
           phrase = Generator.next()            new station? -> Transmitter.new_station()
           samples, word_starts = Transmitter.render(phrase, wpm, farnsworth)
                                     -> render(text, timing, tone, volume, conditions, rng)
           Player.load(samples, word_starts); Player.play(0)
           loop: redraw status line when state changes, read one key, act
                 a / s: sidetone call + gap + phrase (s: at a lower Farnsworth)
                 r: Attempt.record() -> Scoreboard.score(): rate, save;
                    verdict, then your R R on the sidetone
     print the session summary
```

Exit code is 0 on a normal quit (`q`, Esc, Ctrl-C, EOF) or a finished
export, and 2 on an invalid option, a broken corpus or an export file that
cannot be written, with the message on stderr. On Python older than 3.11 the
script stops at once with a message suggesting `uv run pr.py`.

## 4. Inside proper_roger

### 4.1 Config file (`tomllib`): `corpus`

`config.toml` is standard TOML, read with `tomllib.load()`. TOML fits the
corpus well: strings are always quoted (no `NO` turning into `false`, no
`O'BRIEN` quoting puzzles), lists may span lines with a trailing comma, and
syntax errors come with a line and column. Difficulty levels are written as
bare keys `1 = [...]` (TOML keys are strings; the loader converts them).

TOML has no `null`, so an absent key means "use the built-in default". This
is how `farnsworth` is turned off. A `--config` ending in `.yaml`/`.yml` is
refused with a hint, for users upgrading from 0.1.

### 4.2 Morse table and prosigns: `morse`

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

### 4.3 Timing and Farnsworth (`timing_for`, `timeline`): `morse`

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

### 4.4 Audio synthesis (`render`): `audio`

- 16 kHz, 16-bit signed mono, built in an `array('h')`.
- Every tone segment is a sine at `--tone` Hz with a 5 ms raised-cosine
  attack and release to avoid clicks. Tones are cached by sample length, so
  a phrase costs two sine computations (dit and dah) plus concatenation.
- Amplitude is `32767 * volume`; volume is baked into the samples so every
  player backend behaves the same.
- 300 ms of silence is prepended (system players take a moment to start,
  the first dit must not be clipped) and 200 ms appended.
- `render()` also returns the sample index where each word starts (its
  first tone), used by pause/resume.
- `write_wav(path, samples, rate)` writes the file with the `wave` module.

On a 10-second phrase this takes a few milliseconds in pure Python.

### 4.4b Band conditions (`Conditions`): `audio`, `receiver`

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

### 4.4c Per-station sending (`Transmitter`): `transmitter`

`Transmitter` sits between the session and `render()`. It holds its own
`random.Random` (seeded from `--seed`, separate from the phrase generator so
turning conditions on does not change the phrases), and for `--vary N` a
per-station offset: up to +/- N WPM on both speeds (clamped to 5-60 and
`farnsworth <= wpm`) and up to +/- 60 Hz on the tone. `new_station()`
redraws the offsets. `render(phrase, wpm, farnsworth)` applies them.
`new_station()` also draws the station's `strength` for the S-meter (4.14),
from a third `random.Random`, so the meter never changes a seed's audio.
`sidetone(text, wpm)` is you: no conditions, no offsets.

### 4.5 Playback (`find_player`, `Player`): `player`

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

### 4.6 Keyboard (`Keyboard`): `terminal`

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

### 4.7 Corpus model (`load_corpus`, `corpus_from_data`): `corpus`

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

### 4.8 Phrase generation (`Generator`): `corpus`

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

### 4.9 Settings (`build_parser`, `resolve_settings`): `settings`

Every option shared by the command line and `config.toml` is one line of
the `OPTIONS` table: its type, default, limits or choices, and unit. For
each, the value comes from the command line, then `[defaults]` in
`config.toml`, then the table's default (`BUILTIN_DEFAULTS` is derived from
it), and `_check()` validates it with one set of messages. What remains in
`resolve_settings()` are the rules that span options: `--farnsworth`
falls back to the resolved `--wpm`. A `farnsworth` default from the config
that is above a `-w` given on the command line follows it down instead of
being an error (`farnsworth = 15` then `-w 12` gives 12/12). `--show-text` /
`--hide-text` share one destination; hidden is the default. `--clean`
replaces the band conditions before they are checked, so it wins over
anything in the config. A test keeps the table, the flags and `Settings`
in step.

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

### 4.10 Session and UI (`Session`, `UI`): `session`, `terminal`

- The header is two lines, each under 80 columns. `speed_line()`:
  `25 WPM · Farnsworth 18 · Difficulty 3 · Mixed` (`Farnsworth` omitted
  when it equals the character speed, `(QRS, yours is 18)` during a QRS,
  `no audio` under `--no-play`). `band_line()`: the active conditions
  (`fist 5% · vary ±1 · QSB 0.2 · noise 0.15 · filter 400 Hz soft`), or
  `perfect keyer, silent band`. A speed change reprints only the first.
  Names are grey and values bold (`UI.field`).
- There is no key list at start-up: the status line carries the quick keys
  and `? help`; `h` or `?` prints `KEYS_HELP`.
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
- The status line hides `a agn` once a phrase is done, since space
  asks AGN then. On a rated session it ends with `Elo 1412`, and it is
  cut to the terminal width, since a wrapped line can no longer be
  rewritten in place. Verdicts are coloured on a TTY unless `NO_COLOR` is set.
- A player failure is printed once as a `warning:` line.
- `t` toggles the reveal and prints the phrase (or `(text hidden)`) as a
  permanent line, so revealed text stays in the scrollback. With `--vary` the
  speed the station actually sent is appended: `... HI   (17 WPM)`.
- `a` (and space once the phrase is over) is `ask_agn()`. `s` is
  `ask_qrs()`: `qrs_drop` grows by `QRS_STEP_WPM` (3), never below 5 WPM,
  and the phrase is re-rendered at `sent_fw = farnsworth - qrs_drop`, the
  characters still at `--wpm`. The drop outlives the phrase: every render
  uses `sent_fw`, and each Proper Roger takes `QRQ_STEP_WPM` (1) off it
  with a `▲ QRQ` note. An AGN or a peek holds it. `[`/`]` start from
  `sent_fw` and clear the drop, so manual control always wins. Both keys
  go through `_send_then()`. It echoes the call as a `◀ AGN?` line, renders
  it with `Transmitter.sidetone()`, which uses no conditions and no `--vary`
  at your character speed and tone, at `SIDETONE_LEVEL` of the volume, and
  plays sidetone + `SIDETONE_GAP_SECONDS` of silence + the phrase as one
  buffer. The buffer has word marks for every part, so pause/resume still
  work inside it. Because it is one buffer, the "receiver" is naturally
  silent while you send. With `--no-sidetone`, only the phrase is played.
- Keys go through one table, `Session._keys`, from key to method.
- `r` (or `n`, Enter) is `roger()`. It asks the `Attempt` for its `Record`,
  hands it to `Scoreboard.score()` (4.12), prints the phrase dimmed if it
  was hidden, so you can check your copy, then the verdict and any notes (a
  run milestone, a rank change, a new best, a QRQ).
  Finally it plays your `roger` call (after a Proper Roger) or `ack` call
  (otherwise) and waits for it to end. On a TTY any key cuts it short, and
  `q` still quits. In line mode it just waits, since the next line is the
  next command.
- A phrase is rated at the easiest speeds it was *heard* at:
  `_send_phrase()` renders it and hands a `Record` of how it was sent to
  `Attempt.hear()`, which keeps the one with the lowest D. So `-` to copy a phrase is free, but it is not a free win at
  the harder speed. Speeds are the ones the station actually sent (with
  `--vary`); `set_wpm`/`set_fw` keep what you chose, for `--stats`.
- Quitting (`q`, Esc, Ctrl-C, Ctrl-D, EOF) always goes through `finally`:
  the player is stopped, the temp file removed, the terminal restored.

### 4.11 Export (`export`): `cli`

`--export FILE.wav` skips the player and the keyboard entirely. It builds the
same `Generator` and `Transmitter` as a session, renders `--count` phrases
(20 by default), and writes each one followed by 3 s of silence straight
into the WAV with `wave.writeframes`, so memory stays at one phrase. The
text goes to `FILE.txt`, one numbered line per phrase, with a blank line
before each new station. A seeded export gives the same phrases as a seeded
session.

### 4.12 Rating and history (`RatingModel`, `Rating`, `History`): `rating`

- `phrase_rating(model, rec)` is D, a sum of independent terms (README,
  "Your CW RX Elo"). The speed term uses a geometric blend
  `wpm^(1-s) · fw^s` with `s = farnsworth_share = 0.7`, on a log2 scale:
  every doubling of the blended speed is worth `speed` points. Length is
  also log2, relative to `words_ref`, so one-word blocks come out slightly
  below the base. Noise is scaled by `sqrt(filter / 400)`, the same law as
  the audio (4.4b).
- `copy_result()` is S: 1, or `agn` / `qrs` for the first request (a QRS in
  the mix counts as a QRS) minus `extra` for each further one, floored at 0.
  A peek is 0.
- `Rating.play(rec)` is textbook Elo with `k_provisional` for the first
  `provisional` phrases (shown as `1043?`) and a `floor`. It returns a
  `Game` with D, before/after, whether it was a Proper Roger, whether it is
  a new best (only once settled, so the first session does not spam it), and
  the ranks before and after. Runs of Proper Rogers are counted from the
  records by `longest_run()`, and live by the scoreboard for its milestones.
- `scoreboard.Attempt` is the phrase on the air: its text, the AGNs, QRSs
  and peek, the easiest `Record` heard, when it started. `record(when)`
  turns it into the history line. `scoreboard.Scoreboard` replays the
  history into a `Rating` at start, then `score(rec)` counts each phrase,
  keeps the run of Proper Rogers, and on the record rates and appends it.
  A failed save is kept for `take_error()`, the same pattern as `Player`,
  and the session prints it once. Neither makes a sound or prints a line.
- `RatingModel` is a frozen dataclass whose defaults match config.toml's
  `[rating]`. `rating_model_from()` accepts only known fields, checks the
  types and ranges, and sorts `ranks`.
- `History` is JSON Lines, one `Record` per line. A `Record` is a frozen
  dataclass holding what is needed to re-rate the phrase (`wpm`, `fw`,
  `level`, `words`, the conditions), how it was copied (`agn`, `qrs`,
  `peek`, `secs`), the setup chosen (`set_wpm`, `set_fw`, `mode`), a local
  ISO timestamp with offset, and the `text`. It never holds a rating. Its
  properties answer the questions the stats ask: `proper`, `day`, `setup`.
  `Record.from_json()` ignores keys it does not know and fills missing ones
  with their defaults, so history files from older and newer versions load. Appending opens, writes one line and closes, so a crash loses at
  most the phrase in flight. Unreadable lines are skipped and counted, never
  fatal. A failed append is one `warning:` line and the session goes on.
- The default path follows each platform's convention
  (`default_history_path()`): Application Support on macOS,
  `XDG_STATE_HOME` or `~/.local/state` on Linux, `LOCALAPPDATA` on Windows.
  It is state, not configuration: it is not kept next to `pr.py`.
- Off the record: `--no-history` or `--no-play` (no `History` at all), and
  `--show-text` (a `History` exists but `Scoreboard.rated` is false and no
  verdict is printed: reading along is not copying). Quitting mid-phrase
  does not rate it.

### 4.13 Stats (`stats_report`, `elo_trend`, `setups_table`, `heatmap`): `stats`

`--stats` replays the whole history, keeping the rating at the end of each
day. From that it prints:
- the Elo, rank and best, with a bar to the next rank;
- the phrase count, time on air (the sum of `secs`, the time from a phrase's
  start to its Roger) and Proper Roger rates;
- `streaks()`: consecutive days, where a day not yet played does not break
  the current streak;
- a one-character-per-day `sparkline()` over the last 30 days, where a day
  off repeats the previous value;
- the setups of the last 30 days (`setup_of()`: level and *chosen* speeds);
- `next_step()`: at the latest setup, the last 50 phrases. Below 20 it asks
  for more. At 80 % or more Proper Rogers it suggests the next level, then
  +2 Farnsworth, then +2 WPM. Under 40 % it offers -2 Farnsworth or a lower
  level. In between is the sweet spot.
- `heatmap()`, also printed when a session with a history starts: three
  calendar weeks, Monday first, ending with the current one (future days
  blank). A day's level comes from its minutes on air (`HEAT_MINUTES`: any,
  5, 10, 20). `heat_cell()` draws it as shade characters `░▒▓█`, since 16
  colours have only two greens; colour adds the hue, and a day off becomes a
  grey `░░` instead of `··`. The last line is the streak, and a nudge when
  today has not been played yet (no legend: the shades speak for themselves).

### 4.14 S-meter (`meter_track`, `meter_bar`): `meter`

- `Session._play()` is the only way audio is played. It loads the buffer
  and, when the meter is on, computes its `meter_track()` once: per 50 ms
  frame, the RMS of the samples actually played. For the receiver that is
  `strength + dB(rms / full carrier) / 6` S-units, with an instant attack
  and a release of `METER_DECAY` S per frame, like AGC. So a key-down reads
  the station, word gaps fall to the noise floor that comes through the
  filter, and QSB moves both. The first `tx_samples` (your sidetone) are
  frames of key-down level instead, shown as `TX`.
- `meter_text()` maps `Player.position()`, minus `METER_LAG_SECONDS` for
  the player's start-up, to a frame. Paused shows where it stopped. Done
  shows an unlit bar.
- `meter_bar()` has 12 segments: S1-S9, then +10/+20/+30 dB, drawn as runs
  of one colour code each. `s_label()` reads the same thresholds, so the
  label always matches the lit segments.
- `meter_on` needs `--meter` (the default), a TTY and a real player. Off a
  TTY, every change of the status line would print a new line. The key
  poll drops to 50 ms so the bar moves smoothly.
- `UI.status()` cuts the line to the terminal width with `clip()`, which
  counts only visible characters, never cuts a colour code in half, and
  resets the colour after a cut. The Elo comes before the key hints, so a
  narrow terminal loses hints first.
- Colours come from one `STYLES` table that uses only the 16 standard ANSI
  colours (30-37, 90-97, bold, dim), so the terminal's own scheme decides
  what they look like. `NO_COLOR` or a non-TTY output turns them off.
- Text is styled through three helpers, so the look stays consistent:
  `UI.field(label, value, unit)` (label and unit grey, value bold),
  `UI.joined(parts)` (grey `·` between parts) and `UI.keys(pairs)` (key
  bold, action grey). `Session.key_help()` applies the same rule to
  `KEYS_HELP`, which stays plain for `--help`.

## 5. config.toml reference

```toml
[defaults]
wpm = 25            # 5-60
farnsworth = 18     # leave out = same as wpm
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
sidetone = true     # AGN? / QRS PSE / R R on your own sidetone
meter = true        # live S-meter on a terminal
# history = "~/cw/history.jsonl"

[sidetone]
agn = ["AGN?", "PSE AGN"]      # every entry must be sendable in Morse
qrs = ["QRS PSE", "PSE QRS"]
roger = ["R R", "R FB", "FB"]
ack = ["R", "OK"]

[rating]                       # every RatingModel field; see the shipped file
k = 24
agn = 0.5
ranks = [{ at = 0, name = "SWL" }, { at = 900, name = "Novice" }]

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
| sidetone      | AGN = dry sidetone then the phrase as heard, below the received level; calls come from the config and are checked for Morse; `--no-sidetone` just replays; `sidetone` in config and on the CLI |
| scoreboard    | an attempt keeps its easiest hearing and records what you did; off the record counts but rates nothing; the history is replayed, then each phrase rated and saved; runs and milestones; a failed save is reported once |
| records       | a 0.3 history line loads and round-trips; unknown keys ignored, missing ones defaulted; bad lines refused; the setup is what you chose |
| look          | the header is two lines under 80 columns even with every condition at its widest and during a QRS; `--clean` reads "perfect keyer"; no key list until `?` |
| options table | every option is a flag and a `Settings` field, every default passes its own checks, `--clean` wins over bad band values in the config |
| rating        | the base phrase is exactly 1000, the README worked example (1422; +13 / +1 / -4 / -11), every term makes a phrase harder in the right direction and Farnsworth spacing weighs more than character speed, S values, a draw at equal ratings moves nothing, provisional K is double, runs, rank up and bests, the floor, `rank_for`, config overrides and every rejected value |
| history       | round trip, bad lines skipped and counted, missing file, default path per platform, `--history`, `--no-history`, `~` in config |
| rated session | outcomes recorded per key sequence and shown after the Roger; calls echoed; rating carried over between sessions; quitting does not rate; tuning the speed down is free and rated at the slowest; space pauses while playing and is an AGN once done; read-along is off the record; run milestones, rank up and best within a session; a save error warns once |
| QRS / QRQ     | QRS lowers only the Farnsworth speed, keeps the characters and the tone, sticks to the next phrase, never goes below 5 WPM, `]` takes over; a scripted session goes 15, 15, 16, 16 (an AGN holds), 17, 18 with the right QRQ notes |
| heatmap       | minute levels, three weeks Monday first with future days blank, the streak nudge, shown when a session starts |
| S-meter       | S labels and their agreement with the lit segments at every tenth of an S-unit, a tone reads the station and silence nothing, instant attack and slow release, the noise floor rises with `--noise`, TX frames, the bar, per-station strength, seeded audio unchanged, only on a live terminal, settings |
| clip          | colour codes do not count, are never cut, and the colour is reset after a cut |
| stats         | empty history, the full report, streaks with today still to play, every `next_step` branch including `--vary` setups, sparkline, `main --stats` |
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
  concatenation as `Session.ask_qrs()`.
- `--auto`: a matchmaker that picks the Farnsworth speed or the level so
  that D sits just above your Elo, using `phrase_rating()` backwards.
- Fit the `[rating]` weights to your own history with a small logistic
  regression (standard library, gradient descent), so the formula learns
  whether noise or speed is what really costs *you*.
- Region-specific names and QTHs: a second `config-xx.toml` and `--config`.

Things the spec asks to keep out: sending practice, keyer input, two-way
QSO simulation, pileups, typing answers, propagation, contest logic,
browser UI, LLM or network calls. Scoring and statistics were on that list
too, until 0.3 found a form that needs no typing: the rating comes only
from the keys you already press.

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
