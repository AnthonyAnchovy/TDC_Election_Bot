"""Read House calls from the New York Times' results data: one file with every seat and, once called, its winner.

The file isn't published for outside use, so it can change or go away. The calls in it are the AP's (each race says
who called it). This only reads the file; bot.py decides what to do with it. A seat with a winner comes back as a clear
call or as something a person should look at (read()), and a seat with no winner doesn't come back at all.

A race that goes to a runoff has no winner yet, so it stays uncalled. Louisiana votes in a nonpartisan election, which
isn't what the bot means by a general election, so its winners go to a person.
"""
from __future__ import annotations

import asyncio
import json

import aiohttp

import results as LIVE

URL = "https://static01.nyt.com/elections-assets/pages/data/2026-11-03/results-house.json"
OUTLET = "NYT"
PARTY = {"DEM": "D", "GOP": "R", "IND": "I", "NPA": "I", "UNA": "I"}
ENOUGH_SEATS = 400     # fewer than this means the file isn't what it used to be, so don't trust it


def _seat(race: dict) -> str | None:
    """The bot's ID for a race: AL-G-H-1-2026-11-03 is AL-01. The national entry has no district and gets None."""
    parts = str(race.get("nyt_id", "")).split("-")
    return LIVE._house_id(parts[0], parts[3]) if len(parts) >= 7 and parts[2] == "H" else None


def _who(person: dict) -> str:
    return " ".join(x for x in (person.get("first_name"), person.get("last_name")) if x) or "the winner"


def read(data: dict) -> dict[str, dict]:
    """{rid: {"call": True, rid, party, who}} for each clear call, or {rid: {"review": why, rid}} for a seat with a winner
    a person should check. Raises RuntimeError when the file doesn't look like it should."""
    seats = {_seat(r): r for r in data.get("races", [])}
    seats.pop(None, None)
    if len(seats) < ENOUGH_SEATS:
        raise RuntimeError(f"the NYT file only has {len(seats)} House seats")
    found = {}
    for rid, race in seats.items():
        won = (race.get("outcome") or {}).get("won") or []
        if not won:
            continue
        if len(won) != 1:
            found[rid] = {"review": f"it lists {len(won)} winners", "rid": rid}
            continue
        person = (race.get("candidate_metadata") or {}).get(won[0]) or {}
        who, party = _who(person), PARTY.get((person.get("party") or {}).get("nyt_id"))
        if race.get("race_type_id") != "G":
            found[rid] = {"review": f"{who} won, but it's not a regular general election", "rid": rid}
        elif not party:
            found[rid] = {"review": f"couldn't tell {who}'s party", "rid": rid}
        else:
            found[rid] = {"call": True, "rid": rid, "party": party, "who": who}
    return found


async def fetch() -> dict[str, dict]:
    """Every seat with a winner right now, as read() gives them. The file is large, so it's read off the main thread."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25), headers={"User-Agent": LIVE.UA}) as s:
        async with s.get(URL) as r:
            r.raise_for_status()
            raw = await r.read()
    return await asyncio.to_thread(lambda: read(json.loads(raw)))
