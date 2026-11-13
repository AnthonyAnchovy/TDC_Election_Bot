"""The line that goes with a call: which one to use, and not the same one again (no Discord here).

On election night 2026 every projection card had a line, and it still felt as if none did. 118 of the 133 cards drew
from seven generic lines, so one of them appeared 28 times; only 16 races had lines of their own. This module fixes
the picking. The words stay in the pack's flavor.json, where anyone can edit them.

What gets used, in order:
  1. a line written for this race and this winner       flavor.json "races": {"GA-SEN": {"D": [...]}}
  2. a line for the kind of result it was               "upset", "flip", "hold", "control", else "call"
A pool is used like a deck: a line doesn't come round again until every other line in its pool has been used, and
what has been used is remembered in the server's database, so a restart doesn't reshuffle. Lines can name the
winner, the loser and the place: {winner} {loser} {place} {seat} {rating} {party}. A line whose placeholders can't
be filled for a race is skipped for it.

Other moments have pools too: "retract" (and a race's own {"retract": [...]}), "desk_ahead", "desk_behind",
"desk_wrong". If flavor.json has no lines for a moment, the built-in ones below are used, so there is always a line.
"""
from __future__ import annotations

import hashlib
import random
import string

import competitive as CMP
import packs
import races as R

BUILT_IN = {
    "call": ["The desk has seen enough: {winner} takes {seat}.", "{seat} goes to {winner}. No further questions.",
             "Called: {winner} in {place}.", "{winner} wins {seat}. The desk is moving on."],
    "hold": ["{winner} holds {seat}. Nothing changes hands.", "{place} stays put: {winner} keeps the seat.",
             "No change in {seat}. {winner} holds."],
    "flip": ["{seat} changes hands: {winner} takes it from {loser}.", "That's a pickup. {winner} flips {seat}.",
             "New management in {place}: {winner} wins."],
    "upset": ["The ratings had {seat} {rating}. {winner} had other ideas.",
              "Upset in {place}: {winner} wins a seat rated {rating}."],
    "control": ["{winner} take {seat}.", "It's settled: {seat} goes to {winner}."],
    "retract": ["Back to the counting room.", "The desk got ahead of itself. Stand by."],
    "desk_ahead": ["The desk beat {outlet} by {lead}."],
    "desk_behind": ["{outlet} got there first, by {lead}."],
    "desk_wrong": ["⚠️ The desk called this the other way."],
}
MEMORY = "flavor_used"          # the settings key: {pool: [fingerprints of lines used since the pool last ran out]}
KEEP = 400                      # pools remembered at most; the oldest are dropped past this


def _lines() -> dict:
    return packs.active().read("flavor.json")


def _mark(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def _needs(text: str) -> set[str]:
    try:
        return {name for _lit, name, _spec, _conv in string.Formatter().parse(text) if name}
    except ValueError:
        return {"?"}                # unbalanced braces: can't be filled, so never used


def _fits(text: str, fmt: dict) -> bool:
    return all(fmt.get(name) not in (None, "") for name in _needs(text))


def draw(store, pool: str, lines: list[str], fmt: dict | None = None, rng=random) -> str | None:
    """One line from a pool, filled in. Lines already used are skipped until the pool runs out; then it starts
    again, avoiding the line used last. With no store there's no memory, only chance."""
    fmt = fmt or {}
    usable = [t for t in dict.fromkeys(lines or []) if isinstance(t, str) and t.strip() and _fits(t, fmt)]
    if not usable:
        return None
    seen = dict(store.setting(MEMORY, {})) if store is not None else {}
    used = list(seen.get(pool, []))
    fresh = [t for t in usable if _mark(t) not in used]
    if not fresh:                   # every line has had its turn
        fresh = [t for t in usable if _mark(t) != (used[-1] if used else None)] or usable
        used = []
    text = rng.choice(fresh)
    if store is not None:
        seen.pop(pool, None)
        seen[pool] = used + [_mark(text)]
        while len(seen) > KEEP:
            seen.pop(next(iter(seen)))
        store.set_setting(MEMORY, seen)
    return text.format(**fmt) if fmt else text


def situation(rid: str, key: str) -> str:
    """What kind of result a call is: "control", "upset" (the ratings favored someone else), "flip" (the seat changes
    hands), "hold", or just "call" when nobody is known to hold it."""
    r = R.RACES[rid]
    if r["type"] == "control":
        return "control"
    fav = CMP.favored(rid)
    if fav and fav != key:
        return "upset"
    holder = r.get("holder") or CMP.HOLDER_OF.get(rid) or CMP.HOLDER.get(rid)
    if not holder:
        return "call"
    return "flip" if holder != key else "hold"


def context(rid: str, key: str, names: dict[str, str]) -> dict:
    """What a line can mention. names is {option key: display name} for the race."""
    r = R.RACES[rid]
    others = [n for k, n in names.items() if k != key and n]
    holder = r.get("holder") or CMP.HOLDER_OF.get(rid) or CMP.HOLDER.get(rid)
    loser = names.get(holder) if holder and holder != key and len(names) > 2 else (others[0] if len(others) == 1 else None)
    place = R.STATE_NAMES.get(r["state"]) if r["state"] else None
    winner = names.get(key) or R.PARTY_NAMES[key]
    return {"winner": winner[0].upper() + winner[1:], "loser": loser, "place": place or r["label"], "seat": r["label"],
            "rating": CMP.RATING_OF.get(rid), "party": R.PARTY_NAMES[key] if key in packs.active().options else None}


def _pool(kind: str) -> list[str]:
    return _lines().get(kind) or BUILT_IN.get(kind, [])


def call_line(store, rid: str, key: str, names: dict[str, str], rng=random) -> str | None:
    """The line for a projection: the race's own for this winner if it has one left, else one for the kind of
    result. Falls back through "call" and the built-in lines, so a call always gets something."""
    fmt = context(rid, key, names)
    own = (_lines().get("races", {}).get(rid) or {}).get(key) or []
    kind = situation(rid, key)
    for pool, lines in ((f"race:{rid}:{key}", own), (kind, _pool(kind)), ("call", _pool("call")),
                        ("call:built-in", BUILT_IN["call"])):
        text = draw(store, pool, lines, fmt, rng)
        if text:
            return text
    return None


def retract_line(store, rid: str, rng=random) -> str | None:
    """The line for a retraction: the race's own "retract" lines win over the generic ones."""
    own = (_lines().get("races", {}).get(rid) or {}).get("retract") or []
    return draw(store, f"race:{rid}:retract", own, rng=rng) or draw(store, "retract", _pool("retract"), rng=rng)


def line(store, kind: str, rng=random, **fmt) -> str | None:
    """A line for some other moment ("desk_ahead", "desk_behind", "desk_wrong"), filled with fmt."""
    return draw(store, kind, _pool(kind), fmt, rng) or draw(store, f"{kind}:built-in", BUILT_IN.get(kind, []), fmt, rng)


def coverage() -> dict:
    """How much of the pack has lines of its own: for the report in tests, and for whoever is writing them."""
    races = _lines().get("races", {})
    slots = [(rid, key) for rid, by in races.items() for key in by if key != "retract"]
    filled = [(rid, key) for rid, key in slots if races[rid][key]]
    return {"races_listed": len(races), "races_with_lines": len({rid for rid, _ in filled}),
            "slots": len(slots), "slots_filled": len(filled),
            "unknown_races": sorted(rid for rid in races if rid not in R.RACES),
            "pools": {k: len(v) for k, v in _lines().items() if isinstance(v, list)}}
