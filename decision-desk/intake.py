"""County Watch's front door: every county pull is checked here before the bot believes it, and every change it
accepts is written down so the night can be played back later (no Discord and no network here).

Two jobs.

1. Checking. A results site can hand back an error page, half a file, last week's test numbers or a typo, and the
   feed readers will parse all of them without complaint. check() compares a fresh pull with the last good one
   and with what the state normally casts, and gives back what to believe now:

     the whole pull is refused, and the last good numbers kept, when
       - it came back empty
       - it lists far fewer counties than the state has, from a feed that has listed them all before. (A feed
         that has never listed them all may simply add counties as they report, so its short first pulls are
         let through with a note. Refusing those would cost the first counties of the night.)
       - most counties already show every precinct in minutes after polls close (leftover test data)
       - votes are in far fewer counties than before (a cut-off download)
       - the state's total fell a long way. This one is believed after CONFIRM_TOTAL pulls in a row, because
         otherwise one bad high number, once accepted, would lock the state out of its own correction for good.

     one county is held at its last good numbers, and the rest of the pull used, when
       - the county is missing from the pull
       - its count fell by more than a correction would explain
       - its count is far past anything the county has cast before (a typo, or another county's row)

   A held county isn't held for ever. If the new number is still there on CONFIRM pulls in a row it was a real
   correction, and it goes through.

2. Remembering. record() appends each accepted change to snapshots/<state>.jsonl beside the night's other files:
   the time, the source, and only the counties whose numbers moved. Nobody kept this on election night 2026, which
   is why backtest.py has to guess which county reported when. With a log, backtest.py replays the real order.
   replay() reads a log back as the state stood at each moment.

A log line:  {"t": 1794000000.0, "src": "Ohio Secretary of State", "c": {"39035": [D, R, other, share in, complete]}}
A county that left the feed is written as null. Alaska's rows carry a sixth item, the votes still expected.
A race with more than two candidates writes [{candidate: votes}, share in, complete], and every check here counts
each candidate's votes the way it counts the two sides'.
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import cw_model as M
import feeds as FEEDS

log = logging.getLogger("countywatch.intake")

TEST_DATA_MINUTES = 10   # this soon after polls close, a file that is mostly finished counties is test data
CUT_OFF_SHARE = 0.5      # votes in under this share of the counties that had them before: a cut-off file
MIN_LISTED = 0.5         # a pull has to list at least this share of the state's counties
MIN_COUNTIES = 4         # states (or packs) with fewer places than this are too small for the share checks
TOTAL_FALL = 0.10        # the state's two-party total falling by more than this: refuse the pull
SMALL_FALL = (25, 0.02)  # a county's count may fall by this many votes, or this share, as an ordinary correction
JUMP = 2.5               # a county this many times past its expected vote...
JUMP_VOTES = 2000        # ...and this many votes past it, is doubted
CONFIRM = 3              # pulls in a row a doubted number has to survive before it's believed
CONFIRM_TOTAL = 6        # the same for a state whose whole total fell
STATE = "_state"         # where check() keeps its count of those pulls, beside the counties it is holding

_lock = threading.Lock()
_last: dict[Path, dict] = {}        # log file -> {fips: packed row} as last written
_warned = False


def _sides(row: dict | None) -> dict:
    """A row's counts by who they're for: the two sides of a two-sided race, or every candidate of a race with
    more ({"v": {"A": 1200, "B": 900, ...}}, see cw_multi)."""
    if not row:
        return {}
    if "v" in row:
        return {k: n or 0 for k, n in (row["v"] or {}).items()}
    return {"D": row.get("D") or 0, "R": row.get("R") or 0}


def _votes(row: dict | None) -> int:
    return sum(_sides(row).values())


# ------------------------------------------------------------------ checking a pull
@dataclass
class Verdict:
    """What check() made of a pull. rows is what to believe now. rejected says why the whole pull was refused
    (rows is then the last good pull, untouched). notes says, in words, which counties were held back. held is
    what to hand back to check() next time, so a doubted number can be counted across pulls."""
    rows: dict
    rejected: str | None = None
    notes: list[str] = field(default_factory=list)
    held: dict = field(default_factory=dict)


def suspect(st: str, new: dict, old: dict | None, minutes_since_close: float = 999,
            lists_every_county: bool = True) -> str | None:
    """Why a freshly parsed file shouldn't replace the numbers we have at all, or None. Several readers return
    nothing (rather than failing) for an error page or an empty download, and a half-downloaded file loses counties."""
    new, old = new or {}, old or {}
    with_votes = lambda rows: sum(1 for r in rows.values() if _votes(r) > 0)       # noqa: E731
    n_new, n_old = with_votes(new), with_votes(old)
    if not new and old:
        return "the file came back empty (error page or cut-off download); kept the last numbers"
    have = len(M.load().get(st, []))
    if lists_every_county and have >= MIN_COUNTIES and len(new) < MIN_LISTED * have <= len(old):
        return (f"the file listed only {len(new)} of the state's {have} counties (cut off, or its layout changed); "
                "kept the last numbers")
    done = sum(1 for r in new.values() if (r.get("pct_in") or 0) >= 0.999 and _votes(r) > 0)
    if minutes_since_close < TEST_DATA_MINUTES and len(new) >= MIN_COUNTIES and done > 0.5 * len(new):
        return (f"{done} of {len(new)} counties already show every precinct in, {minutes_since_close:.0f} minutes after "
                "polls closed: looks like leftover test data, so it's being ignored for now")
    if n_old >= MIN_COUNTIES and n_new < CUT_OFF_SHARE * n_old:
        return (f"the file had votes in only {n_new} counties, down from {n_old} (probably cut off); "
                "kept the last numbers")
    return None


def _total_fell(new: dict, old: dict) -> str | None:
    t_new, t_old = sum(_votes(r) for r in new.values()), sum(_votes(r) for r in old.values())
    if t_old >= 1000 and t_new < (1 - TOTAL_FALL) * t_old:
        return (f"the state's count fell from {t_old:,} to {t_new:,} votes, more than a correction would explain; "
                f"kept the last numbers (it's believed if it's still there after {CONFIRM_TOTAL} pulls)")
    return None


def _doubt(row: dict, was: dict | None, base: dict | None, switched: bool = False) -> str | None:
    """Why one county's new numbers shouldn't be believed on first sight: "fell", "jumped", or None. Just after the
    state moved to another source, a county a little behind where the last source had it is two sites disagreeing,
    not a count that fell."""
    if was and not switched:
        now = _sides(row)
        for side, before in _sides(was).items():
            if now.get(side, 0) < before - max(SMALL_FALL[0], SMALL_FALL[1] * before):
                return "fell"
    expected = (base or {}).get("w") or 0
    line = max(JUMP * expected, expected + JUMP_VOTES)
    if expected and _votes(row) > line and _votes(was) <= line and not row.get("rest"):
        return "jumped"             # a county already past the line was confirmed there once: let it keep counting
    return None


def check(st: str, new: dict, old: dict | None, minutes_since_close: float = 999, held: dict | None = None,
          lists_every_county: bool = True, switched: bool = False) -> Verdict:
    """Compare a parsed pull with the last good one. See the top of this file for the rules. switched: this pull
    comes from a different source than the last good one (cw_feeds.choose moved the state to another site)."""
    old, held = old or {}, held or {}
    why = suspect(st, new, old, minutes_since_close, lists_every_county)
    if why:
        return Verdict(old, why, [], held)
    fell = _total_fell(new or {}, old)
    if fell:
        seen = (held.get(STATE) or {}).get("seen", 0) + 1
        if seen < CONFIRM_TOTAL:
            return Verdict(old, fell, [], {**held, STATE: {"why": "fell", "seen": seen}})
        old, held = {}, {}                      # it has lasted: a real correction, so take the pull as it is
    base = {c["fips"]: c for c in M.load().get(st, [])}
    name = lambda f: (base.get(f) or {}).get("name") or f       # noqa: E731
    rows, pending = {}, {}
    said = {"fell": [], "jumped": [], "gone": []}
    for fips, row in (new or {}).items():
        was = old.get(fips)
        doubt = _doubt(row, was, base.get(fips), switched)
        if doubt:
            before = held.get(fips) or {}
            seen = (before.get("seen", 0) if before.get("why") == doubt else 0) + 1
            if seen < CONFIRM:
                pending[fips] = {"why": doubt, "seen": seen}
                if was is not None:
                    rows[fips] = was
                said[doubt].append(f"{name(fips)} ({_votes(was):,} to {_votes(row):,})")
                continue
        rows[fips] = row
    for fips, was in old.items():
        if fips not in (new or {}) and _votes(was) > 0:
            rows[fips] = was                # a county with votes doesn't stop existing
            said["gone"].append(name(fips))
    notes = []
    for key, words in (("fell", "the count fell in"), ("jumped", "the count is far past the expected vote in"),
                       ("gone", "missing from the file:")):
        if said[key]:
            more = f" and {len(said[key]) - 4} more" if len(said[key]) > 4 else ""
            notes.append(f"{words} {', '.join(said[key][:4])}{more}")
    if notes:
        notes[-1] += "; kept those counties' last numbers"
    have = len(base)
    if lists_every_county and have >= MIN_COUNTIES and len(rows) < MIN_LISTED * have:
        notes.append(f"the file lists {len(rows)} of the state's {have} counties so far")
    return Verdict(rows, None, notes, pending)


# ------------------------------------------------------------------ the snapshot log
def pack_row(r: dict) -> list:
    """[D, R, other, share in, marked complete], then Alaska's votes still expected by vote type where there are any.
    A row of a race with more than two candidates is [{candidate: votes}, share in, marked complete]."""
    if "v" in r:
        return [dict(r["v"] or {}), r.get("pct_in"), bool(r.get("complete"))]
    return [r.get("D") or 0, r.get("R") or 0, r.get("O") or 0, r.get("pct_in"), bool(r.get("complete"))] + ([r["rest"]] if r.get("rest") else [])


def unpack_row(x: list) -> dict:
    if isinstance(x[0], dict):
        return {"v": dict(x[0]), "pct_in": x[1], **({"complete": True} if x[2] else {})}
    row = {"D": x[0], "R": x[1], "O": x[2], "pct_in": x[3]}
    if x[4]:
        row["complete"] = True
    if len(x) > 5:
        row["rest"] = x[5]
    return row


def folder() -> Path:
    """Where the active pack's snapshot logs are kept: snapshots/, beside live.json."""
    return FEEDS.cw_state("snapshots")


def log_file(st: str, where: Path | None = None) -> Path:
    return (where or folder()) / f"{st}.jsonl"


def _lines(path: Path) -> list[dict]:
    out = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:              # a line cut short by a crash: skip it, keep the rest
            continue
    return out


def _standing(path: Path) -> dict:
    """The state as the log last left it, kept in memory after the first read."""
    if path not in _last:
        now: dict = {}
        for entry in _lines(path):
            for fips, row in (entry.get("c") or {}).items():
                if row is None:
                    now.pop(fips, None)
                else:
                    now[fips] = row
        _last[path] = now
    return _last[path]


def record(st: str, rows: dict, when: float | None = None, source: str | None = None, where: Path | None = None) -> int:
    """Append what changed in a state's counties since the last line of its log. Returns how many counties were
    written (0 when nothing moved). Nothing here may stop a pull: a full disk is logged once and skipped."""
    global _warned
    try:
        with _lock:
            path = log_file(st, where)
            standing = _standing(path)
            packed = {f: pack_row(r) for f, r in (rows or {}).items()}
            diff = {f: x for f, x in packed.items() if standing.get(f) != x}
            diff.update({f: None for f in standing if f not in packed})
            if not diff:
                return 0
            entry = {"t": round(time.time() if when is None else when, 1), "src": source, "c": diff}
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, separators=(",", ":")) + "\n")
            _last[path] = packed
            return len(diff)
    except Exception:
        if not _warned:
            _warned = True
            log.exception("Couldn't write the snapshot log; carrying on without it")
        return 0


def recorded(where: Path | None = None) -> list[str]:
    """The states that have a snapshot log."""
    try:
        return sorted(p.stem for p in (where or folder()).glob("*.jsonl") if p.stat().st_size)
    except OSError:
        return []


def replay(st: str, where: Path | None = None) -> list[tuple[float, dict, dict]]:
    """A state's night from its log: (time, {fips: row}, {fips: when its votes last changed}) at every recorded
    change, oldest first. Each moment's dictionaries are its own, so they can be kept or changed freely."""
    out, now, moved = [], {}, {}
    for entry in sorted(_lines(log_file(st, where)), key=lambda e: e.get("t", 0)):
        t = entry.get("t", 0)
        for fips, x in (entry.get("c") or {}).items():
            if x is None:
                now.pop(fips, None)
                continue
            was, row = now.get(fips), unpack_row(x)
            if not was or _sides(was) != _sides(row):
                moved[fips] = t
            now[fips] = row
        out.append((t, {f: dict(r) for f, r in now.items()}, dict(moved)))
    return out


def set_aside(where: Path | None = None) -> int:
    """After a rehearsal: move the logs into snapshots/rehearsal-<time>/ so practice numbers never sit in the real
    night's record. Returns how many logs were moved."""
    with _lock:
        root = where or folder()
        files = list(root.glob("*.jsonl")) if root.exists() else []
        if files:
            dest = root / f"rehearsal-{time.strftime('%Y%m%d-%H%M%S')}"
            dest.mkdir(parents=True, exist_ok=True)
            for f in files:
                shutil.move(str(f), str(dest / f.name))
                _last.pop(f, None)
        return len(files)
