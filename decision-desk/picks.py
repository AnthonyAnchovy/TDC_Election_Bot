"""The prediction game's rules and its list of races for the active data pack, with no Discord and no web in them.

The website (web/app.py) is built on this. It answers: which races can be picked and who is in them, how the picker
is laid out, whether picks are still open for someone, whether a set of picks is valid, and what the one-click fills
put in. All of it comes from the pack: its races and their options, its game categories, its sections.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import clock
import competitive as CMP
import packs
import races as R
import scoring as S

HERE = Path(__file__).parent
DEFAULT_NAME = {"D": "the Democrat", "R": "the Republican", "I": "the independent"}
RATING_ORDER = ["Toss-up", "Tilt D", "Tilt R", "Lean D", "Lean R", "Likely D", "Likely R"]


def config() -> dict:
    """The bot's own settings (channels, roles, what to hide). Not about any one election."""
    try:
        return json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def points(cfg: dict | None = None) -> dict:
    """What each part of the game is worth: the pack's values. config.json's "points" can still override them for
    the pack that grew up with it (2026)."""
    cfg = config() if cfg is None else cfg
    override = (cfg.get("points") or {}) if packs.active().data.get("shared_files") else {}
    return {**S.DEFAULT_POINTS, **override}


def candidates() -> dict:
    return {k.upper(): v for k, v in packs.active().read("candidates.json").items() if not k.startswith("_")}


def options_for(rid: str, cands: dict | None = None) -> list[str]:
    """What can be picked in a race: the options its pack lists for it, or D and R plus an independent where
    candidates.json names one."""
    own = R.options(rid)
    if own:
        return [o["key"] for o in own]
    cands = candidates() if cands is None else cands
    return ["D", "R"] + (["I"] if "I" in cands.get(rid, {}) else [])


def name_of(rid: str, key: str, cands: dict | None = None) -> str:
    """Who or what an option is: the race's own name for it, else the nominee on file, else 'the Democrat'."""
    for o in R.options(rid) or []:
        if o["key"] == key and o.get("name"):
            return o["name"]
    cands = candidates() if cands is None else cands
    noun = packs.active().options.get(key, {}).get("noun")
    return cands.get(rid, {}).get(key) or (f"the {noun}" if noun else DEFAULT_NAME.get(key) or str(key).title())


def resolve_option(rid: str, text: str, cands: dict | None = None) -> str | None:
    """The option someone means by what they typed: its key ("D", "quimby"), a party name ("democrat",
    "Republican") or a candidate's name or surname ("Ossoff"). None if nothing in the race matches, or two do."""
    want = text.strip().lower()
    if not want:
        return None
    cands = candidates() if cands is None else cands
    keys = options_for(rid, cands)
    for key in keys:
        if want == key.lower():
            return key
    style = packs.active().options
    hits = []
    for key in keys:
        name = name_of(rid, key, cands).lower()
        words = {style.get(key, {}).get(w, "").lower() for w in ("name", "noun", "plural")} - {""}
        if want == name or want in name.split() or want in words or (len(want) >= 3 and name.startswith(want)):
            hits.append(key)
    return hits[0] if len(hits) == 1 else None


# ------------------------------------------------------------------ when picks can change
def deadline(cfg: dict | None = None) -> datetime:
    return packs.active().deadline()


def is_open(store, user_id: int, cfg: dict | None = None, now: float | None = None) -> bool:
    """Before the deadline, or inside a window an admin opened for this person with /unlockpicks."""
    now = clock.time() if now is None else now
    return now < deadline(cfg).timestamp() or now < store.setting("picks_unlocked", {}).get(str(user_id), 0)


def hidden(cfg: dict | None = None, now: float | None = None) -> bool:
    """Whether other people's picks are kept back until the deadline."""
    cfg = config() if cfg is None else cfg
    now = clock.time() if now is None else now
    return bool(cfg.get("hide_predictions_until_deadline", True)) and now < deadline(cfg).timestamp()


# ------------------------------------------------------------------ the races
def _race(rid: str, cands: dict, info: dict) -> dict:
    r = R.RACES[rid]
    st = r["state"]
    extra = info.get(rid) or {}
    style = packs.active().options
    own = {o["key"]: o for o in R.options(rid) or []}
    opts = []
    for i, key in enumerate(options_for(rid, cands)):
        o = own.get(key, {})
        opts.append({"party": key, "name": name_of(rid, key, cands),
                     "named": bool(o.get("name") or cands.get(rid, {}).get(key)),
                     "color": o.get("color") or style.get(key, {}).get("color") or packs.PALETTE[i % len(packs.PALETTE)],
                     "tag": (o.get("party") or key) if (o.get("party") or key) in style else None})
    return {"id": rid, "type": r["type"], "state": st, "state_name": R.STATE_NAMES.get(st), "short": R.STATE_SHORT.get(st),
            "label": r["label"], "holder": r.get("holder") or CMP.HOLDER_OF.get(rid) or CMP.HOLDER.get(rid),
            "weight": r.get("weight", 1), "body": r.get("body"), "group": r.get("group"), "options": opts,
            "rating": CMP.RATING_OF.get(rid), "safe": CMP.SAFE.get(rid), "favored": CMP.favored(rid),
            "pvi": extra.get("pvi"), "incumbent": extra.get("incumbent"), "open": extra.get("open")}


def _by_rating(rid: str):
    rating = CMP.RATING.get(rid) or CMP.RATING_OF.get(rid)
    return (RATING_ORDER.index(rating) if rating in RATING_ORDER else 99, rid)


def sections() -> list[dict]:
    """How the picker is laid out: the pack's "sections", or one plain list per category if it doesn't say.
    kind is "map" (a U.S. map of statewide races), "house" (a control pick, the tiebreaker, and rated seats in a
    list) or "list"."""
    pack = packs.active()
    cats = {c["key"]: c for c in pack.game.get("categories") or []}
    laid = pack.data.get("sections") or [{"key": k, "name": c.get("name", k).title(), "kind": "list", "category": k}
                                         for k, c in cats.items() if not c.get("implied_by")]
    cands, info, pts = candidates(), pack.read("seat_info.json"), points()
    out = []
    for sec in laid:
        cat = cats.get(sec.get("category"), {})
        rids = S.category_races(sec["category"]) if sec.get("category") else []
        if sec["kind"] == "map":
            rids = sorted(rids, key=lambda x: R.RACES[x]["label"])
        elif any(rid in CMP.RATING for rid in rids):
            rids = sorted(rids, key=_by_rating)
        item = {**sec, "races": [_race(rid, cands, info) for rid in rids], "points": pts.get(sec.get("category"), 0),
                "required": bool(cat.get("required")), "blurb": sec.get("blurb") or cat.get("blurb")}
        if sec.get("control"):
            ckey = S.category(sec["control"])
            item["control"] = _race(sec["control"], cands, info)
            item["control_points"] = pts.get(ckey, 0)
        implied = next((c for c in cats.values() if c.get("implied_by") == sec.get("category")), None)
        if implied:
            item["implied"] = {"name": implied.get("name"), "points": pts.get(implied["key"], 0)}
        out.append(item)
    return out


def catalogue() -> dict:
    """Everything the picker needs to know about the active pack."""
    pack = packs.active()
    tb = pack.game.get("tiebreaker")
    return {"pack": pack.summary(), "sections": sections(),
            "bodies": {k: {"name": b.get("name", k), "seats": b.get("seats"), "majority": b.get("majority"),
                           "holdover": b.get("holdover") or {}, "tie_goes_to": b.get("tie_goes_to")}
                       for k, b in pack.bodies.items()},
            "options": {k: {"name": o.get("name", k), "plural": o.get("plural") or o.get("name", k),
                            "color": o.get("color")} for k, o in pack.options.items()},
            "categories": [{"key": c["key"], "name": c.get("name", c["key"]), "implied": bool(c.get("implied_by")),
                            "ids": S.category_races(c["key"]) if len(S.category_races(c["key"])) == 1 else None}
                           for c in pack.game.get("categories") or []],
            "tiebreaker": tb, "flip_bonus": pack.game.get("flip_bonus", 0)}


def pickable() -> set[str]:
    """Every race someone can pick: the game's categories, minus the ones worked out from other picks."""
    out: set[str] = set()
    for c in packs.active().game.get("categories") or []:
        if not c.get("implied_by"):
            out |= set(S.category_races(c["key"]))
    return out


def safe_fill() -> dict[str, str]:
    """The races the pack rates safe, as the picker pre-fills them."""
    return {rid: p for rid, p in CMP.SAFE.items() if rid in R.RACES}


def ratings_fill() -> dict[str, str]:
    """Bonus seats the ratings lean one way (Tilt, Lean, Likely), filled for the favored side."""
    return {rid: CMP.favored(rid) for rid, _rating, _h in CMP.BATTLEGROUND if rid in R.RACES and CMP.favored(rid)}


def validate(picks: dict, tiebreak) -> tuple[dict[str, str], int | None, list[str]]:
    """Clean a submitted entry. Returns the picks that are valid, the tiebreaker, and what was wrong with the rest."""
    cands, allowed, clean, problems = candidates(), pickable(), {}, []
    if not isinstance(picks, dict):
        return {}, None, ["picks must be an object of race: option"]
    for rid, key in picks.items():
        if rid not in allowed:
            problems.append(f"{rid} isn't a race in the game")
        elif key not in options_for(rid, cands):
            problems.append(f"{rid} has no {key} candidate")
        else:
            clean[rid] = key
    seats, rule = None, packs.active().game.get("tiebreaker")
    if tiebreak not in (None, ""):
        if not rule:
            problems.append("this game has no tiebreaker")
        else:
            lo, hi = rule.get("min", 0), rule.get("max", 10 ** 9)
            try:
                seats = int(tiebreak)
            except (TypeError, ValueError):
                seats = lo - 1
            if not lo <= seats <= hi:
                problems.append(f"the tiebreaker has to be a whole number from {lo} to {hi}")
                seats = None
    return clean, seats, problems
