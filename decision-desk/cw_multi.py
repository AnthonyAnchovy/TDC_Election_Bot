"""County Watch for a race with more than two candidates: each one's share, and what the race turns on (no Discord).

cw_forecast answers one question: how far ahead will one side finish against the other? A mayoral first round, a
primary or a ballot with four names asks different ones. Who finishes first? Does anyone clear 50%, or is there a
runoff? Who makes the top two?

How it works, in three steps:

  1. Each candidate is forecast alone, as "this candidate against everyone else", with the same model the
     two-sided races use: places compared by what they're like, early counts discounted, the night's noise learned
     (cw_forecast.forecast). That reuses every piece that is already tested.
  2. The shares are made to add up to 100.
  3. A few thousand possible finishes are drawn from those shares and their uncertainty. In each one no candidate
     can end with fewer votes than they already have. The chances are how often each thing happened in the draws.

Where a candidate starts. Nobody has run as these candidates before, so there is no past result to measure a swing
from. Each place starts every candidate at the same share: their poll number if there is one, an even split if
not. What tells the places apart is their features (the pack's "features": demographics, and how each place voted
in past contests), and those are learned tonight from the places that have reported. Before any place reports the
forecast is the poll with a wide range, and says so. A caller that knows more can pass a starting share per place
(`base`).

Rows. A place's count is {"v": {"A": 1200, "B": 900, "C": 400}, "pct_in": 0.4}, with "complete" and "idle" as in
the two-sided rows. The keys are the race's option keys from the pack.

What this is not: a count of ranked-choice rounds (Alaska, Maine, New York City primaries). It forecasts the
first round only.
"""
from __future__ import annotations

import random

import cw_forecast as CF
import cw_model as M

# Where the settings start for a race like this. They are deliberately loose: there is no election of this kind to
# fit them on yet, and with LEARN on the forecast reads the night's real noise for itself (see backtest.py --multi
# for how it does on made-up cities). A pack's own "forecast" settings still win over these.
HP = {
    "ALONE": True,     # one city: no other states to borrow a swing from
    "SP": 0.10,        # how far a poll may miss a candidate's two-way margin (5 points of share)
    "S0": 0.25,        # and how far a candidate may sit from an even split when there's no poll at all
    "SE": 0.06,        # how far a place strays from what its make-up predicts, to start with
    "SB_NAT": 0.15,    # tilts are the whole story in a race without parties, so they are left free to be large
    "SB_ST": 0.05,
}
CALL_AT = 0.995        # the chance at which a finish counts as settled, as for two-sided races (backtest.CALL_AT)


def total(row: dict | None) -> int:
    return sum((row or {}).get("v", {}).values())


def _two_sided(live: dict, key: str, against: str | None = None) -> dict:
    """The count as this candidate against everyone else (or against one rival, the rest set aside), in the shape
    cw_model.project reads."""
    out = {}
    for fips, row in live.items():
        v = row.get("v") or {}
        mine, everyone = int(v.get(key) or 0), int(sum(v.values()))
        theirs = int(v.get(against) or 0) if against else everyone - mine
        two = {"D": mine, "R": theirs, "O": everyone - mine - theirs, "pct_in": row.get("pct_in")}
        for extra in ("complete", "idle"):
            if row.get(extra) is not None:
                two[extra] = row[extra]
        out[fips] = two
    return out


def _start_rows(places: list[dict], share) -> list[dict]:
    """The places with this candidate's starting share as their baseline: one number everywhere, or one per place."""
    rows = []
    for c in places:
        s = share.get(c["fips"]) if isinstance(share, dict) else share
        s = min(0.98, max(0.02, 0.5 if s is None else s))
        rows.append({**c, "m24": 2 * s - 1, "d24": c["w"] * s, "r24": c["w"] * (1 - s), "t24": c["w"]})
    w = sum(r["w"] for r in rows) or 1.0
    mid = sum(r["m24"] * r["w"] for r in rows) / w
    for r in rows:
        r["breakeven"] = r["m24"] - mid
    return rows


def forecast(st: str, live: dict, keys: list[str], polls: dict[str, float] | None = None, base: dict | None = None,
             hp: dict | None = None, places: list[dict] | None = None, majority: float = 0.5,
             draws: int = 4000, seed: int = 2027) -> dict:
    """The forecast for one many-candidate race.

    st: the pack's place for the race ("CHI"). live: {fips: row} as described at the top. keys: the candidates.
    polls: {key: share} (any scale; they are made to add up to 1). base: {key: starting share, or {fips: share}}
    for a caller that knows more than a poll. majority: the share that wins outright.

    Returns {"options": {key: {"share", "lo", "hi", "sd", "counted", "counted_share", "first", "top_two",
    "outright"}}, "leader", "head_to_head" ({rival: {"ahead": the chance the leader finishes ahead of them}}),
    "outright" (the chance anyone clears `majority`), "runoff", "stage", "counted",
    "expected", "counted_share", "noise", "finished", "places"}."""
    places = M.load()[st] if places is None else places
    settings = {**HP, **CF.PACK_HP, **(hp or {})}
    polled = {k: float(v) for k, v in (polls or {}).items() if k in keys and v}
    if polled:
        scale = sum(polled.values())
        polled = {k: v / scale for k, v in polled.items()}
    even = 1.0 / max(len(keys), 1)
    per, expected, noise, stage, finished = {}, [], 1.0, "prior", 0
    for k in keys:
        start = (base or {}).get(k, polled.get(k, even))
        rows = _start_rows(places, start)
        p = M.project(st, _two_sided(live, k), rows=rows)
        q = CF.forecast({st: p}, {st: p["base_m"]} if (polled or base) else {}, settings)[st]
        per[k] = q
        expected.append(sum(r["proj_D"] + r["proj_R"] for r in q["rows"]))
        noise = max(noise, q.get("noise", 1.0))
        stage = "live" if q["stage"] == "live" else stage
        finished = max(finished, sum(1 for r in q["rows"] if r.get("fin", r["complete"])))
    counted = {k: sum(int((row.get("v") or {}).get(k) or 0) for row in live.values()) for k in keys}
    n_counted = sum(counted.values())
    n_expected = max(sum(expected) / len(expected) if expected else 0.0, float(n_counted), 1.0)
    raw = {k: max(0.0, (1 + per[k]["proj_m"]) / 2) for k in keys}
    add = sum(raw.values()) or 1.0
    share = {k: raw[k] / add for k in keys}
    sd = {k: per[k]["sd"] / 2 for k in keys}

    # the finishes that could still happen
    rng = random.Random(seed)
    first = dict.fromkeys(keys, 0)
    top_two = dict.fromkeys(keys, 0)
    alone = dict.fromkeys(keys, 0)
    took = {k: [] for k in keys}
    for _ in range(draws):
        wide = 3.0 if rng.random() < 0.03 else 1.0          # now and then a much bigger miss, as win_prob allows
        votes = {k: max(counted[k], (share[k] + wide * sd[k] * rng.gauss(0, 1)) * n_expected) for k in keys}
        tot = sum(votes.values()) or 1.0
        order = sorted(keys, key=lambda k: -votes[k])
        first[order[0]] += 1
        for k in order[:2]:
            top_two[k] += 1
        if votes[order[0]] / tot > majority:
            alone[order[0]] += 1
        for k in keys:
            took[k].append(votes[k] / tot)
    # The leader against each rival, head to head, with the two-sided forecast itself. The draws above treat each
    # candidate's miss as their own, but two rivals' misses are tied together (the county that turns out bigger
    # than expected helps one at the other's expense), so the draws are a little too sure about a close first
    # place. A call for first has to pass this too (settled).
    leader = max(keys, key=lambda k: share[k])
    head = {}
    for k in keys:
        if k == leader:
            continue
        mine, theirs = (polled or {}).get(leader, even), (polled or {}).get(k, even)
        rows = _start_rows(places, mine / (mine + theirs))
        both = {f: r for f, r in _two_sided(live, leader, k).items()}
        # the pair's votes are only part of each place's vote: scale what's expected to match
        part = (share[leader] + share[k]) or 1.0
        rows = [{**r, "w": r["w"] * part} for r in rows]
        p = M.project(st, both, rows=rows)
        q = CF.forecast({st: p}, {st: p["base_m"]} if (polled or base) else {}, settings)[st]
        head[k] = {"ahead": q["win_prob"], "data_weight": q.get("data_weight", 0.0)}
    options = {}
    for k in keys:
        xs = sorted(took[k])
        options[k] = {"share": share[k], "lo": xs[int(0.05 * (draws - 1))], "hi": xs[int(0.95 * (draws - 1))], "sd": sd[k],
                      "counted": counted[k], "counted_share": counted[k] / n_counted if n_counted else None,
                      "first": first[k] / draws, "top_two": top_two[k] / draws, "outright": alone[k] / draws,
                      "data_weight": per[k].get("data_weight", 0.0)}
    outright = sum(alone.values()) / draws
    return {"options": options, "leader": leader, "head_to_head": head, "outright": outright, "runoff": 1 - outright,
            "stage": stage, "counted": n_counted, "expected": n_expected, "counted_share": min(1.0, n_counted / n_expected),
            "noise": noise, "finished": finished, "places": len(places), "majority": majority}


def settled(fc: dict, min_weight: float = 0.5) -> dict:
    """What the forecast would call right now, by the two-sided races' rule: a chance of CALL_AT or better, with
    results (not the poll) carrying at least half of it. {"first": key or None, "top_two": [keys], "runoff": True,
    False or None}."""
    if fc["stage"] != "live":
        return {"first": None, "top_two": [], "runoff": None}
    sure = lambda k, what: fc["options"][k][what] >= CALL_AT and fc["options"][k]["data_weight"] >= min_weight    # noqa: E731
    weight = max(o["data_weight"] for o in fc["options"].values())
    lead = fc["leader"]
    beats_all = all(h["ahead"] >= CALL_AT and h["data_weight"] >= min_weight for h in fc.get("head_to_head", {}).values())
    return {"first": lead if sure(lead, "first") and beats_all else None,
            "top_two": [k for k in fc["options"] if sure(k, "top_two")],
            "runoff": None if weight < min_weight else True if fc["runoff"] >= CALL_AT else False if fc["outright"] >= CALL_AT else None}
