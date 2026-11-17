"""Replay past elections county by county and score County Watch's forecast against what happened.

    python backtest.py                 the comparison table: project() alone vs the forecast (cw_forecast)
    python backtest.py --nights 40     more simulated nights per election (default 12)
    python backtest.py --json out.json the numbers, for the website's model page
    python backtest.py --simulate      ignore any recorded night and simulate every election
    python backtest.py --no-extras     skip the early vote and past polls sections (they rerun the elections)
    python backtest.py --stress        how the forecast copes with elections unlike the ones it was set on (slow)
    python backtest.py --multi         races with more than two candidates, on made-up cities (cw_multi)

The elections are the ones the data pack lists (its County Watch "backtests", see election() below). 2026's:
  2022   each state's 2022 race (Senate, governor or House, whichever baseline.csv has), from the 2020 baseline
  2024   the 2024 presidential result, from the 2020 baseline
  2026   the 2026 Senate results the bot was holding when the count ended (countywatch/live.json), from the 2024
         baseline the bot used on the night, with the polls it had

Nobody recorded which county reported when on any of these nights, so the order is simulated: counties start
reporting in waves as polls close, small ones tend to finish first, a county's first votes lean one way (one voting
method) and drift to its final margin, and some states' feeds carry no precinct counts. Every model sees the same
simulated night. It's a test of the arithmetic, not of the feeds.

From now on the bot does record it: intake.py writes every accepted change to snapshots/<state>.jsonl. When the
active pack's election has such a log, that election is replayed once, in the order and at the times the counties
really reported, instead of on simulated nights. Only the states with a log are scored; any others are simulated
alongside so the states can still inform each other.

Two more sections follow the tables, because each is a decision someone has to be able to check:
  early vote    whether each use of the early vote (cw_forecast.early_inputs) made the forecast better
  past polls    2022 and 2024 again with their final polling averages (countywatch/polls_history.json)

Scores, by how much of the state's vote was counted at that moment:
  miss       the average distance between the forecast margin and the final one, in points
  in range   how often the 90% range held the final margin (should be about 90%)
  wrong      how often it had the wrong side ahead
  brier      the mean squared error of the win chance (0 is perfect, 0.25 is a coin)
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from pathlib import Path

import bisect

import autocaller as AUTO
import cw_forecast as F
import cw_model as M
import intake as IN
import packs

HERE = Path(__file__).parent
BINS = [(-1, 0.0, "nothing in"), (0.0, 0.05, "0-5%"), (0.05, 0.15, "5-15%"), (0.15, 0.35, "15-35%"),
        (0.35, 0.65, "35-65%"), (0.65, 0.90, "65-90%"), (0.90, 0.995, "90-99%")]
NIGHT_MINUTES = 300     # a state's count, start to finish, in the simulation's clock
SKIP = {"AK"}       # projected by vote type from files this replay doesn't have


# ------------------------------------------------------------------ the elections
# Which elections there are to replay comes from the pack's County Watch block ("backtests"), each built from the
# past contests its baseline.csv holds (cw_model.contests):
#   {"name": "2024", "result": "2024-pres", "split": "2020-pres", "turnout": "2020-pres", "polls": "2024"}
#       score against the result contest, starting from the split contest's margins at the turnout contest's size,
#       with the polls filed under that name in polls_history.json
#   {"name": "2026", "result": "live"}
#       the pack's own election: the results the bot was holding when the count ended (live.json), from the pack's
#       own baseline and polls
# A pack that says nothing gets these three, which is what this file replayed before packs could say.
BACKTESTS_2026 = [{"name": "2022", "result": "2022", "split": "2020-pres", "turnout": "2020-pres", "polls": "2022"},
                  {"name": "2024", "result": "2024-pres", "split": "2020-pres", "turnout": "2020-pres", "polls": "2024"},
                  {"name": "2026", "result": "live"}]


def backtests() -> list[dict]:
    import feeds as FEEDS
    return list(FEEDS.countywatch().get("backtests") or BACKTESTS_2026)


def _year(name):
    """A backtest's name as the number it usually is ("2024" -> 2024), so results can be keyed and sorted by year."""
    return int(name) if str(name).isdigit() else name


def all_years() -> tuple:
    return tuple(_year(b["name"]) for b in backtests())


def live_year():
    """The name of the backtest that is the pack's own election (its result is "live"), or None."""
    return next((_year(b["name"]) for b in backtests() if b.get("result") == "live"), None)


def _seed(year) -> int:
    return year if isinstance(year, int) else sum(ord(ch) for ch in str(year))


def election(year) -> tuple[dict[str, list[dict]], dict[str, dict], dict[str, float]]:
    """(baseline rows by state, final county results by state, polls) for one of the pack's backtests, in the shapes
    cw_model uses.

    One thing to know about the turnout it starts from. The model is handed each state's real turnout level, give
    or take (the scatter is added per night): "the turnout an operator would have pencilled in". That is kinder
    than a real night. In 2026 the bot's flat guess was off by 8% on average. It stays until baseline.csv holds
    the election a real operator would have used (2018 for 2022), which a backtest can then name as its turnout."""
    entry = next((b for b in backtests() if str(b["name"]) == str(year)), None)
    if entry is None:
        raise KeyError(f"the pack has no backtest called {year} (it has {', '.join(str(b['name']) for b in backtests())})")
    now = M.load()
    if entry["result"] == "live":
        final = json.loads((M.CW_DIR / "live.json").read_text(encoding="utf-8"))["states"]
        rows = {st: now[st] for st in now if st not in SKIP and st in final}
        finals = {st: {f: {"D": v["D"], "R": v["R"], "O": v.get("O", 0)} for f, v in final[st].items()} for st in rows}
        return rows, finals, F.poll_margins()
    polls = past_polls(entry.get("polls", entry["name"]))
    split, size = entry["split"], entry.get("turnout") or entry["split"]
    rows, finals = {}, {}
    for st, cur in now.items():
        if st in SKIP:
            continue
        new, fin = [], {}
        for c in cur:
            (d0, r0, t0), (d1, r1, _t1) = c["past"][split], c["past"][entry["result"]]
            if d0 + r0 == 0 or d1 + r1 == 0:
                continue
            # two-party votes expected: the split contest's own, or its two-party share of the turnout contest's total
            w = float(d0 + r0) if size == split else c["past"][size][2] * (d0 + r0) / (t0 or d0 + r0)
            if not w:
                continue
            n = dict(c)
            n.update(d24=d0, r24=r0, t24=d0 + r0, m24=M.m2(d0, r0), t22=w, w=w)
            new.append(n)
            fin[c["fips"]] = {"D": d1, "R": r1, "O": 0}
        if not new:
            continue
        # the turnout an operator would have pencilled in: the state's real level, give or take (set per night)
        ratio = sum(v["D"] + v["R"] for v in fin.values()) / sum(n["w"] for n in new)
        for n in new:
            n["w"] *= ratio
            n["t22"] = n["w"]
        sm, tw, t = M.base_m(new), sum(n["w"] for n in new), sum(n["t24"] for n in new)
        for n in new:
            n["share24"], n["share22"], n["breakeven"] = n["t24"] / t, n["w"] / tw, n["m24"] - sm
        rows[st], finals[st] = new, fin
    return rows, finals, {st: m for st, m in polls.items() if st in rows}


def past_polls(year) -> dict[str, float]:
    """{state: final polling average, as a two-party margin} for an earlier election, from polls_history.json.
    States the file marks as a gap (no average was published, or the race had none) are simply left out."""
    try:
        states = json.loads((M.CW_DIR / "polls_history.json").read_text(encoding="utf-8")).get(str(year), {}).get("states", {})
    except (OSError, ValueError):
        return {}
    out = {}
    for st, row in states.items():
        d, r = row.get("d"), row.get("r")
        if d and r:
            out[st] = (d - r) / (d + r)
    return out


def early_files(year: int) -> dict | None:
    """The early vote the bot was holding for an election (its early.json), or None. Only the active pack's own
    election has one: no early vote from 2022 or 2024 is in the repo in a form these can use."""
    if year != live_year():
        return None
    try:
        return json.loads((M.CW_DIR / "early.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ a recorded night
def recorded_night(year: int, rows: dict, where: Path | None = None, steps: int = 24) -> dict | None:
    """The night as intake.py logged it, if the active pack's election is `year` and it has a snapshot log:
    {"states": {state: (times, [(time, counties, moved)])}, "moments": [the times to score at]}."""
    if year != live_year():
        return None
    states = {}
    for st in IN.recorded(where):
        if st in rows:
            night = IN.replay(st, where)
            if night:
                states[st] = ([t for t, _rows, _moved in night], night)
    if not states:
        return None
    times = sorted({t for ts, _n in states.values() for t in ts})
    picks = sorted({times[round(i * (len(times) - 1) / steps)] for i in range(steps + 1)}) if len(times) > steps else times
    return {"states": states, "moments": picks}


def recorded_at(rec: dict, st: str, t: float) -> dict:
    """A recorded state's counties as they stood at t, each with the minutes since its votes last changed."""
    times, night = rec["states"][st]
    i = bisect.bisect_right(times, t) - 1
    if i < 0:
        return {}
    _t, rows, moved = night[i]
    return {f: {**r, "idle": (t - moved[f]) / 60} if f in moved else dict(r) for f, r in rows.items()}


# ------------------------------------------------------------------ one simulated night
MESSY = True    # precinct counts behave as real ones do (see night); False gives the tidy ones of the first version


def night(rows: dict, finals: dict, rng: random.Random) -> dict:
    """When each county reports, and how its early count leans. Times run 0 to about 1.5; a state takes 1.0.

    "Share of precincts in" is not the share of votes in, and with MESSY the simulation stops pretending it is.
    Half the states that report precincts post a block of early vote (30 to 65% of each county) before their first
    precinct, so a county can show 0% in with half its vote counted. And everywhere a county's precincts differ
    in size, so its share in runs ahead of or behind its votes (each county's own bend)."""
    plan = {}
    for st, cs in rows.items():
        start = rng.choice([0.0, 0.1, 0.2, 0.3, 0.45])
        lean = rng.gauss(0, 0.13)                       # the state's first votes vs its last, D side +
        precincts = rng.random() > 0.3                  # some feeds have no precinct counts
        guess = math.exp(rng.gauss(0, 0.07))            # how far off the pencilled-in turnout was
        sizes = sorted(c["w"] for c in cs)
        rank = {c["fips"]: sizes.index(c["w"]) / max(len(cs) - 1, 1) for c in cs}
        plan[st] = {"start": start, "precincts": precincts, "guess": guess, "counties": {
            c["fips"]: {"first": start + rng.uniform(0.0, 0.25),
                        "done": start + min(1.0, max(0.12, 0.2 + 0.55 * rank[c["fips"]] + rng.gauss(0, 0.18))),
                        "lean": lean + rng.gauss(0, 0.06), "steps": rng.choice([2, 3, 4, 6])} for c in cs}}
    extra = random.Random(rng.random())             # its own stream, so everything drawn above is as it always was
    for st in rows:
        plan[st]["early"] = extra.uniform(0.30, 0.65) if extra.random() < 0.5 else 0.0
        for c in plan[st]["counties"].values():
            c["bend"] = math.exp(extra.gauss(0, 0.25))
    return plan


def precincts_in(pl: dict, c: dict, f: float) -> float:
    """The share of precincts a county would show with share f of its votes counted."""
    if not MESSY:
        return round(f, 3)
    e = pl.get("early", 0.0)
    p = max(0.0, (f - e) / (1 - e)) if e else f
    return round(min(0.998, p ** c.get("bend", 1.0)), 3)


def live_at(st: str, plan: dict, finals: dict, t: float) -> dict:
    out = {}
    pl = plan[st]
    for fips, c in pl["counties"].items():
        fin = finals[st].get(fips)
        if not fin or fin["D"] + fin["R"] == 0 or t < c["first"]:
            continue
        if t >= c["done"] or c["done"] <= c["first"]:
            f, idle = 1.0, t - c["done"]
        else:
            f = (t - c["first"]) / (c["done"] - c["first"])
            step = math.floor(f * c["steps"])                                    # votes arrive in batches
            idle = t - (c["first"] + step * (c["done"] - c["first"]) / c["steps"])
            f = max(1, step + 1) / (c["steps"] + 1)
        idle *= NIGHT_MINUTES
        n = fin["D"] + fin["R"]
        if f >= 1.0:
            out[fips] = {"D": fin["D"], "R": fin["R"], "O": fin.get("O", 0), "pct_in": 1.0 if pl["precincts"] else None,
                         "idle": idle}
            continue
        m = max(-0.98, min(0.98, M.m2(fin["D"], fin["R"]) + c["lean"] * (1 - f)))
        d = round(n * f * (1 + m) / 2)
        out[fips] = {"D": d, "R": round(n * f) - d, "O": round(fin.get("O", 0) * f), "pct_in": precincts_in(pl, c, f) if pl["precincts"] else None,
                      "idle": idle}
    return out


def field_margin(st: str, projs: dict) -> float:
    """What the night's field showed for a state under project() alone: the projection, else the count plus other
    states' swing, else the baseline."""
    p = projs[st]
    if p["proj_m"] is not None:
        return p["proj_m"]
    return M.pooled_est(st, projs) if p["counted"] else p["base_m"]


# ------------------------------------------------------------------ scoring
def roughen(finals: dict, sd: float, rng: random.Random) -> dict:
    """The same results with every county's margin knocked off course by its own random amount (sd, in margin).
    A stand-in for elections where places don't follow their past as closely as counties do in a partisan
    general: a primary, a city race by ward. Each county keeps its number of votes."""
    out = {}
    for st, rows in finals.items():
        out[st] = {}
        for fips, v in rows.items():
            n = v["D"] + v["R"]
            m = max(-0.98, min(0.98, (M.m2(v["D"], v["R"]) or 0.0) + rng.gauss(0, sd)))
            d = round(n * (1 + m) / 2)
            out[st][fips] = {"D": d, "R": n - d, "O": v.get("O", 0)}
    return out


def run(years=None, nights: int = 12, seed: int = 7, hp: dict | None = None, steps: int = 24,
        use_polls: bool = True, snapshots: Path | None | bool = None, use_early: bool = True, rough: float = 0.0,
        pre_widths: bool | None = None) -> dict:
    """Every (election, night, moment, state) scored for both models. Returns {"rows": [...], "calls": [...],
    "recorded": {year: [states replayed from a snapshot log]}}.

    years: which of the pack's backtests to run, by name (all of them if None).

    snapshots: a folder of snapshot logs to replay, None for the active pack's own, False to simulate everything.
    use_early: hand the forecast the early vote the bot held for that election, where there is any. Which uses of
    it are switched on is in hp (EV_COUNTY_TURNOUT, EV_STATE_TURNOUT, EV_REG).
    rough: knock every county's final margin off course by this much first (roughen), for the stress tests.
    pre_widths: give each poll the width the pre-election model fits (cw_forecast.night_polls) in place of SP and
    SD0. None follows the forecast's own setting (PRE_WIDTHS)."""
    saved = M._CACHE.get("rows")
    recs, calls, replayed = [], [], {}
    try:
        for year in (all_years() if years is None else years):
            year = _year(year)          # "2024" and 2024 are the same election (the midterm test below needs a number)
            rows, finals, polls = election(year)
            yhp = dict(hp or {})
            if use_polls and polls and (F.settings(hp)["PRE_WIDTHS"] if pre_widths is None else pre_widths):
                midterm = (year if isinstance(year, int) else live_year() or 0) % 4 == 2
                # the pack's own election uses the poll counts its polls.json gives, as the live bot does
                counts = F.poll_counts() if year == live_year() else None
                polls, shared_sd = F.night_polls(polls, counts, midterm=midterm, office="SEN" if midterm else "PRES")
                if shared_sd:
                    yhp["SD0"] = shared_sd
            if rough:
                finals = roughen(finals, rough, random.Random(seed * 77 + _seed(year)))
            truth = {st: M.m2(sum(v["D"] for v in finals[st].values()), sum(v["R"] for v in finals[st].values())) for st in rows}
            total = {st: sum(v["D"] + v["R"] for v in finals[st].values()) for st in rows}
            rec = None if snapshots is False else recorded_night(year, rows, snapshots or None, steps)
            if rec:
                replayed[year] = sorted(rec["states"])
            scored = [st for st in rows if not rec or st in rec["states"]]
            files = early_files(year) if use_early else None
            for nt in range(1 if rec else nights):
                rng = random.Random(seed * 1000 + _seed(year) * 10 + nt)
                plan = night(rows, finals, rng)
                # a recorded state ran on the bot's flat turnout that night; a simulated one on a pencilled-in guess
                M._CACHE["rows"] = {st: [dict(c, w=c["w"] * (1.0 if rec and st in rec["states"] else plan[st]["guess"])) for c in cs]
                                    for st, cs in rows.items()}
                early = F.early_inputs(files, hp=hp) if files else {}
                called: dict[str, tuple] = {}
                streak: dict[str, tuple] = {}
                moments = rec["moments"] if rec else [1.5 * i / steps for i in range(steps + 1)]
                for i, t in enumerate(moments):
                    sim_t = 1.5 * i / max(len(moments) - 1, 1)
                    projs = {}
                    for st in rows:
                        live = recorded_at(rec, st, t) if rec and st in rec["states"] else live_at(st, plan, finals, sim_t)
                        factor, by_county = F.turnout_start(st, early)
                        projs[st] = M.project(st, live, factor, by_county)
                    fc = F.forecast(projs, polls if use_polls else {}, yhp, early)
                    for st in scored:
                        p, q = projs[st], fc[st]
                        share = p["counted"] / total[st]
                        old = field_margin(st, projs)
                        rg, rq = p.get("range90"), q["range90"]
                        recs.append({"year": year, "st": st, "share": share, "truth": truth[st],
                                     "old": old, "old_in": None if not rg else rg[0] <= truth[st] <= rg[1],
                                     "new": q["proj_m"], "new_in": rq[0] <= truth[st] <= rq[1], "sd": q["sd"],
                                     "wp": q["win_prob"]})
                        side = call_side(q)
                        streak[st] = (side, streak[st][1] + 1) if side and streak.get(st, (None, 0))[0] == side else (side, 1 if side else 0)
                        if side and st not in called and streak[st][1] >= 2:      # the same call at two moments running
                            called[st] = (side, share, sim_t - plan[st]["start"])
                for st in scored:
                    c = called.get(st)
                    calls.append({"year": year, "st": st, "truth": truth[st], "called": bool(c),
                                  "right": None if not c else (c[0] == "D") == (truth[st] > 0),
                                  "share": c[1] if c else None, "elapsed": c[2] if c else None})
    finally:
        if saved is None:
            M._CACHE.pop("rows", None)
        else:
            M._CACHE["rows"] = saved
    return {"rows": recs, "calls": calls, "recorded": replayed}


# ------------------------------------------------------------------ races known only by their totals (House seats)
OFFICE = {"US Senate": "SEN", "Governor": "GOV", "US House": None}
# Where field.py's old rule put a rating before votes (the middle of its band), in points, D side +.
OLD_RATING_M = {3.0: 22.5, 2.0: 11.5, 1.0: 5.5, 0.5: 2.0, 0.0: 0.0}


def past_ratings() -> dict[str, float]:
    """{race id: the raters' average level} from history/ratings.csv, each rater's latest rating."""
    latest: dict[tuple, tuple] = {}
    try:
        with open(HERE / "history" / "ratings.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                key = (r["race"], r["rater"])
                if not r["level"].strip():          # a rating the table couldn't place on the scale
                    continue
                if key not in latest or r["as_of"] >= latest[key][0]:
                    latest[key] = (r["as_of"], float(r["level"]))
    except OSError:
        return {}
    by: dict[str, list] = {}
    for (race, _rater), (_d, lv) in latest.items():
        by.setdefault(race, []).append(lv)
    return {race: math.floor(sum(v) / len(v) * 2 + 0.5) / 2 for race, v in by.items()}


def totals_test(years=(2018, 2020, 2022, 2024), nights: int = 12, seed: int = 7, steps: int = 24,
                hp: dict | None = None) -> dict:
    """The model for races known only by their running total (cw_forecast.from_totals), against the board's old rule
    (rating x share of precincts not in + count x share in), on the same simulated nights as the county model. Each
    state's race of that year stands in for a House seat: the rating it had beforehand is its prior, and all either
    rule sees is the total and the share of precincts in. Races without a rating on file are left out.

    One thing to know: the ratings' historical spread (history/error_model.json) was fitted on 2000-2024, these races
    included, so the prior is not out of sample. The count's side (LEAN, FLOOR) was not fitted on these nights."""
    ratings, model = past_ratings(), F.pre_model()
    rows_out, calls = [], []
    for year in years:
        year = _year(year)
        rows, finals, _polls = election(year)
        races = {}
        for st in rows:
            label = "PRES" if year % 4 == 0 else OFFICE.get(_race_name(st, year))
            lv = ratings.get(f"{year}-{st}-{label}") if label else None
            if lv is not None:
                races[st] = lv
        for nt in range(nights):
            rng = random.Random(seed * 1000 + _seed(year) * 10 + nt)
            plan = night(rows, finals, rng)
            streak: dict[str, tuple] = {}
            called: dict[str, str] = {}
            for i in range(steps + 1):
                t = 1.5 * i / steps
                for st, lv in races.items():
                    fin = finals[st]
                    truth = M.m2(sum(v["D"] for v in fin.values()), sum(v["R"] for v in fin.values()))
                    live = live_at(st, plan, finals, t)
                    d, r = sum(v["D"] for v in live.values()), sum(v["R"] for v in live.values())
                    n_all = sum(v["D"] + v["R"] for v in fin.values())
                    # a results file's share of precincts in, statewide: counties not yet in count as 0
                    if plan[st]["precincts"]:
                        f = sum((live.get(fp, {}).get("pct_in") or 0) * (v["D"] + v["R"]) for fp, v in fin.items()) / n_all
                    else:
                        f = min(1.0, (d + r) / (n_all * plan[st]["guess"]))
                    counted = M.m2(d, r) if d + r else None
                    side = 1 if lv >= 0 else -1
                    old_prior = side * OLD_RATING_M[min(OLD_RATING_M, key=lambda k: abs(k - abs(lv)))] / 100
                    w = f if plan[st]["precincts"] else (0.5 if d + r else 0.0)
                    old = old_prior * (1 - w) + (counted or 0) * w if counted is not None else old_prior
                    pm, psd = F.level_prior(lv, model) or (old_prior, 0.08)
                    q = F.from_totals(pm, psd, counted, f, d + r, hp)
                    lo, hi = q["proj_m"] - Z90 * q["sd"], q["proj_m"] + Z90 * q["sd"]
                    rows_out.append({"year": year, "st": st, "share": (d + r) / n_all, "truth": truth, "old": old,
                                     "new": q["proj_m"], "new_in": lo <= truth <= hi, "sd": q["sd"], "wp": q["win_prob"]})
                    s = call_side(q)
                    streak[st] = (s, streak[st][1] + 1) if s and streak.get(st, (None, 0))[0] == s else (s, 1 if s else 0)
                    if s and st not in called and streak[st][1] >= 2:
                        called[st] = s
            for st, lv in races.items():
                fin = finals[st]
                truth = M.m2(sum(v["D"] for v in fin.values()), sum(v["R"] for v in fin.values()))
                calls.append({"year": year, "st": st, "called": st in called,
                              "right": None if st not in called else (called[st] == "D") == (truth > 0)})
    return {"rows": rows_out, "calls": calls}


def show_totals(res: dict):
    """totals_test as a table: the old rule's miss and the new one's, by how much was counted."""
    rows = res["rows"]
    print(f"\nRaces known only by their totals: {len({(r['year'], r['st']) for r in rows})} past statewide races, "
          "rating as prior. Miss in points; 'wrong' = the wrong side ahead.")
    print(f"  {'counted':>11}  {'n':>5}  {'old miss':>8}  {'new miss':>8}  {'old wrong':>9}  {'new wrong':>9}  {'in 90%':>6}")
    for lo, hi, name in BINS + [(0.995, 9.0, "all in")]:
        b = [r for r in rows if (r["share"] == 0 if lo < 0 else lo < r["share"] <= hi)]
        if not b:
            continue
        n = len(b)
        mean = lambda key: sum(abs(r[key] - r["truth"]) for r in b) / n * 100          # noqa: E731
        wrong = lambda key: sum((r[key] > 0) != (r["truth"] > 0) for r in b) / n          # noqa: E731
        print(f"  {name:>11}  {n:5d}  {mean('old'):8.2f}  {mean('new'):8.2f}  {wrong('old'):9.1%}  {wrong('new'):9.1%}  "
              f"{sum(r['new_in'] for r in b) / n:6.0%}")


def _race_name(st: str, year: int) -> str:
    """The race baseline.csv holds for a state in a midterm year: "US Senate", "Governor" or "US House"."""
    col = "race22" if year == 2022 else "race18"
    with open(M.CW_DIR / "baseline.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["state"] == st:
                return r.get(col) or ""
    return ""


Z90 = 1.645
CALL_AT = AUTO.CALL_AT                  # the win chance The Bot calls at, from autocaller.py
CALL_MIN_WEIGHT = AUTO.MIN_WEIGHT       # and only once results, not the prior, carry at least this much of the forecast


def call_side(q: dict) -> str | None:
    """The side the forecast would call a state for right now, or None."""
    if q.get("stage") != "live" or q.get("data_weight", 0) < CALL_MIN_WEIGHT:
        return None
    wp = q["win_prob"]
    return "D" if wp >= CALL_AT else "R" if wp <= 1 - CALL_AT else None


def table(recs: list[dict], years=None) -> list[dict]:
    out = []
    for lo, hi, name in BINS:
        g = [r for r in recs if lo < r["share"] <= hi and (years is None or r["year"] in years)]
        if not g:
            continue
        cov_old = [r["old_in"] for r in g if r["old_in"] is not None]
        out.append({"bin": name, "n": len(g),
                    "old_miss": 100 * sum(abs(r["old"] - r["truth"]) for r in g) / len(g),
                    "new_miss": 100 * sum(abs(r["new"] - r["truth"]) for r in g) / len(g),
                    "old_rmse": 100 * (sum((r["old"] - r["truth"]) ** 2 for r in g) / len(g)) ** 0.5,
                    "new_rmse": 100 * (sum((r["new"] - r["truth"]) ** 2 for r in g) / len(g)) ** 0.5,
                    "old_in": sum(cov_old) / len(cov_old) if cov_old else None, "old_has_range": len(cov_old) / len(g),
                    "new_in": sum(r["new_in"] for r in g) / len(g),
                    "old_wrong": sum((r["old"] > 0) != (r["truth"] > 0) for r in g) / len(g),
                    "new_wrong": sum((r["new"] > 0) != (r["truth"] > 0) for r in g) / len(g),
                    "brier": sum((r["wp"] - (r["truth"] > 0)) ** 2 for r in g) / len(g),
                    "width": 100 * 2 * F.Z90 * sum(r["sd"] for r in g) / len(g)})
    return out


def call_summary(calls: list[dict], years=None) -> dict:
    g = [c for c in calls if years is None or c["year"] in years]
    made = [c for c in g if c["called"]]
    shares = sorted(c["share"] for c in made)
    return {"races": len(g), "called": len(made), "wrong": sum(1 for c in made if not c["right"]),
            "median_share": shares[len(shares) // 2] if shares else None,
            "close_races": sum(1 for c in g if abs(c["truth"]) < 0.03),
            "close_called": sum(1 for c in made if abs(c["truth"]) < 0.03)}


def show(res: dict, years) -> None:
    whole = "all three elections" if len(years) == 3 else f"all {len(years)} elections"
    for ys, label in ([(None, whole)] if len(years) > 1 else []) + [((y,), str(y)) for y in years]:
        print(f"\n{label}")
        print(f"  {'vote counted':<13}{'n':>6} | {'miss, pts':^17} | {'in 90% range':^17} | {'wrong side':^15} | {'brier':>6} {'range':>7}")
        print(f"  {'':<13}{'':>6} | {'before':>8}{'now':>8}  | {'before':>8}{'now':>8}  | {'before':>7}{'now':>7}  |")
        for r in table(res["rows"], ys):
            oi = f"{r['old_in']:.0%}*" if r["old_in"] is not None and r["old_has_range"] < 0.98 else f"{r['old_in']:.0%}" if r["old_in"] is not None else "none"
            print(f"  {r['bin']:<13}{r['n']:>6} | {r['old_miss']:>8.2f}{r['new_miss']:>8.2f}  | {oi:>8}{r['new_in']:>8.0%}  | "
                  f"{r['old_wrong']:>7.1%}{r['new_wrong']:>7.1%}  | {r['brier']:>6.3f} {r['width']:>6.1f}")
        c = call_summary(res["calls"], ys)
        ms = f"{c['median_share']:.0%}" if c["median_share"] is not None else "n/a"
        print(f"  calls: {c['called']} of {c['races']} races called, {c['wrong']} wrong; half were called with under {ms} of the vote "
              f"counted; {c['close_called']} of the {c['close_races']} races inside 3 points were called")
    print("\n  * project() alone has no range until a county finishes; its figure covers only the moments it had one.")


# ------------------------------------------------------------------ does the early vote help? do past polls?
EARLY_STAGES = (("nothing in", -1, 0.0), ("up to 15% counted", 0.0, 0.15), ("15% to 65% counted", 0.15, 0.65))


def _miss(recs: list[dict], states, lo: float, hi: float) -> tuple[float, float, int] | None:
    g = [r for r in recs if r["st"] in states and lo < r["share"] <= hi]
    if not g:
        return None
    return 100 * sum(abs(r["new"] - r["truth"]) for r in g) / len(g), sum(r["new_in"] for r in g) / len(g), len(g)


def early_report(nights: int = 12, seed: int = 7) -> dict | None:
    """Whether each use of the early vote helped, on the one election that has early vote on file (the active
    pack's own). None when there is nothing to test with. Each use is switched on alone and set against the same
    nights with all three off."""
    year = live_year()
    files = early_files(year)
    try:
        rows, finals, _polls = election(year)
    except (OSError, ValueError, KeyError):
        return None
    if not files:
        return None
    off = {"EV_COUNTY_TURNOUT": False, "EV_STATE_TURNOUT": False, "EV_REG": False}
    every = F.early_inputs(files, hp={**off, "EV_COUNTY_TURNOUT": True, "EV_STATE_TURNOUT": True})
    actual = {st: sum(v["D"] + v["R"] for v in finals[st].values()) / sum(c["w"] for c in rows[st]) for st in rows}
    out = {"year": year, "uses": []}

    # how close each starting turnout was, before any forecast is run
    st_states = [st for st in rows if every.get(st, {}).get("turnout")]
    flat = lambda sts: 100 * sum(abs(1 - actual[st]) for st in sts) / len(sts) if sts else None           # noqa: E731
    turnout_err = {"states": len(st_states), "flat": flat(st_states),
                   "early": 100 * sum(abs(every[st]["turnout"] - actual[st]) for st in st_states) / len(st_states) if st_states else None}
    county_err = None
    for st in rows:
        cf = every.get(st, {}).get("counties")
        if cf:
            base = {c["fips"]: c for c in rows[st]}
            got = [(base[f]["w"], (v["D"] + v["R"]) / base[f]["w"], cf.get(f, 1.0)) for f, v in finals[st].items() if f in base and base[f]["w"]]
            w = sum(x[0] for x in got)
            county_err = {"state": st, "counties": len(got), "flat": 100 * sum(a * abs(1 - b) for a, b, _c in got) / w,
                          "early": 100 * sum(a * abs(c - b) for a, b, c in got) / w}

    base_run = run((year,), nights, seed, hp=off, snapshots=False)
    for key, name, states, err in (
            ("EV_COUNTY_TURNOUT", "county turnout from early vote by county", [st for st in rows if every.get(st, {}).get("counties")], county_err),
            ("EV_STATE_TURNOUT", "state turnout from early vote totals", st_states, turnout_err),
            ("EV_REG", "party registration of early voters, in the prior",
             [st for st in rows if every.get(st, {}).get("reg_shift") is not None and st not in F.NOT_COMPARABLE], None)):
        use = {"key": key, "name": name, "states": sorted(states), "on_by_default": bool(F.HP[key]), "start": err, "stages": []}
        if states:
            with_it = run((year,), nights, seed, hp={**off, key: True}, snapshots=False)
            for label, lo, hi in EARLY_STAGES:
                a, b = _miss(base_run["rows"], states, lo, hi), _miss(with_it["rows"], states, lo, hi)
                if a and b:
                    use["stages"].append({"stage": label, "n": a[2], "miss_without": a[0], "miss_with": b[0],
                                          "in_range_without": a[1], "in_range_with": b[1]})
            before = sum(x["miss_without"] for x in use["stages"])
            use["helps"] = bool(use["stages"]) and sum(x["miss_with"] for x in use["stages"]) < before - 0.05
        out["uses"].append(use)
    return out


def show_early(rep: dict | None) -> None:
    print("\nearly vote: does each use of it help?")
    if not rep:
        print("  Not tested: this copy has no early vote file (countywatch/early.json) or no final results to score against.")
        return
    print(f"  Tested on {rep['year']} only. No early vote from 2022 or 2024 is in the repo, so those years can't say.")
    for use in rep["uses"]:
        verdict = "no states to test it on" if not use["states"] else "helps" if use.get("helps") else "does not help"
        print(f"\n  {use['name']}: {verdict}; {'ON' if use['on_by_default'] else 'OFF'} by default ({use['key']})")
        if use["states"]:
            print(f"    states: {', '.join(use['states'])}")
        e = use["start"]
        if e and e.get("flat") is not None:
            what = f"{e['counties']} {e['state']} counties" if "counties" in e else f"{e['states']} states"
            print(f"    starting turnout, average miss across {what}: {e['flat']:.1f}% flat at 2022's level, {e['early']:.1f}% from the early vote")
        for x in use["stages"]:
            print(f"    {x['stage']:<19} miss {x['miss_without']:.2f} -> {x['miss_with']:.2f} pts · in the 90% range {x['in_range_without']:.0%} -> {x['in_range_with']:.0%}  (n={x['n']})")


def polls_report(nights: int = 12, seed: int = 7) -> dict:
    """2022 and 2024 with and without their final polling averages, on the states that have one."""
    out = {}
    for entry in backtests():
        if entry.get("result") == "live":
            continue
        year = _year(entry["name"])
        have = sorted(past_polls(entry.get("polls", entry["name"])))
        if not have:
            out[year] = {"states": [], "stages": []}
            continue
        a = run((year,), nights, seed, use_polls=False, snapshots=False)
        b = run((year,), nights, seed, use_polls=True, snapshots=False)
        stages = []
        for label, lo, hi in EARLY_STAGES:
            x, y = _miss(a["rows"], have, lo, hi), _miss(b["rows"], have, lo, hi)
            if x and y:
                stages.append({"stage": label, "n": x[2], "miss_without": x[0], "miss_with": y[0],
                               "in_range_without": x[1], "in_range_with": y[1]})
        out[year] = {"states": have, "stages": stages}
    return out


def show_polls(rep: dict) -> None:
    print("\npast polls: 2022 and 2024 with their final polling averages (countywatch/polls_history.json)")
    for year, r in rep.items():
        if not r["states"]:
            print(f"  {year}: no polling averages on file, so the poll prior can't be tested on it")
            continue
        print(f"  {year}, {len(r['states'])} states with an average ({', '.join(r['states'])}):")
        for x in r["stages"]:
            print(f"    {x['stage']:<19} miss {x['miss_without']:.2f} -> {x['miss_with']:.2f} pts · in the 90% range {x['in_range_without']:.0%} -> {x['in_range_with']:.0%}  (n={x['n']})")


# ------------------------------------------------------------------ elections unlike the ones it was set on
# The settings were chosen on three statewide partisan generals. These runs ask what happens when that is the
# wrong picture: the starting guess for how far places stray is deliberately wrong, the features are taken away
# (a pack that ships none), or the places really are noisier (roughen). A forecast that only works with its fitted
# numbers fails these; one that reads the night for itself should still hold its 90% range about 90% of the time.
STRESS = (("as set", {}, None, 0.0),
          ("starting noise doubled", {"SE": 2.0}, None, 0.0),
          ("starting noise halved", {"SE": 0.5}, None, 0.0),
          ("no features", {}, [("nothing", None)], 0.0),
          ("places 6 pts noisier", {}, None, 0.06),
          ("6 pts noisier, no features", {}, [("nothing", None)], 0.06))


def _stress_score(res: dict) -> dict:
    late = [r for r in res["rows"] if 0.10 < r["share"] <= 0.995]
    early = [r for r in res["rows"] if r["share"] <= 0.10]
    calls = call_summary(res["calls"])
    return {"miss": 100 * sum(abs(r["new"] - r["truth"]) for r in late) / len(late),
            "in_range": sum(r["new_in"] for r in late) / len(late),
            "in_range_early": sum(r["new_in"] for r in early) / len(early) if early else None,
            "width": 100 * 2 * F.Z90 * sum(r["sd"] for r in late) / len(late),
            "called": calls["called"], "races": calls["races"], "wrong": calls["wrong"]}


def stress_report(nights: int = 6, seed: int = 7, years=None) -> list[dict]:
    """Each stress case with the forecast learning the night's noise (LEARN) off and on."""
    out = []
    keep = list(F.PACK_FEATS)
    try:
        for name, scale, feats, rough in STRESS:
            row = {"case": name}
            for label, learn in (("fixed", False), ("learning", True)):
                F.PACK_FEATS[:] = feats if feats is not None else keep
                hp = {"LEARN": learn, **{k: F.settings()[k] * v for k, v in scale.items()}}
                row[label] = _stress_score(run(years, nights, seed, hp=hp, snapshots=False, rough=rough))
            out.append(row)
    finally:
        F.PACK_FEATS[:] = keep
    return out


def show_stress(rows: list[dict]) -> None:
    print("\nstress: elections unlike the ones the settings were chosen on (scored with over 10% of the vote counted)")
    print(f"  {'':<28} | {'settings fixed':^37} | {'learning the night':^37}")
    print(f"  {'case':<28} | {'miss':>6} {'in range':>9} {'width':>6} {'wrong calls':>12} | {'miss':>6} {'in range':>9} {'width':>6} {'wrong calls':>12}")
    for r in rows:
        cell = lambda x: f"{x['miss']:>6.2f} {x['in_range']:>9.0%} {x['width']:>6.1f} {str(x['wrong']) + ' of ' + str(x['called']):>12}"      # noqa: E731
        print(f"  {r['case']:<28} | {cell(r['fixed'])} | {cell(r['learning'])}")
    print("  in range: how often the 90% range held the final margin (should be about 90%). width: of that range, in points.")


# ------------------------------------------------------------------ more than two candidates
# No real many-candidate election is in the data yet, so this makes some up. Each of the pack's states stands in
# for a city and its counties for wards. Four candidates are given support that follows what places are really
# like (their demographics and past vote, with strengths drawn afresh for every city), plus noise of their own.
# The night is then simulated as above: small places first, early counts leaning one way. It tests the arithmetic
# of cw_multi and how honest its ranges and chances are. It cannot say how a real city behaves.
MULTI_KEYS = ["A", "B", "C", "D"]


def made_up_city(places: list[dict], rng: random.Random) -> dict:
    """{fips: {candidate: votes}} for one invented race over real places."""
    feat = lambda c: [(c.get("black") or 0) - 0.2, (c.get("ba") or 0) - 0.25, c.get("m24") or 0.0,       # noqa: E731
                      math.log10(max(c.get("pop") or 0, 1000)) - 4.5]
    level = {"A": rng.gauss(0.3, 0.3), "B": rng.gauss(0.2, 0.3), "C": rng.gauss(-0.2, 0.3), "D": rng.gauss(-1.2, 0.4)}
    pull = {k: [rng.gauss(0, s) for s in (3.0, 3.0, 1.5, 0.4)] for k in MULTI_KEYS}
    turnout = math.exp(rng.gauss(0, 0.1))
    out = {}
    for c in places:
        u = {k: level[k] + sum(a * b for a, b in zip(pull[k], feat(c))) + rng.gauss(0, 0.25) for k in MULTI_KEYS}
        z = sum(math.exp(v) for v in u.values())
        n = max(40, round(c["w"] * turnout * math.exp(rng.gauss(0, 0.1))))
        out[c["fips"]] = {k: round(n * math.exp(u[k]) / z) for k in MULTI_KEYS}
    return out


def multi_report(cities: int = 3, seed: int = 7, steps: int = 12) -> dict:
    """Score cw_multi over made-up cities (`cities` for each of the pack's states): how close each candidate's
    forecast share was, against simply reading the count; how often the 90% range held; and how good the chances
    of finishing first and of a runoff were (Brier: 0 is perfect, 0.25 is a coin)."""
    import cw_multi as MU
    rows = {st: cs for st, cs in M.load().items() if st not in SKIP and len(cs) >= 40}
    recs, calls = [], {"made": 0, "wrong": 0, "races": 0}
    for st, places in rows.items():
        for n in range(cities):
            rng = random.Random(seed * 100003 + sum(map(ord, st)) * 31 + n)
            truth = made_up_city(places, rng)
            tot = {k: sum(v[k] for v in truth.values()) for k in MULTI_KEYS}
            everyone = sum(tot.values())
            final = {k: tot[k] / everyone for k in MULTI_KEYS}
            winner, runoff = max(final, key=final.get), max(final.values()) <= 0.5
            poll = {k: max(0.02, final[k] + rng.gauss(0, 0.04)) for k in MULTI_KEYS}       # a poll 4 points off, give or take
            plan = night({st: places}, {st: truth}, rng)[st]
            lean = {k: rng.gauss(0, 0.05) for k in MULTI_KEYS}
            called = None
            calls["races"] += 1
            for i in range(steps + 1):
                t = plan["start"] + 1.05 * i / steps
                live = {}
                for fips, c in plan["counties"].items():
                    if t < c["first"]:
                        continue
                    f = 1.0 if t >= c["done"] else max(1, math.floor((t - c["first"]) / (c["done"] - c["first"]) * c["steps"]) + 1) / (c["steps"] + 1)
                    v = {k: max(0, round(truth[fips][k] * f * (1 + (lean[k] + c["lean"] / 3) * (1 - f) * (3 if k == "A" else 1)))) for k in MULTI_KEYS}
                    live[fips] = {"v": v if f < 1 else truth[fips], "pct_in": (1.0 if f >= 1 else precincts_in(plan, c, f)) if plan["precincts"] else None,
                                  "idle": 0.0 if f < 1 else (t - c["done"]) * NIGHT_MINUTES}
                fc = MU.forecast(st, live, MULTI_KEYS, polls=poll, places=places, draws=1500, seed=n)
                share = fc["counted"] / everyone
                for k in MULTI_KEYS:
                    o = fc["options"][k]
                    naive = o["counted_share"] if o["counted_share"] is not None else poll[k] / sum(poll.values())
                    recs.append({"share": share, "miss": abs(o["share"] - final[k]), "naive": abs(naive - final[k]),
                                 "in": o["lo"] <= final[k] <= o["hi"], "first": (o["first"] - (k == winner)) ** 2,
                                 "runoff": (fc["runoff"] - runoff) ** 2, "width": o["hi"] - o["lo"]})
                say = MU.settled(fc)["first"]
                if say and not called:
                    called = say
                    calls["made"] += 1
                    calls["wrong"] += say != winner
    table = []
    for lo, hi, name in BINS:
        g = [r for r in recs if lo < r["share"] <= hi]
        if g:
            avg = lambda key: sum(r[key] for r in g) / len(g)        # noqa: E731
            table.append({"bin": name, "n": len(g), "miss": 100 * avg("miss"), "naive": 100 * avg("naive"), "in_range": avg("in"),
                          "first_brier": avg("first"), "runoff_brier": avg("runoff"), "width": 100 * avg("width")})
    return {"table": table, "calls": calls, "cities": calls["races"]}


def show_multi(rep: dict) -> None:
    print(f"\nmore than two candidates: {rep['cities']} made-up four-candidate races over the pack's real places")
    print(f"  {'vote counted':<13}{'n':>6} | {'share miss, pts':^19} | {'in 90% range':>12} | {'first, brier':>12} {'runoff, brier':>14} {'range':>7}")
    print(f"  {'':<13}{'':>6} | {'the count':>9}{'forecast':>10} |")
    for r in rep["table"]:
        print(f"  {r['bin']:<13}{r['n']:>6} | {r['naive']:>9.2f}{r['miss']:>10.2f} | {r['in_range']:>12.0%} | {r['first_brier']:>12.3f} {r['runoff_brier']:>14.3f} {r['width']:>6.1f}")
    c = rep["calls"]
    print(f"  first place called in {c['made']} of {c['races']} races before the count ended, {c['wrong']} wrong")
    print("  the count: reading each candidate's share straight off the votes counted so far (the poll, before any).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--nights", type=int, default=12)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--years", help="which of the pack's backtests to run, by name (default: all of them)")
    ap.add_argument("--pack", help="replay this data pack's elections instead of the default pack's")
    ap.add_argument("--no-polls", action="store_true", help="run every election without its polls")
    ap.add_argument("--simulate", action="store_true", help="simulate every election, even one with a recorded night")
    ap.add_argument("--snapshots", type=Path, help="replay the snapshot logs in this folder (default: the active pack's)")
    ap.add_argument("--no-extras", action="store_true", help="skip the early vote and past polls sections")
    ap.add_argument("--stress", action="store_true", help="only the stress tests: wrong settings, no features, noisier places")
    ap.add_argument("--multi", action="store_true", help="only the many-candidate test, on made-up cities")
    ap.add_argument("--totals", action="store_true", help="only the test of races known by their totals (House seats)")
    ap.add_argument("--json", help="write the tables to this file")
    a = ap.parse_args()
    if a.pack:
        packs.activate(a.pack)
    yrs = tuple(_year(y.strip()) for y in a.years.split(",")) if a.years else all_years()
    if a.stress:
        show_stress(stress_report(min(a.nights, 6), a.seed, yrs))
        raise SystemExit(0)
    if a.multi:
        show_multi(multi_report(min(a.nights, 6), a.seed))
        raise SystemExit(0)
    if a.totals:
        show_totals(totals_test(nights=min(a.nights, 6), seed=a.seed))
        raise SystemExit(0)
    result = run(yrs, a.nights, a.seed, use_polls=not a.no_polls, snapshots=False if a.simulate else a.snapshots)
    show(result, yrs)
    for y, sts in result["recorded"].items():
        print(f"\n  {y} was replayed as recorded (snapshot logs for {', '.join(sts)}), not simulated.")
    early_rep = polls_rep = None
    if not a.no_extras:
        early_rep, polls_rep = early_report(a.nights, a.seed), polls_report(a.nights, a.seed)
        show_early(early_rep)
        show_polls(polls_rep)
    if a.json:
        Path(a.json).write_text(json.dumps({
            "nights": a.nights, "years": list(yrs),
            "all": table(result["rows"]), "by_year": {str(y): table(result["rows"], (y,)) for y in yrs},
            "calls": {"all": call_summary(result["calls"]), **{str(y): call_summary(result["calls"], (y,)) for y in yrs}},
            "recorded": {str(y): sts for y, sts in result["recorded"].items()},
            "early_vote": early_rep, "past_polls": {str(y): r for y, r in (polls_rep or {}).items()}},
            indent=1), encoding="utf-8")
