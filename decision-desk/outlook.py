"""The outlook: everything the website's results pages show, as one plain dictionary (no Discord, no web here).

It puts the forecast (cw_forecast), the desk's calls, the vote still out (remaining), the national read and the
feeds' health side by side, and words each race for someone who doesn't follow politics.

Built to keep answering. latest() works the outlook out at most every REFRESH seconds, keeps the last good one on
disk, and hands that back (marked stale, with why) if anything goes wrong: a half-written live.json, a feed that
changed shape, a bug. The website never shows an error page because the numbers hiccuped; it shows the last
numbers and says how old they are.
"""
from __future__ import annotations

import json
import logging
import threading
import time

import feeds as FEEDS
import packs

log = logging.getLogger("desk.outlook")
REFRESH = 20            # seconds between rebuilds, however many people are looking
TREND_POINTS = 90       # points kept per race for the night's chart
_lock = threading.Lock()
_memo: dict = {}        # {(pack id, server): (built at, outlook)}


# ------------------------------------------------------------------ words for people who don't follow politics
def _pts(m: float) -> str:
    v = abs(m) * 100
    return "less than a point" if v < 0.95 else "about a point" if v < 1.5 else f"about {v:.0f} points"


def chance_words(p: float) -> str:
    """How likely the leader is to win, the way a person would say it."""
    side = max(p, 1 - p)
    return ("a toss-up" if side < 0.55 else "slightly favored" if side < 0.70 else "favored" if side < 0.85
            else "likely to win" if side < 0.95 else "very likely to win" if side < 0.995 else "all but certain to win")


def plain(r: dict) -> str:
    """One or two sentences on a race, with no jargon."""
    lead, trail = (r["d_name"], r["r_name"]) if r["margin"] >= 0 else (r["r_name"], r["d_name"])
    side = max(r["win_d"], 1 - r["win_d"])
    where = r["place"]
    if r["called"]:
        who = r["d_name"] if r["called"] == r["d_key"] else r["r_name"]
        return f"{who} has won {where}, by the desk's call. The count has them ahead by {_pts(r['margin'])}." if r["counted"] else \
            f"{who} has won {where}, by the desk's call."
    if r["stage"] == "prior":
        if side < 0.55:
            return f"No results yet in {where}. Going in, it looks like a toss-up between {r['d_name']} and {r['r_name']}."
        return (f"No results yet in {where}. Going in, {lead} is {chance_words(side)}: polls and what other states are "
                f"showing point to a win by {_pts(r['margin'])}, but that can easily change once votes arrive.")
    counted = f"{r['counted_share']:.0%} of the expected vote counted"
    if side < 0.55:
        return f"{where} is too close to say. With {counted}, {lead} is ahead by {_pts(r['margin'])} in our estimate of the final result."
    if side >= 0.995:
        return f"{lead} is all but certain to win {where}: ahead by {_pts(r['margin'])} with {counted}."
    tail = ""
    if r.get("need_share") and r.get("pace_share") and r["need_share"] <= 1:
        tail = (f" {trail} would need {r['need_share']:.0%} of the votes still out, and is on course for about "
                f"{r['pace_share']:.0%}.")
    return (f"{lead} is {chance_words(side)} in {where}. Our estimate of the final result has them ahead by "
            f"{_pts(r['margin'])}, with {counted}.{tail}")


def chamber_words(name: str, side_name: str, other_name: str, p: float, clinched: str | None) -> str:
    if clinched:
        return f"{clinched} have won control of the {name}."
    lead = side_name if p >= 0.5 else other_name
    side = max(p, 1 - p)
    if side < 0.55:
        return f"Control of the {name} is a toss-up."
    return f"{lead} are {chance_words(side)} the {name}."


# ------------------------------------------------------------------ the pieces
def _trend(snaps: list[dict], rid: str) -> list[list]:
    pts = [[round(s["ts"]), s["races"][rid][0], s["races"][rid][1], s["races"][rid][2],
            (s.get("fc") or {}).get(rid, [None])[0]] for s in snaps if rid in s.get("races", {}) and s["races"][rid][2] != "wait"]
    if len(pts) > TREND_POINTS:
        step = len(pts) / TREND_POINTS
        pts = [pts[int(i * step)] for i in range(TREND_POINTS - 1)] + [pts[-1]]
    return pts


def _race(rid: str, st: str, q: dict, calls: dict, cands: dict, snaps: list[dict]) -> dict:
    import cw_model as M
    import field as F
    import picks as P
    import races as R
    import remaining as REM
    d_key = F._side(rid, cands)
    d_name, r_name = P.name_of(rid, d_key, cands), P.name_of(rid, "R", cands)
    b = REM.breakdown(q, top=8)
    lo, hi = q["range90"]
    prior = q.get("prior") or {}
    out = {
        "id": rid, "state": st, "label": R.RACES[rid]["label"], "place": M.STATES.get(st, st),
        "d_key": d_key, "d_name": d_name, "r_name": r_name, "called": calls.get(rid),
        "holder": R.RACES[rid].get("holder"), "bellwether": st in M.BELLWETHER,
        "margin": q["proj_m"], "lo": lo, "hi": hi, "sd": q["sd"], "win_d": q["win_prob"], "stage": q["stage"],
        "data_weight": q.get("data_weight", 0.0), "confidence": q["confidence"],
        "counted": int(q["counted"]), "counted_share": min(1.0, b["share_in"]), "remaining": b["left"],
        "trailer": q.get("trailer"), "need_share": q.get("need_share"), "pace_share": q.get("pace_share"),
        "raw_m": q["raw_m"], "base_m": q["base_m"], "m24": q["m24"], "needed_swing": q["needed_swing"],
        "poll": prior.get("poll"), "prior_m": q["base_m"] + prior["swing"] if prior else None,
        "prior_sd": prior.get("sd"), "poll_miss": prior.get("poll_miss"), "national_swing": prior.get("national_swing"),
        "classic_m": (q.get("legacy") or {}).get("proj_m"), "swing": q.get("state_swing_model", q.get("state_swing")),
        "tilts": q.get("tilts"), "early_lean": q.get("early_lean"),
        "turnout_vs_22": q.get("turnout_vs_22"), "pace": q.get("pace"),
        "counties": len(q["rows"]), "finished": sum(1 for r in q["rows"] if r.get("fin", r["complete"])),
        "types": [{"type": t["type"], "counties": t["counties"], "complete": t["complete"], "share": t["share"],
                   "breakeven": t["breakeven"], "swing": t["swing"], "t_index": t["t_index"]}
                  for t in M.type_bench(st, q)],
        "left": [{"name": c["name"], "type": c["type"], "left": c["left"], "left_margin": c["left_margin"],
                  "share": c["share_of_left"], "counted": c["counted"], "margin": c["margin"]}
                 for c in sorted(b["counties"], key=lambda c: -c["left"])[:8] if c["left"] > 0.5],
        "keys": [{"name": r["name"], "why": r["why"], "share": r["share22"], "breakeven": r["breakeven"],
                  "margin": r["live_m"], "final": r.get("proj_m") if r["D"] + r["R"] else None,
                  "in": r.get("f", 1.0 if r["complete"] else r["est_in"]), "t_index": r.get("t_index")}
                 for r in M.key_table(st, q)],
        "trend": _trend(snaps, rid),
    }
    out["leader"] = d_name if out["margin"] >= 0 else r_name
    out["chance"] = chance_words(out["win_d"])
    out["plain"] = plain(out)
    return out


def _chambers(fcs: dict, calls: dict, cands: dict) -> list[dict]:
    import competitive as CMP
    import cw_forecast as CF
    import field as F
    import races as R
    import scoring as S
    out = []
    for key, body in R.BODIES.items():
        rids = [rid for rid, r in R.RACES.items() if r.get("body") == key and r["type"] != "control"]
        modeled = {rid: FEEDS.cw_state_of(rid) for rid in rids}
        if not body.get("majority") or not any(st in fcs for st in modeled.values()):
            continue
        hold = body.get("holdover") or {}
        fixed = hold.get("D", 0) + hold.get("I", 0)
        live, unknown = [], 0
        for rid in rids:
            d_key, st = F._side(rid, cands), modeled[rid]
            if calls.get(rid):
                fixed += calls[rid] == d_key
            elif st in fcs:
                q = fcs[st]
                live.append({"m": q["proj_m"], "sd": max(q["sd"], 1e-4), "weight": q.get("data_weight", 0.0)})
            elif CMP.SAFE.get(rid):
                fixed += CMP.SAFE[rid] in ("D", "I")
            else:
                live.append({"wp": 0.5})
                unknown += 1
        need = body["majority"] - (1 if body.get("tie_goes_to") == "D" else 0)
        odds = CF.chamber_odds(live, fixed, need)
        control = calls.get(body.get("control") or "")
        clinched = control or S.clinched(key, calls)
        names = {"D": "Democrats", "R": "Republicans"}
        p = 1.0 if clinched == "D" else 0.0 if clinched == "R" else odds["chance"]
        out.append({"key": key, "name": body.get("name", key), "majority": body["majority"], "need": need,
                    "seats": body.get("seats"), "in_hand": fixed, "in_play": len(live), "unmodeled": unknown,
                    "chance_d": p, "expected_d": odds["mean"], "median_d": odds["median"],
                    "dist": {str(k): round(v, 4) for k, v in odds["dist"].items()}, "clinched": clinched,
                    "plain": chamber_words(body.get("name", key), "Democrats", "Republicans", p, names.get(clinched))})
    return out


def _sources(cache: dict, now: float) -> dict:
    """Each state's sources in the order they're trusted, which one is in use, and the states reading a backup.
    Also what the intake check is holding back (intake.py)."""
    import countywatch_run as CWR
    try:
        report = CWR.source_report(cache)
    except Exception:                       # the feeds tile is a nice-to-have: never let it take the page down
        log.exception("sources for the outlook")
        report = {}
    age = lambda t: None if not t else max(0.0, now - t)       # noqa: E731
    sources, backup = {}, []
    for st, rows in sorted(report.items()):
        sources[st] = [{"name": s.get("name"), "order": s.get("order"), "in_use": bool(s.get("in_use")),
                        "answered_age": age(s.get("answered")), "changed_age": age(s.get("changed")),
                        "error": s.get("error"), "votes": s.get("votes")} for s in rows]
        using = next((s for s in sources[st] if s["in_use"]), None)
        if using and using["order"] > 1:
            first = sources[st][0]
            backup.append({"state": st, "using": using["name"], "first": first["name"], "first_error": first["error"],
                           "first_changed_age": first["changed_age"]})
    return {"sources": sources, "on_backup": backup, "held": dict(cache.get("notes") or {})}


def build(store) -> dict:
    """The outlook for the active pack, from the files on disk right now."""
    import countywatch_run as CWR
    import cw_model as M
    import field as F
    import picks as P
    pack = packs.active()
    out = {"at": time.time(), "pack": pack.id, "pack_name": pack.name, "races": [], "chambers": [], "national": None,
           "feeds": None, "early": [], "has_model": bool(FEEDS.countywatch() and M.STATES)}
    if not out["has_model"]:
        return out
    calls, cands = store.all(), P.candidates()
    cache = CWR.live_cache()
    fcs = CWR.forecasts(cache)
    snaps = F.history("senate")
    for st in M.STATES:
        rid = FEEDS.cw_race(st)
        if st in fcs and rid in __import__("races").RACES:
            out["races"].append(_race(rid, st, fcs[st], calls, cands, snaps))
    out["chambers"] = _chambers(fcs, calls, cands)
    n = M.national(fcs)
    any_prior = next((q["prior"] for q in fcs.values() if q.get("prior")), {})
    out["national"] = {"counties": n["counties"], "states": n["states"], "swing": n["swing"], "turnout": n["turnout"],
                       "by_type": [{"type": t, "swing": v, "counties": n["n_type"][t], "turnout": n["turnout_type"].get(t)}
                                   for t, v in sorted(n["by_type"].items(), key=lambda kv: -kv[1])],
                       "poll_miss": any_prior.get("poll_miss"), "national_swing": any_prior.get("national_swing")}
    now = time.time()
    out["feeds"] = {"fetched": cache.get("fetched"), "age": now - cache["fetched"] if cache.get("fetched") else None,
                    "errors": dict(cache.get("errors") or {}), "blocked": sorted(cache.get("blocked") or {}),
                    "throttled": sorted(cache.get("throttled") or {}), "states": sorted(cache.get("states") or {}),
                    **_sources(cache, now)}
    try:
        out["early"] = [{"state": st, "votes": v, "share22": sh, "reg_gap": gap} for st, v, sh, gap in CWR.early_pace()]
    except Exception:                       # early vote is a nice-to-have: never let it take the page down
        log.exception("early vote for the outlook")
    counted = sum(r["counted"] for r in out["races"])
    out["stage"] = "before" if not counted else "after" if all(r["counted_share"] > 0.985 or r["called"] for r in out["races"]) else "night"
    return out


# ------------------------------------------------------------------ keeping it up
def _keep_file():
    return packs.data_root() / packs.active().id / "outlook.json"


def latest(store, key: tuple = ()) -> dict:
    """The outlook, rebuilt at most every REFRESH seconds. If a rebuild fails, the last good one, marked stale."""
    with _lock:
        k = (packs.active().id, *key)
        at, good = _memo.get(k, (0.0, None))
        if good is not None and time.time() - at < REFRESH:
            return good
        try:
            fresh = build(store)
            _memo[k] = (time.time(), fresh)
            try:
                f = _keep_file()
                f.parent.mkdir(parents=True, exist_ok=True)
                tmp = f.with_suffix(".tmp")
                tmp.write_text(json.dumps(fresh), encoding="utf-8")
                tmp.replace(f)
            except OSError:
                pass
            return fresh
        except Exception as exc:
            log.exception("Couldn't build the outlook; serving the last good one")
            if good is None:
                try:
                    good = json.loads(_keep_file().read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    good = {"at": None, "pack": packs.active().id, "races": [], "chambers": [], "national": None,
                            "feeds": None, "early": [], "has_model": False}
            stale = {**good, "stale": True, "stale_why": f"{type(exc).__name__}: {exc}"[:200]}
            _memo[k] = (time.time(), stale)         # don't retry on every request while it's broken
            return stale
