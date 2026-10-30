"""2026 race definitions.

Race IDs:
  Senate    -> "TX-SEN"
  Governor  -> "TX-GOV"
  House     -> "TX-28", at-large seats -> "AK-AL"

Holder party = which party currently holds the seat that is up. Used to mark flips.
Checked against 270toWin / Ballotpedia counts in Oct 2026: 35 Senate seats up
(22 R, 13 D, incl. FL and OH specials), 36 governorships up (18 R, 18 D).
"""

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

# --- Senate: state -> party holding the seat that's up -------------------------
SENATE = {
    "AL": "R", "AK": "R", "AR": "R", "CO": "D", "DE": "D", "FL": "R", "GA": "D", "ID": "R",
    "IL": "D", "IA": "R", "KS": "R", "KY": "R", "LA": "R", "ME": "R", "MA": "D", "MI": "D",
    "MN": "D", "MS": "R", "MT": "R", "NE": "R", "NH": "D", "NJ": "D", "NM": "D", "NC": "R",
    "OH": "R", "OK": "R", "OR": "D", "RI": "D", "SC": "R", "SD": "R", "TN": "R", "TX": "R",
    "VA": "D", "WV": "R", "WY": "R",
}
SENATE_SPECIAL = {"FL", "OH"}
# Seats NOT up in 2026 (Senate is 53 R / 47 D incl. 2 independents who caucus D)
SENATE_HOLDOVER = {"D": 34, "R": 31}
SENATE_MAJORITY = 51  # VP (R) breaks 50-50 ties

# --- Governors: state -> party currently holding the office ---------------------
GOVERNOR = {
    "AL": "R", "AK": "R", "AZ": "D", "AR": "R", "CA": "D", "CO": "D", "CT": "D", "FL": "R",
    "GA": "R", "HI": "D", "ID": "R", "IL": "D", "IA": "R", "KS": "D", "ME": "D", "MD": "D",
    "MA": "D", "MI": "D", "MN": "D", "NE": "R", "NV": "R", "NH": "R", "NM": "D", "NY": "D",
    "OH": "R", "OK": "R", "OR": "D", "PA": "D", "RI": "D", "SC": "R", "SD": "R", "TN": "R",
    "TX": "R", "VT": "R", "WI": "D", "WY": "R",
}
# Governorships not up in 2026, so the national tally is complete
GOVERNOR_NOT_UP = {
    "DE": "D", "IN": "R", "KY": "D", "LA": "R", "MS": "R", "MO": "R", "MT": "R",
    "NJ": "D", "NC": "D", "ND": "R", "UT": "R", "VA": "D", "WA": "D", "WV": "R",
}

# --- House: seats per state (2020 apportionment, fixed through 2032) -------------
HOUSE_SEATS = {
    "AL": 7, "AK": 1, "AZ": 9, "AR": 4, "CA": 52, "CO": 8, "CT": 5, "DE": 1, "FL": 28, "GA": 14,
    "HI": 2, "ID": 2, "IL": 17, "IN": 9, "IA": 4, "KS": 4, "KY": 6, "LA": 6, "ME": 2, "MD": 8,
    "MA": 9, "MI": 13, "MN": 8, "MS": 4, "MO": 8, "MT": 2, "NE": 3, "NV": 4, "NH": 2, "NJ": 12,
    "NM": 3, "NY": 26, "NC": 14, "ND": 1, "OH": 15, "OK": 5, "OR": 6, "PA": 17, "RI": 2, "SC": 7,
    "SD": 1, "TN": 9, "TX": 38, "UT": 4, "VT": 1, "VA": 11, "WA": 10, "WV": 2, "WI": 8, "WY": 1,
}
HOUSE_TOTAL = 435
HOUSE_MAJORITY = 218

PARTY_NAMES = {"D": "Democratic", "R": "Republican", "I": "Independent"}


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _build():
    races = {}
    for st in SENATE:
        label = f"{STATE_NAMES[st]} Senate" + (" (special)" if st in SENATE_SPECIAL else "")
        races[f"{st}-SEN"] = {"type": "senate", "state": st, "label": label, "holder": SENATE[st]}
    for st in GOVERNOR:
        races[f"{st}-GOV"] = {"type": "governor", "state": st,
                              "label": f"{STATE_NAMES[st]} Governor", "holder": GOVERNOR[st]}
    for st, n in HOUSE_SEATS.items():
        if n == 1:
            races[f"{st}-AL"] = {"type": "house", "state": st,
                                 "label": f"{STATE_NAMES[st]} At-Large (House)", "holder": None}
        else:
            for d in range(1, n + 1):
                races[f"{st}-{d:02d}"] = {"type": "house", "state": st,
                                          "label": f"{STATE_NAMES[st]} {_ordinal(d)} District",
                                          "holder": None}
    # Control of the House: called once a party clearly has 218. Republicans hold it now.
    races["US-HOUSE"] = {"type": "control", "state": None, "label": "Control of the House",
                         "holder": "R"}
    return races


RACES = _build()


def normalize(race_id: str) -> str | None:
    """Accept loose input like 'tx-sen', 'TX 7', 'tx-07', 'ak al' and return a canonical ID."""
    s = race_id.strip().upper().replace(" ", "-").replace("_", "-")
    if s in ("HOUSE", "HOUSE-CONTROL", "US-HOUSE", "HOUSE-MAJORITY"):
        return "US-HOUSE"
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


def races_of(kind: str) -> dict:
    return {rid: r for rid, r in RACES.items() if r["type"] == kind}
