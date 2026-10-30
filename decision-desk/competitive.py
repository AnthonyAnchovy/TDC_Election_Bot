"""House ratings used by the bot.

RATINGS: average of Cook, Inside Elections and Sabato's Crystal Ball for every House seat not rated
Safe, as compiled by Adam Carlson (@admcrlsn) on Oct 23, 2026, transcribed from his posted table.
Holder = party that holds the seat now (the table's cell color). His headline: if Democrats win every
seat they're favored in (Safe/Likely/Lean/Tilt D) they reach 223 seats.

BATTLEGROUND: the bonus seats in the /predict picker (Lean D through Lean R, plus three Likely seats
kept from the earlier Cook-only list so picks already saved still score).
EARLY_WATCH: rated seats (Likely D through Lean R) in states where most polls close by 7:30 PM ET.
Edit freely; restart the bot after changes.
"""
import night as _N

RATINGS = [
    ("CA-06", "Likely D", "R"), ("CA-13", "Likely D", "D"), ("CA-45", "Likely D", "D"),
    ("IN-01", "Likely D", "D"), ("MI-08", "Likely D", "D"), ("MN-02", "Likely D", "D"),
    ("NE-02", "Likely D", "R"), ("NH-01", "Likely D", "D"), ("NH-02", "Likely D", "D"),
    ("NJ-09", "Likely D", "D"), ("NV-01", "Likely D", "D"), ("NV-03", "Likely D", "D"),
    ("NV-04", "Likely D", "D"), ("NY-04", "Likely D", "D"), ("NY-19", "Likely D", "D"),
    ("OH-01", "Likely D", "D"), ("OH-13", "Likely D", "D"), ("VA-07", "Likely D", "D"),
    ("AZ-01", "Lean D", "R"), ("CA-48", "Lean D", "R"), ("IA-01", "Lean D", "R"),
    ("IA-03", "Lean D", "R"), ("NM-02", "Lean D", "D"), ("NJ-07", "Lean D", "R"),
    ("NY-03", "Lean D", "D"), ("PA-07", "Lean D", "R"), ("TX-28", "Lean D", "D"),
    ("AZ-06", "Tilt D", "R"), ("CA-22", "Tilt D", "R"), ("CO-08", "Tilt D", "R"),
    ("FL-25", "Tilt D", "D"), ("IA-02", "Tilt D", "R"), ("NC-01", "Tilt D", "D"),
    ("NC-11", "Tilt D", "R"), ("PA-10", "Tilt D", "R"), ("VA-02", "Tilt D", "R"),
    ("TX-34", "Tilt D", "D"), ("WA-03", "Tilt D", "D"), ("WI-03", "Tilt D", "R"),
    ("FL-14", "Toss-up", "D"), ("FL-22", "Toss-up", "D"), ("MI-04", "Toss-up", "R"),
    ("MI-07", "Toss-up", "R"), ("MI-10", "Toss-up", "R"), ("NY-17", "Toss-up", "R"),
    ("OH-07", "Toss-up", "R"), ("OH-09", "Toss-up", "D"), ("PA-08", "Toss-up", "R"),
    ("TX-15", "Toss-up", "R"), ("KY-06", "Tilt R", "R"), ("MT-01", "Tilt R", "R"),
    ("TX-35", "Tilt R", "D"), ("VA-01", "Tilt R", "R"), ("AL-02", "Lean R", "D"),
    ("CO-05", "Lean R", "R"), ("FL-13", "Lean R", "R"), ("FL-27", "Lean R", "R"),
    ("ME-02", "Lean R", "D"), ("MN-01", "Lean R", "R"), ("NV-02", "Lean R", "R"),
    ("NY-21", "Lean R", "R"), ("PA-01", "Lean R", "R"), ("SC-01", "Lean R", "R"),
    ("TX-23", "Lean R", "R"), ("WI-01", "Lean R", "R"), ("AK-AL", "Likely R", "R"),
    ("AR-02", "Likely R", "R"), ("AZ-02", "Likely R", "R"), ("CO-03", "Likely R", "R"),
    ("CO-04", "Likely R", "R"), ("FL-07", "Likely R", "R"), ("FL-09", "Likely R", "D"),
    ("FL-16", "Likely R", "R"), ("IN-05", "Likely R", "R"), ("KS-02", "Likely R", "R"),
    ("MO-02", "Likely R", "R"), ("NE-01", "Likely R", "R"), ("NC-03", "Likely R", "R"),
    ("OH-10", "Likely R", "R"), ("OH-15", "Likely R", "R"), ("TX-09", "Likely R", "D"),
    ("VA-05", "Likely R", "R"),
]
RATING_OF = {rid: rating for rid, rating, _ in RATINGS}
HOLDER_OF = {rid: holder for rid, _, holder in RATINGS}
D_BASELINE = 223          # Democratic seats if every Safe/Likely/Lean/Tilt D seat goes D


def favored(rid: str) -> str | None:
    r = RATING_OF.get(rid, "")
    return "D" if r.endswith(" D") else "R" if r.endswith(" R") else None


BATTLEGROUND = [
    ("FL-14", "Toss-up", "D"), ("FL-22", "Toss-up", "D"), ("MI-04", "Toss-up", "R"),
    ("MI-07", "Toss-up", "R"), ("MI-10", "Toss-up", "R"), ("NY-17", "Toss-up", "R"),
    ("OH-07", "Toss-up", "R"), ("OH-09", "Toss-up", "D"), ("PA-08", "Toss-up", "R"),
    ("TX-15", "Toss-up", "R"), ("AZ-06", "Tilt D", "R"), ("CA-22", "Tilt D", "R"),
    ("CO-08", "Tilt D", "R"), ("FL-25", "Tilt D", "D"), ("IA-02", "Tilt D", "R"),
    ("NC-01", "Tilt D", "D"), ("NC-11", "Tilt D", "R"), ("PA-10", "Tilt D", "R"),
    ("TX-34", "Tilt D", "D"), ("VA-02", "Tilt D", "R"), ("WA-03", "Tilt D", "D"),
    ("WI-03", "Tilt D", "R"), ("KY-06", "Tilt R", "R"), ("MT-01", "Tilt R", "R"),
    ("TX-35", "Tilt R", "D"), ("VA-01", "Tilt R", "R"), ("AZ-01", "Lean D", "R"),
    ("CA-48", "Lean D", "R"), ("IA-01", "Lean D", "R"), ("IA-03", "Lean D", "R"),
    ("NJ-07", "Lean D", "R"), ("NM-02", "Lean D", "D"), ("NY-03", "Lean D", "D"),
    ("PA-07", "Lean D", "R"), ("TX-28", "Lean D", "D"), ("AL-02", "Lean R", "D"),
    ("CO-05", "Lean R", "R"), ("FL-13", "Lean R", "R"), ("FL-27", "Lean R", "R"),
    ("ME-02", "Lean R", "D"), ("MN-01", "Lean R", "R"), ("NV-02", "Lean R", "R"),
    ("NY-21", "Lean R", "R"), ("PA-01", "Lean R", "R"), ("SC-01", "Lean R", "R"),
    ("TX-23", "Lean R", "R"), ("WI-01", "Lean R", "R"), ("CA-13", "Likely D", "D"),
    ("OH-10", "Likely R", "R"), ("OH-15", "Likely R", "R"),
]
RATING = {rid: rating for rid, rating, _ in BATTLEGROUND}
HOLDER = {rid: holder for rid, _, holder in BATTLEGROUND}

_EARLY_RATINGS = ("Likely D", "Lean D", "Tilt D", "Toss-up", "Tilt R", "Lean R")
EARLY_WATCH = sorted(
    (rid for rid, rating, _ in RATINGS
     if rating in _EARLY_RATINGS and _N.first_close(rid[:2]) <= _N.ELECTION_DAY.replace(hour=19, minute=30)),
    key=lambda r: (_N.first_close(r[:2]), _EARLY_RATINGS.index(RATING_OF[r]), r))

# Races Cook rates Solid, pre-filled in the /predict picker so people only click the real contests.
# Senate as of Oct 20, 2026; governors as of Oct 13, 2026 (cookpolitical.com). Edit if ratings move.
SAFE = {
    # Senate, Solid D
    "CO-SEN": "D", "DE-SEN": "D", "IL-SEN": "D", "MA-SEN": "D", "NJ-SEN": "D", "NM-SEN": "D",
    "OR-SEN": "D", "RI-SEN": "D", "VA-SEN": "D",
    # Senate, Solid R
    # FL-SEN taken out Oct 29: the group is treating it as competitive, so it isn't pre-filled
    "AL-SEN": "R", "AR-SEN": "R", "ID-SEN": "R", "KY-SEN": "R", "LA-SEN": "R",
    "MT-SEN": "R", "OK-SEN": "R", "SD-SEN": "R", "TN-SEN": "R", "WV-SEN": "R", "WY-SEN": "R",
    # Governors, Solid D
    "CA-GOV": "D", "CO-GOV": "D", "CT-GOV": "D", "HI-GOV": "D", "IL-GOV": "D", "MA-GOV": "D",
    "MD-GOV": "D", "ME-GOV": "D", "MN-GOV": "D", "NM-GOV": "D", "NY-GOV": "D", "PA-GOV": "D",
    "RI-GOV": "D",
    # Governors, Solid R
    "AL-GOV": "R", "AR-GOV": "R", "ID-GOV": "R", "SC-GOV": "R", "SD-GOV": "R", "TN-GOV": "R",
    "WY-GOV": "R",
}
