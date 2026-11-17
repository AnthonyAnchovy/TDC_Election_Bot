"""Build `history/pre_history.csv`: one row per statewide race, the table the pre-election model is fitted on.

Run once from decision-desk/ (standard library only, no network):

    python tools/build_pre_history.py            # writes history/pre_history.csv
    python tools/build_pre_history.py --report   # also prints the checks (counts, coverage, misses, gaps)

It reads the downloaded files kept in `research/sources/` (see `research/SOURCES.md` for where each came from
and the credit each needs). Nothing is estimated: where a source has no value the cell is left blank.

THE RACE ID, used by every pre-election table:   {year}-{ST}-{OFFICE}
  year    the election year, four digits
  ST      the state's postal code (DC for the District of Columbia)
  OFFICE  PRES (president, that state's vote), SEN (U.S. Senate), GOV (governor)
          SEN2 is a second Senate race in the same state and year (a special election). In a state-year with
          two Senate races the regular one is SEN and the special one SEN2; a lone special is plain SEN.
  e.g. 2022-PA-SEN, 2020-GA-SEN2, 2018-FL-GOV, 2016-OH-PRES.

Scope: statewide races in even years, 2000 to 2024: president by state, U.S. Senate, governor. House districts are
out of scope (no free district-level lean was found). Odd-year races (specials, Virginia and New Jersey
governors, the 2003 California recall), primaries, runoffs, and the 2012 Wisconsin recall are left out.
President in Maine and Nebraska counts the state's statewide vote only, not the congressional districts.

Columns (one row per race):
  race, year, state, office, midterm (1 in a year with no presidential election)
  d_name, r_name         the Democratic and Republican candidates, as the result's source spells them
  result_margin          D minus R in percentage points of the two-party vote: 100 * (D - R) / (D + R).
                         Blank when the race had no single D and single R candidate (jungle primaries,
                         two Democrats in the final, nobody running for one party) or the source has no result.
  result_source          where the result came from
  polls_final21          polls with a Democrat-vs-Republican question that ended in the 21 days up to and
                         including election day
  avg_margin             cw_polls.average() of those polls on election morning, D minus R (blank with no polls)
  avg_n_eff              how many equal polls that average rests on
  avg_newest_days        days from the newest poll to election day
  lean                   the state's two-party presidential margin minus the nation's, mean of the last two
                         presidential elections before that year (percentage points)
  generic_ballot         the national generic House ballot average on election day, D minus R (blank if the
                         file has no row for that day)
  national_house_vote    the national House vote that year, D minus R, two-party, summed over districts (blank
                         where there is no free source)
  incumbent_running      D or R when the sitting officeholder of that party (appointed ones included) is on the
                         ballot for this office; I when the incumbent is on the ballot under another party;
                         "open" when the seat has no incumbent running; blank only when no source says
  incumbent_source       the source behind incumbent_running (blank exactly when incumbent_running is)
  notes                  flags, separated by semicolons

How the polls are turned into an average. The polls come from two files:
  - 2000 to 2022: FiveThirtyEight's `raw_polls.csv` (the last 61 days of each race, top two finishers only).
    It has one date per poll (the middle of fieldwork), used as both start and end; no voter type; no sample
    size for some. A poll's `partisan` flag (DEM or REP) is taken as its sponsor, so cw_polls shifts it.
  - 2024: the Internet Archive copies of FiveThirtyEight's poll files (president and Senate). Voter type and
    sponsor party are there. Only questions without a hypothetical match-up are used, and where a poll asked
    several full questions the one that lists the most candidates is used.
Only polls in the final 21 days are averaged, so an average always has a poll behind it in that window.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import cw_polls  # noqa: E402

FIRST_YEAR, LAST_YEAR = 2000, 2024
WINDOW_DAYS = 21
OFFICE_ORDER = {"PRES": 0, "SEN": 1, "SEN2": 2, "GOV": 3}
COLUMNS = ["race", "year", "state", "office", "midterm", "d_name", "r_name", "result_margin", "result_source",
           "polls_final21", "avg_margin", "avg_n_eff", "avg_newest_days", "lean", "generic_ballot",
           "national_house_vote", "incumbent_running", "incumbent_source", "notes"]

STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
    "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA",
    "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY"}
POSTAL = set(STATES.values())


# ---------------------------------------------------------------- small helpers

def read_rows(path, delimiter=","):
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        return list(csv.DictReader(f, delimiter=delimiter))


def number(text):
    """A float, or None for a blank or unreadable cell."""
    try:
        x = float(str(text).strip())
    except (ValueError, TypeError):
        return None
    return x if math.isfinite(x) else None


def two_party_margin(d, r):
    """D minus R in points of the two-party vote, or None unless both have votes."""
    if d and r and d > 0 and r > 0:
        return 100.0 * (d - r) / (d + r)
    return None


def election_day(year):
    """The Tuesday after the first Monday of November."""
    first = dt.date(year, 11, 1)
    monday = first + dt.timedelta(days=(0 - first.weekday()) % 7)
    return monday + dt.timedelta(days=1)


def race_id(year, state, office):
    return f"{year}-{state}-{office}"


STRAY = 0.01  # a second candidate of one party with under 1% of that party's leader's votes is a stray ballot line


def without_strays(same_party):
    """Drop stray lines (a handful of votes filed under a party's name) so a real jungle race still shows as two."""
    if len(same_party) < 2:
        return same_party
    top = max(c[2] for c in same_party)
    return [c for c in same_party if c[2] >= STRAY * top]


def without_writeins(lines):
    """[(name, party, votes, incumbent, writein)] -> [(name, party, votes, incumbent)]. Write-in lines are dropped
    when the race has a Democrat and a Republican without them (they can carry stray party labels); a state that
    files everything as write-in (DC) keeps them."""
    plain = [c[:4] for c in lines if not c[4]]
    if {c[1] for c in plain} >= {"D", "R"}:
        return plain
    return [c[:4] for c in lines]


def party_of(detailed, simplified):
    """D, R or other, from the two party columns of an MIT file (their spelling varies: DEMOCRAT, DEMOCRATIC,
    DEMOCRATIC-FARMER-LABOR...). A blank in both columns is 'other'."""
    for text in (detailed, simplified):
        t = (text or "").upper()
        if t.startswith("DEMOCRAT"):
            return "D"
        if t.startswith("REPUBLICAN"):
            return "R"
    return "other"


def pick_sides(candidates):
    """From [(name, party, votes, incumbent)] (party D, R or other) pick the one Democrat and the one Republican.

    Returns (d, r, problem): d and r are (name, votes, incumbent) or None; problem is a sentence when a side is
    missing or has more than one candidate (then both are None, since no single two-party margin exists)."""
    ds, rs = (without_strays([c for c in candidates if c[1] == p]) for p in ("D", "R"))
    if len(ds) != 1 or len(rs) != 1:
        why = []
        if not ds:
            why.append("no candidate labelled Democratic in the source")
        if len(ds) > 1:
            why.append("more than one real Democratic candidate")
        if not rs:
            why.append("no candidate labelled Republican in the source")
        if len(rs) > 1:
            why.append("more than one real Republican candidate")
        return None, None, "; ".join(why)
    return (ds[0][0], ds[0][2], ds[0][3]), (rs[0][0], rs[0][2], rs[0][3]), ""


def merge_by_name(lines):
    """[(name, party, votes, incumbent)] -> one entry per candidate, votes summed over the candidate's ballot lines
    (a fusion candidate appears on several party lines). The party is D or R if any of the lines says so."""
    by = collections.OrderedDict()
    for name, party, votes, inc in lines:
        cur = by.get(name)
        if cur is None:
            by[name] = [name, party, votes, inc]
        else:
            cur[2] += votes
            if cur[1] not in ("D", "R") and party in ("D", "R"):
                cur[1] = party
            cur[3] = cur[3] or inc
    return [tuple(v) for v in by.values()]


# ---------------------------------------------------------------- results and incumbency

def load_mit_president(path):
    """{(year, state): [(name, party, votes, False)]} from the MIT file; party is D, R or other."""
    out = collections.defaultdict(list)
    for r in read_rows(path):
        if r["office"] != "US PRESIDENT":
            continue
        votes = number(r["candidatevotes"])
        if votes is None:
            continue
        party = party_of(r["party_detailed"], r["party_simplified"])
        out[(int(r["year"]), r["state_po"])].append((r["candidate"], party, votes, False, r["writein"] == "True"))
    return {k: merge_by_name(without_writeins(v)) for k, v in out.items()}


def president_margins(pres):
    """({(year, state): margin}, {year: national margin}) for every presidential year in the MIT file."""
    state, totals = {}, collections.defaultdict(lambda: [0.0, 0.0])
    for (year, st), cands in pres.items():
        d, r, _ = pick_sides(cands)
        if d is None:
            continue
        state[(year, st)] = two_party_margin(d[1], r[1])
        totals[year][0] += d[1]
        totals[year][1] += r[1]
    return state, {y: two_party_margin(v[0], v[1]) for y, v in totals.items()}


def lean_for(year, st, state_margin, nation):
    """Mean over the last two presidential years before `year` of (state margin - national margin), or None."""
    years = sorted({y for (y, _s) in state_margin if y < year})[-2:]
    vals = [state_margin[(y, st)] - nation[y] for y in years if (y, st) in state_margin]
    return sum(vals) / len(vals) if len(vals) == 2 else None


# MIT labels Georgia's December 2008 runoff "gen" and the November 4 election "pre" (first-round votes
# 1,867,097 and 1,757,393 on Wikipedia's page). The polls were for November 4, so that is the race used.
MIT_STAGE_FIX = {(2008, "GA"): "pre"}


def load_mit_senate(path):
    """{(year, state, special): [(name, party, votes, False)]} for general-election Senate races, 2000 on."""
    out = collections.defaultdict(list)
    for r in read_rows(path):
        year = int(r["year"])
        want = MIT_STAGE_FIX.get((year, r["state_po"]), "gen")
        if year < FIRST_YEAR or r["stage"].lower() != want or r["mode"].lower() != "total":
            continue
        votes = number(r["candidatevotes"])
        if votes is None:
            continue
        party = party_of(r["party_detailed"], r["party_simplified"])
        out[(year, r["state_po"], r["special"] == "True")].append((r["candidate"], party, votes, False,
                                                                 r["writein"] == "True"))
    return {k: merge_by_name(without_writeins(v)) for k, v in out.items()}


# Vote totals in the candidates file that disagree with the results on Wikipedia's race page, in a way that moves the
# two-party margin by half a point or more: a digit off, a partial count, or the fusion party lines (Working
# Families, Conservative...) left out. The file's own totals add up to its own `totalvotes`, so it cannot catch
# these. Only the Democrat and the Republican are corrected. Found by comparing every governor race in the file
# with its Wikipedia page (see the note pre-history.md). Key: (office, year, state, special) -> {name as the file
# spells it: votes}.
VOTE_FIXES = {
    ("G", 2010, "ME", False): {"MITCHELL, ELIZABETH H. (LIBBY)": 109387.0},  # file: 103,387
    ("G", 2018, "HI", False): {"TUPOLA, ANDRIA": 131719.0},                  # file: 137,719
    ("G", 2008, "UT", False): {"HUNTSMAN, JON M., JR.": 734049.0,            # file: 700,565 and 175,301
                               "SPRINGMEYER, BOB": 186503.0},
    ("G", 2012, "WV", False): {"TOMBLIN, EARL RAY": 335468.0,                # file: 297,207 and 273,130
                               "MALONEY, WILLIAM (BILL)": 303291.0},
    ("G", 2018, "NY", False): {"CUOMO, ANDREW M.": 3635340.0,                # file: main party line only
                               "MOLINARO, MARCUS J. (MARC)": 2207602.0},
    ("G", 2018, "CT", False): {"LAMONT, EDWARD M. (NED)": 694510.0,          # file: main party line only
                               "STEFANOWSKI, BOB": 650138.0},
}


# Results the poll file gives for a race with no result file, replaced where the poll file leaves out the fusion
# party lines: the Democrat's and the Republican's votes on Wikipedia's race page. Key: race id.
WIKIPEDIA_RESULTS = {
    "2002-NY-GOV": (1534064.0, 2262255.0),   # McCall, Pataki: the poll file counts only the main party lines
}
SRC_WP = "Wikipedia race page (Clerk or state results)"
SRC_INC_WP = "Wikipedia results tables of the 2000, 2002 and 2004 elections"

# Incumbency before the candidates file starts (2006). From the "Incumbent" column and result text of the Wikipedia
# page for each of those years' Senate and gubernatorial elections: the party of the sitting officeholder (appointed
# ones included) when that person was on the November ballot, "open" when they retired, were term-limited, lost the
# primary, died or withdrew (Minnesota and New Jersey Senate 2002: the party's replacement is not an incumbent).
INCUMBENTS_2000_2004 = {
    "2000-AZ-SEN": "R", "2000-CA-SEN": "D", "2000-CT-SEN": "D", "2000-DE-GOV": "open", "2000-DE-SEN": "R",
    "2000-FL-SEN": "open", "2000-GA-SEN": "D", "2000-HI-SEN": "D", "2000-IN-GOV": "D", "2000-IN-SEN": "R",
    "2000-MA-SEN": "D", "2000-MD-SEN": "D", "2000-ME-SEN": "R", "2000-MI-SEN": "R", "2000-MN-SEN": "R",
    "2000-MO-GOV": "open", "2000-MO-SEN": "R", "2000-MS-SEN": "R", "2000-MT-GOV": "open", "2000-MT-SEN": "R",
    "2000-NC-GOV": "open", "2000-ND-GOV": "open", "2000-ND-SEN": "D", "2000-NE-SEN": "open", "2000-NH-GOV": "D",
    "2000-NJ-SEN": "open", "2000-NM-SEN": "D", "2000-NV-SEN": "open", "2000-NY-SEN": "open", "2000-OH-SEN": "R",
    "2000-PA-SEN": "R", "2000-RI-SEN": "R", "2000-TN-SEN": "R", "2000-TX-SEN": "R", "2000-UT-GOV": "R",
    "2000-UT-SEN": "R", "2000-VA-SEN": "D", "2000-VT-GOV": "D", "2000-VT-SEN": "R", "2000-WA-GOV": "D",
    "2000-WA-SEN": "R", "2000-WI-SEN": "D", "2000-WV-GOV": "R", "2010-IL-SEN2": "open", "2010-RI-GOV": "open", "2000-WV-SEN": "D", "2000-WY-SEN": "R",
    "2002-AK-GOV": "open", "2002-AK-SEN": "R", "2002-AL-GOV": "D", "2002-AL-SEN": "R", "2002-AR-GOV": "R",
    "2002-AR-SEN": "R", "2002-AZ-GOV": "open", "2002-CA-GOV": "D", "2002-CO-GOV": "R", "2002-CO-SEN": "R",
    "2002-CT-GOV": "R", "2002-DE-SEN": "D", "2002-FL-GOV": "R", "2002-GA-GOV": "D", "2002-GA-SEN": "D",
    "2002-HI-GOV": "open", "2002-IA-GOV": "D", "2002-IA-SEN": "D", "2002-ID-GOV": "R", "2002-ID-SEN": "R",
    "2002-IL-GOV": "open", "2002-IL-SEN": "D", "2002-KS-GOV": "open", "2002-KS-SEN": "R", "2002-KY-SEN": "R",
    "2002-LA-SEN": "D", "2002-MA-GOV": "open", "2002-MA-SEN": "D", "2002-MD-GOV": "open", "2002-ME-GOV": "open",
    "2002-ME-SEN": "R", "2002-MI-GOV": "open", "2002-MI-SEN": "D", "2002-MN-GOV": "open", "2002-MN-SEN": "open",
    "2002-MO-SEN": "D", "2002-MS-SEN": "R", "2002-MT-SEN": "D", "2002-NC-SEN": "open", "2002-NE-GOV": "R",
    "2002-NE-SEN": "R", "2002-NH-GOV": "open", "2002-NH-SEN": "open", "2002-NJ-SEN": "open", "2002-NM-GOV": "open",
    "2002-NM-SEN": "R", "2002-NV-GOV": "R", "2002-NY-GOV": "R", "2002-OH-GOV": "R", "2002-OK-GOV": "open",
    "2002-OK-SEN": "R", "2002-OR-GOV": "open", "2002-OR-SEN": "R", "2002-PA-GOV": "open", "2002-RI-GOV": "open",
    "2002-RI-SEN": "D", "2002-SC-GOV": "D", "2002-SC-SEN": "open", "2002-SD-GOV": "open", "2002-SD-SEN": "D",
    "2002-TN-GOV": "open", "2002-TN-SEN": "open", "2002-TX-GOV": "R", "2002-TX-SEN": "open", "2002-VA-SEN": "R",
    "2002-VT-GOV": "open", "2002-WI-GOV": "R", "2002-WV-SEN": "D", "2002-WY-GOV": "open", "2002-WY-SEN": "R",
    "2004-AK-SEN": "R", "2004-AL-SEN": "R", "2004-AR-SEN": "D", "2004-AZ-SEN": "R", "2004-CA-SEN": "D",
    "2004-CO-SEN": "open", "2004-CT-SEN": "D", "2004-DE-GOV": "D", "2004-FL-SEN": "open", "2004-GA-SEN": "open",
    "2004-HI-SEN": "D", "2004-IA-SEN": "R", "2004-ID-SEN": "R", "2004-IL-SEN": "open", "2004-IN-GOV": "D",
    "2004-IN-SEN": "D", "2004-KS-SEN": "R", "2004-KY-SEN": "R", "2004-LA-SEN": "open", "2004-MD-SEN": "D",
    "2004-MO-GOV": "open", "2004-MO-SEN": "R", "2004-MT-GOV": "open", "2004-NC-GOV": "D", "2004-NC-SEN": "open",
    "2004-ND-GOV": "R", "2004-ND-SEN": "D", "2004-NH-GOV": "R", "2004-NH-SEN": "R", "2004-NV-SEN": "D",
    "2004-NY-SEN": "D", "2004-OH-SEN": "R", "2004-OK-SEN": "open", "2004-OR-SEN": "D", "2004-PA-SEN": "R",
    "2004-SC-SEN": "open", "2004-SD-SEN": "D", "2004-UT-GOV": "open", "2004-UT-SEN": "R", "2004-VT-SEN": "D",
    "2004-WA-GOV": "open", "2004-WA-SEN": "D", "2004-WI-SEN": "D", "2004-WV-GOV": "open",
}
# The president: Clinton was term-limited (2000), Bush ran (2004), Obama ran (2012), Trump ran (2020); in 2008, 2016
# and 2024 no sitting president was on the ballot (Biden withdrew; Trump in 2024 is a former president).
INCUMBENT_PRESIDENT = {2000: "open", 2004: "R", 2008: "open", 2012: "D", 2016: "open", 2020: "R", 2024: "open"}


def load_candidates(path):
    """The Candidates in American General Elections file (2006 to 2024).

    Returns (results, everyone, house). `results` is {(office, year, state, special): [(name, party, votes,
    incumbent)]} for offices P, S and G, leaving out the rows of a runoff (for Louisiana and Georgia the file holds
    only the December or January runoff, not the November jungle election the polls were about). `everyone` is the
    same with the runoff rows kept, used for incumbency. `house` is the national House totals
    {year: [D votes, R votes]} (general elections, not specials), a cross-check only."""
    out = collections.defaultdict(list)
    every = collections.defaultdict(list)
    house = collections.defaultdict(lambda: [0.0, 0.0])
    for r in read_rows(path, "\t"):
        year = int(float(r["year"]))
        votes = number(r["candidatevotes"])
        party = {"D": "D", "R": "R"}.get(r["party"], "other")
        special = r["type"] == "S"
        if r["office"] == "H":
            if not special and votes is not None and party in ("D", "R"):
                house[year][0 if party == "D" else 1] += votes
            continue
        if votes is None:
            continue
        inc = (number(r["inc"]) or 0) > 0
        votes = VOTE_FIXES.get((r["office"], year, r["state"], special), {}).get(r["name_snyder"], votes)
        key = (r["office"], year, r["state"], special)
        every[key].append((r["name_snyder"], party, votes, inc))
        if r["runoff"] != "1.0":
            out[key].append((r["name_snyder"], party, votes, inc))
    return ({k: merge_by_name(v) for k, v in out.items()}, {k: merge_by_name(v) for k, v in every.items()},
            dict(house))


def load_house_totals(wp_dir):
    """National House vote {year: [D votes, R votes]} from the infobox of Wikipedia's page for each House election
    (the figures are the Clerk of the House's). Years whose page is not in `wp_dir` are left out."""
    out = {}
    for year in range(FIRST_YEAR, LAST_YEAR + 1, 2):
        path = os.path.join(wp_dir, f"{year}_United_States_House_of_Representatives_elections.txt")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        box = text[text.find("nfobox election"):][:9000]
        votes = {}
        for i in range(1, 5):
            party = re.search(r"\|\s*party" + str(i) + r"\s*=\s*([^\n]*)", box)
            count = re.search(r"\|\s*popular_vote" + str(i) + r"\s*=\s*([^\n]*)", box)
            if not party or not count:
                continue
            m = re.search(r"\d[\d,]+", re.sub(r"<ref.*", "", count.group(1)))
            if not m:
                continue
            side = "D" if "Democratic" in party.group(1) else "R" if "Republican" in party.group(1) else None
            if side and side not in votes:
                votes[side] = float(m.group(0).replace(",", ""))
        if len(votes) == 2:
            out[year] = [votes["D"], votes["R"]]
    return out


def load_generic_ballot(topline_path, old_path):
    """{election date: D minus R} from 538's two generic-ballot files: the old topline file (to 2016-11-06) and
    the old-model averages (2018 to 2022)."""
    out = {}
    for r in read_rows(topline_path):
        if r["subgroup"] != "All polls":
            continue
        d, rep = number(r["dem_estimate"]), number(r["rep_estimate"])
        if d is None or rep is None:
            continue
        m, day, y = r["modeldate"].split("/")
        out[dt.date(int(y), int(m), int(day))] = d - rep
    old = collections.defaultdict(dict)
    for r in read_rows(old_path):
        pct = number(r["pct_estimate"])
        if pct is not None:
            old[dt.date.fromisoformat(r["date"])][r["candidate"]] = pct
    for day, v in old.items():
        if "Democrats" in v and "Republicans" in v:
            out[day] = v["Democrats"] - v["Republicans"]
    return out


# ---------------------------------------------------------------- polls

def final_polls(records, as_of):
    return [p for p in records if 0 <= (as_of - p["end"]).days <= WINDOW_DAYS]


def load_raw_polls(path):
    """Statewide general-election polls, 2000 to 2022, as cw_polls records plus what the file says the result was.

    Returns (polls {(year, state, kind): [record]}, actual {(year, state, kind): (d_name, r_name, d%, r%)},
    skipped {reason: count}). kind is PRES, SEN, SEN2 (special; later mapped), or GOV. Only polls whose two
    named candidates are the Democrat and the Republican become records."""
    polls = collections.defaultdict(list)
    actual = {}
    names = {}
    skipped = collections.Counter()
    offices = {"Pres": "PRES", "Sen": "SEN", "Gov": "GOV"}
    for r in read_rows(path):
        # The race text tells a special Senate race (Sen-GS) from a regular one (Sen-G); type_simple does not.
        m = re.fullmatch(r"\d{4}_(Pres|Sen|Gov)-(G|GS)_([A-Z]{2})", r["race"])
        if m is None or r["type_simple"] not in ("Pres-G", "Sen-G", "Gov-G"):
            if r["type_simple"] in ("Pres-G", "Sen-G", "Gov-G"):
                skipped["not a statewide general race (district, national or runoff)"] += 1
            continue
        kind = offices[m.group(1)]
        if m.group(2) == "GS":
            kind = {"SEN": "SEN2", "GOV": "GOVS"}.get(kind, kind)
        state = r["location"]
        year = int(r["electiondate"][:4])
        if state not in POSTAL:
            skipped["not a statewide general race (district, national or runoff)"] += 1
            continue
        if year % 2 or not FIRST_YEAR <= year <= LAST_YEAR:
            skipped["odd-year race"] += 1
            continue
        key = (year, state, kind)
        parties = {r["cand1_party"]: (r["cand1_name"], r["cand1_pct"], r["cand1_actual"]),
                   r["cand2_party"]: (r["cand2_name"], r["cand2_pct"], r["cand2_actual"])}
        if set(parties) != {"DEM", "REP"}:
            skipped["poll without a Democrat against a Republican"] += 1
            continue
        d, rep = parties["DEM"], parties["REP"]
        names[key] = (d[0], rep[0])
        da, ra = number(d[2]), number(rep[2])
        if da is not None and ra is not None:
            actual[key] = (d[0], rep[0], da, ra)
        sd, sr = number(d[1]), number(rep[1])
        day = dt.date.fromisoformat(r["polldate"])
        if sd is None or sr is None:
            skipped["poll without shares"] += 1
            continue
        polls[key].append({
            "race": None, "pollster": r["pollster"], "start": day, "end": day,
            "n": number(r["samplesize"]) or None, "population": "",
            "sponsor": {"DEM": "D", "REP": "R"}.get(r["partisan"], ""),
            "shares": {"D": sd, "R": sr},
            "_election": dt.date.fromisoformat(r["electiondate"]),
            "_plain": number(r["margin_poll"]), "_actual_raw": number(r["margin_actual"])})
    return polls, actual, names, skipped


def load_wayback_polls(path, office_type, year, state_of_row):
    """2024 polls from an Internet Archive copy of 538's long-format poll file.

    Returns ({key: [record]}, names {key: (d_name, r_name)}, skipped counter). Keys are (year, state, kind)."""
    skipped = collections.Counter()
    questions = collections.defaultdict(list)
    for r in read_rows(path):
        if r["cycle"] != str(year) or r["office_type"] != office_type or r["stage"] != "general":
            continue
        if r["hypothetical"].lower() == "true":
            skipped["hypothetical match-up"] += 1
            continue
        st = state_of_row(r)
        if st is None:
            skipped["not a statewide race (national, district or territory)"] += 1
            continue
        questions[(r["poll_id"], st, r["seat_number"], r["question_id"])].append(r)
    best = {}
    for (poll, st, seat, qid), rows in questions.items():
        parties = [x["party"] for x in rows]
        if parties.count("DEM") != 1 or parties.count("REP") != 1:
            skipped["question without exactly one Democrat and one Republican"] += 1
            continue
        k = (poll, st, seat)
        if k not in best or (len(rows), -int(qid)) > (len(best[k]), -int(best[k][0]["question_id"])):
            best[k] = rows
    out = collections.defaultdict(list)
    names = {}
    for (poll, st, seat), rows in best.items():
        first = rows[0]
        d = [x for x in rows if x["party"] == "DEM"][0]
        rep = [x for x in rows if x["party"] == "REP"][0]
        sd, sr = number(d["pct"]), number(rep["pct"])
        start, end = (dt.datetime.strptime(first[c], "%m/%d/%y").date() for c in ("start_date", "end_date"))
        if sd is None or sr is None:
            continue
        sponsor = {"DEM": "D", "REP": "R"}.get(first["sponsor_candidate_party"]) or \
                  {"DEM": "D", "REP": "R"}.get(first["partisan"], "")
        pop = first["population"] if first["population"] in ("lv", "rv", "a") else ""
        kind = "PRES" if office_type == "U.S. President" else ("SEN" if seat in ("0", "") else "SEN2")
        names[(year, st, kind)] = (d["candidate_name"], rep["candidate_name"])
        out[(year, st, kind)].append({
            "race": None, "pollster": first["pollster"], "start": start, "end": end,
            "n": number(first["sample_size"]) or None, "population": pop, "sponsor": sponsor,
            "shares": {"D": sd, "R": sr}, "_election": election_day(year)})
    return out, names, skipped


# ---------------------------------------------------------------- the table

SRC_MIT_PRES = "MIT president 1976-2024"
SRC_MIT_SEN = "MIT Senate 1976-2024"
SRC_CAND = "Candidates in American General Elections 2006-2024"
SRC_RAW = "538 raw_polls.csv (the Democrat and the Republican)"


class Race:
    def __init__(self, year, state, office):
        self.year, self.state, self.office = year, state, office
        self.id = race_id(year, state, office)
        self.d_name = self.r_name = ""
        self.margin = None
        self.source = ""
        self.incumbent = ""
        self.incumbent_source = ""
        self.special = False
        self.fixed = False
        self.problems = []
        self.first_source = ""
        self.polls = []
        self.poll_names = None
        self.raw_actual = None   # what the poll file says, for the cross-check

    def sort_key(self):
        return (self.year, OFFICE_ORDER[self.office], self.state)


def build(sources):
    p = lambda *a: os.path.join(sources, *a)
    pres = load_mit_president(p("dv", "pres.csv"))
    pres_state, pres_nation = president_margins(pres)
    senate = load_mit_senate(p("dv", "senate.csv"))
    cands, cands_all, house_cand = load_candidates(p("dv", "candidates_2006-2024.tab"))
    house_wp = load_house_totals(p("wp-results"))
    generic = load_generic_ballot(p("congress-generic-ballot", "generic_topline_historical.csv"),
                                  p("polls", "old_model", "generic_ballot_averages_old_model.csv"))
    raw_polls, raw_actual, raw_names, skipped = load_raw_polls(p("pollster-ratings", "raw_polls.csv"))
    report = {"skipped_polls": skipped, "fallbacks": collections.Counter()}

    races = {}

    def get(year, state, office):
        k = (year, state, office)
        if k not in races:
            races[k] = Race(year, state, office)
        return races[k]

    def use_result(r, cs, source):
        """Fill the race's names and margin from this source's candidates unless an earlier source already did."""
        if r.margin is not None:
            return
        d, rep, why = pick_sides(cs)
        if d is None:
            r.problems.append(f"{source}: {why}")
            return
        r.d_name, r.r_name = d[0], rep[0]
        r.margin = two_party_margin(d[1], rep[1])
        r.source = source
        if source != r.first_source:
            report["fallbacks"][source] += 1

    # Which state-years had one Senate race and which two (a regular one and a special), from both files.
    struct = collections.defaultdict(set)
    for (year, st, special) in senate:
        if year % 2 == 0 and st in POSTAL:
            struct[(year, st)].add(special)
    for (office, year, st, special) in cands:
        if office == "S" and year % 2 == 0 and st in POSTAL:
            struct[(year, st)].add(special)

    def senate_office(year, st, special):
        """SEN or SEN2 for a Senate race, SEN2 being the special one in a state-year that has two."""
        kinds = struct.get((year, st), set())
        return "SEN2" if (special and len(kinds) == 2) else "SEN"

    # President: the MIT file, then the candidates file where MIT has no usable result.
    for (year, st), cs in sorted(pres.items()):
        if year % 4 == 0 and FIRST_YEAR <= year <= LAST_YEAR and st in POSTAL:
            r = get(year, st, "PRES")
            r.first_source = SRC_MIT_PRES
            use_result(r, cs, SRC_MIT_PRES)
    for (office, year, st, special), cs in sorted(cands.items()):
        if office == "P" and year % 4 == 0 and st in POSTAL:
            use_result(get(year, st, "PRES"), cs, SRC_CAND)

    # Senate: the MIT file, then the candidates file.
    for (year, st, special), cs in sorted(senate.items()):
        if year % 2 == 0 and FIRST_YEAR <= year <= LAST_YEAR and st in POSTAL:
            r = get(year, st, senate_office(year, st, special))
            r.special = r.special or special
            r.first_source = SRC_MIT_SEN
            use_result(r, cs, SRC_MIT_SEN)
    for (office, year, st, special), cs in sorted(cands.items()):
        if office == "S" and year % 2 == 0 and st in POSTAL:
            r = get(year, st, senate_office(year, st, special))
            r.special = r.special or special
            use_result(r, cs, SRC_CAND)

    # Governor: the candidates file (2006 on). A special election counts only when it is the state-year's
    # one governor race (Utah 2010, Oregon 2016).
    regular = {(year, st) for (office, year, st, special) in cands if office == "G" and not special}
    for (office, year, st, special), cs in sorted(cands.items()):
        if office == "G" and year % 2 == 0 and st in POSTAL and not (special and (year, st) in regular):
            r = get(year, st, "GOV")
            r.special = r.special or special
            r.first_source = SRC_CAND
            r.fixed = (office, year, st, special) in VOTE_FIXES
            use_result(r, cs, SRC_CAND)

    # Incumbency, from the candidates file (2006 on).
    for (office, year, st, special), cs in cands_all.items():
        if year % 2 or st not in POSTAL or (office == "G" and special and (year, st) in regular):
            continue
        o = {"P": "PRES", "G": "GOV"}.get(office) or (senate_office(year, st, special) if office == "S" else None)
        r = races.get((year, st, o))
        if r is None:
            continue
        r.incumbent_source = SRC_CAND
        flagged = {c[1] for c in cs if c[3]}
        r.incumbent = "D" if "D" in flagged else "R" if "R" in flagged else "I" if flagged else "open"

    def attach(poll_map, names, actuals=None):
        for (year, st, kind), recs in sorted(poll_map.items()):
            office = senate_office(year, st, kind == "SEN2") if kind in ("SEN", "SEN2") else kind
            if kind == "GOVS":
                # A special governor race counts only where it is the state-year's one governor race.
                if not (year, st, "GOV") in races or not races[(year, st, "GOV")].special:
                    report["skipped_polls"]["special governor race with no result file (the 2012 Wisconsin recall)"] += len(recs)
                    continue
                office = "GOV"
            r = get(year, st, office)
            r.polls = recs
            for q in recs:
                q["race"] = r.id
            if (year, st, kind) in names:
                r.poll_names = names[(year, st, kind)]
            if actuals and (year, st, kind) in actuals:
                r.raw_actual = actuals[(year, st, kind)]

    # Polls, 2000 to 2022 (538 raw_polls.csv).
    attach(raw_polls, raw_names, raw_actual)

    # Incumbency the candidates file does not cover: 2000 to 2004 from the table above, and the president for every
    # year (the same everywhere; the file agrees in every state except DC 2020, where it has no flag).
    for r in races.values():
        if r.office == "PRES":
            r.incumbent, r.incumbent_source = INCUMBENT_PRESIDENT[r.year], SRC_INC_WP
        elif not r.incumbent_source and r.id in INCUMBENTS_2000_2004:
            r.incumbent, r.incumbent_source = INCUMBENTS_2000_2004[r.id], SRC_INC_WP

    # A race no result file has: the poll file's own result for the top two finishers, when they are D and R.
    for r in races.values():
        if r.margin is None and r.raw_actual:
            r.d_name, r.r_name = r.raw_actual[:2]
            r.margin = two_party_margin(r.raw_actual[2], r.raw_actual[3])
            r.source = SRC_RAW
            report["fallbacks"][SRC_RAW] += 1

    for r in races.values():
        if r.id in WIKIPEDIA_RESULTS and r.source == SRC_RAW:
            r.margin = two_party_margin(*WIKIPEDIA_RESULTS[r.id])
            r.source = SRC_WP

    # 2024 polls (president and Senate), from the Internet Archive copies of 538's files.
    pres24, pnames, sk = load_wayback_polls(p("live", "president_polls_historical.csv"), "U.S. President", 2024,
                                            lambda row: STATES.get(row["state"]))
    sen24, snames, sk2 = load_wayback_polls(p("live", "senate_polls_historical.csv"), "U.S. Senate", 2024,
                                            lambda row: STATES.get(row["state"]))
    skipped.update({"2024 " + k: v for k, v in (sk + sk2).items()})
    attach(pres24, pnames)
    attach(sen24, snames)

    rows = []
    for r in sorted(races.values(), key=lambda x: x.sort_key()):
        # Nearly always the first Tuesday after the first Monday of November; the poll file says when it was not
        # (the January 2010 Massachusetts special).
        as_of = r.polls[0]["_election"] if r.polls else election_day(r.year)
        recent = final_polls(r.polls, as_of)
        avg = cw_polls.average(recent, r.id, as_of) if recent else None
        have = avg is not None and avg["margin"] is not None
        lean = lean_for(r.year, r.state, pres_state, pres_nation)
        gen = generic.get(as_of)
        house = house_wp.get(r.year)
        nhv = two_party_margin(*house) if house else None
        if not r.d_name and r.poll_names:
            r.d_name, r.r_name = r.poll_names
        notes = []
        if r.special:
            notes.append("special election")
        if r.fixed:
            notes.append("a vote total in the candidates file was wrong and is corrected from Wikipedia")
        if r.margin is None:
            notes.append("result blank: " + ("; ".join(r.problems) if r.problems else "no source has a result"))
        if r.source == SRC_RAW:
            notes.append("result from the poll file; a candidate on several party lines (fusion) may be undercounted")
        if r.source == SRC_WP:
            notes.append("result from Wikipedia: the poll file counted only the main party lines")
        if r.polls and not recent:
            notes.append("polls only before the last 21 days")
        if not r.polls:
            notes.append("no polls")
        rows.append({
            "race": r.id, "year": r.year, "state": r.state, "office": r.office,
            "midterm": 0 if r.year % 4 == 0 else 1,
            "d_name": r.d_name, "r_name": r.r_name,
            "result_margin": "" if r.margin is None else f"{r.margin:.2f}",
            "result_source": r.source if r.margin is not None else "",
            "polls_final21": len(recent) if r.polls else "",
            "avg_margin": f"{avg['margin']:.2f}" if have else "",
            "avg_n_eff": f"{avg['effective_polls']:.2f}" if have else "",
            "avg_newest_days": avg["newest_age"] if have else "",
            "lean": "" if lean is None else f"{lean:.2f}",
            "generic_ballot": "" if gen is None else f"{gen:.2f}",
            "national_house_vote": "" if nhv is None else f"{nhv:.2f}",
            "incumbent_running": r.incumbent,
            "incumbent_source": r.incumbent_source,
            "notes": "; ".join(notes)})
        r.avg = avg if have else None
        r.recent = recent
    report.update(races=races, generic=generic, house_cand=house_cand, house_wp=house_wp)
    return rows, report


def write(rows, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------- the checks

def print_report(rows, report):
    races = report["races"]
    print("== Rows by year and office")
    c = collections.Counter((int(r["year"]), r["office"]) for r in rows)
    offices = ["PRES", "SEN", "SEN2", "GOV"]
    print("year  " + "  ".join(f"{o:>5}" for o in offices) + "  total")
    for y in sorted({k[0] for k in c}):
        print(f"{y}  " + "  ".join(f"{c.get((y, o), 0):>5}" for o in offices) + f"  {sum(c.get((y, o), 0) for o in offices):>5}")
    print("total", len(rows))
    print("\n== Rows with a result / with polls in the last 21 days / with avg_margin")
    for y in sorted({int(r['year']) for r in rows}):
        for o in offices:
            sub = [r for r in rows if int(r["year"]) == y and r["office"] == o]
            if sub:
                print(f"{y} {o:>4}: rows {len(sub):>3}  result {sum(1 for r in sub if r['result_margin'] != ''):>3}"
                      f"  polls21>0 {sum(1 for r in sub if r['polls_final21'] not in ('', 0)):>3}"
                      f"  avg {sum(1 for r in sub if r['avg_margin'] != ''):>3}")
    print("\n== Blank cells")
    for col in COLUMNS:
        print(f"{col}: {sum(1 for r in rows if r[col] == '')} blank")
    print("\n== avg_margin against the result (two-party) and against the poll file's own all-votes margin")
    for o in ["PRES", "SEN", "GOV", "ALL"]:
        res, raw, plain = [], [], []
        for r in races.values():
            if r.avg is None or r.margin is None or r.year > 2022 or (o != "ALL" and r.office[:3] != o[:3]):
                continue
            res.append(abs(r.margin - r.avg["margin"]))
            a = [p for p in r.recent if p.get("_actual_raw") is not None]
            if a and r.recent[0].get("_plain") is not None:
                pm = sum(p["_plain"] for p in r.recent) / len(r.recent)
                raw.append(abs(r.recent[0]["_actual_raw"] - r.avg["margin"]))
                plain.append(abs(r.recent[0]["_actual_raw"] - pm))
        if res:
            print(f"{o:>4} 2000-2022: races {len(res):>3}  |two-party result - avg| {sum(res)/len(res):.2f}"
                  f"   |all-votes result - avg| {sum(raw)/len(raw):.2f}  plain average of same polls {sum(plain)/len(plain):.2f}")
    print("\n== 2024")
    res = [abs(r.margin - r.avg["margin"]) for r in races.values() if r.year == 2024 and r.avg and r.margin is not None]
    print(f"races with both an average and a result: {len(res)}, mean |result - avg| {sum(res)/len(res):.2f}" if res else "none")
    print("\n== Result cross-check, MIT or candidates file against the poll file's result (two-party points)")
    diffs = []
    for r in races.values():
        if r.raw_actual and r.margin is not None and r.source != SRC_RAW:
            _d, _r, da, ra = r.raw_actual
            diffs.append((abs(two_party_margin(da, ra) - r.margin), r.id))
    diffs.sort(reverse=True)
    print(f"{len(diffs)} races in both; mean difference {sum(d for d, _ in diffs)/len(diffs):.3f}; largest:")
    for d, i in diffs[:8]:
        print(f"  {i}: {d:.2f}")
    print("\n== Result blank (race, why)")
    for r in sorted(races.values(), key=lambda x: x.sort_key()):
        if r.margin is None and r.year <= LAST_YEAR:
            print(" ", r.id, "|", "; ".join(r.problems))
    print("\n== Polls skipped")
    for k, v in sorted(report["skipped_polls"].items()):
        print(f"  {v:>5} {k}")
    print("== Results taken from a second or third source:", dict(report["fallbacks"]))
    print("\n== National House vote, D minus R two-party: Wikipedia (used) against the sum of the candidates file")
    hc, hw = report["house_cand"], report["house_wp"]
    for y in sorted(hw):
        a = two_party_margin(*hc[y]) if y in hc else None
        print(f"  {y}: Wikipedia {two_party_margin(*hw[y]):.2f}  candidates-file sum {'-' if a is None else f'{a:.2f}'}")
    print("\n== Generic ballot on election day")
    for y in range(FIRST_YEAR, LAST_YEAR + 1, 2):
        g = report["generic"].get(election_day(y))
        print(f"  {y}: {'-' if g is None else f'{g:.2f}'}")
    print("\n== Incumbency")
    for y in range(FIRST_YEAR, LAST_YEAR + 1, 2):
        sub = [r for r in races.values() if r.year == y]
        print(f"  {y}: rows {len(sub)}  source {sum(1 for r in sub if r.incumbent_source)}  D {sum(1 for r in sub if r.incumbent == 'D')}  R {sum(1 for r in sub if r.incumbent == 'R')}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sources", default=os.path.join(ROOT, "research", "sources"))
    ap.add_argument("--out", default=os.path.join(ROOT, "history", "pre_history.csv"))
    ap.add_argument("--report", action="store_true", help="also print the checks")
    args = ap.parse_args(argv)
    rows, report = build(args.sources)
    write(rows, args.out)
    print(f"wrote {len(rows)} rows to {args.out}")
    if args.report:
        print_report(rows, report)


if __name__ == "__main__":
    main()
