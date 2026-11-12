"""Read Decision Desk HQ's House calls from the file its grab button saves (countywatch/ddhq_grab.js).

DDHQ's results site turns the bot away with a security check, and DDHQ doesn't post every call on Bluesky, so a person
clicks a bookmark in their own browser. This only checks and sorts the file; bot.py decides what to do with it. A seat
comes back as a clear call or as something a person should look at (read()). A file that doesn't look right is refused
whole, because each clear call can become official.
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import countywatch_run as CWR
import races as R

OUTLET = "Decision Desk HQ"
PAGE = "/balance-of-power/2026/house"
PATTERN = "ddhq-calls*.json"
MAX_AGE = 15 * 60       # seconds: an old file sitting in Downloads isn't tonight's latest read
ENOUGH_SEATS = 400      # fewer than this means the page isn't what it was
NOT_TRACKED = {"DC-AL"}  # DDHQ lists DC's delegate; the bot doesn't track that seat


def read(data: dict, now: float | None = None) -> dict[str, dict]:
    """{seat: {"call": True, rid, party}} for each clear call, or {seat: {"review": why, "rid": rid or None}} for one a
    person should check. Raises ValueError when the file as a whole can't be trusted."""
    if not isinstance(data, dict) or data.get("ddhqcalls") != 1:
        raise ValueError("it isn't a DDHQ grab file")
    if data.get("page") != PAGE:
        raise ValueError(f"it came from {data.get('page')}, not the 2026 House page")
    try:
        when = datetime.fromisoformat(str(data.get("grabbed")).replace("Z", "+00:00"))
        age = (now if now is not None else time.time()) - when.astimezone(timezone.utc).timestamp()
    except ValueError:
        raise ValueError("it doesn't say when it was grabbed") from None
    if age > MAX_AGE:
        raise ValueError(f"it's {age / 60:.0f} minutes old")
    seats = data.get("seats")
    calls, tally = data.get("calls"), data.get("tally")
    if not isinstance(seats, int) or seats < ENOUGH_SEATS or not isinstance(calls, list) or not isinstance(tally, dict):
        raise ValueError("DDHQ's page didn't look the way it should")

    found: dict[str, dict] = {}
    count = {"D": 0, "R": 0, "?": 0}
    tracked_d = 0
    for c in calls:
        seat = str((c or {}).get("seat", "")).strip() if isinstance(c, dict) else ""
        party = c.get("party") if isinstance(c, dict) else None
        party = party if party in ("D", "R") else "?"
        count[party] += 1
        if party == "D" and seat not in NOT_TRACKED:
            tracked_d += 1
        if seat in NOT_TRACKED:
            continue
        if seat not in R.RACES or R.RACES[seat]["type"] != "house":
            found[seat or "?"] = {"review": "the bot doesn't know this seat", "rid": None}
        elif "-us-house-" not in str(c.get("url")) or "general-election" not in str(c.get("url")) or "special" in str(c.get("url")):
            found[seat] = {"review": "not the regular general election", "rid": seat}
        elif party == "?":
            found[seat] = {"review": "couldn't tell the winner's party from the page", "rid": seat}
        else:
            found[seat] = {"call": True, "rid": seat, "party": party}
    # DDHQ's own count has to match the seats read, unless a seat's party is already going to a person
    if not count["?"] and (count["R"] != tally.get("R") or tally.get("D") not in (count["D"], tracked_d)):
        raise ValueError(f"it read D {count['D']}, R {count['R']} but DDHQ's count says D {tally.get('D')}, R {tally.get('R')}")
    return found


def scan(folders: list[Path] | None = None, done: Path | None = None) -> list[dict]:
    """Every grab file waiting in Downloads, oldest first, each moved away so it's only read once:
    {"file", "found", "tally"} or {"file", "error"}."""
    folders = CWR._download_dirs() if folders is None else folders
    done = CWR.INBOX / "done" if done is None else done
    out = []
    files = [f for d in folders for f in d.glob(PATTERN) if f.is_file()]
    for f in sorted(files, key=lambda p: p.stat().st_mtime):
        if time.time() - f.stat().st_mtime < 1:      # still being written
            continue
        try:
            data = json.loads(f.read_bytes())
            out.append({"file": f.name, "found": read(data), "tally": data["tally"]})
        except ValueError as exc:
            out.append({"file": f.name, "error": str(exc)[:200]})
        try:
            done.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(done / f"{datetime.now().strftime('%H%M%S')}_{f.name}"))
        except OSError:
            try:
                f.unlink()      # never read the same file twice
            except OSError:
                pass
    return out
