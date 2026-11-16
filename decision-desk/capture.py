"""Keep what the bot saw and did, so a night can be replayed later (no Discord here).

Two logs go under captures/<date the bot started>/:
  index.jsonl + payloads   every response from a results feed, the body saved gzipped the first time it's seen
                           and only noted when it comes back unchanged
  events.jsonl             every command, public post and error, with its time

Nothing here may break the bot: a full disk or a bad path is logged once and the capture is skipped. Set
DESK_CAPTURE=0 in .env to turn it off, or DESK_CAPTURE_DIR to put it somewhere else. Bodies over MAX_BODY (the 85 MB
North Carolina absentee file) are noted, not saved.

Reader gives a captured folder back by time: the body a source had at a given moment. net.py uses it for replays.
"""
from __future__ import annotations

import bisect
import gzip
import hashlib
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

import clock

HERE = Path(__file__).parent
MAX_BODY = 30 * 1024 * 1024
log = logging.getLogger("desk.capture")

_run: Path | None = None
_last: dict[tuple[str, str], tuple[str, str | None]] = {}     # (source, url) -> (sha, file) of the last body saved
_warned = False


def enabled() -> bool:
    return os.getenv("DESK_CAPTURE", "1").strip().lower() not in ("0", "false", "off", "no")


def run_dir() -> Path:
    """This run's folder, made on first use and named for the day the bot started."""
    global _run
    if _run is None:
        root = Path(os.getenv("DESK_CAPTURE_DIR") or HERE / "captures")
        _run = root / datetime.fromtimestamp(clock.time()).strftime("%Y-%m-%d")
        _run.mkdir(parents=True, exist_ok=True)
    return _run


def start(folder: Path | None = None):
    """Point captures at a folder (tests), or forget the current run so the next write starts a new one."""
    global _run, _warned
    _run, _warned = folder, False
    _last.clear()
    if folder:
        folder.mkdir(parents=True, exist_ok=True)


def _append(name: str, row: dict):
    with open(run_dir() / name, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _guard(fn, *args):
    global _warned
    if not enabled():
        return
    try:
        fn(*args)
    except Exception:                               # a capture must never take the bot down
        if not _warned:
            _warned = True
            log.exception("Couldn't write a capture; carrying on without it")


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")[:60] or "source"


def _payload(source: str, url: str, status: int, body: bytes, headers: dict | None):
    t = clock.time()
    sha = hashlib.sha256(body).hexdigest()
    row = {"t": t, "source": source, "url": url, "status": status, "bytes": len(body), "sha": sha}
    for h in ("ETag", "Last-Modified", "Retry-After"):
        if headers and headers.get(h):
            row[h.lower()] = headers[h]
    seen = _last.get((source, url))
    if seen and seen[0] == sha:
        row["file"], row["same"] = seen[1], True
    elif len(body) > MAX_BODY:
        row["file"], row["skipped"] = None, "too big"
        _last[(source, url)] = (sha, None)
    else:
        packed = body[:2] == b"PK" or body[:2] == b"\x1f\x8b"       # already a zip or gzip: don't squeeze it twice
        rel = f"{_slug(source)}/{int(t * 1000)}-{sha[:8]}{'.bin' if packed else '.gz'}"
        path = run_dir() / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body if packed else gzip.compress(body, 3))
        row["file"] = rel
        _last[(source, url)] = (sha, rel)
    _append("index.jsonl", row)


def payload(source: str, url: str, status: int, body: bytes, headers: dict | None = None):
    """One response from a feed. `source` names the feed ("results/FL", "nyt/house"), so a replay can ask for it."""
    _guard(_payload, source, url, status, body or b"", headers)


def event(kind: str, **data):
    """One thing that happened: kind is "command", "post", "error" or whatever the caller needs later."""
    _guard(_append, "events.jsonl", {"t": clock.time(), "kind": kind, **data})


# ------------------------------------------------------------------ reading a capture back
class Reader:
    """A captured folder, by time."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.rows: dict[str, list[dict]] = {}
        index = self.folder / "index.jsonl"
        for line in index.read_text(encoding="utf-8").splitlines() if index.exists() else []:
            row = json.loads(line)
            self.rows.setdefault(row["source"], []).append(row)
        for rows in self.rows.values():
            rows.sort(key=lambda r: r["t"])

    def sources(self) -> list[str]:
        return sorted(self.rows)

    def row(self, source: str, ts: float) -> dict | None:
        """The latest response captured from this source at or before ts."""
        rows = self.rows.get(source, [])
        i = bisect.bisect_right([r["t"] for r in rows], ts) - 1
        return rows[i] if i >= 0 else None

    def body(self, row: dict) -> bytes | None:
        if not row.get("file"):
            return None
        raw = (self.folder / row["file"]).read_bytes()
        return gzip.decompress(raw) if row["file"].endswith(".gz") else raw

    def events(self, kind: str | None = None) -> list[dict]:
        path = self.folder / "events.jsonl"
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
        return [r for r in rows if kind is None or r["kind"] == kind]
