"""Alaska's uncounted vote, by vote type.

Alaska counts in stages, and its absentees come last (for up to 15 days), so a county-style "finished districts" read
doesn't work there: no district is finished on election night, and the votes still out vote differently from those in.
This sizes what's left in each state House district and how it should vote, from each vote type's 2024 numbers
(countywatch/ak_modes24.json, built by tools/make_ak_modes.py) and the swing seen so far:

  Election Day  precincts not in yet: their 2024 margin + the district's Election Day swing (precinct against itself,
                pulled toward the statewide swing until the district has a few thousand votes), at the turnout seen
                in the precincts that are in
  Early         2024 margin + the early-vote swing, up to 2024's early vote at that same turnout
  Absentee      2024 margin + the absentee swing once absentees are counted (Election Day's until then), up to
                ABSENTEE_RATIO of 2024's absentee vote
  Questioned    the same, with QUESTIONED_RATIO

Margins are two-party on first choices, like every other state. Ranked-choice rounds aren't modeled: Heikes (REP) sits
on the Republican side and Daniel J. Sullivan (no party) with "other".
"""
from __future__ import annotations

import json
from pathlib import Path

BASE_FILE = Path(__file__).parent / "countywatch" / "ak_modes24.json"
MODEL_MIN = 5_000           # Election Day votes in matched precincts before this says anything
ABSENTEE_RATIO = 0.70       # 2026 absentees / 2024's 74K: a midterm has fewer. Judgment call, near NYT's estimate of 52K
QUESTIONED_RATIO = 0.72     # the same for questioned ballots: 10K of 2024's 13.8K, also near NYT's estimate
K_DISTRICT = 2_000          # votes before a district's own swing outweighs the state's
K_TYPE = 5_000              # votes before a vote type's own swing outweighs Election Day's

_base: dict = {}


def base() -> dict:
    if not _base:
        _base.update(json.loads(BASE_FILE.read_text(encoding="utf-8")))
    return _base


def _m(x) -> float | None:
    return (x[0] - x[1]) / (x[0] + x[1]) if x[0] + x[1] else None


def _clamp(m: float) -> float:
    return max(-1.0, min(1.0, m))


def _type_swing(counted: dict, then: dict, prior: float) -> float:
    """A vote type's swing where it's been counted, pulled toward `prior` until there are enough votes to trust."""
    num = den = 0.0
    for d, now in counted.items():
        m0, m1 = _m(then.get(d, (0, 0))), _m(now)
        if m0 is not None and m1 is not None:
            v = sum(now)
            num, den = num + (m1 - m0) * v, den + v
    return (num + prior * K_TYPE) / (den + K_TYPE)


def still_out(ed: dict, modes: dict) -> dict:
    """{district: [votes still expected, net D votes among them]}, or {} until enough has been counted to say.
    ed: {precinct id: [D, R]} on Election Day. modes: {"early"/"abs"/"q": {district: [D, R]}}."""
    b = base()
    num = den = v24 = 0.0
    by_d: dict = {}
    for p, now in ed.items():
        then = b["ed"].get(p)
        if then and sum(now) > 0 and sum(then) > 0:
            s, v = _m(now) - _m(then), sum(now)
            num, den, v24 = num + s * v, den + v, v24 + sum(then)
            x = by_d.setdefault(p[:2].lstrip("0"), [0.0, 0.0])
            x[0], x[1] = x[0] + s * v, x[1] + v
    if den < MODEL_MIN:
        return {}
    ed_swing, pace = num / den, den / v24
    out: dict = {}

    def add(d, votes, margin):
        x = out.setdefault(d, [0.0, 0.0])
        x[0], x[1] = x[0] + votes, x[1] + votes * _clamp(margin)
    for p, then in b["ed"].items():
        if sum(then) > 0 and sum(ed.get(p) or (0, 0)) == 0:
            d = p[:2].lstrip("0")
            x = by_d.get(d, (0.0, 0.0))
            add(d, sum(then) * pace, _m(then) + (x[0] + ed_swing * K_DISTRICT) / (x[1] + K_DISTRICT))
    for kind, ratio in (("early", pace), ("abs", ABSENTEE_RATIO), ("q", QUESTIONED_RATIO)):
        now, then = modes.get(kind) or {}, b[kind]
        swing = _type_swing(now, then, ed_swing)
        for d, old in then.items():
            left = sum(old) * ratio - sum(now.get(d) or (0, 0))
            if left > 0 and _m(old) is not None:
                add(d, left, _m(old) + swing)
    return out
