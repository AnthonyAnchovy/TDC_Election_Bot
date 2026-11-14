"""Pure scoring and counting functions (no Discord), so they can be tested directly."""
from __future__ import annotations

import competitive as CMP
import night as N
import packs
import races as R

class _Emoji(dict):
    """The dot shown beside an option. One the pack gives no emoji (a candidate in a nonpartisan race) gets a
    plain dot rather than an error."""

    def __missing__(self, key):
        return "⚪"


# All four are refilled from the active pack's "options" and "game" blocks (see _fill).
EMOJI = _Emoji({"D": "🔵", "R": "🔴", "I": "🟣"})
DEFAULT_POINTS = {"senate": 1, "senate_control": 5, "house_control": 5, "governor": 1, "battleground": 1,
                  "flip_bonus": 1}
CATEGORIES = ("senate", "senate_control", "house_control", "governor", "battleground")
GAME: dict = {}
PART_NAMES = {"senate": "Senate", "house_control": "House majority", "governor": "governors",
              "battleground": "House seats", "tiebreak": "tiebreaker"}


@packs.on_activate
def _fill(pack: packs.Pack):
    global EMOJI, DEFAULT_POINTS, CATEGORIES, GAME, PART_NAMES
    GAME = pack.game
    EMOJI = _Emoji({key: o["emoji"] for key, o in pack.options.items() if o.get("emoji")})
    cats = GAME.get("categories") or []
    CATEGORIES = tuple(c["key"] for c in cats)
    DEFAULT_POINTS = {**{c["key"]: c.get("points", 1) for c in cats}, "flip_bonus": GAME.get("flip_bonus", 0)}
    PART_NAMES = {**{c["key"]: c.get("name", c["key"]) for c in cats},
                  "tiebreak": (GAME.get("tiebreaker") or {}).get("short", "tiebreaker")}


def _in(rule: dict, rid: str) -> bool:
    """Whether a race falls under a category's "races" rule: {"type": "senate"}, {"ids": [...]}, {"battleground":
    true} (the pack's bonus seats) or {"rated": true} (anything the pack rates)."""
    r = R.RACES[rid]
    if "ids" in rule:
        return rid in rule["ids"]
    if rule.get("battleground"):
        return rid in CMP.RATING
    if rule.get("rated"):
        return rid in CMP.RATING_OF
    if "type" in rule:
        return r["type"] == rule["type"] and ("body" not in rule or r.get("body") == rule["body"])
    return False


def category_races(key: str) -> list[str]:
    """Every race in one of the game's categories, in the pack's order."""
    rule = next((c["races"] for c in GAME.get("categories") or [] if c["key"] == key), None)
    return [rid for rid in R.RACES if _in(rule, rid)] if rule else []


# ------------------------------------------------------------------ race helpers
def holder(rid: str) -> str | None:
    return R.RACES[rid].get("holder") or CMP.HOLDER_OF.get(rid) or CMP.HOLDER.get(rid)


def tag(rid: str, party: str) -> str:
    """'(D HOLD)', '(R PICKUP)', or '(D)' when we don't know who held the seat. For an option that isn't a party
    (a candidate in a nonpartisan race) the key is left out: '(HOLD)', '(PICKUP)', '(WIN)'."""
    h = holder(rid)
    word = "WIN" if not h else "HOLD" if h == party else "PICKUP"
    if party not in packs.active().options:
        return f"({word})"
    return f"({party} {word})" if h else f"({party})"


def category(rid: str) -> str | None:
    """Which part of the prediction game a race belongs to: the first of the pack's categories whose rule it fits."""
    for c in GAME.get("categories") or []:
        if _in(c["races"], rid):
            return c["key"]
    return None


# ------------------------------------------------------------------ results
def opens(rid: str) -> float:
    """When a race can first have a result: its jurisdiction's first poll closing. A race with no jurisdiction
    (control of a chamber): the first closing anywhere."""
    st = R.RACES[rid]["state"]
    return (N.first_close(st) if st else N.first_anywhere()).timestamp()


def truth(desk_calls: dict[str, str], procalls: list[dict], mode: str = "pros_then_desk", *,
          called_at: dict[str, float] | None = None, finals: dict[str, str] | None = None,
          gate: bool = True) -> dict[str, str]:
    """What counts as the result of each race: a final result an admin entered, else the earliest pro call, else
    the desk's call (mode "desk": the desk's call only, after any final).

    With gate on, a call stamped before the race's polls closed doesn't count. That's what a rehearsal call left in
    the database is, and on Nov 2 2026 one of those scored everyone "1/1 right" a day early. Desk calls are only
    gated when called_at gives their times."""
    def live(rid: str, ts: float | None) -> bool:
        return rid in R.RACES and (not gate or ts is None or ts >= opens(rid))

    out = {rid: p for rid, p in desk_calls.items() if live(rid, (called_at or {}).get(rid))}
    if mode != "desk":
        seen = set()
        for p in sorted(procalls, key=lambda p: p["ts"]):
            if p["race_id"] not in seen and live(p["race_id"], p["ts"]):
                out[p["race_id"]] = p["party"]
                seen.add(p["race_id"])
    out.update({rid: p for rid, p in (finals or {}).items() if rid in R.RACES})
    return out


# ------------------------------------------------------------------ prediction game
def senate_from_picks(picks: dict[str, str]) -> dict[str, int]:
    vals = [picks[r] for r in R.races_of("senate") if r in picks]
    return {"D": R.SENATE_HOLDOVER["D"] + vals.count("D"), "R": R.SENATE_HOLDOVER["R"] + vals.count("R"),
            "I": vals.count("I"), "unpicked": len(R.races_of("senate")) - len(vals)}


def body_pick(picks: dict[str, str], body: str) -> str | None:
    """Who a set of picks says wins a body (the Senate, the Electoral College): what the picked races add up to, by
    weight, on top of what each side already holds. None until every race in the body is picked, or when nobody
    reaches the majority (independents holding the balance). The body's "tie_goes_to" side needs one fewer."""
    b = R.BODIES.get(body) or {}
    need = b.get("majority")
    races = {rid: r for rid, r in R.RACES.items() if r["type"] != "control" and (r.get("body") == body or r["type"] == body)}
    if not need or not races or any(rid not in picks for rid in races):
        return None
    tally = dict(b.get("holdover") or {})
    for rid, r in races.items():
        tally[picks[rid]] = tally.get(picks[rid], 0) + r.get("weight", 1)
    tie = b.get("tie_goes_to")
    for key in sorted(tally, key=lambda k: -tally[k]):
        if tally[key] >= (need - 1 if key == tie else need):
            return key
    return None


def senate_control_pick(picks: dict[str, str]) -> str | None:
    """Who a set of Senate picks says controls the chamber. Nobody picks this separately: it's what their seats add
    up to, once every seat is picked."""
    return body_pick(picks, "senate")


def implied_picks(picks: dict[str, str]) -> dict[str, str]:
    """The picks nobody makes directly: for each category marked "implied_by", its control race and who the picks
    in the body it decides add up to. Left out while that isn't settled."""
    out = {}
    for c in GAME.get("categories") or []:
        if c.get("implied_by"):
            for rid in category_races(c["key"]):
                who = body_pick(picks, R.RACES[rid].get("body") or "")
                if who:
                    out[rid] = who
    return out


def implied_races() -> set[str]:
    return {rid for c in GAME.get("categories") or [] if c.get("implied_by") for rid in category_races(c["key"])}


def grade(picks: dict[str, str], results: dict[str, str], points: dict | None = None) -> dict:
    pts = {**DEFAULT_POINTS, **(points or {})}
    cats = {c: {"right": 0, "wrong": 0, "pending": 0, "picked": 0} for c in CATEGORIES}
    total, flips = 0, 0
    marks: dict[str, bool] = {}
    # A category marked "implied_by" isn't picked directly: it's what another category's picks add up to.
    worked_out = implied_picks(picks)
    implied = worked_out.get("US-SENATE")
    scored = {**{rid: p for rid, p in picks.items() if rid not in implied_races()}, **worked_out}
    for rid, party in scored.items():
        cat = category(rid) if rid in R.RACES else None
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
    complete = all(cats[c["key"]]["picked"] == len(category_races(c["key"]))
                   for c in GAME.get("categories") or [] if c.get("required"))
    return {"points": total, "cats": cats, "flips": flips, "marks": marks, "complete": complete,
            "senate_control": implied, "implied": worked_out}


def completion(picks: dict[str, str], meta: dict | None = None) -> dict:
    """How much of the game someone has filled in: picked and total per part, the tiebreaker if the pack has one,
    and an overall share. Parts that are worked out from other picks aren't counted."""
    out = {}
    for c in GAME.get("categories") or []:
        if not c.get("implied_by"):
            rids = category_races(c["key"])
            out[c["key"]] = (sum(1 for rid in rids if rid in picks), len(rids))
    asked = bool(GAME.get("tiebreaker"))
    tiebreak = bool(meta and meta.get("house_dem_seats") is not None)
    done = sum(a for a, _ in out.values()) + (tiebreak and asked)
    total = sum(b for _, b in out.values()) + asked
    return {"parts": out, "tiebreak": tiebreak, "share": done / total if total else 0.0,
            "missing": [name for name, (a, b) in out.items() if a < b] + ([] if tiebreak or not asked else ["tiebreak"])}


REMINDERS = ((7 * 86400, "a week"), (86400, "a day"), (3 * 3600, "three hours"))


def time_left(seconds: float, lead: str) -> str:
    """How long until picks close, in words. The reminder's own wording ("a day") unless a bot started late has
    less time than that left: then the real time, so a reminder sent a minute before the deadline says so."""
    nominal = {text: s for s, text in REMINDERS}.get(lead)
    if nominal is None or seconds >= 0.8 * nominal:
        return lead
    minutes = max(1, round(seconds / 60))
    if minutes < 90:
        return f"{minutes} minute{'s' * (minutes != 1)}"
    hours = round(seconds / 3600)
    return f"{hours} hours" if hours < 36 else f"{round(seconds / 86400)} days"


def reminder_due(now: float, deadline: float, sent) -> tuple[str, str] | None:
    """The reminder to post now, as (key, "a day"), or None. Reminders go out a week, a day and three hours before
    picks close. Only the latest one whose time has come is ever sent, so a bot started late posts one, not three."""
    if now >= deadline:
        return None
    passed = [(lead, text) for lead, text in REMINDERS if now >= deadline - lead]
    if not passed:
        return None
    lead, text = min(passed)
    key = f"picks-reminder-{lead}"
    return None if key in sent else (key, text)


def open_items(results: dict[str, str], final_house_dem: int | None, entries: dict | None = None) -> dict:
    """What still has to be settled before the game's result is final: races in the game with no result (control,
    Senate, governors and rated House seats under their own names, anything else a pack adds under "other"), the
    tiebreaker's answer, and entries with no tiebreaker guess."""
    kinds = {"senate": [], "governor": [], "control": [], "battleground": [], "other": []}
    for rid, r in R.RACES.items():
        if rid in results:
            continue
        if r["type"] in ("senate", "governor", "control"):
            kinds[r["type"]].append(rid)
        elif rid in CMP.RATING:
            kinds["battleground"].append(rid)
        elif category(rid):
            kinds["other"].append(rid)
    asked = bool(GAME.get("tiebreaker"))
    no_guess = sorted(uid for uid, (_picks, meta) in (entries or {}).items()
                      if asked and (not meta or meta.get("house_dem_seats") is None))
    return {**kinds, "house_seats": asked and final_house_dem is None, "no_tiebreak": no_guess,
            "closed": not any(kinds.values()) and (final_house_dem is not None or not asked)}


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
    """Seats (or electoral votes, or whatever a race's weight counts) each side has in a body: what was already
    theirs going in, plus the races called for them. `uncalled` is the weight still out."""
    body = R.BODIES.get(kind) or {}
    up = {rid: r for rid, r in R.RACES.items() if r["type"] != "control" and (r["type"] == kind or r.get("body") == kind)}
    out = {"D": 0, "R": 0, "I": 0}
    for key, n in (body.get("holdover") or {}).items():
        out[key] = out.get(key, 0) + n
    for key in (body.get("not_up") or {}).values():
        out[key] = out.get(key, 0) + 1
    uncalled = 0
    for rid, r in up.items():
        if rid in calls:
            out[calls[rid]] = out.get(calls[rid], 0) + r.get("weight", 1)
        else:
            uncalled += r.get("weight", 1)
    return {**out, "uncalled": uncalled}


def clinched(kind: str, calls: dict[str, str]) -> str | None:
    """Whoever's called seats already make a majority of a body, or None. The Senate's tie rule is the pack's
    ("tie_goes_to"): that side needs one seat fewer."""
    body = R.BODIES.get(kind) or {}
    need = body.get("majority")
    if not need:
        return None
    c = chamber_counts(kind, calls)
    tie = body.get("tie_goes_to")
    for key, n in c.items():
        if key != "uncalled" and n >= (need - 1 if key == tie else need):
            return key
    return None


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
        elif not d.get("outlet"):           # a call that followed an outlet's isn't a race against the outlets
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
