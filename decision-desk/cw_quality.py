"""Which polls to trust: pollster ratings, a polling average that uses them, and how sure that average is (no Discord).

A count of polls says little on its own. In 2026 eight states rested on a single Times/Siena poll, and those missed by
0 points on average, while averages of up to twenty polls in the other states missed by about 6, all the same way.
What matters is who took the polls. Two ways of judging that are used here:

  track record   Silver Bulletin's way: how far a pollster's polls landed from the result, against other polls of
                 the same race (so a hard race doesn't count against everyone in it), shrunk toward the average
                 pollster until it has many polls behind it. Below 0 is better than average, in points.
  select         The New York Times' way: a pollster that meets two of three tests counts for more, as long as no
                 party or campaign paid for the poll. The tests: it samples by probability (live phone, a
                 probability panel or mail; not an opt-in online panel), it belongs to AAPOR's Transparency
                 Initiative or archives its data with the Roper Center, and it has a good track record.

How much each counts was chosen by replaying every statewide general from 2008 to 2024 with only what was known
before that election (python cw_quality.py): pollsters rated on earlier years only, the average then scored on the
year itself. A select pollster counts 3 times (SELECT 2) and the track record scales a poll by exp(-plus-minus / 2).
What it bought (Nov 2026):
  - races with 4 or more polls, 2008-2024: average miss 4.04 to 3.93 points; 2016-2024: 4.54 to 4.33. The gain is
    almost all in the miss a year's polls share: better pollsters shared less of it.
  - 2026, with Silver Bulletin's January 2026 ratings (published before the election): 12 Senate races, average miss
    4.56 to 4.11 points. The select rule did most of it (4.21 alone); the track record alone 4.44.
  - all races, 2008-2024: 4.60 to 4.55. Where a race has one or two polls there is nothing to choose between.
It does not touch the miss every poll shares in a year (2026: all twelve states understated Democrats, by 4.1 points
on average). That is the shared miss, sized in fit() and left for the night's results to find.
For the live bot, the ratings Silver Bulletin published in January 2026 are used where they cover a pollster
(history/silver_ratings.csv), with each pollster's method and AAPOR/Roper membership from the 538 archive.

Disagreement, measured the same way: races whose polls sat 6+ points apart missed by 5.7 points (root mean square,
after the year's shared miss), races whose polls agreed within 2 by 3.7. With it in the width, the 90% range held
91% of the time for disagreeing races on 2020-2024 (fitted on 2008-2018), against 88% without.

Data: the poll archive is FiveThirtyEight's (ABC News), CC BY 4.0; 2026's polls are from Wikipedia (CC BY-SA 4.0,
countywatch/polls_credit.txt); the ratings are Silver Bulletin's (credit Nate Silver / Silver Bulletin).

How sure an average is. Two things widen it beyond what the number of polls alone says:
  disagreement   polls of one race that differ a lot (Minnesota 2026: Siena +14, others +7) mean the race is hard
                 to poll, and the average misses by more
  weighting gap  a pollster that publishes its numbers two ways (Times/Siena 2026: its new weighting and its old
                 one, 3 to 5 points apart) is saying how much of the answer is its correction for who doesn't
                 respond. That gap is part of the doubt.

Margins here are in points, D side + (two-party), as in history/.
"""
from __future__ import annotations

import csv
import math
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARCHIVE = HERE / "history" / "polls_final.csv"
SILVER = HERE / "history" / "silver_ratings.csv"

HP = {
    "HALF_LIFE": 14,      # days until a poll counts for half as much
    "N_CAP": 1500,        # a poll of more people than this counts as this many
    "BLANK_N": 600,       # what a poll with no size counts as
    "SAME_POLLSTER": 0.25,  # each older poll from one pollster keeps this share of its weight
    "PARTISAN_WEIGHT": 2 / 3,
    "PARTISAN_SHIFT": 4.0,  # points a party- or campaign-paid poll is moved against its side
    "SHRINK": 15,         # a pollster's track record counts as this many average polls before its own
    "TAU": 0.0,           # weight x exp(-plus-minus / TAU): 0 switches the track record off (set by the replay)
    "SELECT": 0.0,        # a select pollster's polls count (1 + SELECT) times (set by the replay)
    "GOOD": 0.0,          # a track record below this (points) passes the select test's third part
    "MIN_RECORD": 10,     # polls a pollster needs before its record can pass that test
}


# ------------------------------------------------------------------ reading
def load(path: Path = ARCHIVE) -> list[dict]:
    """history/polls_final.csv, with numbers as numbers."""
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["year"], r["days_out"] = int(r["year"]), int(r["days_out"])
        r["margin"], r["result"] = float(r["margin"]), float(r["result"])
        r["n"] = float(r["n"]) if r["n"] not in ("", "NA") else None
        r["aapor_roper"] = r["aapor_roper"] in ("1", "True", "TRUE")
    return rows


def probability(method: str) -> bool:
    """Whether a methodology samples by probability: live phone, a probability panel or mail, alone or mixed in."""
    m = (method or "").lower()
    return any(k in m for k in ("live phone", "probability panel", "mail"))


def key(row: dict) -> str:
    """Who a poll belongs to, for rating: 538's pollster id where there is one, else the name."""
    return row.get("pollster_id") or row["pollster"]


# ------------------------------------------------------------------ track records
def track_records(polls: list[dict], hp: dict | None = None) -> dict[str, dict]:
    """{pollster: {"pm", "n", "aapor", "prob"}} from the polls given (all of them before some year).

    pm: the pollster's polls' error against the other pollsters' polls of the same race, averaged and shrunk toward
    the average pollster of its kind (AAPOR/Roper members and the rest are their own kinds) by SHRINK polls."""
    h = {**HP, **(hp or {})}
    by_race: dict[str, list[dict]] = defaultdict(list)
    for p in polls:
        by_race[p["race"]].append(p)
    rel: dict[str, list[float]] = defaultdict(list)
    info: dict[str, dict] = {}
    for race, ps in by_race.items():
        if len({key(p) for p in ps}) < 2:
            continue
        for p in ps:
            others = [abs(o["margin"] - o["result"]) for o in ps if key(o) != key(p)]
            rel[key(p)].append(abs(p["margin"] - p["result"]) - statistics.fmean(others))
    for p in polls:
        i = info.setdefault(key(p), {"aapor": False, "prob": 0, "polls": 0})
        i["aapor"] = i["aapor"] or p["aapor_roper"]
        i["prob"] += probability(p["methodology"])
        i["polls"] += 1
    kind_mean = {}
    for kind in (True, False):
        vals = [v for k, vs in rel.items() if info[k]["aapor"] == kind for v in vs]
        kind_mean[kind] = statistics.fmean(vals) if vals else 0.0
    out = {}
    for k, i in info.items():
        vs = rel.get(k, [])
        prior = kind_mean[i["aapor"]]
        out[k] = {"pm": (sum(vs) + h["SHRINK"] * prior) / (len(vs) + h["SHRINK"]), "n": len(vs),
                  "aapor": i["aapor"], "prob": i["prob"] / i["polls"] >= 0.5}
    return out


def is_select(rec: dict | None, poll: dict, hp: dict | None = None) -> bool:
    """The Times' rule: two of probability sampling, AAPOR/Roper, a good track record; never a partisan poll."""
    h = {**HP, **(hp or {})}
    if poll.get("partisan"):
        return False
    rec = rec or {}
    prob = probability(poll.get("methodology", "")) if poll.get("methodology") else rec.get("prob", False)
    aapor = poll.get("aapor_roper") or rec.get("aapor", False)
    good = rec.get("n", 0) >= h["MIN_RECORD"] and rec.get("pm", 1.0) < h["GOOD"]
    return prob + bool(aapor) + good >= 2


def quality(rec: dict | None, poll: dict, hp: dict | None = None) -> float:
    """How many times its usual weight a poll counts for, from its pollster's record and the select rule."""
    h = {**HP, **(hp or {})}
    q = 1.0
    if h["TAU"] and rec:
        q *= math.exp(-max(-3.0, min(3.0, rec["pm"])) / h["TAU"])
    if h["SELECT"] and is_select(rec, poll, h):
        q *= 1 + h["SELECT"]
    return q


# ------------------------------------------------------------------ the average
def average(polls: list[dict], records: dict | None = None, hp: dict | None = None) -> dict | None:
    """One race's final average. polls: that race's polls, each with margin, days_out, n, pollster(_id), partisan,
    methodology, aapor_roper. Returns {"margin", "n_eff", "spread", "q"} or None when there are none.
    spread: how far the polls sit from each other (weighted sd, points). q: the average poll's quality weight."""
    h = {**HP, **(hp or {})}
    if not polls:
        return None
    recent = sorted(polls, key=lambda p: p["days_out"])
    seen: dict[str, int] = defaultdict(int)
    ws, ms, qs = [], [], []
    for p in recent:
        k = key(p)
        w = 0.5 ** (p["days_out"] / h["HALF_LIFE"]) * math.sqrt(min(p["n"] or h["BLANK_N"], h["N_CAP"]))
        w *= h["SAME_POLLSTER"] ** min(seen[k], 1)
        seen[k] += 1
        m = p["margin"]
        if p.get("partisan") in ("DEM", "REP"):
            w *= h["PARTISAN_WEIGHT"]
            m += -h["PARTISAN_SHIFT"] if p["partisan"] == "DEM" else h["PARTISAN_SHIFT"]
        q = quality((records or {}).get(k), p, h)
        ws.append(w * q)
        ms.append(m)
        qs.append(q)
    tot = sum(ws)
    mean = sum(w * m for w, m in zip(ws, ms)) / tot
    n_eff = tot ** 2 / sum(w * w for w in ws)
    spread = math.sqrt(sum(w * (m - mean) ** 2 for w, m in zip(ws, ms)) / tot) if len(ms) > 1 else 0.0
    shares: dict[str, float] = defaultdict(float)
    for p, w in zip(recent, ws):
        shares[p["pollster"]] += w / tot
    return {"margin": mean, "n_eff": n_eff, "spread": spread, "q": sum(w * q for w, q in zip(ws, qs)) / tot,
            "shares": dict(shares)}


# ------------------------------------------------------------------ names and the live ratings
_DROP = {"the", "college", "university", "univ", "research", "polling", "poll", "polls", "group", "inc", "llc",
         "co", "associates", "strategies", "company", "institute", "center", "survey", "insights", "and", "of"}


def norm(name: str) -> str:
    """A pollster's name reduced so that 'The New York Times/Siena College' and 'New York Times / Siena' meet."""
    words = re.findall(r"[a-z0-9]+", (name or "").lower().replace("&", " and "))
    return " ".join(w for w in words if w not in _DROP)


def _parts(name: str) -> list[str]:
    """The name, then each partner of a joint poll ('NYT/Siena' -> both), reduced."""
    whole = norm(name)
    return [whole] + [norm(p) for p in re.split(r"[/;]| and | for ", name or "") if norm(p) and norm(p) != whole]


def silver(path: Path = SILVER) -> dict[str, dict]:
    """{reduced name: {"pm", "polls", "grade", "name"}} from Silver Bulletin's ratings (history/silver_ratings.csv)."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = [ln for ln in f if not ln.startswith("#")]
    except OSError:
        return {}
    out = {}
    for r in csv.DictReader(lines):
        out[norm(r["pollster"])] = {"pm": float(r["plus_minus"]), "polls": int(float(r["polls"] or 0)),
                                    "grade": r["grade"], "name": r["pollster"]}
    return out


def attributes(polls: list[dict] | None = None) -> dict[str, dict]:
    """{reduced name: {"aapor", "prob"}}: what the archive knows of each pollster's membership and method."""
    polls = load() if polls is None else polls
    out: dict[str, dict] = {}
    for p in polls:
        a = out.setdefault(norm(p["pollster"]), {"aapor": False, "prob_polls": 0, "polls": 0})
        a["aapor"] = a["aapor"] or p["aapor_roper"]
        a["prob_polls"] += probability(p["methodology"])
        a["polls"] += 1
    return {k: {"aapor": v["aapor"], "prob": v["prob_polls"] / v["polls"] >= 0.5} for k, v in out.items()}


def live_record(name: str, ratings: dict | None = None, attrs: dict | None = None) -> dict | None:
    """A pollster's record for a live election: Silver Bulletin's plus-minus where it rates the pollster (or, for a
    joint poll, the best-rated partner), with the archive's membership and method. None if nobody knows it."""
    ratings = silver() if ratings is None else ratings
    attrs = attributes() if attrs is None else attrs
    found = [ratings[p] for p in _parts(name) if p in ratings]
    known = [attrs[p] for p in _parts(name) if p in attrs]
    if not found and not known:
        return None
    best = min(found, key=lambda r: r["pm"]) if found else None
    return {"pm": best["pm"] if best else 0.0, "n": best["polls"] if best else 0,
            "aapor": any(k["aapor"] for k in known), "prob": any(k["prob"] for k in known),
            "rated_as": best["name"] if best else None}


def from_pack_rows(rows: list[dict], election_day: str, days: int = 21) -> dict[str, list[dict]]:
    """{race: polls} from a pack's polls.csv rows (cw_polls layout: one row per poll per option), D and R only,
    ending in the last `days` days, ready for average(). The sponsor column becomes "partisan" where it names a side."""
    import datetime as dt
    day = dt.date.fromisoformat(election_day)
    polls: dict[tuple, dict] = {}
    for r in rows:
        k = (r["race"], r["pollster"], r["start"], r["end"], r.get("population") or "")
        p = polls.setdefault(k, {"race": r["race"], "pollster": r["pollster"], "pollster_id": "",
                                 "end": r["end"], "n": float(r["n"]) if r.get("n") else None,
                                 "population": r.get("population") or "", "shares": {},
                                 "partisan": {"D": "DEM", "R": "REP"}.get((r.get("sponsor") or "").strip().upper(), ""),
                                 "methodology": "", "aapor_roper": False})
        p["shares"][r["option"]] = float(r["share"])
    out: dict[str, list[dict]] = defaultdict(list)
    best_pop: dict[tuple, int] = {}
    rank = {"lv": 3, "rv": 2, "a": 1, "": 0}
    for (race, who, start, end, pop), p in polls.items():
        best_pop[(race, who, start, end)] = max(best_pop.get((race, who, start, end), -1), rank.get(pop, 0))
    for (race, who, start, end, pop), p in polls.items():
        if rank.get(pop, 0) != best_pop[(race, who, start, end)] or not {"D", "R"} <= set(p["shares"]):
            continue
        d, r = p["shares"]["D"], p["shares"]["R"]
        left = (day - dt.date.fromisoformat(end)).days
        if d + r <= 0 or not 0 <= left <= days:
            continue
        out[race].append({**p, "margin": 100 * (d - r) / (d + r), "days_out": left})
    return dict(out)


def live_average(polls: list[dict], hp: dict | None = None, ratings: dict | None = None,
                 attrs: dict | None = None) -> dict | None:
    """average() for a live race, each pollster rated by live_record()."""
    records = {}
    for p in polls:
        rec = live_record(p["pollster"], ratings, attrs)
        if rec:
            records[key(p)] = rec
    return average(polls, records, hp)


# ------------------------------------------------------------------ the replay
def replay(polls: list[dict], years=range(2008, 2026, 2), hp: dict | None = None, min_polls: int = 1) -> list[dict]:
    """Every race of each year, averaged with pollsters rated on earlier years only. One row per race."""
    out = []
    for y in years:
        before = [p for p in polls if p["year"] < y]
        records = track_records(before, hp)
        by_race: dict[str, list[dict]] = defaultdict(list)
        for p in polls:
            if p["year"] == y:
                by_race[p["race"]].append(p)
        for race, ps in by_race.items():
            if len(ps) < min_polls:
                continue
            a = average(ps, records, hp)
            out.append({"race": race, "year": y, "office": ps[0]["office"], "result": ps[0]["result"],
                        "avg": a["margin"], "n_eff": a["n_eff"], "spread": a["spread"], "polls": len(ps)})
    return out


def score(rows: list[dict]) -> dict:
    """Average miss, and the miss once each year's shared miss is taken out (what a pollster choice can fix)."""
    errs = [r["result"] - r["avg"] for r in rows]
    by_year: dict[int, list[float]] = defaultdict(list)
    for r, e in zip(rows, errs):
        by_year[r["year"]].append(e)
    shared = {y: statistics.fmean(v) for y, v in by_year.items()}
    own = [e - shared[r["year"]] for r, e in zip(rows, errs)]
    return {"races": len(rows), "miss": statistics.fmean(abs(e) for e in errs),
            "rmse": math.sqrt(statistics.fmean(e * e for e in errs)),
            "own": statistics.fmean(abs(e) for e in own), "by_year": {y: statistics.fmean(abs(e) for e in v)
                                                                    for y, v in sorted(by_year.items())}}


FIT = HERE / "history" / "poll_quality.json"
CHOSEN = {"TAU": 2.0, "SELECT": 2.0}       # what the replay chose (see main)


def _own_errors(rows: list[dict]) -> tuple[list[dict], dict[int, float]]:
    by: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        by[r["year"]].append(r["result"] - r["avg"])
    shared = {y: statistics.fmean(v) for y, v in by.items()}
    for r in rows:
        r["own"] = r["result"] - r["avg"] - shared[r["year"]]
    return rows, shared


def fit_widths(rows: list[dict]) -> dict:
    """A race's own miss as sqrt(A^2 + B^2/n_eff + (K x spread)^2), fitted by maximum likelihood on a grid."""
    best = None
    for a2 in [x * 0.25 for x in range(0, 33)]:
        for b2 in [x * 2.0 for x in range(0, 61)]:
            for k2 in [x * 0.02 for x in range(0, 41)]:
                ll = 0.0
                for r in rows:
                    v = a2 + b2 / r["n_eff"] + k2 * r["spread"] ** 2
                    if v <= 0:
                        ll = float("inf")
                        break
                    ll += math.log(v) + r["own"] ** 2 / v
                if best is None or ll < best[0]:
                    best = (ll, a2, b2, k2)
    return {"A": math.sqrt(best[1]), "B": math.sqrt(best[2]), "K": math.sqrt(best[3]), "races": len(rows)}


def fit(polls: list[dict] | None = None, hp: dict | None = None) -> dict:
    """The widths (fit_widths) and the miss all polls share in a year, apart for midterms and presidential years:
    the root mean square of each year's average miss, less the part the races' own misses would put there by chance."""
    polls = load() if polls is None else polls
    hp = {**CHOSEN, **(hp or {})}
    rows, shared = _own_errors(replay(polls, range(2002, 2028, 2), hp))
    widths = fit_widths([r for r in rows if r["year"] >= 2008])
    out = {"hp": {k: hp[k] for k in ("TAU", "SELECT")}, "widths": widths, "years": {}, "shared": {}}
    for kind, odd in (("midterm", 2), ("presidential", 0)):
        ys = [y for y in shared if y % 4 == odd]
        noise = []
        for y in ys:
            own = [r["own"] for r in rows if r["year"] == y]
            noise.append(statistics.fmean(e * e for e in own) / len(own))
        ms = statistics.fmean(shared[y] ** 2 for y in ys)
        out["shared"][kind] = math.sqrt(max(ms - statistics.fmean(noise), 0.0))
    out["years"] = {str(y): round(v, 2) for y, v in sorted(shared.items())}
    out["fitted_on"] = sorted(shared)
    return out


def save_fit(path: Path = FIT) -> dict:
    import json
    f = fit()
    path.write_text(json.dumps(f, indent=1) + "\n", encoding="utf-8")
    return f


def load_fit(path: Path = FIT) -> dict | None:
    import json
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def own_sd(avg: dict, model: dict, gap: float = 0.0) -> float:
    """How far one race's average may sit from its result apart from the shared miss, in points. gap: a pollster's
    two published weightings' difference times that pollster's share of the average (see weighting_gap)."""
    w = model["widths"]
    return math.sqrt(w["A"] ** 2 + w["B"] ** 2 / max(avg["n_eff"], 1e-9) + (w["K"] * avg["spread"]) ** 2
                     + (gap / 2) ** 2)


def main() -> int:
    if "--fit" in sys.argv:
        f = save_fit()
        print(f"wrote {FIT}: widths A {f['widths']['A']:.2f} B {f['widths']['B']:.2f} K {f['widths']['K']:.2f}; "
              f"shared miss midterm {f['shared']['midterm']:.2f}, presidential {f['shared']['presidential']:.2f}")
        print("  each year's shared miss (result minus polls, points): " + ", ".join(f"{y}: {v:+.1f}" for y, v in f["years"].items()))
        return 0
    polls = load()
    print("Polling averages replayed on 2008-2024, pollsters rated on earlier years only. Miss in points.")
    print(f"  {'weighting':<34} {'races':>5} {'miss':>6} {'rmse':>6} {'own':>6}   by year")
    grid = [("plain (count, size, recency)", {})]
    grid += [(f"track record, TAU {t}", {"TAU": t}) for t in (0.5, 1.0, 2.0, 4.0)]
    grid += [(f"select x{1 + s:g}", {"SELECT": s}) for s in (0.5, 1.0, 2.0, 4.0)]
    grid += [(f"track record TAU {t} + select x{1 + s:g}", {"TAU": t, "SELECT": s}) for t in (1.0, 2.0) for s in (1.0, 2.0)]
    for name, hp in grid:
        s = score(replay(polls, hp=hp))
        years = " ".join(f"{y % 100:02d}:{v:.1f}" for y, v in s["by_year"].items())
        print(f"  {name:<34} {s['races']:>5} {s['miss']:>6.2f} {s['rmse']:>6.2f} {s['own']:>6.2f}   {years}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
