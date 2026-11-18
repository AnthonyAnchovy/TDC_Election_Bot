"""The desk's own bot: when it would call a race, from the numbers alone (no Discord here).

It reads the same thing people see beside a vote, the model's projected margin for a race over time, and calls when
the projection has pointed the same way, by enough, for long enough. It is another voice on the board to beat, never
an official call: bot.py logs its calls the way it logs an outlet's, so /scoreboard times the desk against it.

The rule, for one race's history of (time, projected margin, votes counted, state):
  - only projections built on finished counties count (state "proj"); anything earlier restarts the wait
  - the lead has to be at least FLOOR points, and on the same side, for a stretch that depends on its size:
    a wider lead needs less waiting (see STEPS). The stretch is measured on the smallest lead inside it, so one wobble
    toward zero means waiting longer, and a change of leader starts over
  - the projection may not have swung by more than SWING points inside the stretch: early projections lurch, and a
    lurching one isn't ready, whatever it says

Election night 2026 is the only night this has been tried against (tests/test_autocaller.py): it called 14 of the 17
Senate races with data, all correctly, and left the three closest alone, including Mississippi, which the desk called
and had to retract. It was also slow: a median 83 minutes behind the desk, and ahead of it in only two races. One
night is thin evidence. The thresholds are named below so they can be argued with.
"""
from __future__ import annotations

from dataclasses import dataclass

FLOOR = 4.0                     # points: never call a projected lead smaller than this
STEPS = ((15.0, 20), (8.0, 40), (FLOOR, 75))    # (lead of at least this many points, held for this many minutes)
SWING = 6.0                     # points the projection may move inside the stretch and still be trusted
MIN_POINTS = 5                  # snapshots needed inside the stretch, so a single reading can't call anything


@dataclass(frozen=True)
class Call:
    race: str
    side: str           # "D" (the Democratic side of the field) or "R"
    at: float           # when the rule was first satisfied
    margin: float       # the projection at that moment, D side positive
    held: float         # minutes the lead had held


def wait_for(lead: float) -> int | None:
    """Minutes a lead of this many points has to hold, or None if it's too small to call at all."""
    for points, minutes in STEPS:
        if lead >= points:
            return minutes
    return None


def _ready(run: list[tuple[float, float, int, str]], race: str) -> Call | None:
    """Whether the rule is satisfied at the end of `run`, a stretch of same-side projections of at least FLOOR."""
    ts, margin = run[-1][0], run[-1][1]
    low, high, small = margin, margin, abs(margin)
    for j in range(len(run) - 1, -1, -1):           # walk back, widening the stretch
        m = run[j][1]
        low, high, small = min(low, m), max(high, m), min(small, abs(m))
        if high - low > SWING:
            break                                   # any longer stretch swings too much as well
        need, held = wait_for(small), (ts - run[j][0]) / 60
        if len(run) - j >= MIN_POINTS and need is not None and held >= need:
            return Call(race, "D" if margin > 0 else "R", ts, margin, held)
    return None


def _run_start(series: list[tuple[float, float, int, str]], end: int) -> int | None:
    """Where the same-side run of callable projections that ends at `end` began, or None if `end` isn't in one."""
    if series[end][3] != "proj" or abs(series[end][1]) < FLOOR:
        return None
    start = end
    while start > 0 and series[start - 1][3] == "proj" and abs(series[start - 1][1]) >= FLOOR \
            and (series[start - 1][1] > 0) == (series[end][1] > 0):
        start -= 1
    return start


def decide_now(series: list[tuple[float, float, int, str]], race: str = "") -> Call | None:
    """Whether the rule calls the race at its latest snapshot. What the bot asks each minute."""
    if not series:
        return None
    start = _run_start(series, len(series) - 1)
    return None if start is None else _ready(series[start:], race)


def decide(series: list[tuple[float, float, int, str]], race: str = "") -> Call | None:
    """The first moment the rule calls the race, or None: decide_now() at every snapshot in turn. For looking back
    over a night. series is (time, margin, votes, state), oldest first."""
    for i in range(len(series)):
        start = _run_start(series, i)
        call = None if start is None else _ready(series[start:i + 1], race)
        if call:
            return call
    return None


# ------------------------------------------------------------------ the forecast's rule
# With the forecast (cw_forecast) the bot calls on the chance of winning, not on a margin held for a set time: a
# 12-point lead with 5% counted and a 3-point lead with 98% counted can be equally safe, and the forecast's range
# knows the difference. On the five replayed elections (backtest.py, 20 nights each, 1600 races) 99.5%, 99% and 98%
# each made no wrong call, with half the calls by 48%, 42% and 35% counted. 99% was chosen. Its edge case is
# Mississippi 2026, which ended D+0.3: replaying 2026 alone, 99% called it for the wrong side once in 20 nights with
# 94% counted (99.5% never did). The replays are kinder than a real night (turnout roughly known, no bad files).
CALL_AT = 0.99         # the chance of winning it calls at (was 0.995 until Nov 2026; see above)
MIN_WEIGHT = 0.5       # results, not the prior (polls and other states), must carry at least this much of the forecast
HOLD_MINUTES = 8       # and the chance has to have stayed there this long, across at least two snapshots


def decide_prob(points: list[tuple[float, float, float]], race: str = "") -> Call | None:
    """Whether the forecast calls the race at its latest snapshot. points: (time, chance the D side wins, weight of
    results in the forecast), oldest first."""
    if len(points) < 2:
        return None

    def side(wp, weight):
        return None if weight < MIN_WEIGHT else "D" if wp >= CALL_AT else "R" if wp <= 1 - CALL_AT else None
    ts, wp, weight = points[-1]
    now = side(wp, weight)
    if not now:
        return None
    start = len(points) - 1
    while start > 0 and side(points[start - 1][1], points[start - 1][2]) == now:
        start -= 1
    held = (ts - points[start][0]) / 60
    if start == len(points) - 1 or held < HOLD_MINUTES:
        return None
    return Call(race, now, ts, wp, held)


def standing(series: list[tuple[float, float, int, str]]) -> str:
    """Why the rule hasn't called a race yet, in a few words."""
    if not series or all(state != "proj" for *_x, state in series):
        return "no county has finished counting"
    margin = series[-1][1]
    if abs(margin) < FLOOR:
        return f"inside {FLOOR:g} points"
    return "waiting for the lead to hold"
