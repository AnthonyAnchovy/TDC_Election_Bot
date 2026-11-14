"""What the active data pack's maps and tallies are, for /map, /board, the posts after a call and the poll-closing
alerts (no Discord here).

A view is one of the pack's picker sections, drawn on its own:
  "tiles"   a U.S. map of statewide races, one tile per state (Senate, governors, a president's states)
  "house"   the House chart: every district called, and the path to a majority
  "wards"   one race in a city the pack has wards for: the city by ward (Chicago)
  "list"    races that aren't on a map (a ballot question), as lines of text

2026 has three (Senate, House, governors), 2028 adds President, and the Chicago template has two lists. Nothing here
names a chamber: what a view counts toward, and what a majority of it is, comes from the pack's "bodies".
"""
from __future__ import annotations

import packs
import picks as P
import races as R

DRAWS = ("tiles", "house", "wards", "list")


def has_wards(rid: str) -> bool:
    """Whether the pack has a ward map for the place this race is in (its shapes.json)."""
    import graphics as G
    return bool((G.place(R.RACES[rid]["state"]) or {}).get("parts"))


def _types(rids: list[str]) -> list[str]:
    return list(dict.fromkeys(R.RACES[rid]["type"] for rid in rids))


def views() -> list[dict]:
    """The pack's views, in the picker's order: {"key", "name", "draw", "type", "body", "races", "control"}.
    `type` is the race type a tile map draws; `body` is what the races add up to, if the pack says (else None);
    `control` is the race that decides the body, if there is one."""
    out = []
    for sec in P.sections():
        rids = [r["id"] for r in sec["races"]]
        types = _types(rids)
        if sec["kind"] == "house":
            draw, kind = "house", "house"
        elif sec["kind"] == "map" and len(types) == 1 and all(R.RACES[rid]["state"] for rid in rids):
            draw, kind = "tiles", types[0]
        elif len(rids) == 1 and has_wards(rids[0]) and P.options_for(rids[0]):
            draw, kind = "wards", types[0]
        else:
            draw, kind = "list", types[0] if len(types) == 1 else None
        body = sec.get("body") or (kind if kind in R.BODIES else None)
        if body is None and rids and R.RACES[rids[0]].get("body") in R.BODIES:
            body = R.RACES[rids[0]]["body"]
        control = (sec.get("control") or {}).get("id") or (R.BODIES.get(body) or {}).get("control")
        if control and control.upper() not in R.RACES:
            control = None
        out.append({"key": sec["key"], "name": sec["name"], "draw": draw, "type": kind, "body": body,
                    "races": rids, "control": control.upper() if control else None})
    return out


def find(key: str) -> dict | None:
    return next((v for v in views() if v["key"] == key), None)


def of_race(rid: str) -> dict | None:
    """The view a race belongs to. A control race belongs to the view of the body it decides."""
    r = R.RACES.get(rid)
    if not r:
        return None
    for v in views():
        if rid in v["races"] or (r["type"] == "control" and (v["control"] == rid or v["body"] == r.get("body"))):
            return v
    return None


def statewide_types() -> list[str]:
    """Race types drawn as tile maps: the ones a poll-closing alert lists by state."""
    return [v["type"] for v in views() if v["draw"] == "tiles"]


def watched() -> set[str]:
    """The races worth listing when polls close or in /next: every statewide race, the pack's rated seats, and
    every race of a pack with no map at all (a city election is all of one list)."""
    import competitive as CMP
    vs = views()
    out = {rid for v in vs if v["draw"] in ("tiles", "list", "wards") for rid in v["races"]}
    return out | set(CMP.RATING)


def tally(view: dict, calls: dict[str, str]) -> str:
    """The view's state of play in one or two lines: the path to a majority of its body, or, for a list, each
    race and who the desk called it for."""
    import scoring as S
    if view["draw"] in ("list", "wards"):
        lines = []
        for rid in view["races"]:
            got = calls.get(rid)
            lines.append(f"{R.RACES[rid]['label']}: " + (f"{S.EMOJI[got]} **{P.name_of(rid, got)}**" if got else "not called"))
        return "\n".join(lines)
    return S.path_text(view["body"] or view["type"], calls)


def examples(n: int = 4) -> str:
    """A few race IDs from the pack, for a command's help text: "GA-SEN, AZ-GOV, PA-07, HOUSE" in 2026."""
    import competitive as CMP
    picked = []
    for v in views():
        rated = [rid for rid in v["races"] if rid in CMP.RATING_OF]
        choice = (rated or v["races"])[:1]
        picked += choice
    for v in views():
        if v["control"] and v["control"] not in picked:
            picked.append(v["control"])
    shown = []
    for rid in picked[:n]:
        r = R.RACES[rid]
        shown.append(R.chamber_name(rid).upper() if r["type"] == "control" and rid.startswith("US-") else rid)
    return "e.g. " + ", ".join(shown) if shown else "a race's ID"


def sample_card() -> tuple[str, str]:
    """A race and winner to show a card theme with: a flip if the pack has one to show, else any race."""
    import competitive as CMP
    for rid, r in R.RACES.items():
        holder = r.get("holder") or CMP.HOLDER_OF.get(rid)
        if r["type"] != "control" and holder and r["state"] in packs.STATE_NAMES:
            other = next((k for k in P.options_for(rid) if k != holder), None)
            if other:
                return rid, other
    rid = next(rid for rid, r in R.RACES.items() if r["type"] != "control")
    return rid, P.options_for(rid)[0]
