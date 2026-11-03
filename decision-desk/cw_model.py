"""County Watch: baselines, county types, benchmarks, the election-night projection and the written read.
No Discord or Excel code here, so the same numbers feed /counties and the workbook.

All margins are two-party: (D - R) / (D + R). "D" means the Democratic side of each Senate race
(Dan Osborn, an independent, in Nebraska).

The projection, in plain terms:
  1. A county counts as complete when all its precincts have reported (or someone marks it done in the workbook).
  2. In complete counties, swing = 2026 margin minus 2024 presidential margin.
  3. Swings are averaged (weighted by votes) within each county type in that state: big metro cores,
     college suburbs, Black Belt, Hispanic, working/mid-size metros, small towns and rural.
  4. Every county not yet complete is projected as its 2024 margin plus its type's swing (or the state's
     average swing if no county of that type is in yet), applied to all its expected votes. Its partial
     count is ignored for the margin, because early returns are usually one vote method and lean one way.
  5. Expected votes per county = 2022 midterm turnout x a turnout pace. Before results, the pace is the state's
     turnout factor (default 1.0). Once counties finish, each type's pace comes from its complete counties
     (shrunk toward the state's pace when only a few are in).

Benchmarks use 2022's turnout pattern (a midterm) with 2024's margins: the "baseline" is the state margin if every
county voted its 2024 margin with 2022's turnout mix. Break-even = county 2024 margin minus that baseline.

Turnout, in one scale:
  turnout index = a county's two-party votes / (its 2022-pattern votes x the statewide pace). 100 = in line with
  the state. Above 100 it's a bigger share of the state vote than in 2022.
  break-even at actual turnout = break-even / index (a D county with weak turnout needs a bigger margin).
  overall vs tie pace = live margin - break-even / index, in margin points; x votes = net votes vs the tie pace.
  That splits into margin (live - break-even) and turnout (break-even - break-even / index), and summed over
  every county it equals the statewide net margin.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

HERE = Path(__file__).parent
CW_DIR = HERE / "countywatch"

STATES = {"AK": "Alaska", "GA": "Georgia", "IA": "Iowa", "KS": "Kansas", "ME": "Maine", "MI": "Michigan",
          "MN": "Minnesota", "MS": "Mississippi", "NC": "North Carolina", "NE": "Nebraska", "NH": "New Hampshire",
          "OH": "Ohio", "SC": "South Carolina", "TX": "Texas", "FL": "Florida",
          # Bellwethers: safe Senate races in early-closing states, watched for the national swing, not the winner
          "VA": "Virginia", "KY": "Kentucky"}
BELLWETHER = {"VA", "KY"}
TYPES = ["Big metro core", "College suburb/metro", "Mid-size/working metro", "Black Belt", "Hispanic",
         "Native American", "Small town & rural", "Alaska district"]
MODE_NAMES = {"VA": ("early/mail", "Election Day"), "GA": ("advance", "Election Day"), "NC": ("early", "Election Day"), "IA": ("early/absentee", "Election Day"),
              "SC": ("early", "Election Day"), "AK": ("early", "Election Day")}


# "Implied by other states": which counties' swings are pooled across states. Only like with like: Florida's
# Hispanic counties (Cuban, Venezuelan) don't pool with Texas's; rural and working-metro counties pool by region;
# metro cores and college suburbs nationally. Nebraska doesn't feed any pool, because its baseline is Osborn's own
# 2024 race, so its swings aren't comparable with the presidential baseline everywhere else.
REGION = {"GA": "South", "NC": "South", "SC": "South", "FL": "South", "MS": "South", "VA": "South", "KY": "South",
          "TX": "South", "IA": "Midwest", "KS": "Midwest", "NE": "Midwest", "MN": "Midwest", "MI": "Midwest",
          "OH": "Midwest", "ME": "Northeast", "NH": "Northeast", "AK": "Alaska"}
POOL_K = 50_000   # votes: a pool's swing gets weight v/(v+50k) against the all-other-states swing


def pool_group(st: str, t: str) -> str:
    if st == "NE":
        return "Nebraska (Osborn baseline)"
    if t == "Hispanic":
        return "Hispanic (Florida)" if st == "FL" else "Hispanic (Southwest)"
    if t in ("Mid-size/working metro", "Small town & rural"):
        return f"{t} ({REGION.get(st, 'Other')})"
    return t


def county_type(r: dict) -> str:
    if r["state"] == "AK":
        return "Alaska district"
    pop, b, h, n = int(r["pop23"] or 0), float(r["black"] or 0), float(r["hisp"] or 0), float(r["native"] or 0)
    ba = float(r["ba"] or 0)
    if h >= 0.60:
        return "Hispanic"
    if pop >= 750_000:
        return "Big metro core"
    if n >= 0.30:
        return "Native American"
    if b >= 0.40:
        return "Black Belt"
    if h >= 0.40:
        return "Hispanic"
    if pop >= 100_000 and ba >= 0.40:
        return "College suburb/metro"
    if pop >= 100_000:
        return "Mid-size/working metro"
    return "Small town & rural"


def m2(d, r) -> float | None:
    d, r = float(d or 0), float(r or 0)
    return (d - r) / (d + r) if d + r else None


def short(name: str) -> str:
    return name.replace(" County", "").replace(" Parish", "").replace("State House ", "HD ")


# ------------------------------------------------------------------ baseline
_CACHE: dict = {}


def load() -> dict[str, list[dict]]:
    if "rows" in _CACHE:
        return _CACHE["rows"]
    keys = json.loads((CW_DIR / "key_counties.json").read_text(encoding="utf-8"))
    out: dict[str, list[dict]] = {}
    ne24 = {}
    try:      # MIT Election Data + Science Lab, 2024-elections-official, 2024-senate-county.csv (Osborn v Fischer)
        with open(CW_DIR / "ne_senate24.csv", encoding="utf-8") as f:
            ne24 = {r["fips"]: (int(r["osborn"]), int(r["fischer"]), int(r["total"])) for r in csv.DictReader(f)}
    except OSError:
        pass
    with open(CW_DIR / "baseline.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            st = r["state"]
            c = {"state": st, "fips": r["fips"], "county": r["county"], "name": short(r["county"]),
                 "type": county_type(r), "d24": int(r["d24"]), "r24": int(r["r24"]), "t24": int(r["t24"]),
                 "d20": int(r["d20"] or 0), "r20": int(r["r20"] or 0), "t22": int(r["t22"] or 0) or int(r["t24"]),
                 "d22": int(r["d22"] or 0), "r22": int(r["r22"] or 0), "race22": r["race22"],
                 "pop": int(r["pop23"] or 0), "white": float(r["white"] or 0), "black": float(r["black"] or 0),
                 "hisp": float(r["hisp"] or 0), "ba": float(r["ba"] or 0),
                 "modes22": {k: (float(r[f"{k}22_D"]), float(r[f"{k}22_R"])) for k in ("early", "mail", "eday")
                             if r.get(f"{k}22_D")}}
            if st == "NE" and r["fips"] in ne24:
                # Nebraska's D side is Dan Osborn, so his own 2024 Senate race (vs Fischer) is the baseline, not
                # Harris-Trump: he ran about 14 points ahead of Harris, most of it in rural counties.
                c["d24"], c["r24"], c["t24"] = ne24[r["fips"]]
            c["m24"], c["m20"], c["m22"] = m2(c["d24"], c["r24"]), m2(c["d20"], c["r20"]), m2(c["d22"], c["r22"])
            out.setdefault(st, []).append(c)
    for st, rows in out.items():
        for c in rows:
            c["w"] = c["t22"] * (c["d24"] + c["r24"]) / c["t24"]     # two-party votes at 2022 turnout
        sm = base_m(rows)
        t = sum(c["t24"] for c in rows)
        tw = sum(c["w"] for c in rows)
        kmap = {k[0].upper(): k for k in keys.get(st, [])}
        for c in rows:
            c["share24"] = c["t24"] / t
            c["share22"] = c["w"] / tw               # expected share of the state's two-party vote
            c["breakeven"] = c["m24"] - sm          # D margin needed here for a statewide tie on uniform swing
            k = kmap.get(c["name"].upper())
            c["key"], c["why"], c["watch"] = (True, k[1], k[2]) if k else (False, "", "")
    _CACHE["rows"] = out
    return out


def state_m24(rows) -> float:
    return m2(sum(c["d24"] for c in rows), sum(c["r24"] for c in rows))


def base_m(rows) -> float:
    """2024 county margins weighted by 2022 (midterm) turnout: the statewide margin a uniform-swing tie is measured from."""
    w = sum(c["w"] for c in rows)
    return sum(c["m24"] * c["w"] for c in rows) / w if w else 0.0


def base(st: str) -> float:
    return base_m(load()[st])


def mode_note(st: str) -> str | None:
    """How 2022's early and Election Day votes differed, so early returns can be read correctly."""
    rows = load()[st]
    if not all(c["modes22"] for c in rows):
        return None
    tot = {k: [sum(c["modes22"][k][0] for c in rows), sum(c["modes22"][k][1] for c in rows)] for k in ("early", "eday")}
    if "mail" in rows[0]["modes22"]:
        for i in (0, 1):
            tot["early"][i] += sum(c["modes22"]["mail"][i] for c in rows)
    e, d = m2(*tot["early"]), m2(*tot["eday"])
    if e is None or d is None:
        return None
    early, eday = MODE_NAMES.get(st, ("early", "Election Day"))
    lean = "Democrats" if e > d else "Republicans"
    return (f"In 2022 the {early} vote was {fmt(e)} and the {eday} vote {fmt(d)}, so counts that are mostly "
            f"{early} votes flatter {lean}.")


# ------------------------------------------------------------------ formatting
def fmt(m: float | None, d_label: str = "D", r_label: str = "R", digits: int = 1) -> str:
    if m is None:
        return "n/a"
    v = abs(m) * 100
    if round(v, digits) == 0:
        return "even"
    lab = d_label if m > 0 else r_label
    return f"{lab}{' ' if len(lab) > 1 else ''}+{v:.{digits}f}"


def swing_txt(s: float | None) -> str:
    return "n/a" if s is None else ("no change" if round(abs(s) * 100, 1) == 0 else
                                     f"{abs(s) * 100:.1f} pts toward {'D' if s > 0 else 'R'}")


def need_txt(needed: float, d_label: str = "D") -> str:
    """'needs 2.2 pts toward D' or 'can absorb 7.1 pts toward R' for the Democratic side."""
    return (f"needs {abs(needed) * 100:.1f} pts toward D" if needed > 0
            else f"can absorb {abs(needed) * 100:.1f} pts toward R")


# ------------------------------------------------------------------ projection
PACE_K = 3     # shrink a type's turnout pace toward the state's: weight n/(n+3) for n complete counties
SWING_K = 0.03  # shrink a type's swing toward the state's: weight v/(v + 3% of the state's expected vote)
DONE_SHARE = 0.97   # without a precinct count, a county is complete once it has 97% of its expected vote
# Projection range. Uncertainty comes from the vote still out: how much finished counties' swings disagree (spread,
# shrinking with more counties) plus a fixed allowance for counties still out behaving differently from those in
# (TAU). Judgment calls, not fitted: TAU 3 pts, spread 5 pts until 5 counties are in, 2 pts minimum after.
RANGE_Z, RANGE_TAU, RANGE_FLOOR, SPREAD_DEFAULT, SPREAD_MIN = 1.645, 0.03, 0.005, 0.05, 0.02
SURE_MIN = 3    # counties known to be complete (precincts all in, or marked Y) before their turnout sets expectations
LEAN = 0.10    # counties whose break-even is beyond +/-10 pts count as D-leaning / R-leaning for the turnout read


def project(st: str, live: dict, turnout_factor: float = 1.0) -> dict:
    """live: {fips: {"D","R","O","pct_in", optional "complete": bool}} (missing counties = nothing counted)."""
    rows = load()[st]

    def sure(c):
        """Complete for certain: marked done, or every precinct in. Doesn't depend on the expected vote."""
        lv = live.get(c["fips"]) or {}
        v = int(lv.get("D") or 0) + int(lv.get("R") or 0)
        pct = lv.get("pct_in")
        return v > 0 and (bool(lv.get("complete")) or (pct is not None and pct >= 0.999)), v
    # Expected votes follow the night's real turnout: the pace in counties known to be complete, once there are a
    # few, else the turnout factor. (Not the full projection pace, which would make completeness circular.)
    sure_rows = [(c, v) for c in rows for ok, v in [sure(c)] if ok]
    sw_ = sum(c["w"] for c, _ in sure_rows)
    exp_pace = sum(v for _, v in sure_rows) / sw_ if len(sure_rows) >= SURE_MIN and sw_ else turnout_factor
    out_rows = []
    for c in rows:
        lv = live.get(c["fips"]) or {}
        D, R = int(lv.get("D") or 0), int(lv.get("R") or 0)
        exp2 = c["w"] * exp_pace                         # used to judge completeness and % in
        pct = lv.get("pct_in")
        complete = sure(c)[0] or (pct is None and D + R > 0 and D + R >= DONE_SHARE * exp2)
        lm = m2(D, R)
        out_rows.append({**c, "D": D, "R": R, "O": int(lv.get("O") or 0), "pct_in": pct, "exp2": exp2,
                         "complete": complete, "live_m": lm, "swing": (lm - c["m24"]) if complete and lm is not None else None,
                         "est_in": 1.0 if complete else min((D + R) / exp2, 0.99) if exp2 else 0})

    def wavg(items):
        w = sum(x[1] for x in items)
        return sum(x[0] * x[1] for x in items) / w if w else None

    done = [r for r in out_rows if r["complete"]]
    state_swing = wavg([(r["swing"], r["D"] + r["R"]) for r in done])
    type_swing, type_n, type_used = {}, {}, {}
    exp_state = sum(r["exp2"] for r in out_rows)
    for t in TYPES:
        items = [(r["swing"], r["D"] + r["R"]) for r in done if r["type"] == t]
        if items:
            type_swing[t], type_n[t] = wavg(items), len(items)
            # one small finished county shouldn't set the swing for every county of its type: pull it toward the
            # state's swing until the type has a real share of the vote counted
            v = sum(x[1] for x in items)
            wt = v / (v + SWING_K * exp_state) if exp_state else 1.0
            type_used[t] = state_swing + (type_swing[t] - state_swing) * wt
    # turnout pace (two-party votes / 2022-pattern votes): observed in complete counties, by type
    dw = sum(r["w"] for r in done)
    pace_obs = sum(r["D"] + r["R"] for r in done) / dw if dw else None
    pace_state = pace_obs if pace_obs and len(done) >= 3 else turnout_factor
    type_pace = {}
    for t in type_n:
        tw = sum(r["w"] for r in done if r["type"] == t)
        if tw:
            obs = sum(r["D"] + r["R"] for r in done if r["type"] == t) / tw
            n = type_n[t]
            type_pace[t] = pace_state + (obs - pace_state) * n / (n + PACE_K)
    PD = PR = 0.0
    for r in out_rows:
        if r["complete"]:
            r["proj_m"] = r["live_m"]
            pd, pr = r["D"], r["R"]
        else:
            # Partial counts are usually one vote method (early first, or Election Day first), so their margin
            # is biased. Project the county's whole expected vote from its 2024 margin plus its type's swing.
            s = type_used.get(r["type"], state_swing if state_swing is not None else 0.0)
            r["proj_m"] = max(-1.0, min(1.0, r["m24"] + s))
            v = max(r["w"] * type_pace.get(r["type"], pace_state), r["D"] + r["R"])
            pd, pr = v * (1 + r["proj_m"]) / 2, v * (1 - r["proj_m"]) / 2
        r["proj_D"], r["proj_R"] = pd, pr
        PD, PR = PD + pd, PR + pr
    W = sum(r["w"] for r in out_rows)
    P = (PD + PR) / W if W else 1.0                    # projected statewide pace
    out_share = (sum(r["proj_D"] + r["proj_R"] for r in out_rows if not r["complete"]) / (PD + PR)) if PD + PR else 1.0
    rng90 = None
    if done:
        dv = sum(r["D"] + r["R"] for r in done)
        spread = (sum((r["D"] + r["R"]) * (r["swing"] - state_swing) ** 2 for r in done) / dv) ** 0.5 if dv else 0
        spread = max(spread, SPREAD_MIN) if len(done) >= 5 else SPREAD_DEFAULT
        sd = out_share * (RANGE_TAU ** 2 + spread ** 2 / len(done)) ** 0.5 + RANGE_FLOOR
        pm_ = m2(PD, PR)
        rng90 = (max(-1.0, pm_ - RANGE_Z * sd), min(1.0, pm_ + RANGE_Z * sd))
    eff_m = eff_t = 0.0
    for r in out_rows:
        be, tie_v = r["breakeven"], r["w"] * P
        r["par_votes"] = tie_v
        r["net_vs_tie"] = (r["proj_D"] - r["proj_R"]) - be * tie_v      # projected for unfinished counties
        r["t_index"] = r["t_be"] = r["t_effect"] = r["overall"] = r["t_need"] = None
        if r["complete"] and r["live_m"] is not None and tie_v:
            v = r["D"] + r["R"]
            idx = v / tie_v
            r["t_index"] = idx
            r["t_be"] = be / idx                    # break-even at the turnout it actually had
            r["t_effect"] = be - be / idx           # turnout's contribution, in margin points (+ = helps D)
            r["overall"] = r["live_m"] - be / idx   # margin + turnout vs the tie pace
            r["m_votes"], r["t_votes"] = (r["live_m"] - be) * v, be * (v - tie_v)
            eff_m, eff_t = eff_m + r["m_votes"], eff_t + r["t_votes"]
            if abs(r["live_m"]) >= 0.10 and abs(be) >= 0.05 and (r["live_m"] > 0) == (be > 0):
                r["t_need"] = be / r["live_m"]      # turnout index at which this margin exactly hits the tie pace
    exp_total = sum(r["exp2"] for r in out_rows)
    done_exp = sum(r["exp2"] for r in done)
    counted = sum(r["D"] + r["R"] for r in out_rows)
    share_done = done_exp / exp_total if exp_total else 0
    confidence = "none" if not done else "low" if share_done < 0.10 or len(done) < 5 else \
        "medium" if share_done < 0.40 else "high"

    def lean_index(sign):
        g = [r for r in done if r["t_index"] is not None and sign * r["breakeven"] >= LEAN]
        v, t = sum(r["D"] + r["R"] for r in g), sum(r["par_votes"] for r in g)
        return (v / t if t else None), len(g)
    bm = base_m(rows)
    return {"state": st, "rows": out_rows, "proj_m": m2(PD, PR) if done else None, "raw_m": m2(
        sum(r["D"] for r in out_rows), sum(r["R"] for r in out_rows)),
            "counted": counted, "counted_share": counted / exp_total if exp_total else 0,
            "done": len(done), "done_share": share_done, "confidence": confidence,
            "state_swing": state_swing, "type_swing": type_swing, "type_swing_used": type_used, "type_n": type_n,
            "exp_pace": exp_pace, "out_share": out_share, "range90": rng90, **_catch_up(out_rows, PD, PR, bool(done)),
            "m24": state_m24(rows), "base_m": bm, "needed_swing": -bm,
            "pace": P, "pace_obs": pace_obs, "type_pace": type_pace,
            "d_index": lean_index(1), "r_index": lean_index(-1), "eff_margin": eff_m, "eff_turnout": eff_t,
            "turnout_vs_22": (sum(r["D"] + r["R"] + r["O"] for r in done) / sum(r["t22"] for r in done)) if done else None}


def _catch_up(rows, PD: float, PR: float, projected: bool) -> dict:
    """Votes the projection still expects (two-party), what the projected loser needs of them to tie, and what
    they're on pace for. Shares are of the remaining two-party vote, from the trailer's side."""
    cd, cr = sum(r["D"] for r in rows), sum(r["R"] for r in rows)
    remaining = max(0.0, PD + PR - cd - cr)
    out = {"remaining": remaining, "trailer": None, "need_share": None, "pace_share": None}
    if not projected or remaining <= 0:
        return out
    sign = -1 if PD >= PR else 1                      # trailer: +1 = D side, -1 = R
    out["trailer"] = "D" if sign == 1 else "R"
    need_m = -sign * (cd - cr) / remaining           # trailer's margin needed in the remaining vote to tie
    pace_m = sign * ((PD - PR) - (cd - cr)) / remaining
    out["need_share"], out["pace_share"] = (1 + need_m) / 2, (1 + pace_m) / 2
    return out


def votes_txt(v: float) -> str:
    return f"{v / 1e6:.2f}M" if v >= 1e6 else f"{v / 1e3:.0f}K" if v >= 1e3 else f"{v:.0f}"


def type_bench(st: str, p: dict | None = None) -> list[dict]:
    """Aggregate benchmarks per county type: share of the vote, the margin each statewide outcome needs, and
    live swing / turnout from complete counties."""
    p = p or project(st, {})
    rows, out = p["rows"], []
    W = sum(r["w"] for r in rows)
    for t in TYPES:
        g = [r for r in rows if r["type"] == t]
        if not g:
            continue
        w = sum(r["w"] for r in g)
        m = sum(r["m24"] * r["w"] for r in g) / w
        be = m - p["base_m"]
        d = [r for r in g if r["complete"] and r["t_index"] is not None]
        v, par = sum(r["D"] + r["R"] for r in d), sum(r["par_votes"] for r in d)
        idx = v / par if par else None
        sw = p["type_swing"].get(t)
        used = p.get("type_swing_used", {}).get(t)
        proj = m + (used if used is not None else (p["state_swing"] or 0.0)) if p["done"] else None
        out.append({"type": t, "counties": len(g), "complete": len(d), "share": w / W, "m24": m, "breakeven": be,
                    "scen": {x: m + x - p["base_m"] for x in (-0.05, -0.01, 0.0, 0.01, 0.05)},
                    "swing": sw, "proj_m": proj, "t_index": idx, "be_at_turnout": (be / idx) if idx else None,
                    "net_vs_tie": sum(r["net_vs_tie"] for r in g), "pace": p["type_pace"].get(t),
                    "overall": (sum(r["m_votes"] + r["t_votes"] for r in d) / v) if v else None})
    return out


# ------------------------------------------------------------------ the written read
def idx_txt(i: float | None) -> str:
    return "n/a" if i is None else f"{i * 100:.0f}"


def net_txt(v: float, d_label: str = "D", r_label: str = "R") -> str:
    return "even" if round(v) == 0 else f"{d_label if v > 0 else r_label} +{abs(v):,.0f}"


def turnout_lines(p: dict, dl: str = "D", rl: str = "R") -> list[str]:
    """How turnout is faring against what each side needs, from complete counties."""
    out = []
    if not p["done"]:
        return out
    if p["turnout_vs_22"]:
        out.append(f"Turnout in complete counties is {p['turnout_vs_22']:.0%} of 2022"
                   + (f"; projected statewide {p['pace']:.0%} of 2022's two-party vote." if p["proj_m"] is not None else "."))
    (di, dn), (ri, rn) = p["d_index"], p["r_index"]
    if di is not None or ri is not None:
        parts = []
        if di is not None:
            parts.append(f"{dl}-leaning counties {idx_txt(di)} ({dn})")
        if ri is not None:
            parts.append(f"{rl}-leaning counties {idx_txt(ri)} ({rn})")
        gap = (di - ri) if di is not None and ri is not None else None
        tail = ""
        if gap is not None and abs(gap) >= 0.03:
            tail = f": {dl if gap > 0 else rl}'s base is turning out better."
        elif gap is not None:
            tail = ": neither base is out-voting the other."
        out.append("Turnout index, complete counties only (100 = in line with the state): " + " · ".join(parts) + tail)
    if p["eff_margin"] or p["eff_turnout"]:
        sg = lambda v: "nothing" if round(v) == 0 else f"{'+' if v > 0 else '-'}{abs(v):,.0f}"
        out.append(f"Against the tie pace, complete counties are worth {sg(p['eff_margin'])} net votes to {dl} from "
                   f"margin and {sg(p['eff_turnout'])} from turnout.")
    return out


def read_lines(st: str, p: dict, d_name: str = "D", r_name: str = "R", max_key: int = 8) -> list[str]:
    """Qualitative analysis as short lines, most important first."""
    lines = []
    dl, rl = d_name.split()[-1], r_name.split()[-1]
    if not p["counted"]:
        need = abs(p["needed_swing"]) * 100
        lines.append(f"No votes counted yet. 2024: {fmt(p['m24'])}; with 2022's turnout pattern {fmt(p['base_m'])}, so {dl} "
                     + (f"needs a {need:.1f}-pt swing toward D from 2024 to win."
                        if p["needed_swing"] > 0 else f"can absorb up to a {need:.1f}-pt swing toward R from 2024 and still win."))
        note = mode_note(st)
        if note:
            lines.append(note)
        return lines
    if p["proj_m"] is not None:
        rg = p.get("range90")
        rtxt = f", 90% range {fmt(rg[0], dl, rl)} to {fmt(rg[1], dl, rl)}" if rg else ""
        lines.append(f"**Projection: {fmt(p['proj_m'], dl, rl)}**{rtxt} (confidence {p['confidence']}: {p['done']} complete "
                     f"{'county' if p['done'] == 1 else 'counties'}, {p['done_share']:.0%} of the expected vote)")
        if p.get("trailer"):
            who = dl if p["trailer"] == "D" else rl
            need = p["need_share"]
            lines.append(f"About {votes_txt(p['remaining'])} two-party votes left. " + (
                f"{who} can't catch up: would need more than all of them." if need > 1 else
                f"{who} needs {need:.1%} of them to tie and is on pace for {p['pace_share']:.1%}."))
    else:
        lines.append("No county is fully counted yet, so there's no projection. Partial counts are shown raw below.")
    lines.append(f"Raw count: {fmt(p['raw_m'], dl, rl)} with {p['counted']:,} two-party votes "
                 f"(about {p['counted_share']:.0%} of the expected total).")
    if p["state_swing"] is not None:
        need = p["needed_swing"]
        ahead = p["state_swing"] - need
        lines.append(f"Swing vs 2024 in complete counties: {swing_txt(p['state_swing'])}. "
                     f"{dl} {need_txt(need)} to win, so "
                     f"{'running ahead of' if ahead > 0 else 'running behind'} that pace by {abs(ahead) * 100:.1f} pts.")
    if p["type_swing"]:
        parts = [f"{t} {swing_txt(s).replace(' pts toward ', ' ')} ({p['type_n'][t]})"
                 for t, s in sorted(p["type_swing"].items(), key=lambda kv: -kv[1])]
        lines.append("By county type (swing, counties in): " + " · ".join(parts))
    lines += turnout_lines(p, dl, rl)
    done = [r for r in p["rows"] if r["complete"] and r["swing"] is not None and r["D"] + r["R"] > 2000]
    if len(done) >= 3:
        best = max(done, key=lambda r: r["swing"])
        worst = min(done, key=lambda r: r["swing"])
        lines.append(f"Biggest movers: {best['name']} {swing_txt(best['swing'])}, "
                     f"{worst['name']} {swing_txt(worst['swing'])}.")
    keys = [r for r in p["rows"] if r["key"] and r["D"] + r["R"] > 0]
    if keys:
        def score(r):          # margin and turnout together once a county is done; margin alone before that
            return r["overall"] if r["overall"] is not None else (r["live_m"] - r["breakeven"])
        above = [r for r in keys if r["live_m"] is not None and score(r) >= 0]
        below = [r for r in keys if r["live_m"] is not None and score(r) < 0]

        def tag(r):
            t = f"{r['name']} {fmt(r['live_m'])} vs {fmt(r['breakeven'])} needed"
            if r["t_index"] is not None:
                t += f", turnout {idx_txt(r['t_index'])}"
                if abs(r["t_be"] - r["breakeven"]) >= 0.005:
                    t += f" (so {fmt(r['t_be'])} needed)"
            return t + ("" if r["complete"] else f", {r['est_in']:.0%} in")
        if above:
            lines.append(f"Key counties at or above {dl}'s pace: " + "; ".join(tag(r) for r in above[:max_key]))
        if below:
            lines.append(f"Key counties below {dl}'s pace: " + "; ".join(tag(r) for r in below[:max_key]))
    note = mode_note(st)
    if note and p["done_share"] < 0.5:
        lines.append(note)
    return lines


def take(st: str, p: dict, d_name: str = "D", r_name: str = "R") -> list[str]:
    """The bot's written take for the Dashboard, as four cells: where it stands, swing and turnout, key counties ahead of
    the D side's pace, key counties behind it."""
    dl, rl = d_name.split()[-1], r_name.split()[-1]
    if not p["counted"]:
        return ["\n".join(read_lines(st, p, d_name, r_name)), "Nothing counted yet.", "None in yet.", "None in yet."]
    story = []
    if p["proj_m"] is not None:
        rg = p.get("range90")
        story.append(f"Projection: {fmt(p['proj_m'], dl, rl)}" + (f". 90% range: {fmt(rg[0], dl, rl)} to {fmt(rg[1], dl, rl)}." if rg else "."))
        story.append(f"Confidence {p['confidence']}: {p['done']} finished {'county' if p['done'] == 1 else 'counties'}, {p['done_share']:.0%} of the expected vote.")
        if p.get("trailer"):
            who = dl if p["trailer"] == "D" else rl
            need = p["need_share"]
            story.append(f"About {votes_txt(p['remaining'])} votes left. " + (f"{who} can't catch up: it would take more than all of them." if need > 1 else
                                                                               f"{who} needs {need:.1%} of them to tie and is on pace for {p['pace_share']:.1%}."))
    else:
        story.append("No county is fully counted yet, so there's no projection.")
    story.append(f"Raw count: {fmt(p['raw_m'], dl, rl)} with {p['counted']:,} votes, about {p['counted_share']:.0%} of the expected total.")
    note = mode_note(st)
    if note and p["done_share"] < 0.5:
        story.append(note)
    swing = []
    if p["state_swing"] is not None:
        need, ahead = p["needed_swing"], p["state_swing"] - p["needed_swing"]
        sg = lambda s: f"{abs(s) * 100:.1f} {'D' if s >= 0 else 'R'}"
        swing.append(f"Swing in finished counties: {sg(p['state_swing'])}. {dl} " + (f"needs {sg(need)}" if need > 0 else f"can lose {abs(need) * 100:.1f}")
                     + f", so {abs(ahead) * 100:.1f} {'ahead' if ahead > 0 else 'behind'}.")
        for t, s in sorted(p["type_swing"].items(), key=lambda kv: -kv[1]):
            swing.append(f"   {t}: {sg(s)} ({p['type_n'][t]} in)")
        if p["turnout_vs_22"]:
            swing.append(f"Turnout: {p['turnout_vs_22']:.0%} of 2022 in finished counties, {p['pace']:.0%} projected statewide.")
        (di, dn), (ri, rn) = p["d_index"], p["r_index"]
        parts = ([f"{dl} counties {idx_txt(di)}"] if di is not None else []) + ([f"{rl} counties {idx_txt(ri)}"] if ri is not None else [])
        if parts:
            swing.append("Turnout index (100 = state pace): " + ", ".join(parts) + ".")
        moved = [r for r in p["rows"] if r["complete"] and r["swing"] is not None and r["D"] + r["R"] > 2000]
        if len(moved) >= 3:
            best, worst = max(moved, key=lambda r: r["swing"]), min(moved, key=lambda r: r["swing"])
            swing.append(f"Most D swing: {best['name']} {sg(best['swing'])}. Least: {worst['name']} {sg(worst['swing'])}.")

    def score(r):
        return r["overall"] if r["overall"] is not None else r["live_m"] - r["breakeven"]

    def tag(r):
        needs = r["t_be"] if r["t_index"] is not None else r["breakeven"]
        t = f"{r['name']}: {fmt(r['live_m'])}, needs {fmt(needs)}"
        return t + (f", turnout {idx_txt(r['t_index'])}" if r["t_index"] is not None else f", {r['est_in']:.0%} in")
    keys = sorted((r for r in p["rows"] if r["key"] and r["D"] + r["R"] > 0 and r["live_m"] is not None), key=score, reverse=True)
    ahead = [tag(r) for r in keys if score(r) >= 0]
    behind = [tag(r) for r in reversed(keys) if score(r) < 0]
    return ["\n".join(story), "\n".join(swing) or "No county finished yet.", "\n".join(ahead) or "None so far.", "\n".join(behind) or "None so far."]


def key_table(st: str, p: dict | None = None) -> list[dict]:
    rows = (p or project(st, {}))["rows"]
    return sorted([r for r in rows if r["key"]], key=lambda r: -r["t24"])


# ------------------------------------------------------------------ national read
def national(projs: dict) -> dict:
    """Pool every fully counted county across all states: swing vs 2024 by county type and by state,
    and turnout vs 2022. projs: {state: project(...) result}."""
    rows = [r for p in projs.values() for r in p["rows"] if r["complete"] and r["swing"] is not None]
    def wavg(items):
        w = sum(v for _, v in items)
        return sum(x * v for x, v in items) / w if w else None
    by_type, n_type, turn_type = {}, {}, {}
    for t in TYPES:
        it = [r for r in rows if r["type"] == t]
        if it:
            by_type[t] = wavg([(r["swing"], r["D"] + r["R"]) for r in it])
            n_type[t] = len(it)
            t22 = sum(r["t22"] for r in it)
            turn_type[t] = sum(r["D"] + r["R"] + r["O"] for r in it) / t22 if t22 else None
    by_state = {st: (p["state_swing"], p["done"]) for st, p in projs.items() if p["state_swing"] is not None}
    t22 = sum(r["t22"] for r in rows)
    return {"counties": len(rows), "states": len(by_state), "swing": wavg([(r["swing"], r["D"] + r["R"]) for r in rows]),
            "by_type": by_type, "n_type": n_type, "turnout_type": turn_type, "by_state": by_state,
            "turnout": (sum(r["D"] + r["R"] + r["O"] for r in rows) / t22) if t22 else None}


def national_lines(n: dict) -> list[str]:
    if not n["counties"]:
        return ["No county is fully counted yet."]
    out = [f"**{n['counties']} fully counted counties in {n['states']} state{'' if n['states'] == 1 else 's'} are swinging {swing_txt(n['swing'])} vs 2024.** "
           f"Turnout there is {n['turnout']:.0%} of 2022."]
    if n["by_type"]:
        out.append("By county type: " + " · ".join(
            f"{t} {swing_txt(v).replace(' pts toward ', ' ')} ({n['n_type'][t]}"
            + (f", turnout {n['turnout_type'][t]:.0%} of 2022" if n['turnout_type'].get(t) else "") + ")"
            for t, v in sorted(n["by_type"].items(), key=lambda kv: -kv[1])))
    if n["by_state"]:
        out.append("By state: " + " · ".join(
            f"{st} {swing_txt(v).replace(' pts toward ', ' ')} ({k})"
            for st, (v, k) in sorted(n["by_state"].items(), key=lambda kv: -kv[1][0])))
    need = {st: -base(st) for st in STATES if st not in BELLWETHER}
    if n["swing"] is not None:
        within = [st for st, v in need.items() if v > 0 and n["swing"] >= v]
        if within:
            out.append("If that swing held everywhere, the Democratic side would flip: " + ", ".join(within) + ".")
    return out
