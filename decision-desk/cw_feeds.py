"""County Watch: live county-level results feeds (no Discord, no Excel code here).

Every parser returns {fips: {"D": int, "R": int, "O": int, "pct_in": float | None}} for one contest,
where D is the Democratic side (Osborn in Nebraska), R the Republican, O everyone else, and pct_in the
share of precincts reporting (None if the feed doesn't say).

Verified against real files (Nov 2024 or the live 2026 setup) on Oct 29, 2026:
  GA  Enhanced Voting media export (results.sos.ga.gov)   county totals + vote-method breakdown
  NC  NCSBE precinct results zip                            precinct -> county
  MN  SOS media file allracesbycounty.txt                   county
  NE  NE SOS results web service (already set up for 2026)  county
  IA  Clarity detailxml.zip (electionresults.iowa.gov)      county; Nov 2024 file matched exactly
  OH  liveresults.ohiosos.gov API (2026 contest is set up)  county, one request
  TX  results.texas-election.com County.json                county by FIPS; Nov 2024 file matched exactly
  AK  ENRbyPrecinct.csv (elections.alaska.gov)              precinct -> state House district
  KS  ent.sos.ks.gov county pages                           only through the County Watch grab button
OH, TX, KS (and Florida's early-vote files) sit behind Cloudflare's bot check. A normal request from the bot may
get a challenge page instead of data; the bot then stops trying that state and says to use the grab button
(countywatch/grab.js), which saves the same data from your own browser with one click.
States without a feed are entered by hand in the workbook.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import aiohttp

HERE = Path(__file__).parent
CW_DIR = HERE / "countywatch"
UA = "DecisionDeskDiscordBot/1.0 (friend-group election night bot)"


# ------------------------------------------------------------------ county name -> FIPS
def _norm(name: str) -> str:
    n = name.upper().replace("&", "AND").replace(" COUNTY", "").replace(" PARISH", "").replace(".", "").replace("'", "")
    n = n.replace("SAINT ", "ST ").replace("-", " ")
    return re.sub(r"\s+", " ", n).strip()


_NAME_INDEX: dict[str, dict[str, str]] | None = None


def fips_for(state: str, county_name: str) -> str | None:
    global _NAME_INDEX
    if _NAME_INDEX is None:
        _NAME_INDEX = {}
        with open(CW_DIR / "baseline.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                _NAME_INDEX.setdefault(r["state"], {})[_norm(r["county"])] = r["fips"]
    return _NAME_INDEX.get(state, {}).get(_norm(county_name))


def side(state: str, party: str, name: str, cfg: dict) -> str:
    """Which side a candidate is on: 'D', 'R' or 'O'. cfg can name candidates explicitly."""
    up = (name or "").upper()
    for key, s in (("d_name", "D"), ("r_name", "R")):
        if cfg.get(key) and cfg[key].upper() in up:
            return s
    p = (party or "").upper().strip()
    if cfg.get("d_name"):           # a named D-side candidate (e.g. Osborn) means the DEM line isn't the D side
        return "R" if p.startswith("REP") or p == "R" else "O"
    if p.startswith("DEM") or p in ("D", "DFL"):
        return "D"
    if p.startswith("REP") or p == "R":
        return "R"
    m = re.search(r"\((DEM|REP|D|R)\)", up)
    if m:
        return "D" if m.group(1).startswith("D") else "R"
    return "O"


def _blank():
    return {"D": 0, "R": 0, "O": 0, "pct_in": None}


# ------------------------------------------------------------------ Georgia (Enhanced Voting)
def parse_ga(raw: bytes, cfg: dict) -> dict:
    data = json.loads(raw)
    match = cfg.get("contest", "US Senate").lower()
    out: dict = {}
    for loc in data.get("localResults", []):
        fips = fips_for("GA", loc.get("name", ""))
        if not fips:
            continue
        for item in loc.get("ballotItems", []):
            if match not in (item.get("name") or "").lower():
                continue
            row = out.setdefault(fips, _blank())
            for opt in item.get("ballotOptions", []):
                row[side("GA", opt.get("politicalParty"), opt.get("name"), cfg)] += int(opt.get("voteCount") or 0)
            pp, pr = item.get("precinctsParticipating"), item.get("precinctsReporting")
            if pp:
                row["pct_in"] = (pr or 0) / pp
            statuses = {s.get("groupName"): s.get("status") for s in loc.get("reportingStatuses", [])}
            if statuses:
                row["groups"] = statuses
            break
    return out


def parse_ga_item(raw: bytes, cfg: dict) -> dict:
    """Enhanced Voting single-contest endpoint (Georgia, Virginia): about 1.6 MB instead of the 33 MB full
    export, so it can be polled every 20-30 seconds. Same county totals (checked against Georgia, Nov 2024)."""
    st = cfg.get("state", "GA")
    data = json.loads(raw)
    item = data.get("ballotItemWithBreakdown") or {}
    out: dict = {}
    for br in item.get("breakdownResults") or []:
        loc = br.get("locality") or {}
        name = next((n.get("text") for n in loc.get("name") or [] if n.get("languageId") == "en"), "")
        fips = fips_for(st, name)
        if not fips:
            continue
        row = out.setdefault(fips, _blank())
        for opt in br.get("ballotOptions") or []:
            oname = next((n.get("text") for n in opt.get("name") or []), "")
            party = (opt.get("party") or {}).get("abbreviation")
            row[side(st, party, oname, cfg)] += int(opt.get("voteCount") or 0)
        rs = br.get("precinctReportingStatus") or {}
        if rs.get("totalUnits"):
            row["pct_in"] = (rs.get("reportingUnits") or 0) / rs["totalUnits"]
    return out


# ------------------------------------------------------------------ North Carolina (precinct file)
def parse_nc(raw: bytes, cfg: dict) -> dict:
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            raw = z.read(next(n for n in z.namelist() if n.lower().endswith(".txt")))
    match = cfg.get("contest", "US SENATE").upper()
    out: dict = {}
    pcts: dict = {}
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace")), delimiter="\t")
    for r in reader:
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        if r.get("contest name", "").upper() != match:
            continue
        fips = fips_for("NC", r.get("county", ""))
        if not fips:
            continue
        row = out.setdefault(fips, _blank())
        votes = int(r.get("total votes") or 0)
        row[side("NC", r.get("choice party"), r.get("choice"), cfg)] += votes
        for mode in ("early voting", "absentee by mail", "election day"):
            row.setdefault("modes", {}).setdefault(mode, {"D": 0, "R": 0, "O": 0})[
                side("NC", r.get("choice party"), r.get("choice"), cfg)] += int(r.get(mode) or 0)
        if r.get("real precinct", "Y").upper().startswith("Y"):
            key = r.get("precinct", "")
            p = pcts.setdefault(fips, {})
            p[key] = p.get(key, False) or votes > 0
    for fips, p in pcts.items():
        if p:
            out[fips]["pct_in"] = sum(p.values()) / len(p)   # precincts with any votes (NC has no status column)
    return out


# ------------------------------------------------------------------ Florida (statewide county file)
def parse_fl(raw: bytes, cfg: dict) -> dict:
    """Division of Elections tab-delimited file: one row per county per candidate per race."""
    match = cfg.get("contest", "United States Senator").lower()
    out: dict = {}
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace")), delimiter="\t")
    for r in reader:
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        name = r.get("RaceName", "").lower()
        if match not in name or (match == "united states senator" and "state senator" in name and "united" not in name):
            continue
        fips = fips_for("FL", r.get("CountyName", ""))
        if not fips:
            continue
        row = out.setdefault(fips, _blank())
        cand = f"{r.get('CanNameFirst', '')} {r.get('CanNameLast', '')}"
        row[side("FL", r.get("PartyCode"), cand, cfg)] += int(float(r.get("CanVotes") or 0))
        try:
            tot = int(r.get("Precincts") or 0)
            row["pct_in"] = int(r.get("PrecinctsReporting") or 0) / tot if tot else None
        except ValueError:
            pass
    return out


# ------------------------------------------------------------------ Minnesota (media file)
def parse_mn(raw: bytes, cfg: dict) -> dict:
    match = cfg.get("contest", "U.S. Senator").lower()
    out: dict = {}
    for line in raw.decode("utf-8-sig", errors="replace").splitlines():
        p = line.split(";")
        if len(p) < 16 or match not in p[4].lower():
            continue
        try:
            cid = int(p[1])
        except ValueError:
            continue
        fips = f"27{2 * cid - 1:03d}"      # MN's county IDs are alphabetical 1-87; FIPS are the odd numbers
        row = out.setdefault(fips, _blank())
        row[side("MN", p[10], p[7], cfg)] += int(p[13] or 0)
        try:
            row["pct_in"] = int(p[11]) / int(p[12]) if int(p[12]) else None
        except ValueError:
            pass
    return out


# ------------------------------------------------------------------ Nebraska (results web service)
def parse_ne(raw: bytes, cfg: dict) -> dict:
    data = json.loads(raw).get("d", [])
    out: dict = {}
    for r in data:
        fips = fips_for("NE", r.get("CountyName", ""))
        if not fips:
            continue
        row = out.setdefault(fips, _blank())
        row[side("NE", r.get("PartyCode"), r.get("calcCandidate"), cfg)] += int(r.get("calcCandidateVotes") or 0)
        tp = r.get("TotalPrecincts") or 0
        row["pct_in"] = (r.get("PrecinctsReporting") or 0) / tp if tp else None
    return out


# ------------------------------------------------------------------ Clarity detail XML (untested on 2026)
def parse_clarity(raw: bytes, cfg: dict) -> dict:
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            raw = z.read(next(n for n in z.namelist() if n.lower().endswith(".xml")))
    root = ET.fromstring(raw)
    st = cfg["state"]
    if cfg.get("contest_re"):
        pat = re.compile(cfg["contest_re"], re.I)
    else:
        pat = re.compile(re.escape(cfg.get("contest", "United States Senat")), re.I)
    pct = {}
    turnout = root.find("ElectionVoterTurnout")
    for c in (turnout.iter("County") if turnout is not None else []):
        try:
            pct[c.get("name")] = int(c.get("precinctsReported") or 0) / int(c.get("precinctsParticipating"))
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    out: dict = {}
    for contest in root.iter("Contest"):
        if not pat.search(contest.get("text") or ""):
            continue
        part = contest.find("ParticipatingCounties")
        for c in (part.iter("County") if part is not None else []):
            try:
                pct[c.get("name")] = int(c.get("precinctsReported") or 0) / int(c.get("precinctsParticipating"))
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        for choice in contest.iter("Choice"):
            s = side(st, choice.get("party"), choice.get("text"), cfg)
            for vt in choice.iter("VoteType"):
                for c in vt.iter("County"):
                    fips = fips_for(st, c.get("name", ""))
                    if not fips:
                        continue
                    row = out.setdefault(fips, _blank())
                    row[s] += int(float(c.get("votes") or 0))
                    row["pct_in"] = pct.get(c.get("name"))
        break
    return out


US_SENATE_RE = r"(united states|u\.\s?s\.)\s+senat"


def _key_to_fips(st: str, key: str) -> str | None:
    key = str(key).strip()
    return key if re.fullmatch(r"\d{5}", key) else fips_for(st, key)


def parse_cw(raw: bytes, cfg: dict) -> dict:
    """A file saved by the County Watch grab button: {"countywatch":1,"state":..,"counties":{name or FIPS: row}}."""
    data = json.loads(raw)
    st = cfg.get("state") or data.get("state")
    out = {}
    for k, r in (data.get("counties") or {}).items():
        f = _key_to_fips(st, k)
        if f:
            out[f] = {"D": int(r.get("D") or 0), "R": int(r.get("R") or 0), "O": int(r.get("O") or 0),
                      "pct_in": r.get("pct_in")}
    return out


def parse_oh(raw: bytes, cfg: dict) -> dict:
    """Ohio SOS API, GetContestDetailsObject with resultsLevel=County: one entry per county."""
    data = json.loads(raw)
    out = {}
    for c in data.get("contestList") or []:
        f = fips_for("OH", c.get("name", ""))
        if not f:
            continue
        row = out.setdefault(f, _blank())
        for x in c.get("candidatesRunning") or []:
            row[side("OH", x.get("candidateParty"), x.get("primaryCandidate"), cfg)] += int(x.get("vCount") or 0)
        tp = c.get("totalPrecincts") or 0
        row["pct_in"] = (c.get("reportedPrecincts") or 0) / tp if tp else None
    return out


def parse_tx(raw: bytes, cfg: dict) -> dict:
    """Texas SOS County.json: {fips: {N, Races: {id: {ON, C: {id: {P, V}}, PR, TPR}}}}."""
    data = json.loads(raw)
    pat = re.compile(cfg.get("contest_re", r"^U\.\s?S\.\s?SENATOR"), re.I)
    out = {}
    for f, c in data.items():
        race = next((r for r in (c.get("Races") or {}).values() if pat.search(r.get("ON") or "")), None)
        if not race or not re.fullmatch(r"\d{5}", f):
            continue
        row = out.setdefault(f, _blank())
        for x in (race.get("C") or {}).values():
            row[side("TX", x.get("P"), x.get("N"), cfg)] += int(x.get("V") or 0)
        row["pct_in"] = race["PR"] / race["TPR"] if race.get("TPR") and race.get("PR") else None
    return out


def parse_ak(raw: bytes, cfg: dict) -> dict:
    """Alaska ENRbyPrecinct.csv: one row per precinct x candidate. Precincts are named '01-600 Ketchikan No. 1' or
    'District 1 - Absentee'; the number is the state House district, which is what the baseline uses."""
    text = raw.decode("utf-8-sig", errors="replace")
    rows = csv.DictReader(io.StringIO(text))
    match = cfg.get("contest", "U.S. Senator").lower()
    out: dict = {}
    for r in rows:
        if match not in (r.get("Contest_title") or "").lower():
            continue
        name = r.get("Precinct_name") or ""
        m = re.match(r"^(\d\d)-", name) or re.match(r"^District (\d+)\b", name)
        if not m:
            continue
        f = f"02{int(m.group(1)):03d}"
        row = out.setdefault(f, _blank())
        row[side("AK", r.get("Party_Code"), r.get("candidate_name"), cfg)] += int(float(r.get("total_votes") or 0))
    return out


PARSERS = {"ga_ev": parse_ga, "ga_item": parse_ga_item, "nc_pct": parse_nc, "fl_txt": parse_fl, "mn_media": parse_mn,
           "ne_ws": parse_ne, "clarity": parse_clarity, "clarity_enr": parse_clarity, "oh_api": parse_oh,
           "tx_static": parse_tx, "ak_csv": parse_ak, "cw_json": parse_cw}


# ------------------------------------------------------------------ config + fetching
OVERRIDES = CW_DIR / "feed_overrides.json"


def load_sources() -> dict:
    """feeds.json, plus anything set on the night with /countyfeed (kept in feed_overrides.json so an update
    to feeds.json never wipes it)."""
    p = CW_DIR / "feeds.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {"states": {}}
    try:
        ov = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ov = {}
    for st, extra in ov.items():
        d.setdefault("states", {}).setdefault(st, {}).update(extra)
    return d


def set_override(state: str, values: dict | None):
    """values=None clears the state's override."""
    try:
        ov = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ov = {}
    if values is None:
        ov.pop(state, None)
    else:
        ov[state] = {**ov.get(state, {}), **values}
    OVERRIDES.write_text(json.dumps(ov, indent=2), encoding="utf-8")


def clarity_from_link(link: str) -> dict | None:
    """'https://www.enr-scvotes.org/SC/127500/web.345435/#/summary' -> {'base': '.../SC', 'eid': '127500'}."""
    m = re.match(r"(https?://[^/]+/[A-Z]{2}(?:/[A-Za-z_-]+)?)/(\d{5,7})(?:/|$)", link.strip())
    return {"format": "clarity_enr", "base": m.group(1), "eid": m.group(2)} if m else None


_COND: dict = {}        # url -> (etag, last_modified, parsed result)
_GA_IDS: dict = {}      # (election_date or id, contest) -> (election_id, ballot_item_id)
GA_API = "https://results.sos.ga.gov/results/public/api"


class NotModified(Exception):
    pass


class BotCheck(RuntimeError):
    """The site answered with a bot-check page (Cloudflare and the like) instead of data."""


def _is_challenge(status: int, headers, body: bytes) -> bool:
    ctype = (headers.get("Content-Type") or "").lower()
    head = body[:4000].lower()
    return (status in (403, 503, 429) and (b"just a moment" in head or b"challenge-platform" in head
                                            or b"cf-chl" in head or headers.get("cf-mitigated")
                                            or b"access denied" in head)) or \
        (status == 200 and "text/html" in ctype and b"challenge-platform" in head and b"just a moment" in head)


async def _get(session, url: str, conditional: bool = False) -> bytes:
    """GET with If-None-Match / If-Modified-Since, so unchanged files cost almost nothing to re-check."""
    headers = {}
    if conditional and url in _COND:
        etag, lm, _ = _COND[url]
        if etag:
            headers["If-None-Match"] = etag
        if lm:
            headers["If-Modified-Since"] = lm
    async with session.get(url, headers=headers) as r:
        if r.status == 304:
            raise NotModified()
        body = await r.read()
        if _is_challenge(r.status, r.headers, body):
            raise BotCheck("the site answered with a bot check instead of data, so the bot can't pull it; "
                           "use the County Watch grab button in your browser")
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        if conditional:
            _COND[url] = (r.headers.get("ETag"), r.headers.get("Last-Modified"), None)
        return body


async def _ga_ids(session, cfg: dict) -> tuple[str, str]:
    """An Enhanced Voting site's election ID and the Senate contest's ID. Looked up once, then cached."""
    api, jur = cfg.get("api", GA_API), cfg.get("jurisdiction", "Georgia")
    key = (api, cfg.get("election_id") or cfg.get("election_date", "2026-11-03"), cfg.get("contest", "US Senate"))
    if key in _GA_IDS:
        return _GA_IDS[key]
    eid = cfg.get("election_id")
    if not eid:
        j = json.loads(await _get(session, f"{api}/jurisdictions/{jur}"))
        el = next((e for e in j.get("elections", []) if e.get("electionDate") == cfg.get("election_date", "2026-11-03")
                   and "primary" not in e.get("publicElectionId", "").lower()), None)
        if not el:
            raise RuntimeError(f"{jur} hasn't posted the Nov 3 election on its results site yet")
        eid = el["publicElectionId"]
    d = json.loads(await _get(session, f"{api}/elections/{jur}/{eid}/data"))
    match = cfg.get("contest", "US Senate").lower()
    item = next((b for b in d.get("ballotItems", [])
                 if any(match in (n.get("text") or "").lower() for n in b.get("name") or [])), None)
    if not item:
        names = sorted({n.get("text") for b in d.get("ballotItems", [])[:40] for n in b.get("name") or []})
        raise RuntimeError(f"{jur}'s results site has no contest matching '{cfg.get('contest')}' yet "
                           f"(first contests listed: {', '.join(names[:8])})")
    _GA_IDS[key] = (eid, item["id"], api, jur)
    return _GA_IDS[key]


async def _clarity_url(session, cfg: dict) -> str:
    """Clarity/SOE results site: find the election (by date, unless 'eid' is set), then its current
    version's detailxml.zip."""
    base = cfg["base"].rstrip("/")
    eid = cfg.get("eid")
    if not eid:
        lst = json.loads(await _get(session, f"{base}/elections.json"))
        want = cfg.get("election_date", "11/3/2026")
        el = next((e for e in lst if str(e.get("Date", "")).startswith(want)
                   and "primary" not in str(e.get("ElectionName", "")).lower()), None)
        if not el:
            raise RuntimeError(f"the results site hasn't listed the {want} election yet "
                               f"(an admin can paste its link with /countyfeed)")
        eid = el["EID"]
    ver = (await _get(session, f"{base}/{eid}/current_ver.txt")).decode().strip()
    return f"{base}/{eid}/{ver}/reports/detailxml.zip"


async def _oh_url(session, cfg: dict) -> str:
    api = cfg.get("api", "https://liveresults.ohiosos.gov/Api/v1/Contest/GetContestDetailsObject")
    q = f"{api}?contestName={cfg.get('contest', 'U.S. Senator').replace(' ', '%20')}"
    first = json.loads(await _get(session, q))
    lst = first.get("contestList") or []
    if not lst:
        raise RuntimeError("Ohio's site has no contest called " + cfg.get("contest", "U.S. Senator"))
    return f"{q}&contestObjectId={lst[0]['objectId']}&resultsLevel=County"


async def _tx_url(session, cfg: dict) -> str:
    base = cfg.get("base", "https://results.texas-election.com/static/data")
    v = json.loads(await _get(session, f"{base}/Version.json"))["___versionNo"]
    info = json.loads(await _get(session, f"{base}/ElectionConstants_{v}.json"))["currentelectionInfo"]
    if cfg.get("election_year", "2026") not in str(info.get("N")) or info.get("ET") != "G":
        raise RuntimeError(f"Texas's site is still on \"{info.get('N')}\", not the 2026 general")
    ev = json.loads(await _get(session, f"{base}/election/{info['ID']}/Version.json"))["___versionNo"]
    return f"{base}/election/{info['ID']}/{ev}/County.json"


async def fetch_state(state: str, *, test: bool = False, session=None) -> dict:
    """{'counties': {fips: row}, 'url': str, 'changed': bool} for one state, or raises."""
    import asyncio
    cfg = dict(load_sources().get("states", {}).get(state) or {})
    if not cfg or cfg.get("format") not in PARSERS:
        raise LookupError(f"No live county feed for {state}")
    cfg["state"] = state
    if test:
        cfg = {**cfg, **cfg.get("test", {})}
    own = session is None
    if own:
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60), headers={"User-Agent": UA})
    try:
        fmt = cfg["format"]
        if fmt == "ga_item":
            eid, bid, api, jur = await _ga_ids(session, cfg)
            url = f"{api}/elections/{jur}/{eid}/data/ballot-item/{bid}"
        elif fmt == "clarity_enr":
            url = await _clarity_url(session, cfg)
        elif fmt == "oh_api":
            url = await _oh_url(session, cfg)
        elif fmt == "tx_static":
            url = cfg.get("url") or await _tx_url(session, cfg)
        else:
            url = cfg["url"]
        try:
            raw = await _get(session, url, conditional=not test)
        except NotModified:
            return {"counties": _COND[url][2], "url": url, "changed": False}
    finally:
        if own:
            await session.close()
    counties = await asyncio.to_thread(PARSERS[fmt], raw, cfg)
    if not test and url in _COND:
        _COND[url] = (_COND[url][0], _COND[url][1], counties)
    return {"counties": counties, "url": url, "changed": True}


def feed_states() -> list[str]:
    return sorted(s for s, c in load_sources().get("states", {}).items() if c.get("format") in PARSERS)
