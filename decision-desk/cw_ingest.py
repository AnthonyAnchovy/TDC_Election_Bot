"""County Watch: read a results file you downloaded by hand (or uploaded in Discord) into county numbers.

Accepts:
  - any file the live feeds read: Georgia's JSON, NC's precinct zip/txt, Minnesota's media txt,
    Nebraska's service JSON, Clarity/Scytl detail XML (zipped or not)
  - a plain table (CSV, TSV, TXT, XLSX) in either shape:
      wide:  one row per county, a column per candidate or party   (County | Ossoff | Collins | ...)
      long:  one row per county per candidate                      (County | Candidate | Party | Votes)
    Optional columns: precincts reporting / total precincts, % reporting, a 'complete' or 'final' flag,
    and a contest/race column (only rows mentioning the Senate race are kept).
The state comes from the caller, the file name (e.g. TX_results.csv, "Texas results.xlsx"), a state column,
or, failing those, whichever state's county names the file matches best.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from pathlib import Path

import cw_feeds as FD
import cw_model as M

HERE = Path(__file__).parent


class IngestError(Exception):
    pass


def _cands(st: str) -> tuple[str, str]:
    try:
        c = json.loads((HERE / "candidates.json").read_text(encoding="utf-8")).get(f"{st}-SEN", {})
    except (OSError, ValueError):
        c = {}
    d = c.get("I") if st == "NE" else c.get("D")
    return d or "", c.get("R") or ""


def _state_from_name(name: str) -> str | None:
    base = Path(name).stem.upper().replace("-", " ").replace("_", " ")
    for st, full in M.STATES.items():
        if full.upper() in base:
            return st
    for tok in re.split(r"\s+", base):
        if tok in M.STATES:
            return tok
    return None


def _num(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().replace(",", "").replace("%", "")
    if t in ("", "-"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


# ------------------------------------------------------------------ tables
def _rows_from(raw: bytes, name: str) -> list[list]:
    low = name.lower()
    if low.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        best = max(wb.worksheets, key=lambda ws: ws.max_row or 0)
        return [list(r) for r in best.iter_rows(values_only=True)]
    text = raw.decode("utf-8-sig", errors="replace")
    sample = text[:5000]
    delim = "\t" if sample.count("\t") > sample.count(",") else ";" if sample.count(";") > sample.count(",") else ","
    return [r for r in csv.reader(io.StringIO(text), delimiter=delim)]


def _find_header(rows: list[list]) -> int:
    for i, r in enumerate(rows[:30]):
        cells = [str(c or "").strip().lower() for c in r]
        if any(c in ("county", "county name", "jurisdiction", "parish", "locality", "district") or
               c.startswith("county") for c in cells):
            return i
    raise IngestError("Couldn't find a header row with a 'County' column.")


def _side_of_header(h: str, st: str) -> str | None:
    d, r = _cands(st)
    hl = h.lower()
    for name, side in ((d, "D"), (r, "R")):
        if name and name.split()[-1].lower() in hl:
            return side
    if re.search(r"\b(dem|democrat|democratic|dfl)\b", hl) or hl in ("d", "(d)"):
        return "D"
    if re.search(r"\b(rep|republican|gop)\b", hl) or hl in ("r", "(r)"):
        return "R"
    return None


def parse_table(raw: bytes, name: str, st: str | None) -> tuple[str, dict]:
    rows = _rows_from(raw, name)
    hi = _find_header(rows)
    hdr = [str(c or "").strip() for c in rows[hi]]
    low = [h.lower() for h in hdr]
    body = rows[hi + 1:]

    def col(*keys, exact=False):
        for i, h in enumerate(low):
            if any((h == k) if exact else (k in h) for k in keys):
                return i
        return None
    c_cty = col("county", "jurisdiction", "parish", "locality", "district")
    c_state = col("state", exact=True)
    c_contest = col("contest", "race", "office")
    c_cand = col("candidate", "choice")
    if c_cand is None:
        c_cand = col("name", exact=True)
    if col("votes", "vote count", "total votes") is None:
        c_cand = None
    c_party = col("party")
    c_votes = col("total votes", "votes", "vote count")
    c_prep = col("precincts reporting", "reporting precincts", "precincts counted")
    c_ptot = col("total precincts", "precincts total", "precincts participating")
    c_pct = col("% reporting", "percent reporting", "pct reporting", "reporting %")
    c_done = col("complete", "final", "done")

    if not st and c_state is not None:
        vals = {str(r[c_state]).strip().upper() for r in body if len(r) > c_state and r[c_state]}
        st = next((v for v in vals if v in M.STATES), None) or \
            next((k for k, v in M.STATES.items() if v.upper() in vals), None)

    def guess_state():
        names = [str(r[c_cty]) for r in body if len(r) > c_cty and r[c_cty]]
        scores = {s: sum(1 for n in names if FD.fips_for(s, n)) for s in M.STATES if s != "AK"}
        best = max(scores, key=scores.get)
        if scores[best] < max(3, 0.5 * len(set(names))):
            raise IngestError("Couldn't tell which state this is. Name the file like 'TX results.csv' or pick the state.")
        return best
    st = st or guess_state()

    out: dict = {}
    long_form = c_votes is not None and (c_cand is not None or c_party is not None) and c_cand != c_cty
    if long_form:
        for r in body:
            if len(r) <= max(c_cty, c_votes):
                continue
            if c_contest is not None and r[c_contest] and "senat" not in str(r[c_contest]).lower():
                continue
            fips = FD.fips_for(st, str(r[c_cty] or ""))
            v = _num(r[c_votes])
            if not fips or v is None:
                continue
            side = FD.side(st, str(r[c_party]) if c_party is not None else "", str(r[c_cand]) if c_cand is not None else "",
                           {"d_name": _cands(st)[0].split()[-1]} if st == "NE" else {})
            row = out.setdefault(fips, {"D": 0, "R": 0, "O": 0, "pct_in": None})
            row[side] += int(v)
    else:
        sides = {i: _side_of_header(h, st) for i, h in enumerate(hdr)}
        skip = {c_cty, c_state, c_contest, c_prep, c_ptot, c_pct, c_done}
        dcols = [i for i, s in sides.items() if s == "D" and i not in skip]
        rcols = [i for i, s in sides.items() if s == "R" and i not in skip]
        if not dcols or not rcols:
            raise IngestError("Couldn't find the Democratic and Republican columns. Head them with the candidate "
                              "names or 'Dem' / 'Rep'.")
        ocols = [i for i in range(len(hdr)) if i not in skip and i not in dcols + rcols and
                 not any(k in low[i] for k in ("total", "turnout", "registered", "ballots", "%", "percent", "fips"))]
        for r in body:
            if len(r) <= c_cty or not r[c_cty]:
                continue
            fips = FD.fips_for(st, str(r[c_cty]))
            if not fips:
                continue
            g = lambda cols: int(sum(_num(r[i]) or 0 for i in cols if i < len(r)))
            out[fips] = {"D": g(dcols), "R": g(rcols), "O": g([i for i in ocols if _num(r[i] if i < len(r) else None) is not None]),
                         "pct_in": None}
    # reporting status
    if c_prep is not None or c_pct is not None or c_done is not None:
        for r in body:
            if len(r) <= c_cty:
                continue
            fips = FD.fips_for(st, str(r[c_cty] or ""))
            if fips not in out:
                continue
            if c_pct is not None and _num(r[c_pct]) is not None:
                p = _num(r[c_pct])
                out[fips]["pct_in"] = p / 100 if p > 1 else p
            elif c_prep is not None and c_ptot is not None and _num(r[c_ptot]):
                out[fips]["pct_in"] = (_num(r[c_prep]) or 0) / _num(r[c_ptot])
            if c_done is not None and str(r[c_done]).strip().lower() in ("y", "yes", "true", "1", "x", "final", "complete", "done"):
                out[fips]["complete"] = True
    if not out:
        raise IngestError(f"Read the file but matched no {M.STATES[st]} counties.")
    return st, out


# ------------------------------------------------------------------ entry point
def ingest(raw: bytes, name: str, state: str | None = None) -> tuple[str, dict, str]:
    """Returns (state, {fips: row}, what it recognized)."""
    st = state or _state_from_name(name)
    low = name.lower()
    head = raw[:4]
    if head[:2] == b"PK" and not low.endswith((".xlsx", ".xlsm")):
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = z.namelist()
            xmls = [n for n in names if n.lower().endswith(".xml")]
            if xmls:
                inner, kind = z.read(xmls[0]), "Clarity XML"
                if not st:
                    raise IngestError("Pick the state for a Clarity file.")
                return st, FD.parse_clarity(inner, {"state": st, "contest_re": FD.US_SENATE_RE}), kind
            txt = next((n for n in names if n.lower().endswith((".txt", ".csv", ".tsv"))), None)
            if not txt:
                raise IngestError("The zip has no XML, TXT or CSV inside.")
            raw, name = z.read(txt), txt
            low = name.lower()
    text_head = raw[:3000].decode("utf-8-sig", errors="replace")
    if text_head.lstrip().startswith("<") and "Contest" in raw[:200000].decode("utf-8", errors="replace"):
        if not st:
            raise IngestError("Pick the state for a Clarity file.")
        return st, FD.parse_clarity(raw, {"state": st, "contest_re": FD.US_SENATE_RE}), "Clarity XML"
    if text_head.lstrip().startswith("{"):
        data = json.loads(raw)
        if "localResults" in data:
            return "GA", FD.parse_ga(raw, {"contest": "US Senate"}), "Georgia results export"
        if "ballotItemWithBreakdown" in data:
            return "GA", FD.parse_ga_item(raw, {"contest": "US Senate"}), "Georgia contest file"
        if "d" in data:
            return "NE", FD.parse_ne(raw, {"d_name": "Osborn"}), "Nebraska results service"
        if data.get("countywatch") and data.get("state") in M.STATES:
            src = data.get("source") or "a results site"
            return data["state"], FD.parse_cw(raw, {"state": data["state"]}), f"County Watch grab from {src}"
        if "contestList" in data and "contestStatistics" in data:
            return "OH", FD.parse_oh(raw, {}), "Ohio results (county level)"
        if any(isinstance(v, dict) and "Races" in v for v in list(data.values())[:3]):
            return "TX", FD.parse_tx(raw, {}), "Texas County.json"
        raise IngestError("Unrecognized JSON file.")
    if "RaceCode" in text_head and "CanVotes" in text_head:
        return "FL", FD.parse_fl(raw, {"contest": "United States Senator"}), "Florida results file"
    if "Contest Name" in text_head and "\t" in text_head:
        return "NC", FD.parse_nc(raw, {"contest": "US SENATE"}), "NC precinct file"
    if text_head.startswith("Precinct_name,Reporting_flag"):
        return "AK", FD.parse_ak(raw, {"contest": "U.S. Senator"}), "Alaska precinct results"
    first = text_head.splitlines()[0] if text_head else ""
    if first.startswith(("MN;", "﻿MN;")):
        return "MN", FD.parse_mn(raw, {"contest": "U.S. Senator"}), "Minnesota media file"
    st, out = parse_table(raw, name, st)
    return st, out, "table"
