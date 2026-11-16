"""Data packs: everything that makes the desk about one particular election, as data.

A pack is a folder under packs/ with a pack.json. It can be as big as a general election (2026: 35 Senate seats, 36
governors, 435 House seats, control of both chambers) or as small as one contest (a mayoral race with five
candidates). The code doesn't know which: it reads the races, who can win them, how they add up, when polls close and
how the prediction game scores them from the active pack.

pack.json, in brief (docs/data-packs.md has every field):
  id, name, summary, election_day, timezone, predictions_deadline
  options       the answers races share, usually parties: {"D": {"name", "noun", "plural", "color", "emoji"}}
  jurisdictions names for places that aren't U.S. states: {"CHI": "Chicago"}
  races         [{"id", "type", "jurisdiction", "label", "holder", "body", "weight", "options", "rating", "safe"}]
                `options` is a list of shared keys (["D", "R"]) or of the race's own
                [{"key", "name", "party", "color"}]; left out, it's D and R plus anyone candidates.json names
  house_seats   {"TX": 38, ...}: shorthand that expands to every U.S. House district
  bodies        things races add up to: {"senate": {"name", "seats", "majority", "holdover", "tie_goes_to",
                "control": "US-SENATE"}}. A race counts `weight` toward its body (electoral votes, for a president)
  ratings       {"rated": [[race, rating, holder]], "battleground": [...], "safe": {race: option}}
  closings      {"GA": ["19:00", "19:00"]} by jurisdiction, in the pack's timezone; "*" is the default
  game          {"categories": [{"key", "name", "points", "required", "races": {...}}], "flip_bonus", "tiebreaker"}
  sections      how the website lays the picker out
  feeds         where its numbers come from on the night: state results files, outlets read for calls, early
                vote, County Watch (feeds.py). Left out, nothing is fetched and everything is entered by hand

One pack is active at a time in a process: activate() swaps it in, and the modules that used to hard-code 2026
(races, competitive, night, scoring) refill themselves from it. The bot runs one pack (DESK_PACK in .env, or the
packs/ACTIVE file). The website switches per request.

Each pack keeps its own records, and each Discord server its own within a pack: data/<pack>/<server>/desk.db. So two
servers can run the same election side by side without seeing each other's calls or picks (tenancy.py).
"""
from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE / "packs"
DATA = HERE / "data"
DEFAULT = "2026-general"
PARTISAN = ["D", "R"]

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
DEFAULT_OPTIONS = {
    "D": {"name": "Democratic", "noun": "Democrat", "plural": "Democrats", "color": "#1f5fbf", "emoji": "🔵"},
    "R": {"name": "Republican", "noun": "Republican", "plural": "Republicans", "color": "#c8322d", "emoji": "🔴"},
    "I": {"name": "Independent", "noun": "independent", "plural": "independents", "color": "#7a4fc4", "emoji": "🟣"},
}
PALETTE = ["#0f766e", "#b45309", "#7c3aed", "#be185d", "#1d4ed8", "#4d7c0f", "#b91c1c", "#475569"]
TYPE_LABEL = {"senate": "Senate", "governor": "Governor", "president": "President", "mayor": "Mayor"}


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class PackError(ValueError):
    """A pack that can't be used, with every problem found in it."""


class Pack:
    """One loaded pack. `races` is {id: race}, in the order the pack lists them (House districts, expanded from
    house_seats, come after the listed races and before any race of type "control")."""

    def __init__(self, folder: Path, data: dict):
        self.dir, self.data = folder, data
        self.id = data["id"]
        self.name = data.get("name", self.id)
        self.options = {**DEFAULT_OPTIONS, **(data.get("options") or {})} if data.get("options") is None \
            else dict(data["options"])
        self.places = {**STATE_NAMES, **(data.get("jurisdictions") or {})}
        self.bodies = data.get("bodies") or {}
        self.ratings = data.get("ratings") or {}
        self.game = data.get("game") or {}
        self.races = self._races()

    # -------------------------------------------------------------- races
    def _label(self, r: dict) -> str:
        if r.get("label"):
            return r["label"]
        place, kind = self.places.get(r.get("jurisdiction"), r.get("jurisdiction") or ""), r["type"]
        if kind == "senate":
            return f"{place} Senate" + (" (special)" if r.get("special") else "")
        return f"{place} {TYPE_LABEL.get(kind, kind.replace('_', ' ').title())}".strip()

    def _race(self, r: dict) -> dict:
        rid, kind = r["id"].upper(), r["type"]
        body = r.get("body") or (kind if kind in self.bodies else None)
        out = {"type": kind, "state": r.get("jurisdiction"), "label": self._label(r), "holder": r.get("holder"),
               "body": body, "weight": r.get("weight", 1), "options": self._options(r.get("options")),
               "level": r.get("level"), "group": r.get("group")}
        if r.get("special"):
            out["special"] = True
        return rid, out

    def _options(self, listed) -> list[dict] | None:
        """A race's own choices as [{"key", "name", "party", "color"}], or None for "the usual parties"."""
        if not listed:
            return None
        out = []
        for i, o in enumerate(listed):
            o = {"key": o} if isinstance(o, str) else dict(o)
            style = self.options.get(o.get("party") or o["key"], {})
            out.append({"key": str(o["key"]).upper(), "name": o.get("name"), "party": o.get("party"),
                        "color": o.get("color") or style.get("color") or PALETTE[i % len(PALETTE)]})
        return out

    def _races(self) -> dict:
        listed = [self._race(r) for r in self.data.get("races", [])]
        races = {rid: r for rid, r in listed if r["type"] != "control"}
        for st, n in (self.data.get("house_seats") or {}).items():
            if n == 1:
                races[f"{st}-AL"] = {"type": "house", "state": st, "label": f"{self.places[st]} At-Large (House)",
                                     "holder": None, "body": "house" if "house" in self.bodies else None,
                                     "weight": 1, "options": None, "level": "federal", "group": None}
            else:
                for d in range(1, n + 1):
                    races[f"{st}-{d:02d}"] = {"type": "house", "state": st,
                                              "label": f"{self.places[st]} {ordinal(d)} District", "holder": None,
                                              "body": "house" if "house" in self.bodies else None, "weight": 1,
                                              "options": None, "level": "federal", "group": None}
        races.update({rid: r for rid, r in listed if r["type"] == "control"})
        return races

    # -------------------------------------------------------------- files and dates
    def path(self, name: str) -> Path:
        """A data file that goes with the pack (candidates.json, flavor.json...). A pack with "shared_files": true
        falls back to the copy in decision-desk/, which is where 2026's have always lived."""
        own = self.dir / name
        return own if own.exists() or not self.data.get("shared_files") else HERE / name

    def read(self, name: str) -> dict:
        try:
            return json.loads(self.path(name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def election_day(self) -> datetime:
        from zoneinfo import ZoneInfo
        y, m, d = map(int, self.data["election_day"].split("-"))
        return datetime(y, m, d, tzinfo=ZoneInfo(self.data.get("timezone", "America/New_York")))

    def deadline(self) -> datetime:
        text = self.data.get("predictions_deadline")
        return datetime.fromisoformat(text) if text else self.election_day().replace(hour=18)

    def summary(self) -> dict:
        kinds: dict[str, int] = {}
        for r in self.races.values():
            kinds[r["type"]] = kinds.get(r["type"], 0) + 1
        return {"id": self.id, "name": self.name, "summary": self.data.get("summary", ""),
                "election_day": self.data["election_day"], "races": len(self.races), "types": kinds,
                "example": bool(self.data.get("example")),
                "feeds": [k for k, v in (self.data.get("feeds") or {}).items() if v]}


# ------------------------------------------------------------------ checking a pack
def problems(data: dict) -> list[str]:
    """Everything wrong with a pack.json, in words. Empty means it can be loaded."""
    out = []
    for key in ("id", "name", "election_day"):
        if not data.get(key):
            out.append(f'"{key}" is missing')
    if data.get("id") and not re.fullmatch(r"[a-z0-9][a-z0-9-]*", data["id"]):
        out.append('"id" may only use lower-case letters, digits and hyphens')
    if data.get("election_day") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(data["election_day"])):
        out.append('"election_day" has to look like 2028-11-07')
    shared = {**DEFAULT_OPTIONS, **(data.get("options") or {})} if data.get("options") is None else data["options"]
    places = {**STATE_NAMES, **(data.get("jurisdictions") or {})}
    bodies = data.get("bodies") or {}
    seen = set()
    for i, r in enumerate(data.get("races") or []):
        rid = str(r.get("id", "")).upper()
        where = rid or f"race #{i + 1}"
        if not rid or not r.get("type"):
            out.append(f'{where} needs an "id" and a "type"')
            continue
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]*", rid):
            out.append(f"{where}: an id may only use letters, digits and hyphens")
        if rid in seen:
            out.append(f"{where} is listed twice")
        seen.add(rid)
        if r.get("jurisdiction") and r["jurisdiction"] not in places:
            out.append(f'{where}: "{r["jurisdiction"]}" isn\'t a state or one of the pack\'s jurisdictions')
        if not r.get("label") and not r.get("jurisdiction"):
            out.append(f'{where} needs a "label" or a "jurisdiction"')
        if r.get("body") and r["body"] not in bodies:
            out.append(f'{where}: there is no body called "{r["body"]}"')
        keys = [str(o if isinstance(o, str) else o.get("key", "")).upper() for o in r.get("options") or []]
        if r.get("options") is not None and len(keys) < 2:
            out.append(f"{where} needs at least two options")
        if len(set(keys)) != len(keys) or "" in keys:
            out.append(f"{where}: every option needs its own key")
        for o in r.get("options") or []:
            if isinstance(o, str) and o not in shared:
                out.append(f'{where}: option "{o}" isn\'t in the pack\'s options')
        if r.get("holder") and keys and str(r["holder"]).upper() not in keys:
            out.append(f'{where}: the holder "{r["holder"]}" isn\'t one of its options')
    for st in data.get("house_seats") or {}:
        if st not in STATE_NAMES:
            out.append(f'house_seats: "{st}" isn\'t a state')
    for name, b in bodies.items():
        if b.get("control") and b["control"].upper() not in seen:
            out.append(f'body "{name}": its control race {b["control"]} isn\'t in the races')
    cats = (data.get("game") or {}).get("categories") or []
    if len({c.get("key") for c in cats}) != len(cats):
        out.append("game: every category needs its own key")
    for c in cats:
        if not c.get("key") or "races" not in c:
            out.append('game: a category needs a "key" and a "races" rule')
    import feeds
    return out + feeds.problems(data)


# ------------------------------------------------------------------ finding and loading
_loaded: dict[str, Pack] = {}
_active: Pack | None = None
_listeners: list = []


def folder_of(pack_id: str) -> Path:
    return ROOT / pack_id


def load(pack_id: str, fresh: bool = False) -> Pack:
    if fresh or pack_id not in _loaded:
        folder = folder_of(pack_id)
        try:
            data = json.loads((folder / "pack.json").read_text(encoding="utf-8"))
        except OSError:
            raise PackError(f'There is no data pack called "{pack_id}" in {ROOT}.') from None
        except ValueError as exc:
            raise PackError(f"{pack_id}/pack.json isn't valid JSON: {exc}") from None
        wrong = problems(data)
        if data.get("id") != pack_id:
            wrong.insert(0, f'its "id" is "{data.get("id")}" but its folder is "{pack_id}"')
        if wrong:
            raise PackError(f"The data pack {pack_id} can't be used:\n- " + "\n- ".join(wrong))
        _loaded[pack_id] = Pack(folder, data)
    return _loaded[pack_id]


def available() -> list[dict]:
    """Every pack that loads, soonest election last. A broken pack is left out (load() it to see why)."""
    out = []
    for folder in sorted(ROOT.iterdir()) if ROOT.exists() else []:
        if (folder / "pack.json").exists():
            try:
                out.append(load(folder.name).summary())
            except PackError:
                continue
    return sorted(out, key=lambda p: p["election_day"])


def default_id() -> str:
    """The pack this process starts on: DESK_PACK, else the one named in packs/ACTIVE, else 2026."""
    named = os.getenv("DESK_PACK")
    if not named:
        try:
            named = (ROOT / "ACTIVE").read_text(encoding="utf-8").strip()
        except OSError:
            named = ""
    return named or DEFAULT


def on_activate(fn):
    """Register a module's refill function: it's called with the Pack whenever one becomes active."""
    _listeners.append(fn)
    if _active is not None:
        fn(_active)
    return fn


def activate(pack_id: str | None = None) -> Pack:
    global _active
    pack = load(pack_id or default_id())
    if pack is not _active:
        _active = pack
        for fn in _listeners:
            fn(pack)
    return pack


def active() -> Pack:
    return _active or activate()


@contextmanager
def using(pack_id: str):
    """Run a block on another pack, then go back. Nothing inside may await while other work is running, because
    the active pack is one per process: the website wraps each request's own work in this."""
    before = active()
    try:
        yield activate(pack_id)
    finally:
        activate(before.id)


def set_default(pack_id: str):
    """Make a pack the one the bot starts on next time (writes packs/ACTIVE)."""
    load(pack_id)
    (ROOT / "ACTIVE").write_text(pack_id + "\n", encoding="utf-8")


# ------------------------------------------------------------------ where a pack's records live
def data_root() -> Path:
    """Where records are kept: decision-desk/data, or DESK_DATA if that's set."""
    return Path(os.environ["DESK_DATA"]) if os.getenv("DESK_DATA") else DATA


def _legacy() -> Path | None:
    """decision-desk/desk.db, where 2026's records for the first server have always been. Not used once records
    are kept somewhere else with DESK_DATA."""
    old = HERE / "desk.db"
    return old if not os.getenv("DESK_DATA") and old.exists() else None


def db_path(pack_id: str | None = None, server: int | str | None = None) -> Path:
    """The database for one pack in one Discord server. DESK_DB overrides everything (tests, one-off copies)."""
    if os.getenv("DESK_DB"):
        chosen = Path(os.environ["DESK_DB"])
        if ".fixture." in chosen.name:
            raise PackError(f"{chosen.name} is a test fixture, kept as it was on the night. Copy it and point "
                            "DESK_DB at the copy.")
        return chosen
    pack_id = pack_id or active().id
    home = str(os.getenv("GUILD_ID") or "")
    if pack_id == DEFAULT and (server is None or str(server) == home) and _legacy():
        return _legacy()
    path = data_root() / pack_id / str(server or home or "local") / "desk.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def servers(pack_id: str | None = None) -> list[str]:
    """Every server that has records for a pack."""
    pack_id = pack_id or active().id
    root = data_root() / pack_id
    found = sorted(p.parent.name for p in root.glob("*/desk.db")) if root.exists() else []
    home = str(os.getenv("GUILD_ID") or "local")
    if pack_id == DEFAULT and _legacy() and home not in found:
        found.insert(0, home)
    return found
