# proper-roger

**Learn to follow a real CW ragchew, not just copy callsigns.**

proper-roger is a tiny command-line trainer that keys endless, realistic
fragments of conversational Morse: the kind of thing you hear when two
operators settle in for a chat on 40 m. You listen, replay, pause, reveal
the text, and move on. That is the whole loop, and it works.

```text
$ python3 pr.py -w 20 -f 15 -d 3 --fist 10 --noise 0.3 --filter 250

CW Ragchew RX
20 WPM · Farnsworth 15 · Difficulty 3 · Mixed · fist 10% · noise 0.3 · filter 250 Hz soft

Exercise 1
▶ playing...   [space] pause  [r] replay  [t] reveal  [n] next  [q] quit
```

Press `t` when you want the answer:

```text
NAME HR IS STEVE AND QTH IS GRENOBLE
▶ playing...   [space] pause  [r] replay  [t] hide  [n] next  [q] quit
```

## Why it is different

You can already copy `TU 599 73`, and short POTA or SOTA exchanges are no
problem. But when someone sends `RIG HR KX3 ES ANT IS LOOP = SO HW? <AR> <KN>`,
the words blur. Callsign trainers and random-word drills don't train you for
that. proper-roger does.

- **Real ragchew language, never the same twice.** More than 170 templates
  with names, QTHs, rigs, antennas, weather, jobs and hobbies, filled at
  random, with the abbreviations and prosigns operators actually use: `FB`,
  `HPE CUAGN`, `=`, `<KN>`, `<SK>`.
- **Difficulty is language, not speed.** Five levels, from `NAME HANS HANS`
  to `BEEN HAM SINCE 1987 BUT ONLY STARTED CW AGAIN LAST YR`, at whatever
  speed you choose. Try level 4 at 15 WPM, or level 1 at 30.
- **You follow one operator.** Name, QTH, rig and antenna stay consistent
  for several phrases, like listening to a real station tell you about
  their shack, then a new station comes on.
- **Off-the-air sound, when you want it.** A human fist, fading, band noise
  and another station calling CQ nearby, all heard through a proper CW
  filter, soft and round by default or sharp when the band is crowded.
  Narrow the filter and the noise really does drop and the QRM really does
  fade, just like on your rig.
- **Take it on a walk.** Export a session to a WAV file with its transcript
  and listen on your phone.
- **Nothing to install.** One Python file, standard library only. No pip, no
  account, no network, no browser.

## Quick start

```bash
git clone <this repo> && cd proper-roger
python3 pr.py
```

That's it: 18 WPM, difficulty 2, endless session, `q` to quit. You need
Python 3.11 or newer. With [uv](https://docs.astral.sh/uv/),
`uv run pr.py` works too and gets a suitable Python if yours is older.

Audio goes through what your computer already has: `afplay` on macOS,
`paplay`, `pw-play`, `aplay`, `play`, `ffplay` or `mpv` on Linux, `winsound`
on Windows. You can also pass any other command with `--player "cmd"`.

## Pick your session

| you want...                               | try                                                        |
|-------------------------------------------|------------------------------------------------------------|
| to get started gently, on a calm band      | `python3 pr.py -d 1 -m blocks -w 20 -f 12`                 |
| a perfect keyer on a silent band          | `python3 pr.py --clean`                                    |
| real sentences, comfortable spacing       | `python3 pr.py -d 3 -w 20 -f 15`                           |
| fast but predictable chunks               | `python3 pr.py -d 1 -m blocks -w 28`                       |
| the feel of a real QSO                    | `python3 pr.py -d 3 --fist 10 --vary 2 --qsb 0.4`          |
| a busy evening on 40 m                    | `python3 pr.py -d 4 --noise 0.4 --qrm 0.3 --filter 400`    |
| to fight the QRM with a narrow filter     | `python3 pr.py -d 4 --noise 0.4 --qrm 0.4 --filter 200 --shape sharp` |
| 40 phrases for the commute                | `python3 pr.py --export commute.wav --count 40 -d 3`       |
| read along while a level is still new     | `python3 pr.py -d 5 --show-text`                           |
| the same sequence again tomorrow          | `python3 pr.py --seed 42 --count 20`                       |

## Keys during a session

| key        | action                                                     |
|------------|------------------------------------------------------------|
| `space`    | pause / resume on the current word (replays once done)     |
| `r`        | replay the phrase from the beginning                       |
| `s`        | **slow replay**: stretched spacing first, then full speed  |
| `t`        | reveal / hide the text                                     |
| `n`, Enter | next phrase                                                |
| `+` / `-`  | character speed +1 / -1 WPM (re-plays the phrase)          |
| `]` / `[`  | Farnsworth effective speed +1 / -1 WPM                     |
| `h`, `?`   | show this key list                                         |
| `q`, Esc   | quit                                                       |

Keys act at once, with no Enter needed, on macOS, Linux and Windows
terminals.

## Difficulty is about language, not speed

| level | feel                          | example                                                   |
|-------|-------------------------------|-----------------------------------------------------------|
| 1     | familiar QSO fields           | `NAME HANS HANS`, `UR RST 579`, `73 <SK>`                 |
| 2     | compact QSO sentences         | `RIG HR K2 PWR 5W`, `TNX FER CALL = UR RST 559 559`       |
| 3     | normal ragchew                | `BEEN HAM FER 25 YRS`, `UR SIG FB WITH SOME QSB`          |
| 4     | natural conversational CW     | `JUST CAME BACK FROM A WALK WITH THE DOG HI`              |
| 5     | messy real-world ragchew      | `BEEN HAM SINCE 1987 BUT ONLY STARTED CW AGAIN LAST YR`   |

`-m blocks` plays short chunks to be heard as one unit (`TNX FER CALL`),
`-m phrases` plays longer fragments, and `mixed` (the default) draws from
both.

## Farnsworth and the slow replay

`--wpm` is the character speed and `--farnsworth` the effective speed. With
`-w 20 -f 15`, every dit and dah is sent at 20 WPM, and only the gaps between
characters and words are stretched to give 15 WPM overall (ARRL method). You
learn the *sound* of each character at real speed.

Missed a phrase? Press `s`. You hear it with extra spacing, then at full
speed right after, so you catch the words slowly and then recognise them at
speed.

## Follow a station

Real ragchews are one operator telling you about their shack. That is what
you get. Here are six phrases in a row from the same station:

```text
NOT MUCH DX WITH 10W HI
ANT IS LOOP BUT XYL WANTS IT DOWN HI
RIG HR KX3 ES ANT IS LOOP = SO HW? <AR> <KN>
I AM 60 YRS OLD AND RETIRED TEACHER
OP HR IS MIKE AGN
PSE QRS A BIT
```

Name, QTH, rig, antenna, power, key, job, hobby and band stay the same for
8 phrases, then a new station comes on (`Exercise 9 · new station`). Use
`--station N` to change how long a station stays, or `--station 0` for new
values every phrase. With `--vary`, each station also keys at its own speed
and pitch, and the revealed text tells you what was actually sent:
`PSE QRS A BIT   (17 WPM)`.

## Band conditions

Out of the box you hear a **calm, real band**: a discreet human fist
(`--fist 5`), stations a little faster or slower than each other
(`--vary 1`), gentle fading (`--qsb 0.2`) and a soft noise floor
(`--noise 0.15`), with no QRM. It is realistic, but relaxed enough for a
long session. Want the perfect keyer on a silent band? Add `--clean`. Want
a busier band? Turn the knobs up:

| option         | what you hear                                                          |
|----------------|------------------------------------------------------------------------|
| `--fist 10`    | a human hand: every dit, dah and gap a little off (about 10 %)         |
| `--vary 2`     | each station at its own speed (+/- 2 WPM) and slightly off frequency   |
| `--qsb 0.5`    | slow fading, down to half the signal                                   |
| `--noise 0.3`  | band noise                                                             |
| `--qrm 0.3`    | another station calling CQ, somewhere between 80 and 600 Hz away       |
| `--filter 400` | your receiver's CW filter bandwidth, 100-1000 Hz (default 400)         |
| `--shape soft` | filter shape: `soft` (default) is round and never rings; `sharp` cuts nearby QRM hard but rings |

The receiver is modelled properly. It is tuned so the station you follow
sounds at `--tone` (600 Hz by default), and everything, including the
signal, the QRM and the noise, goes through a CW filter centred on that
note. So the noise is the soft rush you hear on your rig, not a harsh
full-band hiss, and going from `--filter 500` to `--filter 250` lowers it by
3 dB.

Like the SOFT / SHARP setting on many rigs, `--shape` picks the filter:

- **soft** (default): a round, flat, Gaussian-like shape with no ringing at
  all, so you can listen for hours. It is gentle right next to the passband
  (a station 400 Hz away is only 10 dB down with a 400 Hz filter), so fight
  close QRM by narrowing it: `--filter 200`.
- **sharp**: brick-wall skirts. That same station drops 48 dB and only its
  key clicks remain. But like a very narrow crystal filter, it rings, and
  every dit leaves a short hollow tail.

Experiment: it is exactly what the filter knobs on your rig do.

## Listening on the go

```bash
python3 pr.py --export commute.wav --count 40 -d 3 --fist 10
```

This writes 40 phrases with 3 seconds of silence between them to
`commute.wav`. The text goes to `commute.txt`, one numbered line per phrase,
with a blank line when a new station comes on. Every option applies: speed,
difficulty, stations, conditions, seed. Copy the WAV to your phone, listen,
and check the transcript afterwards.

## All options

```text
-w, --wpm N          character speed in WPM (5-60, default 18)
-f, --farnsworth N   effective speed in WPM, <= --wpm (default: same as --wpm)
-d, --difficulty N   language difficulty 1-5, independent from speed (default 2)
-m, --mode MODE      blocks | phrases | mixed (default mixed)
    --tone HZ        sidetone / receive pitch (default 600)
    --volume X       0.0-1.0 (default 0.5)
-c, --count N        stop after N exercises (default: endless)
    --show-text      show the phrase before it plays (read-along practice)
    --hide-text      hide until t is pressed (the default)
    --station N      phrases from one station before the next (default 8, 0 = off)
    --export F.wav   write --count phrases (default 20) to F.wav and F.txt, then exit
    --seed N         reproducible phrase sequence
    --no-play        generate without audio (debugging, tests)
    --player CMD     force the WAV player command
    --config PATH    another corpus / defaults file (default: config.toml next to pr.py)

band conditions (default: a calm band):
    --clean          perfect keyer on a silent band: every condition below off
    --fist PCT       human timing variation, % of each element (0-50, default 5)
    --vary WPM       each station keys up to +/- WPM off --wpm, on its own pitch (0-10, default 1)
    --qsb X          fading depth 0-1 (default 0.2)
    --noise X        band noise 0-1 (default 0.15; 0.3 busy, 0.5 noisy)
    --qrm X          another station calling CQ nearby, 0-1 (default 0, try 0.3)
    --filter HZ      receiver CW filter, 100-1000 Hz (default 400), with noise or QRM
    --shape SHAPE    receiver filter shape: soft (default, no ringing) | sharp
```

Invalid values are refused with a clear message, for example
`--farnsworth 20 --wpm 15`, `--difficulty 7` or `--filter 50`. When the bad
value comes from `config.toml`, the message says so
(`config defaults.wpm must be between 5 and 60`).

## Make it yours: config.toml

Everything the trainer says, and all its defaults, live in `config.toml`, a
plain TOML file you can edit:

- `[defaults]`: your usual settings, so `python3 pr.py` alone starts the
  session you want. It holds every option above except `--count`, `--seed`,
  `--export` and the show/hide choice. Leave `farnsworth` out for none.
- `[station]`: which placeholders belong to the station you follow.
- `[vocab]`: word lists for `{name}`, `{qth}`, `{rig}`...: add your local
  towns, your club's favourite rigs.
- `[ranges]`: integer ranges such as `temp = [8, 24]` for `{temp}`.
- `[blocks]` and `[phrases]`: templates for difficulty levels `1` to `5`.

Template rules:

- `{name}` picks a value, and the same placeholder twice repeats it:
  `NAME {name} {name}` gives `NAME HANS HANS`, as operators do.
- A digit suffix asks for a different value: `HAVE {rig} ES {rig2}`. It is
  also how a template talks about the *other* operator's things, since
  `{rig}` is the station's own: `UR {rig2} SOUNDS FB`, `WORKED {qth2}`.
- Letters, digits, spaces, `. , ? / = + - @` and prosigns in angle brackets
  (`<KN>`, `<SK>`, `<AR>`, `<BK>`) are allowed. `=` is BT. Anything that
  cannot be sent in Morse is refused at start-up, with the template that
  contains it.

## Tips

- Listen for meaning, not letters. Missing a word is normal; keep listening.
- Replay is one key away, but so is `n`. Move on more often than you replay.
- Start with `-d 1 -m blocks` a little above your comfort speed, then raise
  the difficulty before you raise the speed.
- Add conditions one at a time. `--fist 10` alone is already a big step from
  a perfect keyer.

## Troubleshooting

- **`no audio player found`**: install one of the players above, or pass
  `--player "ffplay -nodisp -autoexit -loglevel quiet"` (any command that
  plays the WAV path given as its last argument works).
- **`warning: audio player ... failed`**: the player could not play the file
  (no sound server, wrong device...). Check your sound setup, or pick another
  player with `--player CMD`.
- **`pr.py needs Python 3.11 or newer`**: use `uv run pr.py`, or install a
  newer Python.
- **Odd symbols instead of `▶ · ■`**: your terminal encoding cannot show
  them. The trainer falls back to ASCII when it can detect that.
- **Terminal left in a strange state** after a crash: type `reset`.
- **See what the generator produces without listening**:
  `yes n | python3 pr.py --no-play --show-text --count 10 --seed 1 -d 5`

## Under the hood

```bash
python3 -m unittest -v     # 83 tests, a few seconds
```

[DOC.md](DOC.md) explains how it all works: the Farnsworth timing maths,
prosigns, the I/Q receiver filter, the band conditions, stations,
pause/resume on word boundaries, and the keyboard handling on each platform.

## License

MIT, see [LICENSE](LICENSE).

73 <SK>
