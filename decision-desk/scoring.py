"""Pure scoring and counting functions (no Discord), so they can be tested directly."""
from __future__ import annotations

import competitive as CMP
import races as R

EMOJI = {"D": "🔵", "R": "🔴", "I": "🟣"}
DEFAULT_POINTS = {"senate": 1, "house_control": 5, "governor": 1, "battleground": 1, "flip_bonus": 1}


# ------------------------------------------------------------------ race helpers
def holder(rid: str) -> str | None:
    return R.RACES[rid].get("holder") or CMP.HOLDER_OF.get(rid) or CMP.HOLDER.get(rid)


def tag(rid: str, party: str) -> str:
    """'(D HOLD)', '(R PICKUP)', or '(D)' when we don't know who held the seat."""
    h = holder(rid)
    if not h:
        return f"({party})"
    return f"({party} {'HOLD' if h == party else 'PICKUP'})"


def category(rid: str) -> str | None:
    """Which prediction bucket a race belongs to."""
    kind = R.RACES[rid]["type"]
    if kind == "senate":
        return "senate"
    if kind == "governor":
        return "governor"
    if kind == "control":
        return "house_control"
    if rid in CMP.RATING:
        return "battleground"
    return None


# ------------------------------------------------------------------ results
def truth(desk_calls: dict[str, str], procalls: list[dict], mode: str = "pros_then_desk") -> dict[str, str]:
    """What counts as the result of each race. Pro calls (earliest per race) beat desk calls."""
    out = dict(desk_calls)
    if mode != "desk":
        seen = set()
        for p in sorted(procalls, key=lambda p: p["ts"]):
            if p["race_id"] not in seen:
                out[p["race_id"]] = p["party"]
                seen.add(p["race_id"])
    return out


# ------------------------------------------------------------------ prediction game
def senate_from_picks(picks: dict[str, str]) -> dict[str, int]:
    vals = [picks[r] for r in R.races_of("senate") if r in picks]
    return {"D": R.SENATE_HOLDOVER["D"] + vals.count("D"), "R": R.SENATE_HOLDOVER["R"] + vals.count("R"),
            "I": vals.count("I"), "unpicked": len(R.races_of("senate")) - len(vals)}


def grade(picks: dict[str, str], results: dict[str, str], points: dict | None = None) -> dict:
    pts = {**DEFAULT_POINTS, **(points or {})}
    cats = {c: {"right": 0, "wrong": 0, "pending": 0, "picked": 0}
            for c in ("senate", "house_control", "governor", "battleground")}
    total, flips = 0, 0
    marks: dict[str, bool] = {}
    for rid, party in picks.items():
        cat = category(rid)
        if not cat:
            continue
        c = cats[cat]
        c["picked"] += 1
        if rid not in results:
            c["pending"] += 1
            continue
        ok = results[rid] == party
        marks[rid] = ok
        if ok:
            c["right"] += 1
            total += pts[cat]
            h = holder(rid)
            if h and h != party:
                flips += 1
                total += pts["flip_bonus"]
        else:
            c["wrong"] += 1
    complete = cats["senate"]["picked"] == len(R.races_of("senate")) and cats["house_control"]["picked"] == 1
    return {"points": total, "cats": cats, "flips": flips, "marks": marks, "complete": complete}


def leaderboard(entries: dict[int, tuple[dict, dict | None]], results: dict[str, str],
                points: dict | None = None, final_house_dem: int | None = None) -> list[dict]:
    """entries: user_id -> (picks, meta). Tiebreak: closest guess of Democratic House seats."""
    rows = []
    for uid, (picks, meta) in entries.items():
        g = grade(picks, results, points)
        guess = meta.get("house_dem_seats") if meta else None
        g.update(user_id=uid, guess=guess,
                 miss=abs(guess - final_house_dem) if guess is not None and final_house_dem is not None else None)
        rows.append(g)
    rows.sort(key=lambda r: (-r["points"], r["miss"] if r["miss"] is not None else 10**6,
                             -sum(c["right"] for c in r["cats"].values())))
    return rows


def differences(a: dict[str, str], b: dict[str, str]) -> list[tuple[str, str | None, str | None]]:
    out = []
    for rid in sorted(set(a) | set(b)):
        if a.get(rid) != b.get(rid):
            out.append((rid, a.get(rid), b.get(rid)))
    return out


# ------------------------------------------------------------------ path to majority
def chamber_counts(kind: str, calls: dict[str, str]) -> dict:
    up = R.races_of(kind)
    got = [calls[r] for r in up if r in calls]
    if kind == "senate":
        base = dict(R.SENATE_HOLDOVER)
    elif kind == "governor":
        base = {"D": 0, "R": 0}
        for p in R.GOVERNOR_NOT_UP.values():
            base[p] += 1
    else:
        base = {"D": 0, "R": 0}
    return {"D": base["D"] + got.count("D"), "R": base["R"] + got.count("R"),
            "I": got.count("I"), "uncalled": len(up) - len(got)}


def path_text(kind: str, calls: dict[str, str]) -> str:
    c = chamber_counts(kind, calls)
    d, r, unc = c["D"], c["R"], c["uncalled"]
    if kind == "senate":
        d_need, r_need = R.SENATE_MAJORITY - d, 50 - r  # VP (R) breaks a 50-50 tie
        label = "Senate"
    elif kind == "house":
        d_need, r_need = R.HOUSE_MAJORITY - d, R.HOUSE_MAJORITY - r
        label = "House"
    else:
        return f"Governors: D {d} · R {r} · {unc} uncalled"
    if d_need <= 0:
        return f"{label}: **Democrats clinch control** ({d} seats)"
    if r_need <= 0:
        tie = " with the VP tiebreak" if kind == "senate" and r == 50 else ""
        return f"{label}: **Republicans clinch control**{tie} ({r} seats)"
    if d_need > unc + c["I"]:
        return f"{label}: Democrats can no longer reach a majority · R need {r_need} of {unc} uncalled"
    return f"{label}: D need **{d_need}** of {unc} uncalled · R need **{r_need}**"


# ------------------------------------------------------------------ calling scoreboard
def minutes_text(minutes: float) -> str:
    m = abs(round(minutes))
    return "under a minute" if m == 0 else f"{m} min"


def signed(minutes: float) -> str:
    m = round(minutes)
    return f"+{m}m" if m > 0 else f"{m}m"


def caller_stats(votes: list[tuple], procalls: list[dict], results: dict[str, str]) -> dict[int, dict]:
    """votes: (user_id, race_id, party, ts) oldest first, every call vote ever cast.
    A person's call on a race is their latest vote; lead = outlet time minus their vote time."""
    latest: dict[tuple[int, str], tuple[str, float]] = {}
    for uid, rid, party, ts in votes:
        latest[(uid, rid)] = (party, ts)

    first_correct: dict[str, tuple[float, int]] = {}
    for (uid, rid), (party, ts) in latest.items():
        if results.get(rid) == party and (rid not in first_correct or ts < first_correct[rid][0]):
            first_correct[rid] = (ts, uid)

    pros_by_race: dict[str, list[dict]] = {}
    for p in procalls:
        pros_by_race.setdefault(p["race_id"], []).append(p)

    stats: dict[int, dict] = {}
    for (uid, rid), (party, ts) in latest.items():
        s = stats.setdefault(uid, {"calls": 0, "correct": 0, "wrong": 0, "pending": 0, "first": 0,
                                   "beat": 0, "by_outlet": {}, "best": None})
        s["calls"] += 1
        if rid not in results:
            s["pending"] += 1
            continue
        if results[rid] != party:
            s["wrong"] += 1
            continue
        s["correct"] += 1
        if first_correct.get(rid, (None, None))[1] == uid:
            s["first"] += 1
        for p in pros_by_race.get(rid, []):
            if p["party"] != party:
                continue
            lead = (p["ts"] - ts) / 60
            s["by_outlet"].setdefault(p["outlet"], []).append(lead)
            if lead >= 0.5:  # at least 30 seconds ahead counts as beating them
                s["beat"] += 1
                if not s["best"] or lead > s["best"][0]:
                    s["best"] = (lead, rid, p["outlet"])
    return stats


def desk_vs_pros(desk: dict[str, dict], procalls: list[dict]) -> dict:
    """desk: race -> {party, called_at}. Per-outlet leads for the official desk."""
    by_outlet: dict[str, list[float]] = {}
    disagree, pros_only = set(), set()
    for p in procalls:
        d = desk.get(p["race_id"])
        if not d:
            pros_only.add(p["race_id"])
        elif d["party"] != p["party"]:
            disagree.add(p["race_id"])
        else:
            by_outlet.setdefault(p["outlet"], []).append((p["ts"] - d["called_at"]) / 60)
    called_by_pros = {p["race_id"] for p in procalls}
    return {"by_outlet": by_outlet, "disagree": sorted(disagree), "pros_only": sorted(pros_only),
            "desk_only": sorted(r for r in desk if r not in called_by_pros)}


# ------------------------------------------------------------------ House ratings math
def house_tracker(results: dict[str, str]) -> dict:
    """Start from the ratings baseline (D 223 = every Safe/Likely/Lean/Tilt D seat) and adjust for each
    called seat: a D-favored seat lost subtracts one; any other rated seat (toss-up or R-leaning) won by
    D adds one, since the 223 doesn't count it."""
    lost, gained = [], []
    for rid, rating, _ in CMP.RATINGS:
        if rid not in results:
            continue
        fav, won = CMP.favored(rid), results[rid]
        if fav == "D" and won != "D":
            lost.append(rid)
        elif fav != "D" and won == "D":
            gained.append(rid)
    d = CMP.D_BASELINE - len(lost) + len(gained)
    called = sum(1 for rid, _, _ in CMP.RATINGS if rid in results)
    return {"d": d, "lost": lost, "gained": gained, "called": called, "rated": len(CMP.RATINGS)}


def house_tracker_text(results: dict[str, str]) -> str:
    t = house_tracker(results)
    diff = t["d"] - CMP.D_BASELINE
    head = (f"Ratings math: Dems at **{t['d']}** if the rest go as rated "
            f"({'+' if diff >= 0 else ''}{diff} vs. the 223 baseline · {R.HOUSE_MAJORITY} for majority)")
    bits = []
    if t["gained"]:
        bits.append("D beat the ratings in " + ", ".join(t["gained"]))
    if t["lost"]:
        bits.append("D lost favored " + ", ".join(t["lost"]))
    return head + (f"\n-# {' · '.join(bits)} · {t['called']}/{t['rated']} rated seats called" if bits
                   else f"\n-# {t['called']}/{t['rated']} rated seats called, no upsets yet")


def upset_note(rid: str, winner: str) -> str | None:
    fav = CMP.favored(rid)
    rating = CMP.RATING_OF.get(rid)
    if not rating:
        return None
    if fav and fav != winner:
        return f"🚨 **Upset:** the ratings had this {rating}."
    if not fav:
        return f"Rated a toss-up going in."
    return f"Rated {rating} going in."


def early_watch(results: dict[str, str]) -> list[tuple[str, str, str | None, str | None]]:
    """(race, rating, favored party, result or None) for each early-closing rated seat."""
    return [(rid, CMP.RATING_OF[rid], CMP.favored(rid), results.get(rid)) for rid in CMP.EARLY_WATCH]
