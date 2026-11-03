"""Read House calls from Bluesky: Decision Desk HQ's own account and the mirror of Dave Wasserman's posts.

Bluesky's public API needs no key. This only reads and sorts posts; bot.py decides what to do with them. A post comes
back as one of three things: a clear call, a post a person should look at, or nothing (read()).

The accounts are pinned by their permanent IDs, so a new account can't pass for them by taking a handle. Wasserman's
account here is an unofficial mirror of his posts, used on purpose (it's the account that's active on Bluesky).
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp

import races as R
import results as LIVE

HERE = Path(__file__).parent
API = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
DDHQ = "Decision Desk HQ"
WASSERMAN = "Dave Wasserman"
SOURCES = {"did:plc:catipgdd42lzwcqfsfcc6ype": DDHQ, "did:plc:oegdayoj7y4heuqwngwg2d2p": WASSERMAN}
# Older posts are ignored: 5 PM ET, an hour before the first polls close
SINCE = datetime(2026, 11, 3, 17, 0, tzinfo=ZoneInfo("America/New_York"))

STATE_BY_NAME = {name.upper(): st for st, name in R.STATE_NAMES.items()}
_DDHQ_CALL = re.compile(r"^Decision Desk HQ projects\s+(?:that\s+)?(?P<who>.+?)\s+wins the\s+(?:20\d\d\s+)?"
                        r"(?P<state>.+?)\s+US House\s+"
                        r"(?P<seat>\d+|At-Large)\b(?P<rest>.*?)(?:#DecisionMade|$)", re.S)
_SEEN_ENOUGH = re.compile(r"^I[’']ve seen enough\b[:,]?\s*(?P<body>.+)", re.S | re.I)
_SEAT_TAG = re.compile(r"#([A-Z]{2})-?(\d{1,2}|AL)\b")
_WINNER = re.compile(r"^(?:(?:Rep|Sen|Gov|Del)\.\s+)?(?P<who>[^()]+?)\s*\((?P<party>[DRI])\)\s+"
                     r"(?:defeats|beats|ousts|unseats|wins|holds|keeps|flips)\b")
_NOT_GENERAL = re.compile(r"\b(primary|runoff|advances?)\b", re.I)
_CORRECTION = re.compile(r"\b(retract|rescind)\w*\b.*\b(call|projection)\b|\b(call|projection)\b.*\b(retract|rescind)\w*\b",
                         re.I | re.S)
_SUFFIX = {"JR", "SR", "II", "III", "IV", "V"}


def _house_seat(state: str, seat: str) -> str | None:
    return LIVE._house_id(state, 1 if seat.upper() in ("AT-LARGE", "AL") else seat)


def _name_words(name: str) -> list[str]:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().upper().replace("-", " ")
    return [w for w in re.sub(r"[^A-Z ]", "", s).split() if w not in _SUFFIX]


def _load_parties() -> dict:
    try:
        return json.loads((HERE / "house_parties.json").read_text(encoding="utf-8"))["states"]
    except (OSError, ValueError, KeyError):
        return {}


def party_of(rid: str, who: str, nominees: dict | None = None, filed: dict | None = None) -> str | None:
    """The winner's party, or None unless exactly one party fits. Nominees for this seat first, then FEC filings for
    this district, then for the state, since filings can trail redrawn lines; the state needs the first name too."""
    words = _name_words(who)
    if len(words) < 2:
        return None
    for party, name in (nominees or {}).items():
        theirs = _name_words(name)
        if theirs and words[-len(theirs[-1:]):] == theirs[-1:]:
            return party
    state, district = rid[:2], "00" if rid.endswith("-AL") else rid[-2:]
    rows = [r for r in (_load_parties() if filed is None else filed).get(state, [])
            if r[0].split() == words[-len(r[0].split()):]]
    here = {r[2] for r in rows if r[3] == district}
    if len(here) == 1:
        return here.pop()
    named = {r[2] for r in rows if r[1] and r[1][0] == words[0][0] and (not here or r[3] == district)}
    return named.pop() if len(named) == 1 else None


def read(source: str, text: str, nominees_of=None, filed: dict | None = None) -> dict | None:
    """{"call": True, rid, party, who} for a clear general-election House call, {"review": why} for a post a person
    should see, or None for anything else. nominees_of(rid) gives that seat's nominees as {party: name}."""
    text = text.strip()
    if _CORRECTION.search(text):
        return {"review": "may correct a call"}
    return (_read_ddhq if source == DDHQ else _read_wasserman)(text, nominees_of, filed)


def _read_ddhq(text: str, nominees_of, filed) -> dict | None:
    if not text.startswith("Decision Desk HQ projects") or "US House" not in text:
        return None
    m = _DDHQ_CALL.match(text)
    if _NOT_GENERAL.search(text) and (not m or _NOT_GENERAL.search(m.group("rest"))):
        return None
    if not m:
        return {"review": "couldn't read this call"}
    name = m.group("state").strip()
    state = name.upper() if name.upper() in R.STATE_NAMES else STATE_BY_NAME.get(name.upper())
    rid = _house_seat(state, m.group("seat")) if state else None
    if not rid:
        return {"review": "couldn't tell which seat"}
    if m.group("rest").strip().lower() not in ("", "general", "general election"):
        return {"review": "not the regular general election", "rid": rid}
    who = re.sub(r"\s+", " ", m.group("who")).strip()
    party = party_of(rid, who, nominees_of(rid) if nominees_of else None, filed)
    if not party:
        return {"review": f"couldn't tell {who}'s party", "rid": rid}
    return {"call": True, "rid": rid, "party": party, "who": who}


def _read_wasserman(text: str, _nominees_of, _filed) -> dict | None:
    m = _SEEN_ENOUGH.match(text)
    if not m:
        return None
    body = re.sub(r"\s+", " ", m.group("body")).strip()
    seats = {_house_seat(st, n) for st, n in _SEAT_TAG.findall(body) if st in R.STATE_NAMES}
    seats.discard(None)
    if not seats or _NOT_GENERAL.search(body):
        return None
    if len(seats) != 1:
        return {"review": "names more than one seat"}
    rid = seats.pop()
    if re.search(r"\bspecial\b", body, re.I):
        return {"review": "not the regular general election", "rid": rid}
    w = _WINNER.match(body)
    if not w or re.search(r"\band\b|&", w.group("who")):
        return {"review": "couldn't tell who won", "rid": rid}
    return {"call": True, "rid": rid, "party": w.group("party"), "who": w.group("who").strip()}


def post_link(did: str, uri: str) -> str:
    return f"https://bsky.app/profile/{did}/post/{uri.rsplit('/', 1)[-1]}"


def _when(stamp: str) -> datetime | None:
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else None


async def fetch(did: str, known: set, since: datetime = SINCE, pages: int = 4) -> list[dict]:
    """New posts by this account since `since`, oldest first. Paging can hand back a page it already gave, so posts
    are matched on their IDs and the walk stops at a page with nothing new."""
    found: dict[str, dict] = {}
    cursor = None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25),
                                     headers={"User-Agent": "decision-desk"}) as s:
        for _ in range(pages):
            q = {"actor": did, "limit": "100", "filter": "posts_no_replies"}
            if cursor:
                q["cursor"] = cursor
            async with s.get(API, params=q) as r:
                r.raise_for_status()
                data = await r.json()
            fresh, older = 0, False
            for item in data.get("feed", []):
                post, record = item.get("post", {}), item.get("post", {}).get("record", {})
                uri, when = post.get("uri"), _when(record.get("createdAt"))
                if not uri or uri in found or uri in known or when is None:
                    continue
                if when < since:
                    older = True
                    continue
                if item.get("reason") or record.get("reply") or post.get("author", {}).get("did") != did:
                    continue
                found[uri] = {"uri": uri, "did": did, "when": when, "text": record.get("text", "")}
                fresh += 1
            cursor = data.get("cursor")
            if older or not fresh or not cursor:
                break
    return sorted(found.values(), key=lambda p: p["when"])
