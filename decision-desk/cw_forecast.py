"""County Watch's forecast: every state's margin, how sure it is, and the chance each side wins (no Discord here).

cw_model.project() does the bookkeeping for one state: which counties have finished, how many votes each still
expects. This module sits on top of every state's projection at once and answers the question the night is about:
where will each race end, and how sure can we be right now?

What it does that project() alone doesn't, and why (each one is a lesson from election night 2026):

  1. It has an answer before a county finishes. Each state starts from a prior: its final poll (if it has one) and
     its baseline moved by the swing other states are showing. On the night, Georgia sat at its 2024 margin (R+1)
     until 8 PM while Kentucky's counties were already swinging 17 points; it ended D+18.
  2. States learn from each other. The polls' shared miss and the national swing are estimated from every state
     with results and applied to the states still waiting, each time leaving out the state being forecast.
  3. Swing depends on what a county is like, not which bucket it's in. A county's swing is its state's swing plus a
     tilt for race, education, size and its old margin, fitted across all states and pulled toward that national
     pattern in each one. The seven county types remain for reading; they no longer drive the numbers.
  4. A half-counted county is evidence, discounted. Its margin so far says something about where it will finish,
     the more so the more of it is in. project() ignored partial margins, so New Hampshire and Maine (which report
     by town, and never "finish" a county) had no projection until morning.
  5. A county's votes so far don't get mixed into its final margin at face value. The first votes out are usually
     one voting method. project() kept them and projected only the rest, which let the early vote's lean leak in:
     Georgia read D+23.5 at 8 PM and North Carolina D+22.6 at 9, both about 3 to 5 points too blue. Here the
     county's final margin is estimated whole, and the count so far only moves it as far as it has earned.
  6. Early vote is used where it has earned it (early_inputs). North Carolina publishes who voted early by county,
     so each county there starts from its early vote against the same day in 2022 instead of a flat 2022 turnout.
     Two other uses are built and switched off, because they didn't help on 2026 (backtest.py prints why): state
     early vote totals as a turnout start, and the change in early voters' party registration as a weak term in
     the prior.
  7. The range is fitted, not guessed. Its width comes from the model's own uncertainty, scaled across five replayed
     elections (backtest.py) so that the far tail, which a call rests on, holds. That leaves the 90% range a little
     wide in calm years, on purpose: fitted on 2018-2024 alone (CAL 0.8, OUT_CAL 0.6) the range held 90% in 2026,
     which that fit never saw, but missed far twice as often as it should and made one wrong call in 20 replays.
     The wider settings below made none, and cost about one call in 500 in the other four years (Nov 2026).

All margins are two-party fractions with the Democratic side positive, as everywhere in County Watch.
"""
from __future__ import annotations

import json
import math

import cw_early as E
import cw_model as M
import feeds as FEEDS
import packs

# Set with backtest.py. The numbers that move the estimate (SE, SL, SG, F_MIN, the tilts) were chosen on 2022 and
# 2024, both rebuilt from 2020's baseline, and checked on 2026's real county results. The numbers that set the width
# of the range (SP, SD0, SS, SG0, TAU, STC, CAL) were set so the 90% range holds in all three, 2026's 15-point swing
# included, which leaves it wider than a calm year needs. `python backtest.py` prints the comparison.
HP = {
    "SE": 0.03,        # how far a county's swing typically sits from what its state and its make-up predict
    "NK": 1500,        # votes: counties smaller than this are noisier, by a factor of 1 + NK/votes
    "SL": 0.20,        # how far a state's first votes sit from its last (one voting method is counted first), shared by its counties
    "SG": 0.04,        # the same for one county, on top of its state's lean
    "F_MIN": 0.20,     # a county has to be this far in before its margin counts as evidence at all
    "SB_NAT": 0.02,    # prior size of each demographic tilt, per standard deviation of the feature
    "SB_ST": 0.012,    # how far one state's tilts may sit from the national ones
    "SP": 0.06,        # a state's own polling miss, beyond the miss all states share
    "SD0": 0.04,       # the miss all polls share, before any results
    "SS": 0.07,        # how far one state's swing sits from the national swing
    "SG0": 0.08,       # the national swing, before any results
    "TAU": 0.025,      # allowance for the vote still out behaving unlike the vote that's in, per share outstanding
    "TL": 0.03,        # and for the last votes in any county differing from its first, per share of the vote uncounted
    "ST": 0.25,        # turnout: how wrong the size of an unfinished county can be, as a share of what's left
    "STC": 0.3,        # the same for counties with no precinct count, all off the same way: what looks like votes still out may not exist
    "CAL": 1.0,        # the final scale on the range, set so 90% ranges hold 90% of the time
    "FLOOR": 0.0015,
    "FIN_PREC": 0.70,  # every precinct in and this share of the expected vote: finished, whatever turnout did
    "FIN_IDLE": 60,    # minutes without a new vote, with FIN_IDLE_SHARE of the expected vote in: finished
    "FIN_IDLE_SHARE": 0.75,
    # Learning the night (see _noise). SE and SS above were set on statewide partisan generals. With LEARN on they
    # are only where the forecast starts: it measures how far finished places are really straying tonight, and how
    # far apart the states are really landing, and moves to that as the evidence comes in.
    "LEARN": True,
    "NU_SE": 12,       # the starting SE counts for this many finished places; tonight's places count one each
    "NU_SS": 6,        # the starting SS counts for this many states
    "LEARN_TAU": True, # when places stray further than expected, widen the allowances for the vote still out to match
    # How big an unfinished county will end up (see _size). Before, that was its past turnout at tonight's pace and
    # nothing else, so a county running far ahead of its past looked finished when it was three quarters in.
    "SIZE": True,      # also use the share of precincts reporting, weighed against the turnout expectation. With it,
                       # 2026's forecast with 90-99% counted missed by 0.9 points, not 1.2, and its 90% range held 95%
                       # of the time, not 87-89% (the same with tidy and with messy precinct counts, backtest.MESSY)
    "ST_ALL": 0.15,    # how far a county's final vote sits from its turnout expectation, as a share of it (to start with)
    "NU_ST": 8,        # that starting value counts for this many finished counties
    "SP_PCT": 0.5,     # how wrong "votes counted / share of precincts in" can be about the votes left, at its best
    "SIZE_RHO": 0.5,   # how much counties' sizes are off the same way (one turnout surprise moves them together)
    "FRAC_SIZE": True, # judge how far in a county is by its votes against that size, not by the cautious reading.
                       # With 15-35% counted the average miss fell from 2.2 points to 1.8 (3.0 to 2.2 in 2026), and
                       # half of all calls came by 43% counted, not 47%, with none wrong
    # The two parts of the range that are allowances, not measurements (county sizes, and the vote still out being
    # unlike the vote in), were set wide enough for 2026 and left the range holding 98% of the time when it says
    # 90%. But the misses are not bell-shaped: most are small and a few are large, so the middle can be over-covered
    # while the far tail, which is what a call rests on, is only just covered. 0.75 is where the far tail (2.58
    # ranges out, 1% of the time by rights) came to about 1% in each of 2022, 2024 and 2026 taken separately. Lower
    # buys a tidier 90% and pays for it with wrong calls.
    "OUT_CAL": 0.75,
    "LEAN_MAX": 0.40,  # the furthest a state's first votes can sit from its last (twice SL); the fit is held to it
    "PRE_WIDTHS": True,   # each poll's width from the pre-election model (history/error_model.json), not SP and SD0.
                          # Before any vote it cut the miss in all three elections with polls (4.64 to 4.56 points on
                          # average, Brier .080 to .075), and changed no call
    "ROBUST": 3.0,     # a place this many of its own expected misses from the fit stops pulling harder on it (_robust)
    "ALONE": False,    # this place has no other states to learn from (a city on its own): no national swing in its prior
    "S0": 0.10,        # how far a place with no poll and no other states may sit from its baseline
    # Early vote (early_inputs). Each switch is on only if backtest.py's early vote section says it helps.
    # These three rest on one election (2026), and the county one on one state in it (North Carolina). Treat them as
    # the best current reading, to be checked again on the next election that has early vote on file, not as fitted.
    "EV_COUNTY_TURNOUT": True,   # start each county's expected vote from its early vote against the same day last time
    "EV_STATE_TURNOUT": False,   # the same from state totals alone: it didn't track 2026's turnout, so it's off
    "EV_POWER": 0.5,             # early voting grows faster than turnout does: half the growth, on a log scale
    "EV_REG": False,             # the change in who voted early, by party registration, as a term in the prior:
                                 # better before the first vote in 2026, worse once counting began, so it's off
    "EV_REG_K": 1.0,             # points of swing per point the registration gap moved
    "EV_REG_SD": 0.10,           # how far that sits from the real swing: wide, so it's a nudge and never the answer
}
# What a county is like, for the tilts: (name, scale). The scale is one "spread" of the feature, so that a tilt
# always means "points of swing per spread" and SB_NAT can be one number for all of them. A scale of None means
# "measure it": the feature's own spread across the places in tonight's states (_features). That is the normal
# case. These five were first given hand-set scales (0.15, 0.15, 0.10, 0.60, 0.30); measuring them instead scored
# the same across 2022, 2024 and 2026 (average miss 2.139 points both ways), so nothing is set by hand now.
# Dropping the features altogether cost a lot: 2.59 points, and the 90% range held only 80% of the time in 2026.
# A pack chooses its own in its County Watch block:
#   "features": ["black", "ba", "2023-runoff", {"name": "logpop", "scale": 0.6}]
# A name can be a column of baseline.csv, one of the pack's past contests (its margin), "logpop" (population, on
# a log scale) or "split" (the baseline's own margin). Past contests are what a race without parties needs:
# nobody has run as these candidates before, but how a place voted last time still says who it will favour.
FEATS = (("black", None), ("hisp", None), ("ba", None), ("logpop", None), ("m24", None))
PACK_FEATS: list = []
Z90 = 1.645
# What belongs to one election and not to the model comes from the data pack's County Watch block, so another
# election never needs a change here:
#   "not_comparable": ["NE"]        places whose baseline is a different kind of race, so their swing says nothing
#                                   about the others (in 2026 Nebraska's is Osborn's own 2024 race)
#   "forecast": {"SE": 0.05, ...}   any of HP's settings, for an election this model wasn't fitted on. HP was set
#                                   on statewide partisan generals (2022, 2024, 2026). A city race by ward, or a
#                                   primary, has no party baseline holding its places together, so its counties
#                                   stray further and its range should be wider: raise SE, SS, SG0 and SP there
#                                   until a backtest of that kind of election says otherwise.
NOT_COMPARABLE: set[str] = set()
PACK_HP: dict = {}


@packs.on_activate
def _pack_settings(_pack):
    cw = FEEDS.countywatch()
    NOT_COMPARABLE.clear()
    NOT_COMPARABLE.update(cw.get("not_comparable") or [])
    PACK_HP.clear()
    PACK_HP.update({k: v for k, v in (cw.get("forecast") or {}).items() if k in HP})
    PACK_FEATS.clear()
    for f in cw.get("features") or []:
        PACK_FEATS.append((f["name"], f.get("scale")) if isinstance(f, dict) else (str(f), None))


def features() -> tuple:
    """The features in force, as (name, scale or None): the active pack's, or 2026's."""
    return tuple(PACK_FEATS) or FEATS


def settings(hp: dict | None = None) -> dict:
    """The settings in force: HP, then the active pack's own, then whatever the caller passes."""
    return {**HP, **PACK_HP, **(hp or {})}


# ------------------------------------------------------------------ small linear algebra (no numpy on the bot's PC)
def _inv(a: list[list[float]]) -> list[list[float]]:
    n = len(a)
    m = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(a)]
    for i in range(n):
        piv = max(range(i, n), key=lambda r: abs(m[r][i]))
        m[i], m[piv] = m[piv], m[i]
        d = m[i][i] or 1e-12
        m[i] = [v / d for v in m[i]]
        for r in range(n):
            if r != i and m[r][i]:
                k = m[r][i]
                m[r] = [v - k * w for v, w in zip(m[r], m[i])]
    return [row[n:] for row in m]


def _posterior(obs: list[tuple[list[float], float, float]], mean0: list[float], sd0: list[float]):
    """Bayesian linear regression. obs: (x, y, variance). Returns (mean, covariance)."""
    k = len(mean0)
    a = [[(1.0 / sd0[i] ** 2 if i == j else 0.0) for j in range(k)] for i in range(k)]
    b = [mean0[i] / sd0[i] ** 2 for i in range(k)]
    for x, y, var in obs:
        p = 1.0 / var
        for i in range(k):
            xi = x[i] * p
            b[i] += xi * y
            for j in range(k):
                a[i][j] += xi * x[j]
    cov = _inv(a)
    return [sum(cov[i][j] * b[j] for j in range(k)) for i in range(k)], cov


def _robust(obs: list[tuple[list[float], float, float]], mean0: list[float], sd0: list[float], limit: float):
    """_posterior, but a place that sits far outside the pattern can't drag the fit to itself. After a first fit,
    any place more than `limit` of its own expected misses away has its weight cut by the square of how far out it
    is (twice over, so the second look is from a fit the outliers no longer bend). So a place a little outside
    still counts for most of its weight, and one wildly outside for almost nothing. limit 0 turns this off.

    Why: one replay of 2018 had sixteen North Carolina counties with no Democratic votes at all (their House seat
    was uncontested), which read as 70-point swings. The plain fit explained them with a wild tilt and a wild
    early-vote lean, and called the state for the wrong side."""
    mean, cov = _posterior(obs, mean0, sd0)
    if not limit:
        return mean, cov
    for _ in range(2):
        again, cut = [], False
        for x, y, var in obs:
            z = abs(y - sum(a * b for a, b in zip(mean, x))) / math.sqrt(var)
            if z > limit:
                cut = True
                var = var * (z / limit) ** 2         # the further out, the less it counts: its pull falls away
            again.append((x, y, var))
        if not cut:
            break
        mean, cov = _posterior(again, mean0, sd0)
    return mean, cov


def _cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def win_prob(margin: float, sd: float) -> float:
    """The chance the Democratic side finishes ahead. Slightly fat-tailed: never quite 0 or 1 while votes are out."""
    if sd <= 0:
        return 1.0 if margin > 0 else 0.0 if margin < 0 else 0.5
    z = margin / sd
    return 0.97 * _cdf(z) + 0.03 * _cdf(z / 3.0)      # 3% of the weight on a range three times as wide


# ------------------------------------------------------------------ one county, as the model sees it
def _value(r: dict, name: str) -> float:
    """One feature of one place, unscaled. Anything the place has no number for counts as 0."""
    if name == "logpop":
        return math.log10(max(r.get("pop") or 0, 1000))
    if name in ("m24", "split"):
        return r.get("m24") or 0.0
    if name in (r.get("past") or {}):
        d, rep, _total = r["past"][name]
        return M.m2(d, rep) or 0.0
    v = r.get(name)
    if v is None:
        v = (r.get("extra") or {}).get(name)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _centred(rows: list[dict], names: list[str]) -> dict[str, list[float]]:
    """Each place's features, centred on the state's expected vote: so the state's own term is its swing, and the
    tilts only move votes between its places."""
    raw = {r["fips"]: [_value(r, n) for n in names] for r in rows}
    w = sum(r["w"] for r in rows) or 1.0
    mean = [sum(raw[r["fips"]][i] * r["w"] for r in rows) / w for i in range(len(names))]
    return {f: [v - m for v, m in zip(x, mean)] for f, x in raw.items()}


def _features(states: dict[str, list[dict]], feats: tuple | None = None) -> dict[str, dict[str, list[float]]]:
    """{state: {fips: [each feature, centred and scaled]}} for the states being modelled together. A feature with
    no scale of its own is divided by its spread: how far places sit from their own state's average, across every
    state here, weighted by expected vote. One that doesn't vary at all drops out (it is 0 everywhere)."""
    feats = features() if feats is None else feats
    names = [n for n, _s in feats]
    centred = {st: _centred(rows, names) for st, rows in states.items()}
    scales = []
    for i, (_name, scale) in enumerate(feats):
        if scale is None:
            tot = sum(r["w"] for rows in states.values() for r in rows) or 1.0
            var = sum(r["w"] * centred[st][r["fips"]][i] ** 2 for st, rows in states.items() for r in rows) / tot
            scale = math.sqrt(var)
        scales.append(scale if scale > 1e-9 else float("inf"))
    return {st: {f: [v / s for v, s in zip(x, scales)] for f, x in cs.items()} for st, cs in centred.items()}


def finished(r: dict, hp: dict = HP) -> bool:
    """Whether a county is done counting, as far as anyone can tell. project() asks for every precinct and 85% of
    the expected vote, which a county whose turnout fell never reaches: on the night Ohio ended with 1 of 88 counties
    "complete", New Hampshire 1 of 10. So also: every precinct in with most of the vote, or a count that has
    stopped moving for an hour with most of the vote in (idle, in minutes, is kept by the bot per county)."""
    if r.get("sure", r["complete"]):
        return True
    n, exp = r["D"] + r["R"], r.get("exp2") or 0
    if not n or not exp or r.get("rest"):
        return r["complete"]
    pct, idle = r.get("pct_in"), r.get("idle")
    if pct is not None:
        return r["complete"] or (pct >= 0.999 and n >= hp["FIN_PREC"] * exp)
    if idle is None:
        return r["complete"]        # nobody is timing its count, so its size is all there is to go on
    # No precinct count, and its count is being timed. Reaching the expected vote doesn't finish it: in a year when
    # turnout runs ahead, a county passes its expected vote with a tenth still to come (in the replays this put
    # forecasts up to 9 of their own ranges away from the result). Only a count that has stopped does.
    return idle >= hp["FIN_IDLE"] and n >= hp["FIN_IDLE_SHARE"] * exp


def _size_spread(p: dict, hp: dict) -> float:
    """How far finished counties' votes have landed from the turnout expectation tonight, as a share of it: ST_ALL
    to start with, then what the finished counties show."""
    done = [r for r in p["rows"] if r.get("exp_v") and r.get("fin", finished(r, hp))]
    if not (hp["SIZE"] and hp["LEARN"] and done):
        return hp["ST_ALL"]
    tot = sum(r["exp_v"] for r in done)
    seen_var = sum(r["exp_v"] * ((r["D"] + r["R"]) / r["exp_v"] - 1) ** 2 for r in done) / tot
    return math.sqrt((hp["NU_ST"] * hp["ST_ALL"] ** 2 + len(done) * seen_var) / (hp["NU_ST"] + len(done)))


def _frac_in(r: dict, hp: dict | None = None, spread: float | None = None) -> float:
    """How much of a county's vote is in.

    With FRAC_SIZE: the votes counted against the size _size expects, which weighs precincts and turnout together.
    Without: the cautious reading this started with, the smaller of precincts reporting and votes against what's
    expected. That one is blind to a county whose early vote is all counted before its first precinct reports
    ("0% in" with half its vote there), which is most counties in the states that count early vote first."""
    if r.get("fin", r["complete"]):
        return 1.0
    est = r.get("est_in") or 0.0
    pct = r.get("pct_in")
    if hp and hp["FRAC_SIZE"] and hp["SIZE"]:
        n = r["D"] + r["R"]
        sized = _size(r, n, hp["ST_ALL"] if spread is None else spread, hp)
        if sized and sized[0] > 0:
            return min(n / sized[0], 0.99)
    return min(pct, est) if pct is not None else est * 0.95


def _observations(p: dict, x: dict, hp: dict) -> list[dict]:
    out = []
    for r in p["rows"]:
        r["fin"] = finished(r, hp)
    spread = _size_spread(p, hp)
    for r in p["rows"]:
        n = r["D"] + r["R"]
        if n < 50 or r["live_m"] is None:
            continue
        f = _frac_in(r, hp, spread)
        if f < hp["F_MIN"]:
            continue
        se2 = hp["SE"] ** 2 * (1 + hp["NK"] / n)
        mode2 = (hp["SG"] * (1 - f)) ** 2
        # the last column is the state's lean in a half-counted county: 0 once it's finished
        out.append({"fips": r["fips"], "x": [1.0] + x[r["fips"]] + [1 - f], "y": r["live_m"] - r["m24"], "var": se2 + mode2,
                    "k": se2 / (se2 + mode2), "f": f, "n": n})
    return out


def _noise(obs: dict, fits: dict, hp: dict) -> float:
    """How far finished places are straying tonight, as a multiple of what SE expects (1.0 = as expected).

    For each finished place: its miss against its own state's fit, squared, over the miss SE says to expect there
    (less the part the fit itself absorbs). The average of those is 1 when SE is right. The starting belief that
    SE is right counts as NU_SE places, so a handful of places can't swing it and a hundred can."""
    k_all = None
    total, n = 0.0, 0
    cap = (2 * hp["ROBUST"]) ** 2 if hp["ROBUST"] else float("inf")
    for st, o in obs.items():
        if st not in fits:
            continue
        mean, cov = fits[st]
        k_all = len(mean)
        for q in o:
            if q["f"] < 0.999:
                continue                # a half-counted place's miss is mostly its early votes' lean, not this
            x = q["x"]
            miss = q["y"] - sum(a * b for a, b in zip(mean, x))
            absorbed = sum(x[i] * cov[i][j] * x[j] for i in range(k_all) for j in range(k_all)) / q["var"]
            expect = hp["SE"] ** 2 * (1 + hp["NK"] / q["n"]) * max(0.2, 1 - absorbed)
            total += min(miss * miss / expect, cap)       # one wild place is one wild place, not a noisy night
            n += 1
    return math.sqrt((hp["NU_SE"] + total) / (hp["NU_SE"] + n))


def _size(r: dict, n: int, spread: float, hp: dict) -> tuple[float, float] | None:
    """(final two-party vote, how unsure) for an unfinished county, or None to leave the old estimate alone.

    Two readings, weighed by how far each can be trusted:
      turnout    what the county casts at tonight's pace (r["exp_v"]), give or take `spread` of it
      precincts  votes counted / share of precincts in. Good when most precincts are in and useless early: a
                 county that posts its whole early vote before its first precinct shows "3% in" with half its
                 vote counted. So the doubt on it grows fast as the share in falls (SP_PCT / share in).
    The result is never less than the votes already counted."""
    exp, pct = r.get("exp_v"), r.get("pct_in")
    if not exp or r.get("rest") or not n:
        return None
    sa = spread * exp
    if pct is None:
        if r.get("idle") is None:
            return None
        # No precinct count: all that's known is that the final vote is at least what's counted. So take the
        # turnout reading and cut off everything below the count. A county already past its expected vote then
        # still has a little more expected, and real doubt about how much, in place of none of either.
        a = (n - exp) / sa
        tail = max(1 - _cdf(a), 1e-12)
        lam = math.exp(-a * a / 2) / math.sqrt(2 * math.pi) / tail if a < 8 else a + 1 / a
        return max(exp + sa * lam, float(n)), sa * math.sqrt(max(1 + a * lam - lam * lam, 1e-6))
    if pct >= 0.999:                # every precinct in, yet not counted as finished: little can be left
        tb, sb = float(n), 0.05 * exp
    elif pct > 0:
        left = n * (1 - pct) / pct
        tb, sb = n + left, max(hp["SP_PCT"] * left / pct, 1.0)
    else:
        return max(exp, float(n)), sa
    wa, wb = 1 / sa ** 2, 1 / sb ** 2
    return max((exp * wa + tb * wb) / (wa + wb), float(n)), (wa + wb) ** -0.5


# ------------------------------------------------------------------ early vote
def _clamp(x: float, lo: float = 0.6, hi: float = 1.7) -> float:
    return max(lo, min(hi, x))


def _reg_gap(row: dict | None) -> float | None:
    """Registered Democrats minus Republicans among early voters, as a share of them all. None where the state
    doesn't report party."""
    row = row or {}
    n = (row.get("dem") or 0) + (row.get("rep") or 0) + (row.get("none") or 0)
    return ((row.get("dem") or 0) - (row.get("rep") or 0)) / n if n and (row.get("dem") or row.get("rep")) else None


def early_inputs(early: dict | None, before: dict | None = None, hp: dict | None = None) -> dict[str, dict]:
    """What the early vote says about each state before a result is in. early is the bot's early vote cache
    (countywatch_run.early_cache(): the tracker's state table under "uf", North Carolina's counties under "nc");
    before is the tracker's table from an earlier election (cw_early.load_before()).

    Per state:
      turnout    how many times its 2022-pattern vote the state is expected to cast, or None to leave it at 1.0
      counties   the same per county, where the early vote is known by county against the same day last time
                 (North Carolina). When this is filled in, it is used in place of the state's number.
      growth     early votes as a share of the last election's turnout, against the same share in `before`
      reg_shift  how far the registration gap among early voters has moved toward Democrats since `before`

    turnout and counties are only filled in for the switches that are on in HP."""
    hp = settings(hp)
    uf = (early or {}).get("uf") or {}
    before = E.load_before() if before is None else before
    share = lambda r: (r.get("voted") or 0) / r["turn22"] if r and r.get("turn22") else None        # noqa: E731
    out = {}
    for st in M.STATES:
        now, was = uf.get(st) or {}, before.get(st) or {}
        row = {"turnout": None, "counties": {}, "growth": None, "reg_shift": None}
        a, b = share(now), share(was)
        if a and b:
            row["growth"] = a / b
            if hp["EV_STATE_TURNOUT"]:
                row["turnout"] = _clamp(row["growth"] ** hp["EV_POWER"])
        ga, gb = _reg_gap(now), _reg_gap(was)
        if ga is not None and gb is not None:
            row["reg_shift"] = ga - gb
        out[st] = row
    # Early vote by county, against the same day before the election being compared with. Any state can have
    # one: {"by_county": {state: {"rows": [{"county": name, "now": votes, "before": votes}]}}}. North Carolina's
    # table from the bot ("nc", counts by group under g26 and g22) is read the same way.
    tables = dict((early or {}).get("by_county") or {})
    if (early or {}).get("nc"):
        tables.setdefault("NC", early["nc"])
    count = lambda v: sum(v.values()) if isinstance(v, dict) else (v or 0)        # noqa: E731
    for st, table in tables.items():
        if st not in out or not (table or {}).get("rows"):
            continue
        by_name = {c["name"].upper(): c for c in M.load().get(st, [])}
        got, a_all, b_all = {}, 0, 0
        for r in table["rows"]:
            c = by_name.get(str(r.get("county", "")).upper())
            a, b = count(r.get("now", r.get("g26"))), count(r.get("before", r.get("g22")))
            if c and a and b >= 200:
                got[c["fips"]] = _clamp((a / b) ** hp["EV_POWER"])
                a_all, b_all = a_all + a, b_all + b
        if got and b_all:
            rest = _clamp((a_all / b_all) ** hp["EV_POWER"])
            out[st]["county_growth"] = a_all / b_all
            if hp["EV_COUNTY_TURNOUT"]:
                out[st]["counties"] = {c["fips"]: got.get(c["fips"], rest) for c in by_name.values()}
    return out


def turnout_start(st: str, early: dict | None, typed: float = 1.0) -> tuple[float, dict | None]:
    """(the state's turnout factor, {fips: factor} or None) to hand to cw_model.project. A factor someone typed
    into the workbook wins over anything the early vote says."""
    e = (early or {}).get(st) or {}
    if typed != 1.0 or not e:
        return typed, None
    if e.get("counties"):
        return 1.0, e["counties"]
    return e.get("turnout") or 1.0, None


# ------------------------------------------------------------------ the forecast
def pre_model() -> dict | None:
    """The pre-election model's fitted widths (history/error_model.json, written by cw_pre), or None."""
    try:
        return json.loads((M.HERE / "history" / "error_model.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, AttributeError):
        return None


def night_inputs(polls: dict[str, float], hp: dict | None = None) -> tuple[dict, dict]:
    """(polls, settings) for forecast() as the live bot should run it: with the pre-election model's widths when
    PRE_WIDTHS is on and a model is on file, else the polls bare. A pack's own n_eff per state can sit in its
    polls.json as "n_eff"."""
    hp = dict(hp or {})
    if not settings(hp)["PRE_WIDTHS"] or not polls:
        return polls, hp
    year = packs.active().election_day().year
    out, shared = night_polls(polls, poll_counts(), office="SEN" if year % 4 == 2 else "PRES", midterm=year % 4 == 2)
    if shared:
        hp["SD0"] = shared
    return out, hp


def poll_counts() -> dict[str, float]:
    """{state: how many polls its number effectively rests on}, from the pack's polls.json ("n_eff"), where it says."""
    try:
        states = json.loads((M.CW_DIR / "polls.json").read_text(encoding="utf-8")).get("states", {})
    except (OSError, ValueError):
        return {}
    return {st: row["n_eff"] for st, row in states.items() if row.get("n_eff")}


def night_polls(polls: dict[str, float], n_eff: dict[str, float] | None = None, office: str = "SEN",
                midterm: bool = True, model: dict | None = None, default_n: float = 3.0) -> tuple[dict, float | None]:
    """The night model's poll inputs with widths from the pre-election model: ({state: {"m", "sd"}}, shared width),
    both as two-party fractions. A state's own width follows how many polls its average rests on (n_eff; a pack
    that doesn't say gets `default_n`). The shared width is the pre-election model's C for midterm or presidential
    years. Without a fitted model, the polls come back bare and the shared width is None (SP and SD0 then apply)."""
    import cw_pre as PRE
    model = pre_model() if model is None else model
    if not model:
        return dict(polls), None
    out = {}
    for st, m in polls.items():
        sd = PRE.poll_sd(model, office, (n_eff or {}).get(st) or default_n)
        out[st] = {"m": m, "sd": sd / 100} if sd else m
    c = (model.get("c") or {}).get("midterm" if midterm else "presidential")
    return out, (c / 100 if c else None)


def poll_margins() -> dict[str, float]:
    """{state: final poll's two-party margin} from the pack's polls.json, for the states that have one."""
    try:
        states = json.loads((M.CW_DIR / "polls.json").read_text(encoding="utf-8")).get("states", {})
    except (OSError, ValueError):
        return {}
    out = {}
    for st, row in states.items():
        d, r = row.get("d"), row.get("r")
        if d and r and d + r:
            out[st] = (d - r) / (d + r)
    return out


def forecast(projs: dict[str, dict], polls: dict[str, float] | None = None, hp: dict | None = None,
             early: dict[str, dict] | None = None) -> dict[str, dict]:
    """projs: {state: cw_model.project(...)}. Returns the same projections with the forecast's numbers in place:
    proj_m, range90 and each county's proj_m/proj_D/proj_R, plus sd, win_prob, stage ("prior" before any county is
    far enough in, then "live"), prior, data_weight and legacy (what project() alone said). early is
    early_inputs(): with EV_REG on, how far the party registration of a state's early voters has moved is one more
    term in its prior."""
    hp = settings(hp)
    # A poll can come with its own width ({"m": margin, "sd": its own miss}, from cw_pre's fitted widths, which grow
    # when a race has few polls); a bare number gets SP, as it always has.
    poll_sd = {st: (v.get("sd") or hp["SP"]) if isinstance(v, dict) else hp["SP"] for st, v in (polls or {}).items()}
    polls = {st: (v["m"] if isinstance(v, dict) else v) for st, v in (polls or {}).items()}
    early = early or {}
    using = features()
    k = len(using) + 1          # the state's swing and its tilts; one more column carries the lean of early counts
    # a state is modelled with the others unless it's projected by vote type (Alaska) or has no population on file
    # while county size is one of the features (also Alaska, whose districts carry no demographics)
    needs_pop = any(n == "logpop" for n, _s in using)
    model = {st: p for st, p in projs.items()
             if not p.get("by_type") and (not needs_pop or all(r.get("pop") for r in p["rows"][:3]))}
    feats = _features({st: p["rows"] for st, p in model.items()}, using)
    def out_share(st: str, o: list[dict]) -> float:
        """The share of the state's expected vote that no observation speaks for yet."""
        p = model[st]
        seen = {q["fips"]: q["k"] for q in o}
        tot = sum(r["exp2"] for r in p["rows"]) or 1.0
        return sum(r["exp2"] * (1 - seen.get(r["fips"], 0.0)) for r in p["rows"]) / tot

    def first_pass(hp: dict):
        """The places far enough in to be evidence, the national tilts, and each state fitted on its own results."""
        obs = {st: _observations(p, feats[st], hp) for st, p in model.items()}
        # 1. the national tilts: every state's counties together, each state's own level taken out
        pooled = []
        for st, o in obs.items():
            if len(o) < 6:
                continue
            var = [q["var"] + (hp["SL"] * (1 - q["f"])) ** 2 for q in o]
            w = sum(1 / v for v in var)
            ym = sum(q["y"] / v for q, v in zip(o, var)) / w
            xm = [sum(q["x"][i] / v for q, v in zip(o, var)) / w for i in range(1, k)]
            pooled += [([a - b for a, b in zip(q["x"][1:k], xm)], q["y"] - ym, v) for q, v in zip(o, var)]
        beta_nat = _robust(pooled, [0.0] * (k - 1), [hp["SB_NAT"]] * (k - 1), hp["ROBUST"])[0] if pooled else [0.0] * (k - 1)
        # 2. each state on its own results alone, for what the states say about each other
        alone, fits = {}, {}
        for st, o in obs.items():
            if len(o) < 3:
                continue
            mean, cov = _robust([(q["x"], q["y"], q["var"]) for q in o], [0.0] + beta_nat + [0.0],
                                [0.30] + [hp["SB_ST"]] * (k - 1) + [hp["SL"]], hp["ROBUST"])
            fits[st] = (mean, cov)
            alone[st] = (mean[0], cov[0][0] + (hp["TAU"] * out_share(st, o)) ** 2)
        return obs, beta_nat, alone, fits

    # 0. how noisy is tonight? Fit with the starting SE, measure how far finished places really stray, and fit again
    #    at that level (twice, so the measurement is taken from a fit that already allows for it).
    start, noise = hp, 1.0
    obs, beta_nat, alone, fits = first_pass(hp)
    if start["LEARN"]:
        for _ in range(2):
            noise = max(0.5, min(4.0, _noise(obs, fits, start)))
            hp = {**start, "SE": start["SE"] * noise}
            if start["LEARN_TAU"]:
                # against the SE those allowances were set alongside, not the starting one: a pack that starts SE
                # somewhere else is saying something about SE, not about them
                wider = start["SE"] * noise / HP["SE"]
                hp.update(TAU=start["TAU"] * wider, TL=start["TL"] * wider)
            obs, beta_nat, alone, fits = first_pass(hp)

    def spread(skip: str) -> float:
        """How far one state's swing sits from the national one: SS to start with, then wider if the states that
        have results (all but `skip`) are landing further apart than that, counted against SS as NU_SS states."""
        if not hp["LEARN"]:
            return hp["SS"]
        got = [(a, var) for st, (a, var) in alone.items() if st != skip and st not in NOT_COMPARABLE]
        if len(got) < 3:
            return hp["SS"]
        w = [1 / (var + hp["SS"] ** 2) for _a, var in got]
        mid = sum(a * x for (a, _v), x in zip(got, w)) / sum(w)
        n = len(got)
        seen = max(0.0, sum((a - mid) ** 2 for a, _v in got) / (n - 1) - sum(var for _a, var in got) / n)
        # One way only: the states can show they're landing further apart than SS, never closer. Three states that
        # happen to agree are no reason to narrow the range for a fourth that hasn't reported, and when this was
        # allowed to narrow, states with nothing in were inside their 90% range 88% of the time in 2026.
        return max(hp["SS"], math.sqrt((hp["NU_SS"] * hp["SS"] ** 2 + n * seen) / (hp["NU_SS"] + n)))

    def shared(skip: str):
        """The polls' common miss and the national swing, from every state but `skip`: (mean, variance) each."""
        pd_, wd = 0.0, 1 / hp["SD0"] ** 2
        pg, wg = 0.0, 1 / hp["SG0"] ** 2
        ss = spread(skip)
        for st, (a, var) in alone.items():
            if st == skip:
                continue
            if st in polls:
                w = 1 / (var + poll_sd[st] ** 2)
                pd_, wd = pd_ + w * (a - (polls[st] - model[st]["base_m"])), wd + w
            if st not in NOT_COMPARABLE:
                w = 1 / (var + ss ** 2)
                pg, wg = pg + w * a, wg + w
        return (pd_ / wd, 1 / wd), (pg / wg, 1 / wg), ss

    def prior_for(st: str, base_m: float) -> dict:
        (dm, dv), (gm, gv), ss = shared(st)
        parts = []
        if st in polls:
            parts.append(("poll", polls[st] - base_m + dm, poll_sd[st] ** 2 + dv))
        if st not in NOT_COMPARABLE and not hp["ALONE"]:
            parts.append(("national swing", gm, ss ** 2 + gv))
            shift = (early.get(st) or {}).get("reg_shift")
            if hp["EV_REG"] and shift is not None:
                parts.append(("early vote", hp["EV_REG_K"] * shift, hp["EV_REG_SD"] ** 2))
        if not parts:
            parts.append(("baseline", 0.0, hp["S0"] ** 2))
        w = sum(1 / v for _n, _m, v in parts)
        return {"swing": sum(m / v for _n, m, v in parts) / w, "sd": (1 / w) ** 0.5,
                "parts": {n: (m, v ** 0.5) for n, m, v in parts}, "poll_miss": dm, "national_swing": gm,
                "poll": polls.get(st), "state_spread": ss}

    out = {}
    for st, p in projs.items():
        legacy = {"proj_m": p["proj_m"], "range90": p.get("range90"), "confidence": p["confidence"]}
        prior = prior_for(st, p["base_m"])
        q = dict(p)
        if st not in model:
            # Projected by vote type (Alaska): keep its own projection, and blend the prior in while it's unsure
            pm, ps = p["base_m"] + prior["swing"], prior["sd"]
            if p["proj_m"] is not None and p.get("range90"):
                ls = max((p["range90"][1] - p["range90"][0]) / 2 / Z90, 0.002)
                w0, w1 = 1 / ps ** 2, 1 / ls ** 2
                pm, ps = (pm * w0 + p["proj_m"] * w1) / (w0 + w1), (1 / (w0 + w1)) ** 0.5
                q["stage"] = "live"
            else:
                q["stage"] = "prior"
            q.update(proj_m=pm, sd=ps, range90=(max(-1.0, pm - Z90 * ps), min(1.0, pm + Z90 * ps)),
                     win_prob=win_prob(pm, ps), prior=prior, legacy=legacy,
                     data_weight=max(0.0, 1 - (ps / prior["sd"]) ** 2))
            out[st] = q
            continue

        o = obs[st]
        mean, cov = _robust([(z["x"], z["y"], z["var"]) for z in o], [prior["swing"]] + beta_nat + [0.0],
                            [prior["sd"]] + [hp["SB_ST"]] * (k - 1) + [hp["SL"]], hp["ROBUST"])
        if abs(mean[k]) > hp["LEAN_MAX"]:
            # No state's first votes sit further than this from its last. A fit that says so has run out of honest
            # explanations (a few nearly finished counties can demand any lean at all), so hold the lean at the
            # limit and fit everything else around it.
            held = math.copysign(hp["LEAN_MAX"], mean[k])
            mean, cov = _robust([(z["x"], z["y"], z["var"]) for z in o], [prior["swing"]] + beta_nat + [held],
                                [prior["sd"]] + [hp["SB_ST"]] * (k - 1) + [1e-4], hp["ROBUST"])
        lean = mean[k]
        seen = {z["fips"]: z for z in o}
        x = feats[st]
        rows, PD, PR = [], 0.0, 0.0
        # how far finished counties' votes have landed from the turnout expectation tonight
        size_spread = _size_spread(p, hp)
        for r in p["rows"]:
            r = dict(r)
            n = r["D"] + r["R"]
            xv = [1.0] + x[r["fips"]]
            pred = sum(a * b for a, b in zip(mean, xv))
            z = seen.get(r["fips"])
            r["swing_pred"] = pred
            if r["fin"]:
                r["f"], r["k"] = 1.0, 1.0
                pd, pr = float(r["D"]), float(r["R"])
                r["proj_m"] = r["live_m"]
            else:
                kk = z["k"] if z else 0.0
                r["f"], r["k"] = _frac_in(r, hp, size_spread), kk
                m = max(-1.0, min(1.0, r["m24"] + pred + (kk * (z["y"] - lean * (1 - z["f"]) - pred) if z else 0.0)))
                v = max(r.get("proj_D", 0.0) + r.get("proj_R", 0.0), n)
                if r.get("pct_in") is None and n and r.get("exp2"):
                    # no precinct count, so nobody knows whether it's finished: the nearer it is to its expected
                    # vote, the likelier that what looks like votes still out is only turnout that fell short
                    v = n + (v - n) * (1 - 0.7 * max(0.0, min(1.0, (n / r["exp2"] - 0.75) / 0.25)))
                sized = _size(r, n, size_spread, hp) if hp["SIZE"] else None
                if sized:
                    v, r["size_sd"] = sized
                pd, pr = v * (1 + m) / 2, v * (1 - m) / 2
                if pd < r["D"]:                      # a county can't end with fewer votes than it has counted
                    pd, pr = float(r["D"]), max(v - r["D"], float(r["R"]))
                elif pr < r["R"]:
                    pr, pd = float(r["R"]), max(v - r["R"], float(r["D"]))
                r["proj_m"] = M.m2(pd, pr)
            r["proj_D"], r["proj_R"] = pd, pr
            PD, PR = PD + pd, PR + pr
            rows.append(r)
        V = PD + PR or 1.0
        pm = M.m2(PD, PR) if PD + PR else p["base_m"] + mean[0]
        # how sure: the state's level and tilts, each unfinished county's own surprise, turnout, and an allowance
        # for the vote still out being unlike the vote that's in
        a = [0.0] * (k + 1)
        resid = turn = eff_out = tilt = together = 0.0
        for r in rows:
            if r["fin"]:
                continue
            u = (r["proj_D"] + r["proj_R"]) / V
            z = seen.get(r["fips"])
            kk = r["k"]
            xv = [1.0] + x[r["fips"]]
            for i in range(k):
                a[i] += u * (1 - kk) * xv[i]
            a[k] -= u * kk * (1 - r["f"])
            n = max(r["D"] + r["R"], 1)
            se2 = hp["SE"] ** 2 * (1 + hp["NK"] / max(n, r["proj_D"] + r["proj_R"]))
            resid += u * u * (kk * (hp["SG"] * (1 - r["f"])) ** 2 if z else se2)
            left = 1 - min((r["D"] + r["R"]) / max(r["proj_D"] + r["proj_R"], 1.0), 1.0)
            gap = (r["proj_m"] if r["proj_m"] is not None else pm) - pm
            if r.get("size_sd"):                     # sized by _size: its own doubt, part of it shared with the others
                e = gap * r["size_sd"] / V
                turn += (1 - hp["SIZE_RHO"]) * e * e
                together += e
            else:
                turn += (u * left * hp["ST"] * gap) ** 2
            if r.get("pct_in") is None and r["D"] + r["R"]:      # no precinct count: it may be finished already
                tilt += u * left * abs(gap)
            eff_out += u * (1 - kk)
        param = sum(a[i] * cov[i][j] * a[j] for i in range(k + 1) for j in range(k + 1))
        tau = (hp["TAU"] * eff_out) ** 2 if o else 0.0     # before any results the prior's own width is the whole story
        turn += (hp["STC"] * tilt) ** 2                     # every unfinished county's size being off the same way
        turn += hp["SIZE_RHO"] * together ** 2
        tau += (hp["TL"] * (1 - (sum(r["D"] + r["R"] for r in rows) / V))) ** 2 if o else 0.0
        turn, tau = hp["OUT_CAL"] ** 2 * turn, hp["OUT_CAL"] ** 2 * tau
        sd = hp["CAL"] * math.sqrt(max(param + resid + turn + tau, 0.0)) + (hp["FLOOR"] if eff_out > 0 else 0.0)
        counted_d, counted_r = sum(r["D"] for r in rows), sum(r["R"] for r in rows)
        if counted_d + counted_r == 0:
            pm = p["base_m"] + mean[0]              # nothing counted: the prior, at the baseline's turnout mix
        q.update(rows=rows, proj_m=pm, est_m=pm, sd=sd, stage="live" if o else "prior",
                 range90=(max(-1.0, pm - Z90 * sd), min(1.0, pm + Z90 * sd)), win_prob=win_prob(pm, sd),
                 prior=prior, legacy=legacy, state_swing_model=mean[0], tilts=dict(zip([f for f, _s in using], mean[1:k])), early_lean=lean,
                 obs=len(o), out_share_model=eff_out, noise=noise,
                 national_tilts=dict(zip([f for f, _s in using], beta_nat)),
                 # what the range is made of, as variances: the state's level and tilts, each unfinished county's
                 # own surprise, how big the unfinished counties turn out, and the allowance for the vote still out
                 sd_parts={"fit": max(param, 0.0), "counties": resid, "size": turn, "allowance": tau},
                 data_weight=max(0.0, min(1.0, 1 - cov[0][0] / prior["sd"] ** 2)),
                 **M._catch_up(rows, PD, PR, True))
        q["confidence"] = "none" if not o else "high" if sd < 0.015 else "medium" if sd < 0.04 else "low"
        out[st] = q
    return out


def chance_txt(p: float) -> str:
    """A probability the way a person would say it."""
    side = max(p, 1 - p)
    if side < 0.55:
        return "a coin flip"
    if side < 0.70:
        return "slightly favored"
    if side < 0.85:
        return "favored"
    if side < 0.95:
        return "likely"
    if side < 0.995:
        return "very likely"
    return "all but certain"


# ------------------------------------------------------------------ what the races add up to
# ------------------------------------------------------------------ a race known only by its totals
# House seats (and any race without county feeds) arrive as one total and a share of precincts reporting. The board
# used to show rating x (share not in) + count x (share in). That trusts a count's margin in proportion to precincts
# in, so a district that posts its early vote first (often "0% in" with half its vote counted) either ignored real
# votes or swallowed their lean whole. Here the rating is a prior with its historical spread (history/error_model.json,
# what each rating has meant across 2000-2024), and the count is evidence whose doubt shrinks as the vote comes in.
#
# NOT IN USE: the House board (field.py) still runs the old rule, because this didn't beat it. On 56 past
# statewide races replayed as totals only (python backtest.py --totals, Nov 2026) it was worse before votes (5.7
# points against 5.1: the ratings history's means fit these races worse than the bands' middles) and no better from
# 15% to 90% counted. It was only clearly better with 90-99% counted (0.7 points against 1.2), and part of that is
# the simulation never showing a full 100% of precincts in, which real files do. What it would add is a win chance
# per seat; switch it on only when a replay of real House reporting says it helps.
TOTALS = {
    "LEAN": 0.10,       # how far a race's counted margin can sit from its final one when nothing is in (first votes
                        # are one voting method); shrinks in proportion to the share still out
    "FLOOR": 0.004,     # the doubt left once every precinct is in (late ballots, corrections)
    "EARLY_F": 0.35,    # votes counted with no precincts reporting: treated as this share in
}
RATING_LEVEL = {"Safe": 3.0, "Solid": 3.0, "Likely": 2.0, "Lean": 1.0, "Tilt": 0.5}


def rating_level(rating: str) -> float | None:
    """"Lean D" -> 1.0, "Likely R" -> -2.0, "Toss-up" -> 0.0, as the ratings history counts them. None if unknown."""
    words = (rating or "").replace("-", " ").split()
    if not words:
        return None
    if words[0].lower() in ("toss", "tossup"):
        return 0.0
    if len(words) < 2 or words[0] not in RATING_LEVEL or words[-1] not in ("D", "R"):
        return None
    return RATING_LEVEL[words[0]] * (1 if words[-1] == "D" else -1)


def rating_prior(rating: str, model: dict | None = None) -> tuple[float, float] | None:
    """(margin, sd) as two-party fractions: what races with this rating have done (history/error_model.json), or
    None when the rating or the model isn't there."""
    return level_prior(rating_level(rating), model)


def level_prior(level: float | None, model: dict | None = None) -> tuple[float, float] | None:
    """The same for a rating already on the history's scale (3 = Safe D ... -3 = Safe R, halves allowed)."""
    model = pre_model() if model is None else model
    cells = ((model or {}).get("ratings") or {}).get("lookup") or {}
    if level is None or not cells:
        return None
    key = min(cells, key=lambda k: abs(float(k) - level))
    cell = cells[key]
    return cell["mean"] / 100, max(cell["sd"], 3.0) / 100


def from_totals(prior_m: float, prior_sd: float, counted_m: float | None, f: float, votes: int = 0,
                hp: dict | None = None) -> dict:
    """A race's forecast from its prior and its running total. f: the share of precincts reporting (0 to 1).
    Returns {"proj_m", "sd", "win_prob", "data_weight"}, margins as two-party fractions."""
    t = {**TOTALS, **(hp or {})}
    if not votes or counted_m is None:
        return {"proj_m": prior_m, "sd": prior_sd, "win_prob": win_prob(prior_m, prior_sd), "data_weight": 0.0}
    f = max(0.0, min(1.0, f))
    if f == 0:
        f = t["EARLY_F"]
    obs_sd = math.hypot(t["LEAN"] * (1 - f), t["FLOOR"])
    wp_, wo = 1 / prior_sd ** 2, 1 / obs_sd ** 2
    m = (prior_m * wp_ + counted_m * wo) / (wp_ + wo)
    sd = (wp_ + wo) ** -0.5
    return {"proj_m": m, "sd": sd, "win_prob": win_prob(m, sd), "data_weight": wo / (wp_ + wo)}


def chamber_odds(races: list[dict], fixed: int, majority: int, draws: int = 8000, seed: int = 2026) -> dict:
    """The chance one side reaches a majority. races: [{"wp" or ("m" and "sd"), "weight"}] for the seats still in
    play, from that side's point of view; fixed: the seats it already has (not up, called, or safe).

    Races move together while they lean on the prior (one polling miss moves them all) and separately once their
    own results lead, so each race's error is part shared, part its own, by how much of its forecast is results."""
    import random
    rng = random.Random(seed)
    counts: dict[int, int] = {}
    for _ in range(draws):
        z = rng.gauss(0, 1)
        seats = fixed
        for r in races:
            if "m" in r:
                rho = 0.6 * (1 - r.get("weight", 0.0))
                e = rho ** 0.5 * z + (1 - rho) ** 0.5 * rng.gauss(0, 1)
                seats += r["m"] + r["sd"] * e > 0
            else:
                seats += rng.random() < r["wp"]
        counts[seats] = counts.get(seats, 0) + 1
    dist = {k: v / draws for k, v in sorted(counts.items())}
    mean = sum(k * v for k, v in dist.items())
    return {"chance": sum(v for k, v in dist.items() if k >= majority), "mean": mean, "dist": dist,
            "low": min(dist), "high": max(dist), "median": next(k for k in dist if sum(v for j, v in dist.items() if j <= k) >= 0.5)}
