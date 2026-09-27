"""--stats and the practice heatmap."""
import math
from datetime import date, timedelta

from .limits import WPM_MAX, WPM_MIN
from .terminal import UI, plural
from .rating import History, Rating, RatingModel, _day, _proper, longest_run, phrase_rating, rank_for, setup_of, speed_label, streaks


def sparkline(values, blocks: str) -> str:
    if not values:
        return ""
    low, high = min(values), max(values)
    if high - low < 1:
        return blocks[len(blocks) // 2] * len(values)
    top = len(blocks) - 1
    return "".join(blocks[round((v - low) / (high - low) * top)] for v in values)


def bar(fraction: float, width: int, sym: dict) -> str:
    filled = round(max(0.0, min(1.0, fraction)) * width)
    return sym["full"] * filled + sym["empty"] * (width - filled)


def next_step(records: list) -> str:
    """What to try next, from the last 50 phrases at your latest setup."""
    setup = setup_of(records[-1])
    recent = [r for r in records if setup_of(r) == setup][-50:]
    level, wpm, fw = setup
    where = f"d{level} {speed_label(wpm, fw)}"
    if len(recent) < 20:
        return (f"{20 - len(recent)} more phrases at {where} and I can tell you "
                "what to try next")
    rate = sum(1 for r in recent if _proper(r)) / len(recent)
    pct = f"{rate:.0%} Proper Rogers over your last {len(recent)} at {where}"
    if rate >= 0.8:
        options = []
        if level < 5:
            options.append(f"-d {level + 1}")
        if fw < wpm:
            options.append(f"-f {min(wpm, fw + 2)}")
        elif wpm + 2 <= WPM_MAX:
            options.append(f"-w {wpm + 2}")
        return f"{pct}: time to step up, try {' or '.join(options)}"
    if rate < 0.4:
        options = []
        if fw - 2 >= WPM_MIN:
            options.append(f"-f {fw - 2}")
        if level > 1:
            options.append(f"-d {level - 1}")
        easier = f", or ease off with {' or '.join(options)}" if options else ""
        return f"{pct}: a stretch. Stay if you like the fight{easier}"
    return f"{pct}: the sweet spot, stay here, this is where you learn fastest"


HEAT_MINUTES = (5, 10, 20)   # a day under 5 min is level 1, then 2, 3, and 4 from 20 min


def day_minutes(records) -> dict:
    """Minutes on air per day."""
    minutes = {}
    for rec in records:
        day = _day(rec)
        minutes[day] = minutes.get(day, 0.0) + rec.get("secs", 0) / 60
    return minutes


def heat_level(minutes: float | None) -> int:
    if minutes is None:
        return 0
    return 1 + sum(1 for m in HEAT_MINUTES if minutes >= m)


def heat_cell(level: int, ui: "UI") -> str:
    """Two characters for a day. The shade carries the level, so it reads in
    any colour scheme (16 colours have only two greens); on a colour
    terminal a day off is a faint square instead of dots."""
    cell = ui.sym["heat"][level]
    if level == 0 and ui.color:
        cell = ui.sym["heat"][1]
    return ui.paint(cell, f"heat{level}")


def heatmap(records, ui: "UI", today: date | None = None, weeks: int = 3) -> list:
    """A practice calendar, one row per week, Monday first: build the habit."""
    today = today or date.today()
    minutes = day_minutes(records)
    first = today - timedelta(days=today.weekday(), weeks=weeks - 1)
    lines = [f"  {'':6}  " + ui.paint(" ".join(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")),
                                        "label")]
    for week in range(weeks):
        monday = first + timedelta(weeks=week)
        cells = []
        for offset in range(7):
            day = monday + timedelta(days=offset)
            if day > today:
                cells.append("  ")
                continue
            cells.append(heat_cell(heat_level(minutes.get(day)), ui))
        lines.append("  " + ui.paint(f"{monday:%b} {monday.day:>2}", "label") + "  "
                     + " ".join(cells))
    legend = " ".join(heat_cell(level, ui) for level in range(5))
    current, _ = streaks(minutes, today)
    done_today = minutes.get(today)
    if done_today is not None:
        nudge = ui.field("today", max(1, round(done_today)), "min")
    elif current:
        nudge = ui.paint(f"today: not yet, keep your {current}-day streak alive", "bold")
    else:
        nudge = ui.paint("today: not yet", "label")
    streak = ui.field("streak", current, plural(current, "day"))
    lines.append(f"  {'':6}  " + ui.paint("less", "label") + f" {legend} "
                 + ui.paint("more", "label") + "   " + ui.joined([streak, nudge]))
    return lines


def elo_trend(by_day: dict, ui: "UI", today: date) -> list:
    """Your Elo at the end of each of the last 30 days, one character per
    day; a day off keeps the day before's value."""
    start = max(min(by_day), today - timedelta(days=29))
    value = next((by_day[day] for day in sorted(by_day, reverse=True) if day < start), None)
    values = []
    for offset in range((today - start).days + 1):
        value = by_day.get(start + timedelta(days=offset), value)
        values.append(value)
    if len(values) < 2:
        return []
    return ["", "  " + ui.paint(f"Elo, last {len(values)} days", "label") + "   "
            + ui.paint(str(round(min(values))), "label") + " "
            + ui.paint(sparkline(values, ui.sym["blocks"]), "good") + " "
            + ui.paint(str(round(max(values))), "label")]


def setups_table(records, model: RatingModel, ui: "UI", today: date) -> list:
    """The setups played in the last 30 days, most played first."""
    groups = {}
    for rec in records:
        if _day(rec) >= today - timedelta(days=29):
            groups.setdefault(setup_of(rec), []).append(rec)
    if not groups:
        return []
    row = "  {:<14} {:<6} {:<7} {:>7} {:>13} {:>11}".format
    lines = ["", ui.paint(row("last 30 days", "level", "speed", "phrases", "Proper Roger",
                              "phrase Elo"), "label")]
    for (level, wpm, fw), recs in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:6]:
        rate = sum(1 for r in recs if _proper(r)) / len(recs)
        avg = sum(phrase_rating(model, r) for r in recs) / len(recs)
        lines.append(row("", f"d{level}", speed_label(wpm, fw), len(recs),
                         f"{rate:.0%}", round(avg)))
    return lines


def stats_report(history: History, model: RatingModel, ui: "UI",
                 today: date | None = None) -> list:
    """The lines of ``--stats``."""
    sym = ui.sym
    today = today or date.today()
    records = history.records
    lines = [ui.paint(f"proper-roger {sym['dot']} CW RX rating", "value"), ""]
    if not records:
        return lines + [
            "No phrases on record yet. Play a session: every phrase you Roger",
            f"with r is rated, and your Elo starts at {round(model.start)}.",
            "", f"history: {history.path}"]
    rating = Rating(model)
    by_day = {}
    for rec in records:
        by_day[_day(rec)] = rating.play(rec).after
    rank, following, at = rank_for(model, rating.value)
    lines.append("  " + ui.joined([ui.field("Elo", rating.label()), ui.paint(rank, "value"),
                                   ui.field("best", round(rating.best))]))
    if following:
        floor = max((a for a, _ in model.ranks if a <= rating.value), default=model.ranks[0][0])
        fraction = (rating.value - floor) / (at - floor)
        lines.append(f"  {rank} {ui.paint(bar(fraction, 24, sym), 'good')} {following}   "
                     + ui.field("", math.ceil(at - rating.value), "to go"))
    else:
        lines.append(f"  {sym['star']} top of the ladder")
    if rating.provisional:
        left = model.provisional - rating.count
        lines.append("  " + ui.paint(f"provisional: {left} more {plural(left, 'phrase')} "
                                     "before it settles", "label"))
    lines.append("")
    seconds = sum(r.get("secs", 0) for r in records)
    hours, minutes = divmod(int(seconds // 60), 60)
    on_air = f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"
    proper = sum(1 for r in records if _proper(r))
    last50 = records[-50:]
    recent = sum(1 for r in last50 if _proper(r)) / len(last50)
    current, best_streak = streaks(by_day, today)
    run = longest_run(records)
    count = len(records)
    lines.append("  " + ui.joined([
        ui.field("", count, plural(count, "phrase")),
        ui.field("", on_air, "on air"),
        ui.field("", f"{proper / count:.0%}",
                 f"Proper Rogers ({recent:.0%} in the last {len(last50)})")]))
    lines.append("  " + ui.joined([
        ui.field("streak", current, plural(current, "day")),
        ui.field("best", best_streak),
        ui.field("longest run", run, f"{plural(run, 'Proper Roger')} in a row")]))
    lines += [""] + heatmap(records, ui, today)
    lines += elo_trend(by_day, ui, today)
    lines += setups_table(records, model, ui, today)
    lines += ["", "  " + ui.paint("next:", "label") + " " + next_step(records), "",
              ui.paint(f"history: {history.path}", "label")]
    if history.skipped:
        lines.append(f"warning: {history.skipped} unreadable lines skipped")
    return lines
