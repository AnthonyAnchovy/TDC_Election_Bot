"""County Watch: early vote (no Discord, no Excel code here).

Two sources:
  UF Election Lab (Michael McDonald) state-level tracker, data-downloads/earlyvote/2026/US.csv.
    Covers every state with a 2026 Senate race except NH. Licensed CC BY-NC-ND: credit "UF Election Lab",
    personal non-commercial use only.
  NC State Board of Elections absentee file (voter-level; we keep only county x party x race x age counts).
    Lets us compare 2026 with 2022 at the same number of days before the election, by county, and
    estimate the early vote's partisan lean from who has voted.
"""
from __future__ import annotations

import collections
import csv
import io
import json
import zipfile
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).parent
CW_DIR = HERE / "countywatch"
UF_URL = "https://election.lab.ufl.edu/data-downloads/earlyvote/2026/US.csv"
NC_ABS_URL = "https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/2026_11_03/absentee_20261103.zip"
ELECTION_2026 = date(2026, 11, 3)
ELECTION_2022 = date(2022, 11, 8)
UF_AGE = ["18-25", "26-40", "41-65", "65+"]

# Share of each group's early votes assumed to go to the Democrat. DEM/REP loyalty is fixed at survey-typical
# values; the two unaffiliated shares are fitted to 2022 county results (see fit_nc_model). Groups follow
# the voter file's party registration and self-reported race.
NC_GROUPS = ["DEM_BLACK", "DEM_NONBLACK", "REP", "UNA_BLACK", "UNA_NONBLACK"]
NC_BOUNDS = {"DEM_BLACK": (0.95, 0.98), "DEM_NONBLACK": (0.88, 0.95), "REP": (0.03, 0.07),
             "UNA_BLACK": (0.75, 0.95), "UNA_NONBLACK": (0.30, 0.70)}


# ------------------------------------------------------------------ NC absentee file
def _group(party: str, race: str) -> str:
    p = party.upper().strip()
    if p == "REP":
        return "REP"
    p = "DEM" if p == "DEM" else "UNA"          # Libertarian, Green, etc. are counted with unaffiliated
    return f"{p}_{'BLACK' if race.upper().startswith('BLACK') else 'NONBLACK'}"


def _age(a: str) -> str:
    try:
        n = int(a)
    except ValueError:
        return "?"
    return "18-29" if n < 30 else "30-44" if n < 45 else "45-64" if n < 65 else "65+"


def aggregate_nc(zip_source, election: date) -> dict:
    """Stream an NCSBE absentee zip (path or bytes). Returns counts of ACCEPTED ballots:
    {"by_county": {county: Counter}, "by_offset": {county: {days_before: Counter(group)}}, "through": date}
    Counter keys: group names, 'age:18-29', 'method:EARLY'/'method:MAIL', 'race:BLACK' etc., and 'all'."""
    zf = zipfile.ZipFile(io.BytesIO(zip_source) if isinstance(zip_source, bytes) else zip_source)
    rows = csv.DictReader(io.TextIOWrapper(zf.open(zf.namelist()[0]), encoding="utf-8", errors="replace"))
    by_county: dict = collections.defaultdict(collections.Counter)
    by_offset: dict = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    latest = None
    for r in rows:
        if not r.get("ballot_rtn_status", "").strip().upper().startswith("ACCEPTED"):
            continue
        try:
            d = datetime.strptime(r["ballot_rtn_dt"].strip(), "%m/%d/%Y").date()
        except ValueError:
            continue
        latest = d if latest is None or d > latest else latest
        cty = r["county_desc"].strip().upper()
        g = _group(r["voter_party_code"], r["race"])
        race = "BLACK" if r["race"].upper().startswith("BLACK") else \
            "HISPANIC" if r["ethnicity"].upper().startswith("HISPANIC") else \
            "WHITE" if r["race"].upper() == "WHITE" else "OTHER"
        method = "EARLY" if any(k in r["ballot_req_type"].upper() for k in ("EARLY", "ONE")) else "MAIL"
        c = by_county[cty]
        c["all"] += 1
        c[g] += 1
        c[f"party:{r['voter_party_code'].strip().upper()}"] += 1
        c[f"race:{race}"] += 1
        c[f"age:{_age(r['age'])}"] += 1
        c[f"method:{method}"] += 1
        by_offset[cty][(election - d).days][g] += 1
    return {"by_county": by_county, "by_offset": by_offset, "through": latest}


def save_offsets(agg: dict, path: Path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["county", "days_before", "group", "n"])
        for cty, days in sorted(agg["by_offset"].items()):
            for off, cnt in sorted(days.items()):
                for g, n in sorted(cnt.items()):
                    w.writerow([cty, off, g, n])


def load_offsets(path: Path) -> dict:
    out: dict = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    with open(path) as f:
        for r in csv.DictReader(f):
            out[r["county"]][int(r["days_before"])][r["group"]] += int(r["n"])
    return out


def as_of(offsets: dict, days_before: int) -> dict:
    """County group counts for ballots returned at least `days_before` days before the election."""
    out = {}
    for cty, days in offsets.items():
        c = collections.Counter()
        for off, cnt in days.items():
            if off >= days_before:
                c.update(cnt)
        out[cty] = c
    return out


# ------------------------------------------------------------------ NC lean model
def fit_nc_model(comp22: dict, results22: dict) -> dict:
    """comp22: county -> Counter(group) for all 2022 accepted early/mail ballots.
    results22: county -> (D votes, R votes) in 2022's early + mail vote (Senate race).
    Fits each group's D share within NC_BOUNDS by weighted least squares."""
    import numpy as np
    from scipy.optimize import lsq_linear
    X, y, w = [], [], []
    for cty, c in comp22.items():
        if cty not in results22:
            continue
        d, r = results22[cty]
        tot = sum(c[g] for g in NC_GROUPS)
        if not tot or not d + r:
            continue
        X.append([c[g] / tot for g in NC_GROUPS])
        y.append(d / (d + r))
        w.append((d + r) ** 0.5)
    X, y, w = np.array(X), np.array(y), np.array(w)
    lo = [NC_BOUNDS[g][0] for g in NC_GROUPS]
    hi = [NC_BOUNDS[g][1] for g in NC_GROUPS]
    fit = lsq_linear(X * w[:, None], y * w, bounds=(lo, hi))
    pred = X @ fit.x
    W = w ** 2
    r2 = 1 - np.sum(W * (y - pred) ** 2) / np.sum(W * (y - np.average(y, weights=W)) ** 2)
    return {"shares": dict(zip(NC_GROUPS, [round(float(v), 4) for v in fit.x])), "r2": round(float(r2), 3),
            "mae_pts": round(float(np.average(np.abs(y - pred), weights=W) * 100), 2), "counties": len(y)}


def lean(counter: collections.Counter, shares: dict) -> float | None:
    """Modeled two-party D margin (D share minus R share) of a group of early voters."""
    tot = sum(counter[g] for g in NC_GROUPS)
    if not tot:
        return None
    d = sum(counter[g] * shares[g] for g in NC_GROUPS) / tot
    return 2 * d - 1


# ------------------------------------------------------------------ UF Election Lab
def parse_uf(raw: bytes) -> dict:
    out = {}
    for r in csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace"))):
        def n(k):
            try:
                return int(float(r.get(k) or 0))
            except ValueError:
                return 0
        out[r["state_abbv"]] = {
            "updated": r.get("last_update"), "source": r.get("data_source"), "notes": r.get("notes"),
            "turn22": n("turn_2022"), "voted": n("voted_all"), "inperson": n("inperson_all"), "mail": n("accept_all"),
            "requested": n("request_all"),
            "dem": n("voted_dem"), "rep": n("voted_rep"), "none": n("voted_none"),
            "white": n("voted_nh_white"), "black": n("voted_nh_black"), "hisp": n("voted_hispanic"),
            "asian": n("voted_nh_asian"),
            "age": [n(f"voted_age_{i}") for i in range(1, 5)],
            "male": n("voted_male"), "female": n("voted_female"),
        }
    return out


def uf_summary(row: dict) -> list[str]:
    """Plain-language lines for one state's UF row."""
    lines = []
    if not row or not row["voted"]:
        return ["No early vote reported yet."]
    pct22 = row["voted"] / row["turn22"] if row["turn22"] else None
    lines.append(f"{row['voted']:,} early votes so far" +
                 (f", {pct22:.0%} of the state's total 2022 turnout" if pct22 else "") +
                 (f" (in person {row['inperson']:,} · mail {row['mail']:,})" if row["inperson"] and row["mail"] else ""))
    p = row["dem"] + row["rep"] + row["none"]
    if p:
        lines.append(f"By party registration: D {row['dem'] / p:.0%} · R {row['rep'] / p:.0%} · other {row['none'] / p:.0%}"
                     " (registration, not votes)")
    r = row["white"] + row["black"] + row["hisp"] + row["asian"]
    if r and row["black"]:
        lines.append(f"Black voters are {row['black'] / row['voted']:.0%} of early voters")
    a = sum(row["age"])
    if a:
        lines.append(f"Ages: 18-25 {row['age'][0] / a:.0%} · over 65 {row['age'][3] / a:.0%}")
    return lines


def load_json(name: str):
    p = CW_DIR / name
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_json(name: str, data):
    (CW_DIR / name).write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
