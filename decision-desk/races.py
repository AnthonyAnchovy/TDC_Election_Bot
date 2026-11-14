"""The races of the active data pack, under the names the rest of the code has always used.

Race IDs:
  Senate    -> "TX-SEN"
  Governor  -> "TX-GOV"
  House     -> "TX-28", at-large seats -> "AK-AL"
  Control   -> "US-HOUSE", "US-SENATE"
  anything else a pack defines -> whatever ID the pack gives it ("CHI-MAYOR")

Nothing about a particular year lives here any more: packs.py loads it from packs/<id>/pack.json, and this module
refills itself whenever a pack becomes active. RACES[id] is {"type", "state", "label", "holder", "body", "weight",
"options"}: `state` is the race's jurisdiction (a state code, or a pack's own place code), `body` is what the race
counts toward, and `holder` is who holds the seat now, used to mark flips.
"""
import packs

STATE_NAMES = dict(packs.STATE_NAMES)

# Short state names that can be told apart at a glance. The two-letter codes can't: MS, MO, MT, MI, MN and ME sat
# side by side in the 2026 picker and people clicked the wrong one. These are the AP's abbreviations.
STATE_SHORT = {
    "AL": "Ala.", "AK": "Alaska", "AZ": "Ariz.", "AR": "Ark.", "CA": "Calif.", "CO": "Colo.", "CT": "Conn.",
    "DE": "Del.", "FL": "Fla.", "GA": "Ga.", "HI": "Hawaii", "ID": "Idaho", "IL": "Ill.", "IN": "Ind.",
    "IA": "Iowa", "KS": "Kan.", "KY": "Ky.", "LA": "La.", "ME": "Maine", "MD": "Md.", "MA": "Mass.",
    "MI": "Mich.", "MN": "Minn.", "MS": "Miss.", "MO": "Mo.", "MT": "Mont.", "NE": "Neb.", "NV": "Nev.",
    "NH": "N.H.", "NJ": "N.J.", "NM": "N.M.", "NY": "N.Y.", "NC": "N.C.", "ND": "N.D.", "OH": "Ohio",
    "OK": "Okla.", "OR": "Ore.", "PA": "Pa.", "RI": "R.I.", "SC": "S.C.", "SD": "S.D.", "TN": "Tenn.",
    "TX": "Texas", "UT": "Utah", "VT": "Vt.", "VA": "Va.", "WA": "Wash.", "WV": "W.Va.", "WI": "Wis.",
    "WY": "Wyo.",
}


class _Names(dict):
    """Option names by key. A key the pack doesn't describe (a candidate in a nonpartisan race) reads as itself."""

    def __missing__(self, key):
        return str(key).title() if key else "Nobody"


# Filled from the active pack by _fill() below.
RACES: dict = {}
SENATE: dict = {}               # state -> party holding the Senate seat that's up
SENATE_SPECIAL: set = set()
SENATE_HOLDOVER: dict = {"D": 0, "R": 0}    # seats not up
SENATE_MAJORITY = 0             # 0: this pack has no Senate
GOVERNOR: dict = {}
GOVERNOR_NOT_UP: dict = {}
HOUSE_SEATS: dict = {}
HOUSE_TOTAL = 0
HOUSE_MAJORITY = 0
PARTY_NAMES: dict = _Names()
BODIES: dict = {}
_ordinal = packs.ordinal


@packs.on_activate
def _fill(pack: packs.Pack):
    global RACES, SENATE, SENATE_SPECIAL, SENATE_HOLDOVER, SENATE_MAJORITY, GOVERNOR, GOVERNOR_NOT_UP
    global HOUSE_SEATS, HOUSE_TOTAL, HOUSE_MAJORITY, PARTY_NAMES, BODIES, STATE_NAMES
    RACES = pack.races
    BODIES = pack.bodies
    STATE_NAMES = pack.places
    SENATE = {r["state"]: r["holder"] for r in RACES.values() if r["type"] == "senate"}
    SENATE_SPECIAL = {r["state"] for r in RACES.values() if r["type"] == "senate" and r.get("special")}
    GOVERNOR = {r["state"]: r["holder"] for r in RACES.values() if r["type"] == "governor"}
    senate, house, gov = BODIES.get("senate", {}), BODIES.get("house", {}), BODIES.get("governor", {})
    SENATE_HOLDOVER = dict(senate.get("holdover") or {"D": 0, "R": 0})
    SENATE_MAJORITY = senate.get("majority", 0)
    GOVERNOR_NOT_UP = dict(gov.get("not_up") or {})
    HOUSE_SEATS = dict(pack.data.get("house_seats") or {})
    HOUSE_TOTAL = house.get("seats", sum(HOUSE_SEATS.values()))
    HOUSE_MAJORITY = house.get("majority", 0)
    PARTY_NAMES = _Names({key: o.get("name", key) for key, o in pack.options.items()})


def _control_aliases() -> dict[str, str]:
    """What people type for a control race: HOUSE, HOUSE-CONTROL, US-HOUSE, HOUSE-MAJORITY."""
    out = {}
    for rid, r in RACES.items():
        if r["type"] == "control":
            word = (r.get("body") or rid.split("-")[-1]).upper().replace("_", "-")
            for alias in (word, f"{word}-CONTROL", f"{word}-MAJORITY", rid):
                out[alias] = rid
    return out


def normalize(race_id: str) -> str | None:
    """Accept loose input like 'tx-sen', 'TX 7', 'tx-07', 'ak al', 'house' and return a canonical ID."""
    s = race_id.strip().upper().replace(" ", "-").replace("_", "-")
    control = _control_aliases()
    if s in control:
        return control[s]
    if s in RACES:
        return s
    parts = [p for p in s.split("-") if p]
    if len(parts) == 2:
        st, rest = parts
        if rest.isdigit():
            cand = f"{st}-{int(rest):02d}"
            if cand in RACES:
                return cand
            if int(rest) == 1 and f"{st}-AL" in RACES:
                return f"{st}-AL"
        for alias, canon in (("SENATE", "SEN"), ("GOVERNOR", "GOV"), ("ATLARGE", "AL")):
            if rest == alias and f"{st}-{canon}" in RACES:
                return f"{st}-{canon}"
    return None


def search(text: str, limit: int = 25) -> list[str]:
    """Race IDs for what someone has typed so far, best match first: "florida gov", "fl gov", "tx 15", "ohio 9th",
    "senate". Every word typed has to appear in the race's ID, label or place name. With nothing typed, the control
    races lead."""
    order = sorted(RACES, key=lambda r: (RACES[r]["type"] != "control", RACES[r]["type"] == "house"))
    words = text.upper().replace("-", " ").replace("_", " ").split()
    if not words:
        return order[:limit]
    exact = normalize(text)
    scored = []
    for i, rid in enumerate(order):
        r = RACES[rid]
        parts = rid.split("-")
        number = str(int(parts[1])) if len(parts) > 1 and parts[1].isdigit() else ""
        hay = f"{rid} {' '.join(parts)} {number} {r['label'].upper()} {STATE_NAMES.get(r['state'], 'US').upper()}"
        if all(w in hay for w in words):
            starts = sum(1 for w in words if any(tok.startswith(w) for tok in hay.split()))
            scored.append((rid != exact, -starts, i, rid))
    return [rid for *_rank, rid in sorted(scored)][:limit]


def chamber(race_id: str) -> str:
    """What a race counts toward: "senate", "house", "governor", or a pack's own body or race type. A control race
    belongs to the body it decides."""
    r = RACES[race_id]
    if r["type"] == "control":
        return r.get("body") or ("senate" if race_id == "US-SENATE" else "house")
    return r.get("body") or r["type"]       # a president's states count toward the Electoral College


def chamber_name(race_id: str) -> str:
    key = chamber(race_id)
    known = {"senate": "Senate", "house": "House", "governor": "governors"}
    return known.get(key) or BODIES.get(key, {}).get("name") or key.replace("_", " ").title()


def options(race_id: str) -> list[dict] | None:
    """A race's own choices, when its pack lists them: [{"key", "name", "party", "color"}]. None means the usual
    parties, with names from candidates.json."""
    return RACES[race_id].get("options")


def races_of(kind: str) -> dict:
    return {rid: r for rid, r in RACES.items() if r["type"] == kind}


packs.activate()
