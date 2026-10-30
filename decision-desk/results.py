"""Live vote counts from official state results files (no Discord code here).

Each supported state publishes a machine-readable file on election night:
  FL  tab-delimited statewide file from the Division of Elections
  NC  zipped tab-delimited precinct file from the State Board of Elections
  VA  JSON from the Department of Elections

URLs live in results_sources.json so they can be fixed on the night without touching code.
Every parser returns {race_id: {"cands": [{"name", "party", "votes"}], "reporting": (done, total) | None}}.
Counts are unofficial and are never treated as a call.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import time
import zipfile
from pathlib import Path

import aiohttp

import races as R

HERE = Path(__file__).parent
SOURCES_FILE = HERE / "results_sources.json"
UA = "DecisionDeskDiscordBot/1.0 (friend-group election night bot)"

_cache: dict[str, tuple[float, dict]] = {}
_locks: dict[str, asyncio.Lock] = {}


# ------------------------------------------------------------------ config
def sources() -> dict:
    try:
        return json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"states": {}}


def state_cfg(state: str) -> dict | None:
    cfg = sources().get("states", {}).get(state)
    return cfg if cfg and cfg.get("format") in PARSERS and cfg.get("url") else None


def page_link(state: str) -> str | None:
    """Official results page for a person to open, live feed or not."""
    s = sources()
    return (s.get("states", {}).get(state) or {}).get("page") or s.get("pages", {}).get(state)


def supported() -> list[str]:
    return sorted(st for st in sources().get("states", {}) if state_cfg(st))


# ------------------------------------------------------------------ helpers
def party_code(text: str | None) -> str:
    t = (text or "").strip().upper()
    if t.startswith("DEM") or t == "D":
        return "D"
    if t.startswith("REP") or t == "R":
        return "R"
    return "I" if t else "?"


def _house_id(state: str, district: str | int) -> str | None:
    try:
        n = int(str(district).strip())
    except ValueError:
        return None
    if R.HOUSE_SEATS.get(state) == 1:
        return f"{state}-AL" if f"{state}-AL" in R.RACES else None
    rid = f"{state}-{n:02d}"
    return rid if rid in R.RACES else None


def _race_from_name(state: str, name: str) -> str | None:
    """Map a contest name like 'US HOUSE OF REPRESENTATIVES DISTRICT 01' or
    'Member House of Representatives (03)' to a race ID."""
    n = name.upper()
    if "PRIMARY" in n:
        return None
    if "HOUSE OF REP" in n or "REPRESENTATIVE IN CONGRESS" in n or "U.S. REPRESENTATIVE" in n \
            or "UNITED STATES REPRESENTATIVE" in n or n.startswith("US REPRESENTATIVE"):
        if "STATE HOUSE" in n or "DELEGATES" in n or "NC HOUSE" in n:
            return None
        m = re.search(r"(\d+)", n)
        return _house_id(state, m.group(1)) if m else (f"{state}-AL" if f"{state}-AL" in R.RACES else None)
    if ("UNITED STATES SENATE" in n or "US SENATE" in n or "U.S. SENATE" in n or "UNITED STATES SENATOR" in n):
        rid = f"{state}-SEN"
        return rid if rid in R.RACES else None
    if n.strip() in ("GOVERNOR", f"{R.STATE_NAMES[state].upper()} GOVERNOR", "GOVERNOR AND LIEUTENANT GOVERNOR") \
            or n.startswith("GOVERNOR") or n.endswith(" GOVERNOR") and "LIEUTENANT" not in n:
        rid = f"{state}-GOV"
        return rid if rid in R.RACES else None
    return None


def _add(out: dict, rid: str, name: str, party: str, votes: int):
    race = out.setdefault(rid, {"cands": {}, "reporting": None})
    key = (name.strip(), party)
    race["cands"][key] = race["cands"].get(key, 0) + votes


def _finish(out: dict) -> dict:
    """Turn the working dicts into sorted candidate lists, dropping write-in noise."""
    final = {}
    for rid, race in out.items():
        cands = [{"name": n, "party": p, "votes": v} for (n, p), v in race["cands"].items()
                 if n and "WRITE" not in n.upper()]
        cands.sort(key=lambda c: -c["votes"])
        if cands:
            final[rid] = {"cands": cands, "reporting": race.get("reporting")}
    return final


def _int(v) -> int:
    try:
        return int(str(v).replace(",", "").strip() or 0)
    except ValueError:
        return 0


# ------------------------------------------------------------------ Florida
FL_COLUMNS = ["ElectionDate", "PartyCode", "PartyName", "RaceCode", "RaceName", "CountyCode", "CountyName",
              "Juris1num", "Juris2num", "Precincts", "PrecinctsReporting", "CanNameLast", "CanNameFirst",
              "CanNameMiddle", "CanVotes"]
FL_RACE_CODES = {"USS": "SEN", "USR": "HOUSE", "GOV": "GOV"}


def parse_fl(raw: bytes) -> dict:
    text = raw.decode("utf-8-sig", errors="replace")
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return {}
    has_header = "RaceCode" in lines[0] or "ElectionDate" in lines[0]
    rows = csv.reader(lines[1:] if has_header else lines, delimiter="\t")
    cols = [c.strip() for c in lines[0].split("\t")] if has_header else FL_COLUMNS
    idx = {c: i for i, c in enumerate(cols)}
    out: dict = {}
    precincts: dict[str, dict[str, tuple[int, int]]] = {}
    for row in rows:
        def g(c):
            i = idx.get(c)
            return row[i].strip() if i is not None and i < len(row) else ""
        code, rname = g("RaceCode").upper(), g("RaceName")
        kind = FL_RACE_CODES.get(code)
        if kind == "HOUSE":
            rid = _house_id("FL", g("Juris1num"))
        elif kind:
            rid = f"FL-{kind}"
        else:
            rid = _race_from_name("FL", rname)
        if not rid or rid not in R.RACES:
            continue
        name = " ".join(x for x in (g("CanNameFirst"), g("CanNameLast")) if x)
        _add(out, rid, name, party_code(g("PartyCode") or g("PartyName")), _int(g("CanVotes")))
        precincts.setdefault(rid, {})[g("CountyCode") or g("CountyName")] = (
            _int(g("PrecinctsReporting")), _int(g("Precincts")))
    for rid, counties in precincts.items():
        done, total = sum(d for d, _ in counties.values()), sum(t for _, t in counties.values())
        if rid in out and total:
            out[rid]["reporting"] = (done, total)
    return _finish(out)


# ------------------------------------------------------------------ North Carolina
def parse_nc(raw: bytes) -> dict:
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            member = next(n for n in z.namelist() if n.lower().endswith((".txt", ".tsv", ".csv")))
            raw = z.read(member)
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    out: dict = {}
    seen: dict[str, dict[tuple[str, str], bool]] = {}
    for row in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        rid = _race_from_name("NC", row.get("contest name", ""))
        if not rid:
            continue
        votes = _int(row.get("total votes"))
        _add(out, rid, row.get("choice", ""), party_code(row.get("choice party")), votes)
        # The file has no "precincts reporting" column. Count real precincts that show any votes.
        if row.get("real precinct", "Y").upper().startswith("Y"):
            key = (row.get("county", ""), row.get("precinct", ""))
            s = seen.setdefault(rid, {})
            s[key] = s.get(key, False) or votes > 0
    for rid, pcts in seen.items():
        if rid in out and pcts:
            out[rid]["reporting"] = (sum(pcts.values()), len(pcts))
            out[rid]["reporting_note"] = "precincts with votes"
    final = _finish(out)
    for rid in final:
        if out[rid].get("reporting_note"):
            final[rid]["reporting_note"] = out[rid]["reporting_note"]
    return final


# ------------------------------------------------------------------ Virginia
def _ci(d: dict, *names, default=None):
    """Case-insensitive key lookup."""
    low = {k.lower(): v for k, v in d.items()}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return default


def parse_va(raw: bytes) -> dict:
    data = json.loads(raw.decode("utf-8-sig", errors="replace"))
    out: dict = {}
    for race in _ci(data, "Races", default=[]) or []:
        rid = _race_from_name("VA", _ci(race, "RaceName", default="") or "")
        if not rid:
            continue
        for c in _ci(race, "Candidates", default=[]) or []:
            _add(out, rid, _ci(c, "BallotName", "Name", default="") or "",
                 party_code(_ci(c, "PoliticalParty", "Party")), _int(_ci(c, "Votes", default=0)))
        done, total = _int(_ci(race, "PrecinctsReporting", default=0)), _int(_ci(race, "PrecinctsParticipating", default=0))
        if rid in out and total:
            out[rid]["reporting"] = (done, total)
    return _finish(out)


def parse_ev(raw: bytes) -> dict:
    """Enhanced Voting /data endpoint (Virginia since 2025, Georgia): every contest with statewide totals."""
    data = json.loads(raw.decode("utf-8-sig", errors="replace"))
    jur = ((data.get("jurisdiction") or {}).get("shortName") or "").lower()
    st = next((k for k, v in R.STATE_NAMES.items() if v.lower() == jur), "")
    out: dict = {}
    for item in data.get("ballotItems", []):
        name = next((n.get("text") for n in item.get("name") or []), "") or ""
        rid = _race_from_name(st, name) if st in R.STATE_NAMES else None
        if not rid:
            continue
        for opt in (item.get("summaryResults") or {}).get("ballotOptions") or []:
            oname = next((n.get("text") for n in opt.get("name") or []), "")
            _add(out, rid, oname, party_code((opt.get("party") or {}).get("abbreviation")), int(opt.get("voteCount") or 0))
        rs = item.get("reportingStatus") or {}
        if rid in out and rs.get("totalUnits"):
            out[rid]["reporting"] = (rs.get("reportingUnits") or 0, rs["totalUnits"])
    return _finish(out)


PARSERS = {"fl_txt": parse_fl, "nc_zip": parse_nc, "va_json": parse_va, "ev_data": parse_ev}


# ------------------------------------------------------------------ fetching
async def _download(url: str) -> bytes:
    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        async with s.get(url) as r:
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status} from {url}")
            return await r.read()


async def fetch_state(state: str, *, test: bool = False, force: bool = False) -> dict:
    """{"races": {...}, "fetched_at": ts, "url": str} or raises. Cached for cache_seconds."""
    cfg = state_cfg(state)
    if not cfg:
        raise LookupError(f"No live feed set up for {state}")
    url = cfg.get("test_url") if test else cfg["url"]
    if not url:
        raise LookupError(f"No test_url for {state} in results_sources.json")
    ttl = sources().get("cache_seconds", 60)
    key = f"{state}|{url}"
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _cache.get(key)
        if hit and not force and time.time() - hit[0] < ttl:
            return hit[1]
        raw = await _download(url)
        parsed = await asyncio.to_thread(PARSERS[cfg["format"]], raw)
        result = {"races": parsed, "fetched_at": time.time(), "url": url, "bytes": len(raw)}
        _cache[key] = (result["fetched_at"], result)
        return result


async def race_result(rid: str, *, force: bool = False) -> dict | None:
    st = R.RACES[rid]["state"]
    if not st or not state_cfg(st):
        return None
    data = await fetch_state(st, force=force)
    race = data["races"].get(rid)
    return {**race, "fetched_at": data["fetched_at"], "url": data["url"]} if race else \
        {"cands": [], "reporting": None, "fetched_at": data["fetched_at"], "url": data["url"]}


# ------------------------------------------------------------------ summarising
def summary(race: dict) -> dict:
    """Leader, margin in points and votes, share per candidate."""
    cands = race.get("cands") or []
    total = sum(c["votes"] for c in cands)
    shares = [(c, (100 * c["votes"] / total) if total else 0.0) for c in cands]
    lead = None
    if len(cands) >= 2 and total:
        a, b = cands[0], cands[1]
        lead = {"party": a["party"], "name": a["name"], "votes": a["votes"] - b["votes"],
                "points": 100 * (a["votes"] - b["votes"]) / total}
    elif len(cands) == 1 and total:
        lead = {"party": cands[0]["party"], "name": cands[0]["name"], "votes": cands[0]["votes"], "points": 100.0}
    rep = race.get("reporting")
    pct = (100 * rep[0] / rep[1]) if rep and rep[1] else None
    return {"total": total, "shares": shares, "lead": lead, "reporting_pct": pct}
