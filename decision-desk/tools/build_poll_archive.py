"""Build history/polls_final.csv: every poll of a statewide general (president, Senate, governor) taken in its last
21 days, 2000 to 2024, with who ran it, how, and how the race came out. cw_quality.py rates pollsters from it.

    python tools/build_poll_archive.py

Reads (from research/sources/, which stays on this PC; see research/SOURCES.md):
  pollster-ratings/raw_polls.csv           538's archive of final-three-weeks polls, 1998-2022, with each poll's
                                            methodology, the pollster's AAPOR/Roper membership and partisan sponsor
  live/senate_polls_historical.csv         538's poll database (Internet Archive copy, March 2025): 2024's polls
  live/president_polls_historical.csv
and history/pre_history.csv for each race's result, so every margin here is two-party, in points, D side +.

Kept: two-candidate Democrat-against-Republican questions in races history/pre_history.csv knows, not runoffs, not
hypothetical match-ups, not states with two Senate races the same year (which seat a 2024 poll asked about can't be
told apart reliably). Data from FiveThirtyEight (ABC News), licensed CC BY 4.0.
"""
from __future__ import annotations

import csv
import datetime as dt
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SRC = HERE / "research" / "sources"
OUT = HERE / "history" / "polls_final.csv"
DAYS = 21
TYPES = {"Sen-G": "SEN", "Gov-G": "GOV", "Pres-G": "PRES"}
STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
    "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY",
    "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH",
    "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND",
    "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
    "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY"}
COLUMNS = ("race", "year", "state", "office", "pollster", "pollster_id", "end", "days_out", "n", "population",
           "methodology", "aapor_roper", "transparency", "partisan", "margin", "result")


def _date(text: str) -> dt.date | None:
    for fmt in ("%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _two_party(d: float, r: float) -> float | None:
    return 100 * (d - r) / (d + r) if d + r > 0 else None


def results() -> dict[str, float]:
    with open(HERE / "history" / "pre_history.csv", encoding="utf-8") as f:
        return {r["race"]: float(r["result_margin"]) for r in csv.DictReader(f) if r["result_margin"]}


def from_raw(known: dict[str, float]) -> tuple[list[dict], dict[str, dict]]:
    """Polls through 2022, and what 538 knew about each pollster (by its rating id) for the later files."""
    out, about = [], {}
    with open(SRC / "pollster-ratings" / "raw_polls.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            about[r["pollster_rating_id"]] = {"aapor_roper": r["aapor_roper"] == "TRUE"}
            office = TYPES.get(r["type_simple"])
            year, st = r["cycle"], r["location"]
            if not office or r["race"] != f"{year}_{r['type_simple']}_{st}":
                continue                            # runoffs, specials and district votes are named otherwise
            race = f"{year}-{st}-{office}"
            if race not in known or {r["cand1_party"], r["cand2_party"]} != {"DEM", "REP"}:
                continue
            days = int(float(r["time_to_election"] or 99))
            if days > DAYS:
                continue
            d, rp = (r["cand1_pct"], r["cand2_pct"]) if r["cand1_party"] == "DEM" else (r["cand2_pct"], r["cand1_pct"])
            m = _two_party(float(d), float(rp))
            if m is None:
                continue
            out.append({"race": race, "year": int(year), "state": st, "office": office, "pollster": r["pollster"],
                        "pollster_id": r["pollster_rating_id"], "end": r["polldate"], "days_out": days,
                        "n": r["samplesize"] if r["samplesize"] not in ("", "NA") else "", "population": "",
                        "methodology": "" if r["methodology"] == "NA" else r["methodology"],
                        "aapor_roper": int(r["aapor_roper"] == "TRUE"), "transparency": r["transparency_score"],
                        "partisan": "" if r["partisan"] == "NA" else r["partisan"], "margin": round(m, 2),
                        "result": known[race]})
    return out, about


def from_live(name: str, office: str, year: int, known: dict[str, float], about: dict[str, dict]) -> list[dict]:
    """One year's polls from 538's poll database: one row per answer, so each question is put back together."""
    questions: dict[str, dict] = defaultdict(lambda: {"ans": {}})
    seats: dict[str, set] = defaultdict(set)
    with open(SRC / "live" / name, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["cycle"] != str(year) or r["stage"] != "general" or r["hypothetical"] == "true" or not r["state"]:
                continue
            st = STATES.get(r["state"])
            if not st:
                continue                            # districts (Maine CD-2), national polls
            seats[st].add(r.get("seat_name") or "")
            q = questions[r["question_id"]]
            q.update(row=r, st=st)
            q["ans"].setdefault(r["party"], 0.0)
            q["ans"][r["party"]] += float(r["pct"] or 0)
    out = []
    for q in questions.values():
        r, st = q["row"], q["st"]
        race = f"{year}-{st}-{office}"
        if race not in known or len(seats[st]) > 1 or not {"DEM", "REP"} <= set(q["ans"]):
            continue
        end, day = _date(r["end_date"]), _date(r["election_date"])
        if not end or not day or (day - end).days > DAYS:
            continue
        m = _two_party(q["ans"]["DEM"], q["ans"]["REP"])
        if m is None:
            continue
        pid = r["pollster_rating_id"]
        out.append({"race": race, "year": year, "state": st, "office": office, "pollster": r["pollster"],
                    "pollster_id": pid, "end": end.isoformat(), "days_out": (day - end).days,
                    "n": r["sample_size"], "population": r["population"], "methodology": r["methodology"],
                    "aapor_roper": int((about.get(pid) or {}).get("aapor_roper", False)),
                    "transparency": r["transparency_score"],
                    "partisan": r["partisan"] or ("internal" if r.get("internal") == "true" else ""),
                    "margin": round(m, 2), "result": known[race]})
    return out


def from_pack_2026(earlier: list[dict]) -> list[dict]:
    """2026's Senate polls from the 2026 pack's countywatch/polls.csv (read from Wikipedia, see polls_credit.txt),
    scored against the county results the bot held when the count ended (countywatch/live.json). Each pollster is
    tied to its 538 identity by name where the archive knows it, with the method and membership it had there."""
    sys.path.insert(0, str(HERE))
    import cw_quality as Q
    try:
        with open(HERE / "countywatch" / "polls.csv", encoding="utf-8") as f:
            pack_rows = list(csv.DictReader(f))
        live = __import__("json").loads((HERE / "countywatch" / "live.json").read_text(encoding="utf-8"))["states"]
    except OSError:
        return []
    ident: dict[str, dict] = {}
    for p in earlier:                               # the latest record of each name wins
        ident[Q.norm(p["pollster"])] = p
    out = []
    for race, polls in Q.from_pack_rows(pack_rows, "2026-11-03", DAYS).items():
        st = race.split("-")[0]
        if st not in live or st == "NE":            # Nebraska's D side is an independent: not a D-R race
            continue
        d, r = sum(v["D"] for v in live[st].values()), sum(v["R"] for v in live[st].values())
        result = round(_two_party(d, r), 2)
        for p in polls:
            seen = next((ident[k] for k in Q._parts(p["pollster"]) if k in ident), None) or {}
            out.append({"race": f"2026-{st}-SEN", "year": 2026, "state": st, "office": "SEN",
                        "pollster": p["pollster"], "pollster_id": seen.get("pollster_id", ""), "end": p["end"],
                        "days_out": p["days_out"], "n": int(p["n"]) if p["n"] else "", "population": p["population"],
                        "methodology": seen.get("methodology", ""), "aapor_roper": int(bool(seen.get("aapor_roper"))),
                        "transparency": seen.get("transparency", ""), "partisan": p["partisan"],
                        "margin": round(p["margin"], 2), "result": result})
    return out


def build() -> list[dict]:
    known = results()
    rows, about = from_raw(known)
    rows = [r for r in rows if r["year"] < 2024]
    rows += from_live("senate_polls_historical.csv", "SEN", 2024, known, about)
    rows += from_live("president_polls_historical.csv", "PRES", 2024, known, about)
    rows += from_pack_2026(rows)
    rows.sort(key=lambda r: (r["year"], r["race"], r["end"], r["pollster"]))
    return rows


def main() -> int:
    rows = build()
    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    years = defaultdict(int)
    for r in rows:
        years[r["year"]] += 1
    print(f"wrote {len(rows)} polls in {len({r['race'] for r in rows})} races to {OUT}")
    print("  " + ", ".join(f"{y}: {n}" for y, n in sorted(years.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
