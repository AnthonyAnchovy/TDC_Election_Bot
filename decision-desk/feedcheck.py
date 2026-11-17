"""Check every source the bot reads on election night, months before it matters (no Discord here).

    python feedcheck.py                 check everything, print a table, exit 1 if anything is broken
    python feedcheck.py --only GA,NC    just these states
    python feedcheck.py --json out.json also write the results (the website's health page can read them)
    python feedcheck.py --markdown      print the table as Markdown (GitHub writes it into the run's summary)

States change their results sites between elections: a new vendor, a renamed file, a column that moves. On the
night that looks like a feed that returns nothing, with minutes to fix it. This runs the bot's own readers against
each state's last real election (the "test" settings in feeds.json and results_sources.json), so a change shows up
as a failed check here instead. A state with more than one county source has each of them checked, in the order
the bot trusts them, and the states with only one are listed at the end. GitHub runs it every Monday (.github/workflows/feeds.yml) and opens an issue when
something breaks.

Three answers per source:
  ok     the site answered and the bot's reader made sense of it
  warn   it couldn't be checked from here, in a way that's expected: a site that blocks automated visitors (use the
         grab button on the night), or a file that only exists around election day
  FAIL   the site is gone or the reader no longer understands it. Someone has to look before the next election.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import aiohttp

import cw_feeds as CF
import cw_model as M
import feeds as FEEDS
import packs
import results as RS

TIMEOUT = 120
MIN_COUNTIES = 0.9      # a county feed has to give votes for this share of the state's counties


def _row(kind: str, name: str, status: str, detail: str, took: float, url: str = "") -> dict:
    return {"kind": kind, "name": name, "status": status, "detail": detail[:300], "seconds": round(took, 1), "url": url}


async def _timed(kind: str, name: str, coro) -> dict:
    t = time.time()
    try:
        status, detail, url = await asyncio.wait_for(coro, TIMEOUT)
    except CF.BotCheck as exc:
        status, detail, url = "warn", f"the site blocks automated visitors ({exc}); use the grab button on the night", ""
    except CF.Throttled as exc:
        status, detail, url = "warn", f"the site asked us to slow down ({exc})", ""
    except asyncio.TimeoutError:
        status, detail, url = "FAIL", f"no answer in {TIMEOUT} seconds", ""
    except Exception as exc:
        status, detail, url = "FAIL", f"{type(exc).__name__}: {exc}", ""
        if "hasn't listed" in str(exc):         # a Clarity site lists an election only in the weeks around it
            status = "warn"
        elif "CERTIFICATE_VERIFY_FAILED" in str(exc):
            detail = ("the site's security certificate wasn't accepted from this computer. If it fails from GitHub "
                      "too, the state's server is misconfigured and the feed needs its backup; if only here, this "
                      f"PC's certificates or antivirus are in the way. ({type(exc).__name__})")
    return _row(kind, name, status, detail, time.time() - t, url)


def source_kind(i: int) -> str:
    """What a state's first, second and third sources are called in the table."""
    return "county feed" if i == 0 else "county feed (backup)" if i == 1 else f"county feed (backup {i})"


def single_source(states: dict | None = None) -> list[str]:
    """The feed states with only one source: if that site fails on the night, the state is typed in by hand."""
    states = CF.load_sources().get("states", {}) if states is None else states
    return sorted(st for st, cfg in states.items() if cfg.get("format") in CF.PARSERS and len(CF.source_list(cfg)) < 2)


async def _county_feed(st: str, cfg: dict, session, label: str, place: int = 1, of: int = 1) -> tuple[str, str, str]:
    """One county feed against its last real election."""
    status, detail, url = await _county_feed_check(st, cfg, session, label)
    return status, (f"{label}, source {place} of {of}: {detail}" if of > 1 else detail), url


async def _county_feed_check(st: str, cfg: dict, session, label: str) -> tuple[str, str, str]:
    test = cfg.get("test")
    if test is None:
        return "warn", "no test election set for this feed in feeds.json, so it can't be checked ahead of time", ""
    merged = {**cfg, **test, "state": st}
    merged.pop("fallback", None)
    res = await CF._pull(merged, True, session)
    counties = res["counties"]
    have = len(M.load().get(st, []))
    if test.get("count_only"):
        return "ok", f"the site answers ({test.get('note') or 'no old election to read'})", res["url"]
    voted = sum(1 for r in counties.values() if (r.get("D") or 0) + (r.get("R") or 0) > 0)
    if have and voted < MIN_COUNTIES * have:
        return "FAIL", (f"read votes for only {voted} of {have} counties from the {label} test election: the file's "
                        "layout, the contest's name or the county names have probably changed"), res["url"]
    d, r = sum(x.get("D") or 0 for x in counties.values()), sum(x.get("R") or 0 for x in counties.values())
    return "ok", f"{voted} of {have or len(counties)} counties, {d + r:,} two-party votes read from the test election", res["url"]


async def _results_feed(st: str) -> tuple[str, str, str]:
    res = await RS.fetch_state(st, test=True, force=True)
    races = res["races"]
    if not races:
        if res.get("bytes", 0) > 2000:
            return "warn", ("the test file was read, but it's from an election with none of this pack's races in it, "
                            "so the reader couldn't be checked end to end"), res["url"]
        return "FAIL", "the test file came back empty", res["url"]
    return "ok", f"{len(races)} races read from the test file ({res.get('bytes', 0):,} bytes)", res["url"]


async def _plain(session, url: str, must_parse=None, seasonal: bool = False) -> tuple[str, str, str]:
    async with session.get(url) as r:
        body = await r.read()
        if r.status == 404 and seasonal:
            return "warn", "not posted (HTTP 404): this file only exists around its election day", url
        if r.status in (403, 429):
            return "warn", f"HTTP {r.status}: the site turns automated visitors away", url
        if r.status != 200:
            return "FAIL", f"HTTP {r.status}", url
    if must_parse:
        note = must_parse(body)
        if isinstance(note, tuple):
            return note[0], note[1], url
        return "ok", note, url
    return "ok", f"{len(body):,} bytes", url


def _uf(body: bytes):
    import cw_early as E
    rows = E.parse_uf(body)
    return ("FAIL", "the early vote tracker's table has changed: no states read from it") if not rows else \
        f"{len(rows)} states in the early vote tracker"


def _json_ok(body: bytes):
    try:
        data = json.loads(body)
    except ValueError:
        return "FAIL", "not JSON any more"
    return f"JSON, {len(body):,} bytes" + (f", {len(data.get('races', []))} races" if isinstance(data, dict) and "races" in data else "")


async def run(only: set[str] | None = None) -> list[dict]:
    """Every check, a few at a time so no site sees a burst."""
    jobs = []
    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": CF.UA}) as s:
        states = CF.load_sources().get("states", {})
        for st in CF.feed_states():
            if only and st not in only:
                continue
            srcs = CF.source_list(states[st])
            for i, src in enumerate(srcs):          # every source the state can read, in the order it's trusted
                jobs.append(_timed(source_kind(i), st, _county_feed(st, src, s, CF.source_name(src), i + 1, len(srcs))))
        for st in RS.supported():
            if not only or st in only:
                jobs.append(_timed("results file", st, _results_feed(st)))
        if not only:
            for kind, url in FEEDS.nyt().items():
                jobs.append(_timed("NYT calls", kind, _plain(s, url, _json_ok, seasonal=True)))
            ev = FEEDS.early_vote()
            if ev.get("uf"):
                jobs.append(_timed("early vote", "UF Election Lab", _plain(s, ev["uf"], _uf)))
            if ev.get("nc_absentee"):
                jobs.append(_timed("early vote", "NC absentee file", _head(s, ev["nc_absentee"])))
            for did, outlet in FEEDS.bluesky_accounts().items():
                url = f"https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor={did}&limit=5"
                jobs.append(_timed("Bluesky", outlet, _plain(s, url, _json_ok)))
        sem = asyncio.Semaphore(4)

        async def gated(job):
            async with sem:
                return await job
        return list(await asyncio.gather(*(gated(j) for j in jobs)))


async def _head(session, url: str) -> tuple[str, str, str]:
    """A big file: ask whether it's there without downloading it."""
    async with session.head(url, allow_redirects=True) as r:
        if r.status == 404:
            return "warn", "not posted (HTTP 404): this file only exists while early voting is on", url
        if r.status != 200:
            return "FAIL", f"HTTP {r.status}", url
        return "ok", f"there, {int(r.headers.get('Content-Length') or 0):,} bytes", url


def table(rows: list[dict], markdown: bool = False) -> str:
    order = {"FAIL": 0, "warn": 1, "ok": 2}
    rows = sorted(rows, key=lambda r: (order[r["status"]], r["kind"], r["name"]))
    if markdown:
        mark = {"FAIL": "❌ FAIL", "warn": "⚠️ warn", "ok": "✅ ok"}
        head = "| | Source | | What happened |\n|---|---|---|---|\n"
        return head + "\n".join(f"| {mark[r['status']]} | {r['kind']} | {r['name']} | {r['detail']} |" for r in rows)
    return "\n".join(f"{r['status']:<5} {r['kind']:<21} {r['name']:<18} {r['detail']}" for r in rows)


def summary(rows: list[dict]) -> str:
    n = {s: sum(1 for r in rows if r["status"] == s) for s in ("ok", "warn", "FAIL")}
    return f"{n['ok']} ok, {n['warn']} couldn't be checked from here, {n['FAIL']} broken"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="comma-separated states")
    ap.add_argument("--json", help="write the results to this file")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--pack", help="check this data pack instead of the default")
    a = ap.parse_args()
    ctx = packs.using(a.pack) if a.pack else None
    if ctx:
        ctx.__enter__()
    try:
        only = {s.strip().upper() for s in a.only.split(",")} if a.only else None
        rows = asyncio.run(run(only))
        alone = [st for st in single_source() if not only or st in only]
    finally:
        if ctx:
            ctx.__exit__(None, None, None)
    print(table(rows, a.markdown))
    print("\n" + summary(rows))
    if alone:
        print(f"Only one source, so nothing to switch to if it fails: {', '.join(alone)}")
    if a.json:
        Path(a.json).write_text(json.dumps({"at": time.time(), "summary": summary(rows), "rows": rows}, indent=1), encoding="utf-8")
    return 1 if any(r["status"] == "FAIL" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
