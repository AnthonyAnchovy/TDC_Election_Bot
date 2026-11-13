"""Write the two starter data packs: a 2028 general election skeleton and a single-contest city template.

    python tools/make_starter_packs.py

Both are starting points, marked "draft": they load and can be played, but names and some facts are left for a person
to fill in. Re-running overwrites them, so edit the packs, not this file, once you've started on them.

What is filled in, and from where:
  electoral votes     the 2020-census apportionment, which applies to 2024 and 2028
  presidential holder who carried each state (and each Maine and Nebraska district) in 2024
  Senate seats        the 34 Class 3 seats, up in 2028. Holders are left blank: 2026 changed some of them
  governors           the 11 states that elect a governor in presidential years. Holders left blank
  House               the same 435 districts as 2026 (no reapportionment until 2032)
  poll closings       copied from the 2026 pack, with DC added. Check them nearer the day
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import packs  # noqa: E402

EV = {"AL": 9, "AK": 3, "AZ": 11, "AR": 6, "CA": 54, "CO": 10, "CT": 7, "DE": 3, "DC": 3, "FL": 30, "GA": 16,
      "HI": 4, "ID": 4, "IL": 19, "IN": 11, "IA": 6, "KS": 6, "KY": 8, "LA": 8, "ME": 2, "MD": 10, "MA": 11,
      "MI": 15, "MN": 10, "MS": 6, "MO": 10, "MT": 4, "NE": 2, "NV": 6, "NH": 4, "NJ": 14, "NM": 5, "NY": 28,
      "NC": 16, "ND": 3, "OH": 17, "OK": 7, "OR": 8, "PA": 19, "RI": 4, "SC": 9, "SD": 3, "TN": 11, "TX": 40,
      "UT": 6, "VT": 3, "VA": 13, "WA": 12, "WV": 4, "WI": 10, "WY": 3}
DISTRICTS = {"ME1": ("Maine 1st District", "D"), "ME2": ("Maine 2nd District", "R"), "NE1": ("Nebraska 1st District", "R"),
             "NE2": ("Nebraska 2nd District", "D"), "NE3": ("Nebraska 3rd District", "R")}
WON_2024_D = {"CA", "CO", "CT", "DE", "DC", "HI", "IL", "ME", "MD", "MA", "MN", "NH", "NJ", "NM", "NY", "OR", "RI",
              "VT", "VA", "WA"}
SENATE_CLASS_3 = ["AL", "AK", "AZ", "AR", "CA", "CO", "CT", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA",
                  "MD", "MO", "NV", "NH", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "SC", "SD", "UT", "VT", "WA", "WI"]
GOVERNORS_2028 = ["DE", "IN", "MO", "MT", "NH", "NC", "ND", "UT", "VT", "WA", "WV"]


def general_2028() -> dict:
    base = packs.load("2026-general").data
    races = []
    for st, votes in EV.items():
        races.append({"id": f"{st}-PRES", "type": "president", "jurisdiction": st, "body": "electoral_college",
                      "weight": votes, "holder": "D" if st in WON_2024_D else "R", "level": "federal",
                      **({"label": f"{packs.STATE_NAMES[st]} (statewide)"} if st in ("ME", "NE") else {})})
    for code, (label, holder) in DISTRICTS.items():
        races.append({"id": f"{code}-PRES", "type": "president", "jurisdiction": code[:2], "label": label,
                      "body": "electoral_college", "weight": 1, "holder": holder, "level": "federal"})
    races += [{"id": f"{st}-SEN", "type": "senate", "jurisdiction": st, "level": "federal"} for st in SENATE_CLASS_3]
    races += [{"id": f"{st}-GOV", "type": "governor", "jurisdiction": st, "level": "state"} for st in GOVERNORS_2028]
    races += [{"id": "US-PRES", "type": "control", "body": "electoral_college", "label": "The presidency", "holder": "R"},
              {"id": "US-HOUSE", "type": "control", "body": "house", "label": "Control of the House"},
              {"id": "US-SENATE", "type": "control", "body": "senate", "label": "Control of the Senate"}]
    assert sum(r["weight"] for r in races if r["type"] == "president") == 538
    return {
        "schema": 1, "id": "2028-general", "name": "2028 general election", "draft": True,
        "summary": "President, U.S. Senate, House and governors, November 7, 2028. A skeleton: nominees, Senate and "
                   "governor holders, ratings and the Senate's holdover seats still need filling in.",
        "election_day": "2028-11-07", "timezone": "America/New_York",
        "predictions_deadline": "2028-11-07T18:00:00-05:00",
        "jurisdictions": {"DC": "District of Columbia"},
        "options": base["options"],
        "bodies": {
            "electoral_college": {"name": "Electoral College", "seats": 538, "majority": 270, "control": "US-PRES"},
            "senate": {"name": "Senate", "seats": 100, "majority": 51, "control": "US-SENATE",
                       "holdover": {"D": 0, "R": 0},
                       "_todo": "Set holdover to the seats each party holds that aren't up, and tie_goes_to to the "
                                "sitting Vice President's party if that's how you want a 50-50 Senate called."},
            "house": {"name": "House", "seats": 435, "majority": 218, "control": "US-HOUSE"},
            "governor": {"name": "Governors"},
        },
        "races": races,
        "house_seats": base["house_seats"],
        "ratings": {"rated": [], "battleground": [], "safe": {}},
        "closings": {**base["closings"], "DC": ["20:00", "20:00"]},
        # The two Bluesky accounts are pinned by permanent ID, so they carry over. NYT's file addresses, the state
        # results files and County Watch's baselines are per election and have to be added when they exist.
        "feeds": {"bluesky": {"since": "2028-11-07T17:00:00-05:00", "accounts": base["feeds"]["bluesky"]["accounts"]}},
        "game": {
            "categories": [
                {"key": "president", "name": "President, state by state", "points": 1, "required": True, "races": {"type": "president"}},
                {"key": "presidency", "name": "The presidency", "points": 10, "implied_by": "president", "races": {"ids": ["US-PRES"]}},
                {"key": "senate", "name": "Senate", "points": 1, "required": True, "races": {"type": "senate"}},
                {"key": "house_control", "name": "House majority", "points": 5, "required": True, "races": {"ids": ["US-HOUSE"]}},
                {"key": "governor", "name": "governors", "points": 1, "races": {"type": "governor"}},
            ],
            "flip_bonus": 1,
            "tiebreaker": {"label": "How many electoral votes does the winner get?", "name": "the winner's electoral votes",
                           "short": "tiebreaker", "min": 270, "max": 538,
                           "hint": "Out of 538. If two people finish level on points, the closer guess wins."},
        },
        "sections": [
            {"key": "president", "name": "President", "kind": "map", "category": "president", "suffix": "PRES", "body": "electoral_college"},
            {"key": "senate", "name": "Senate", "kind": "map", "category": "senate", "suffix": "SEN"},
            {"key": "house", "name": "House", "kind": "house", "control": "US-HOUSE"},
            {"key": "governor", "name": "Governors", "kind": "map", "category": "governor", "suffix": "GOV"},
        ],
    }


def city_mayor() -> dict:
    return {
        "schema": 1, "id": "2027-chicago-mayor", "name": "Chicago mayoral election, 2027", "draft": True, "example": True,
        "summary": "A single contest, as a template: one nonpartisan race with several candidates. The names are "
                   "placeholders. Replace them, and check the date and poll hours, before anyone plays.",
        "election_day": "2027-02-23", "timezone": "America/Chicago",
        "predictions_deadline": "2027-02-23T18:00:00-06:00",
        "jurisdictions": {"CHI": "Chicago"},
        "options": {},
        "races": [
            {"id": "CHI-MAYOR", "type": "mayor", "jurisdiction": "CHI", "level": "local", "label": "Mayor of Chicago",
             "options": [{"key": "A", "name": "Candidate A"}, {"key": "B", "name": "Candidate B"},
                         {"key": "C", "name": "Candidate C"}, {"key": "D", "name": "Candidate D"}]},
            {"id": "CHI-RUNOFF", "type": "question", "jurisdiction": "CHI", "level": "local",
             "label": "Does it go to a runoff?",
             "options": [{"key": "YES", "name": "Yes, nobody clears 50%", "color": "#b45309"},
                         {"key": "NO", "name": "No, it's won outright", "color": "#0f766e"}]},
        ],
        "closings": {"CHI": ["19:00", "19:00"]},
        "game": {
            "categories": [
                {"key": "mayor", "name": "Mayor", "points": 10, "required": True, "races": {"type": "mayor"}},
                {"key": "question", "name": "Side bets", "points": 3, "races": {"type": "question"}},
            ],
            "flip_bonus": 0,
            "tiebreaker": {"label": "What share of the vote does the first-place candidate get?", "name": "the leader's share",
                           "short": "tiebreaker", "min": 0, "max": 100, "hint": "A whole number of percent. Closest guess breaks a tie."},
        },
        "sections": [{"key": "city", "name": "The race", "kind": "list", "category": "mayor"},
                     {"key": "bets", "name": "Side bets", "kind": "list", "category": "question"}],
    }


def main():
    for pack in (general_2028(), city_mayor()):
        wrong = packs.problems(pack)
        if wrong:
            raise SystemExit(f"{pack['id']}: " + "; ".join(wrong))
        folder = packs.ROOT / pack["id"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "pack.json").write_text(json.dumps(pack, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{pack['id']}: {len(packs.load(pack['id'], fresh=True).races)} races")


if __name__ == "__main__":
    main()
