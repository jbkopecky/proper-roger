# proper-roger

**Learn to follow a real CW ragchew, not just copy callsigns.**

proper-roger is a tiny command-line trainer that keys endless, realistic
fragments of conversational Morse: the kind of thing you hear when two
operators settle in for a chat on 40 m. You listen. If you copied it, you
send **R R** and the next one comes. If you didn't, you send **AGN?** or
**QRS PSE** on your own sidetone and the other station sends it again. Every
phrase you Roger counts toward your **CW RX Elo**, and a little calendar
shows you whether you're building the habit.

```text
$ python3 pr.py -d 3

CW Ragchew RX
25 WPM · Farnsworth 18 · Difficulty 3 · Mixed
fist 5% · vary ±1 · QSB 0.2 · noise 0.15 · filter 400 Hz soft
Elo 1412 · Ragchewer · 88 to Head copier · 612 phrases on record

          Mo Tu We Th Fr Sa Su
  Sep  7  ▒▒ ▓▓ ·· ▒▒ ██ ░░ ··
  Sep 14  ▓▓ ▒▒ ▒▒ ·· ▓▓ ██ ▒▒
  Sep 21  ▒▒ ▓▓ ▓▓ ▒▒ ▓▓ ▒▒ ··
          streak 6 days · today: not yet, keep your 6-day streak alive

Exercise 1
S7    ▮▮▮▮▮▮▮▯▯▯▯▯  ▶ playing  Elo 1412  r roger  space pause  a agn  s qrs  t peek  q quit  ? help
NAME HR IS STEVE AND QTH IS GRENOBLE
✓ PROPER ROGER   +17  → Elo 1429   phrase 1579
  ★ 5 Proper Rogers in a row

Exercise 2
◀ AGN?
SO I USE A VERTICAL FER NW HI
✓ ROGER after AGN   +5  → Elo 1434   phrase 1572
```

When you press `r`, the phrase is printed, so you can check your copy.

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
- **You talk back.** `r` sends `R R`, `a` sends `AGN?`, `s` sends
  `QRS PSE`, all on your own dry sidetone. Then the other station comes back.
- **A CW RX Elo.** Each phrase has its own rating, from its speed, spacing,
  language level, length and band conditions. It is scored as a game you
  win, draw or lose. You get ranks from SWL to Legend, runs of Proper
  Rogers, streaks, personal bests, and `--stats` to tell you what to try
  next.
- **Nothing to install.** One small folder of Python, standard library
  only. No pip, no account, no network, no browser.

## Quick start

```bash
git clone <this repo> && cd proper-roger
python3 pr.py
```

That's it: characters at 25 WPM spaced out to 18 WPM (Farnsworth),
difficulty 2, endless session, `q` to quit. You need
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
| fast but predictable chunks               | `python3 pr.py -d 1 -m blocks -w 28 -f 28`                 |
| the feel of a real QSO                    | `python3 pr.py -d 3 --fist 10 --vary 2 --qsb 0.4`          |
| a busy evening on 40 m                    | `python3 pr.py -d 4 --noise 0.4 --qrm 0.3 --filter 400`    |
| to fight the QRM with a narrow filter     | `python3 pr.py -d 4 --noise 0.4 --qrm 0.4 --filter 200 --shape sharp` |
| 40 phrases for the commute                | `python3 pr.py --export commute.wav --count 40 -d 3`       |
| read along while a level is still new     | `python3 pr.py -d 5 --show-text`                           |
| the same sequence again tomorrow          | `python3 pr.py --seed 42 --count 20`                       |
| to see how far you've come                | `python3 pr.py --stats`                                    |
| practice off the record, nothing rated    | `python3 pr.py --no-history`                               |

## Keys during a session

| key              | you send  | what happens                                                 |
|------------------|-----------|--------------------------------------------------------------|
| `r` (`n`, Enter) | `R R`     | **Roger**: you copied it. It is scored, printed, and the next phrase comes |
| `a`              | `AGN?`    | the phrase again, from the top                               |
| `s`              | `QRS PSE` | the phrase again with more space, and the station **stays** slower until you Roger |
| `space`          |           | pause / resume on the current word; once the phrase is over, `AGN?` |
| `t`              |           | peek: reveal / hide the text (the phrase then scores nothing) |
| `+` / `-`        |           | character speed +1 / -1 WPM (re-plays the phrase, free)      |
| `]` / `[`        |           | Farnsworth effective speed +1 / -1 WPM (free)                |
| `h`, `?`         |           | show this key list                                           |
| `q`, Esc         |           | quit (the phrase you were on is not scored)                  |

The status line always shows the quick keys; press `?` (or `h`) for the full
list. Keys act at once, with no Enter needed, on macOS, Linux and Windows
terminals. Any key cuts your `R R` short if you're in a hurry.

## Talk back: AGN?, QRS, R R

On the air you don't press replay. You ask. So here, `a` keys `AGN?` (or
`PSE AGN`) on your own sidetone. There's a short pause, then the other
station sends the phrase again. `s` keys `QRS PSE`, and it comes back
slower (see [QRS and QRQ](#qrs-and-qrq)). When you've got it, `r` keys `R R`,
`R FB` or `FB` (a plain `R` or `OK` if you needed help), and the next phrase
starts.

Your sidetone sounds like a sidetone should: a perfect keyer at your
character speed and on your `--tone`, heard dry. The band goes quiet while
you send, as it does on a rig with semi break-in. Then you hear the other
station again, with its fist, QSB and noise.

The calls live in `config.toml` under `[sidetone]`, if you'd rather send
`RPT PSE` or `QRS QRS`. Use `--no-sidetone`, or `sidetone = false`, for
silent requests. By the way, `QRS?` with a question mark means "shall *I*
send slower?". To ask the other station to slow down, you send `QRS` or
`PSE QRS`.

## Your CW RX Elo

Chess ratings work because every game is played against an opponent with a
rating. Here, every phrase is the opponent.

**1. The phrase is rated.** D is how hard it was to copy, as it was sent:

```text
D = 1000
  + 500 · log2(v / 15)             speed: v = wpm^0.3 × farnsworth^0.7
  + 120 · (difficulty − 1)         language level 1-5
  +  40 · log2(words / 3)          longer phrases are harder to hold in your head
  +   4 · fist%  + 100 · qsb       band conditions
  + 200 · noise · √(filter / 400)  a wider filter lets in more noise
  + 150 · qrm    +  10 · vary
```

1000 is 15 WPM, level 1, three words, a perfect keyer on a silent band. The
speed blend leans on the Farnsworth speed, because in a ragchew the time you
have between the words matters more than how fast each character is keyed.
The scale is logarithmic because going from 15 to 20 WPM is a bigger step
than going from 30 to 35. Level 5 at 30 WPM on a noisy band is above 2100.

**2. You play it.** Your result S:

| you copied it...                          | S                         |
|-------------------------------------------|---------------------------|
| **Proper Roger**: `r`, no help            | 1 (a win)                 |
| after one `AGN?`                          | 0.5 (a draw)              |
| after `QRS PSE`                           | 0.5, rated at the slower speed |
| each further `AGN?` or `QRS`              | −0.15                     |
| after a peek with `t`                     | 0 (a loss)                |

**3. Plain Elo.** How you were expected to do, then your new rating:

```text
E = 1 / (1 + 10^((D − R) / 400))
R ← R + K · (S − E)          K = 48 for your first 50 phrases (Elo 1043?), then 24
```

Worked example: you are rated 1400 and play `-w 20 -f 15 -d 3` on the
default calm band. A 6-word phrase is rated 1000 + 62 (speed) + 240 (level)
+ 40 (length) + 80 (band) = **1422**, so you are expected to score 0.47.
From there:

| you copy it ...    | Elo  |
|--------------------|------|
| as a Proper Roger  | +13  |
| after one AGN      | +1   |
| after a QRS (heard at 20/12, D = 1310) | −3   |
| after a peek       | −11  |

What that means in practice:

- **It's fair across settings.** A Proper Roger at level 5 and 28 WPM earns
  far more than one at level 1 and 18 WPM, so pushing yourself never costs
  you rating for no reason. Play what you like, the rating follows.
- **A phrase rated like you is a coin toss.** When D = R you're expected to
  score 0.5, a draw on average. Phrases 100 below you are comfortable
  (E = 0.64); phrases at or just above you are where you learn.
- **Tuning is free.** Turning the speed down with `-` or `[` to copy a
  phrase is not an AGN. The phrase is simply rated at the easiest speed you
  heard it at.
- **Honesty is on you.** A Roger is self-reported, so press `r` when you
  copied the phrase, not when you'd like to have. The text printed after
  your Roger lets you check.
- `--show-text` (read-along) and `--no-history` sessions are off the
  record. So are `--no-play` and `--export`.

### Ranks

| Elo   | rank           |
|-------|----------------|
| 0     | SWL            |
| 900   | Novice         |
| 1100  | Pencil copier  |
| 1300  | Ragchewer      |
| 1500  | Head copier    |
| 1700  | Brass pounder  |
| 1900  | Old timer      |
| 2100  | Legend         |

Out of the box (25/18 WPM, level 2, calm band), phrases are rated around 1400.
Everyone starts at 1000 and climbs fast while the rating is provisional.
Along the way you'll see `★ 5 Proper Rogers in a row` (then 10, 25, 50,
100), `▲ RANK UP` and `★ new best`. Each session ends with a summary:

```text
73 · 24 phrases · 17 Proper Rogers (71%) · 4 AGN · 2 QRS · 1 peek · best run 9 · 18 min
Elo 1388 → 1431 (+43) · Ragchewer · best 1440
```

### `--stats`

```text
$ python3 pr.py --stats
proper-roger · CW RX rating

  Elo 1519   Head copier   best 1632
  Head copier ██░░░░░░░░░░░░░░░░░░░░░░ Brass pounder  182 to go

  657 phrases · 2 h 19 min on air · 56% Proper Rogers (48% in the last 50)
  streak 14 days · best 14 · longest run 12 Proper Rogers in a row

          Mo Tu We Th Fr Sa Su
  Sep  7  ▒▒ ▒▒ ▒▒ ░░ ░░ ·· ··
  Sep 14  ░░ ░░ ▒▒ ▒▒ ▒▒ ▒▒ ░░
  Sep 21  ▒▒ ░░ ▒▒ ▒▒ ▒▒ ▒▒ ▒▒
          streak 14 days · today 7 min

  Elo, last 29 days   1197 ▁▄▄▄▄▄▄▅▅▅▅▅▆▆▆▇▇▆▆▇▆▇▆▆▇▇██▆ 1627

  last 30 days   level  speed   phrases  Proper Roger  phrase Elo
                 d3     20/15       370        53%       1413
                 d2     20/13       223        62%       1221

  next: 48% Proper Rogers over your last 50 at d3 20/15: the sweet spot, stay here, this is where you learn fastest
```

The `next:` line looks at your last 50 phrases at your current setup. Above
80 % Proper Rogers, it tells you to step up, with the level first, then the
Farnsworth spacing, then the speed. Below 40 %, it offers an easier setting.
In between, you're in the sweet spot.

### Build the habit

Ten minutes a day beats an hour on Sunday. Every session, and `--stats`,
starts with the last three weeks, one square per day, Monday first:

| square | minutes on air that day |
|--------|-------------------------|
| `··`   | none                    |
| `░░`   | under 5                 |
| `▒▒`   | 5 to 10                 |
| `▓▓`   | 10 to 20                |
| `██`   | 20 or more              |

On a colour terminal they're green, and a day off is a faint grey square.
Minutes on air count from the start of each phrase to your Roger. Next to
the calendar is your streak of days in a row. If you haven't played today
yet, it reminds you to keep the streak alive; the day isn't lost until
midnight.

### Your history

Every phrase you Roger is saved as one line of JSON: when, how it was sent,
how you copied it, and the text. It goes to:

- macOS: `~/Library/Application Support/proper-roger/history.jsonl`
- Linux: `$XDG_STATE_HOME/proper-roger/history.jsonl`, or `~/.local/state/...`
- Windows: `%LOCALAPPDATA%\proper-roger\history.jsonl`

Use `--history FILE`, or `history = "..."` in `config.toml`, to keep it
elsewhere, for example in a synced folder. The history never stores a
rating. Your Elo is replayed from the first phrase each time the trainer
starts, so if you change a weight in `config.toml`'s `[rating]` section,
every phrase you ever copied is re-rated with it. Think noise should count
more for you? Raise `noise`. Want a fresh start? Rename the file.

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

## Farnsworth: 25 WPM characters, 18 WPM overall

`--wpm` is the character speed and `--farnsworth` the effective speed. The
default is `-w 25 -f 18`: every dit and dah is sent at 25 WPM, and only the
gaps between characters and words are stretched to give 18 WPM overall
(ARRL method). You learn the *sound* of each character at real speed, the
way you'll hear it from a good operator, and the spacing gives you time to
follow. As you improve, close the gap with `]` or `-f`, not by slowing the
characters down.

A `-w` below 18 takes the Farnsworth speed down with it (`-w 15` is 15/15).
Use `-f` on its own for other spacing, or `-f 25` for none at all.

### QRS and QRQ

Missed a phrase because it came too fast? Press `s` to send `QRS PSE`. The
other station does what a real one would do. It resends the phrase with 3
WPM more space between the characters, which are still keyed at 25 WPM.
Then it **stays** at that speed. Each Proper Roger after that brings it
back up by 1 WPM (QRQ), until it's back to your own Farnsworth speed:

```text
◀ QRS PSE
  QRS: Farnsworth 15, back to 18 as you copy
✓ ROGER after QRS   ...
✓ PROPER ROGER   ...
  ▲ QRQ: Farnsworth 16, 2 to go
✓ ROGER after AGN   ...            (an AGN or a peek holds the speed)
✓ PROPER ROGER   ...
  ▲ QRQ: Farnsworth 17, 1 to go
✓ PROPER ROGER   ...
  ▲ QRQ: back to your Farnsworth 18
```

Press `s` again to slow down further. The header shows
`Farnsworth 15 (QRS, yours is 18)` while a QRS is on. `[` or `]` takes over
from there and ends the QRS. A QRS'd phrase is a repeat, scored like an AGN
(0.5), and rated at the slower speed you heard it at, as are the phrases
after it until you're back up to speed.

## The S-meter

On a terminal, the status line starts with a retro LED S-meter, like the
bar graphs on small QRP rigs:

```text
S7    ▮▮▮▮▮▮▮▯▯▯▯▯  ▶ playing
S3    ▮▮▮▯▯▯▯▯▯▯▯▯  ▶ playing     between words: the noise floor
S9+10 ▮▮▮▮▮▮▮▮▮▮▯▯  ▶ playing     a loud station, over S9 in red
TX    ▮▮▮▮▮▮▮▮▮▮▮▮  ▶ playing     you, sending AGN?
```

It reads the audio you actually hear, like a real meter. Each station
comes on at its own strength, from about S4 to S9+20. The meter rises at
once and falls back slowly, as with AGC, so it stays up through a word and
dips in the gaps. The dips show the noise floor through your filter: about
S3 on the default calm band, higher with `--noise 0.5`, lower when you
narrow `--filter`. QSB makes it breathe. While you send, it shows `TX`. It
only appears on a live terminal. `--no-meter` or `meter = false` turns it
off.

## Colours

Colours use only the 16 standard terminal colours, so they follow your
terminal's own colour scheme: light, dark, Solarized, whatever you run.
The look is quiet on purpose. Names are grey and values bold
(`Farnsworth` **`18`**), keys are bold with what they do in grey. Green is
good, red is a loss. In the heatmap, the shade carries the level, so it reads
even where the terminal has only one green. Set `NO_COLOR=1` for none. They
are off automatically when the output isn't a terminal.

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
-w, --wpm N          character speed in WPM (5-60, default 25)
-f, --farnsworth N   effective speed in WPM, <= --wpm (default 18, or --wpm when lower)
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

your rating:
    --stats          your Elo, rank, streak, progress and what to try next, then exit
    --sidetone       hear yourself send AGN?, QRS PSE and R R (the default)
    --no-sidetone    requests are silent, the phrase just plays again
    --meter          a live S-meter on the status line (the default, on a terminal)
    --no-meter       no S-meter
    --history FILE   where rated phrases are kept (default: see "Your history")
    --no-history     off the record: nothing rated, nothing saved

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
  `--export` and the show/hide choice, plus `sidetone = true/false`,
  `meter = true/false` and `history = "path"`. Leave `farnsworth` out for none.
- `[sidetone]`: what you send for `agn`, `qrs`, `roger` and `ack`.
- `[rating]`: every weight of the Elo formula, K, the provisional period,
  the results for AGN and QRS, and the rank ladder.
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
- `AGN?` is one key away, but so is `r`. Move on more often than you ask
  for a repeat.
- Start with `-d 1 -m blocks` a little above your comfort speed, then raise
  the difficulty before you raise the speed.
- Add conditions one at a time. `--fist 10` alone is already a big step from
  a perfect keyer.
- Don't chase the number by staying on easy settings. The Elo already
  accounts for difficulty, and you gain the most on phrases rated at or
  above you.
- Check `--stats` once a week, not after every phrase. A few bad sessions
  are noise. The rating over a month is the signal.
- Fill the calendar before you chase the Elo. Short and daily wins.
- Don't be shy with `s`. A QRS costs no more than an AGN, and the QRQ
  back up to speed is some of the best training there is.

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
- **`warning: N unreadable lines ... skipped`**: a line of your history was
  cut short or edited by hand. The other lines still count.
- **See what the generator produces without listening**:
  `yes n | python3 pr.py --no-play --show-text --count 10 --seed 1 -d 5`

## Under the hood

```bash
python3 -m unittest -v     # 149 tests, a few seconds
```

The code is in `proper_roger/`, one plain module per concern (Morse
timing, audio, the receiver filter, the player, the rating, the S-meter,
the session...), and `pr.py` just starts it. [DOC.md](DOC.md) explains how
it all works: the Farnsworth timing maths,
prosigns, the I/Q receiver filter, the band conditions, stations,
pause/resume on word boundaries, the keyboard handling on each platform,
and the rating and history.

## License

MIT, see [LICENSE](LICENSE).

73 <SK>
