"""County Watch orchestration: pulls feeds and early vote, writes caches and the workbook.
Used by the bot's background loop and its /counties, /earlyvote and /countytest commands.
Can also run alone:  python countywatch_run.py early   |   python countywatch_run.py live   |   python countywatch_run.py build
(build updates the shared workbook when it's set up; add --local to rebuild the file in this folder instead)
"""
from __future__ import annotations

import asyncio
import collections
import csv
import json
import os
import functools
import threading
import logging
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import aiohttp

import cw_cloud as CC
import cw_early as E
import cw_feeds as FD
import cw_model as M
import cw_workbook as W

HERE = Path(__file__).parent
CW = HERE / "countywatch"
WORKBOOK = HERE / "County Watch 2026.xlsx"
log = logging.getLogger("countywatch")
UA = FD.UA


# Feeds, alerts, the workbook writer and Discord commands run in different threads and share these JSON files.
# Every read-change-write holds _LOCK, saves are atomic (write a temp file, then swap it in), and a read that
# catches a file mid-swap on Windows retries instead of mistaking it for an empty cache.
_LOCK = threading.RLock()


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        with _LOCK:
            return fn(*a, **kw)
    return wrapper


def _load(name, default):
    path = CW / name
    for attempt in range(20):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except (OSError, ValueError):
            time.sleep(0.05)
    log.warning("couldn't read %s; using an empty one this time", name)
    return default


def _save(name, data):
    path = CW / name
    tmp = path.with_name(f".{name}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, default=str), encoding="utf-8")
    for attempt in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:          # Windows: someone is reading it right now
            time.sleep(0.05)
    log.error("couldn't save %s (file busy)", name)


def live_cache() -> dict:
    return _load("live.json", {"states": {}, "errors": {}, "fetched": None})


def early_cache() -> dict:
    return _load("early.json", {})


def names() -> dict:
    return W._names()


# ------------------------------------------------------------------ live results
def now_et() -> datetime:
    import os
    import night as N
    fake = os.getenv("DESK_FAKE_NOW")
    return datetime.fromisoformat(fake) if fake else datetime.now(N.ET)


BLOCK_RETRY = 15 * 60       # seconds before re-trying a site that answered with a bot check
THROTTLE_MAX = 5 * 60       # longest wait between tries of a site that answered 429 or 403, unless it asks for longer
THROTTLE_WARN = 3 * 60      # how long that goes on before the board hears about it


def _suspect_pull(new: dict, old: dict | None, minutes_since_close: float = 999) -> str | None:
    """Why a freshly parsed file shouldn't replace the numbers we have, or None. Several parsers return nothing
    (rather than failing) for an error page or an empty download, and a half-downloaded file loses counties."""
    with_votes = lambda rows: sum(1 for r in (rows or {}).values() if (r.get("D") or 0) + (r.get("R") or 0) > 0)
    n_new, n_old = with_votes(new), with_votes(old)
    if not new and old:
        return "the file came back empty (error page or cut-off download); kept the last numbers"
    done = sum(1 for r in (new or {}).values() if (r.get("pct_in") or 0) >= 0.999 and (r.get("D") or 0) + (r.get("R") or 0) > 0)
    if minutes_since_close < 10 and len(new or {}) >= 4 and done > 0.5 * len(new):
        return (f"{done} of {len(new)} counties already show every precinct in, {minutes_since_close:.0f} minutes after "
                "polls closed: looks like leftover test data, so it's being ignored for now")
    if n_old >= 4 and n_new < 0.5 * n_old:
        return (f"the file had votes in only {n_new} counties, down from {n_old} (probably cut off); "
                "kept the last numbers")
    return None


def extended_states(sources: dict, when: datetime) -> dict:
    """{state: seconds between pulls} for the feeds that keep running after election night, because their late ballots
    get counted for days (feeds.json's extended_feeds). A bad date leaves that state off rather than stopping anything."""
    out = {}
    for st, cfg in (sources.get("extended_feeds") or {}).items():
        try:
            if when < datetime.fromisoformat(cfg["until"]):
                out[st] = cfg.get("refresh_seconds", 120)
        except (KeyError, TypeError, ValueError):
            log.warning("extended_feeds: bad entry for %s", st)
    return out


async def refresh_live(only=None) -> dict:
    """Pull every feed state at once, or just the states in `only`. A state's file is ignored until its first polls
    close, because several states load test numbers into the live file beforehand (Minnesota's did in late October).
    Sets cache['changed'] to the states whose numbers moved since the last pull."""
    import night as N
    before = live_cache()
    blocked0, throttled0 = before.get("blocked", {}), before.get("throttled", {})
    todo, waiting = [], []
    for st in FD.feed_states():
        if only is not None and st not in only:
            continue
        if now_et() < N.first_close(st):
            waiting.append(st)
        elif time.time() - blocked0.get(st, 0) < BLOCK_RETRY and not FD.has_fallback(st):
            continue            # its site answered with a bot check; don't keep knocking
        elif time.time() < throttled0.get(st, {}).get("until", 0):
            continue            # its site said to slow down; wait out the time it's owed
        else:
            todo.append(st)
    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        results = await asyncio.gather(*(FD.fetch_state(st, session=s) for st in todo), return_exceptions=True)
    with _LOCK:                 # re-read under the lock: a loaded file may have landed while we were downloading
        return _apply_pulls(todo, results, waiting)


def _slow_down(entry: dict | None, exc: FD.Throttled) -> dict:
    """The next try for a site that answered 429 or 403. The wait doubles from a minute up to THROTTLE_MAX, or is what
    the site asked for if that's longer."""
    t, fails = time.time(), (entry or {}).get("fails", 0) + 1
    wait = max(min(30 * 2 ** fails, THROTTLE_MAX), exc.wait or 0)
    return {"fails": fails, "since": (entry or {}).get("since", t), "until": t + wait, "why": str(exc)}


def _apply_pulls(todo, results, waiting) -> dict:
    import night as N
    cache = live_cache()
    cache.setdefault("states", {})
    cache.setdefault("errors", {})
    cache["changed"] = []
    blocked = cache.setdefault("blocked", {})
    throttled = cache.setdefault("throttled", {})
    for st in waiting:
        cache["states"].pop(st, None)
        cache["errors"][st] = f"waiting for polls to close ({N.fmt(N.first_close(st), N.ET)})"
    for st, res in zip(todo, results):
        if isinstance(res, Exception):          # one state's outage shouldn't stop the others
            cache["errors"][st] = str(res)[:200] or type(res).__name__
            if isinstance(res, FD.BotCheck):
                blocked[st] = time.time()
                throttled.pop(st, None)
            elif isinstance(res, FD.Throttled):
                throttled[st] = _slow_down(throttled.get(st), res)
                cache["errors"][st] += f"; kept the last numbers, next try at {datetime.fromtimestamp(throttled[st]['until']):%I:%M:%S %p}"
            continue
        blocked.pop(st, None)
        throttled.pop(st, None)
        bad = _suspect_pull(res["counties"], cache["states"].get(st),
                            (now_et() - N.first_close(st)).total_seconds() / 60)
        if bad:                                  # an error page or a cut-off file: keep the last good numbers
            cache["errors"][st] = bad
            continue
        cache["errors"].pop(st, None)
        # compare even when the site said "not modified": its numbers come from memory then, and they restore a
        # state whose cache was lost or reset
        if res["counties"] != cache["states"].get(st):
            cache["states"][st] = res["counties"]
            cache.get("files", {}).pop(st, None)       # a newer feed pull replaces a hand-loaded file
            cache["changed"].append(st)
    cache["fetched"] = time.time()
    _save("live.json", cache)
    return cache


def national(cache=None) -> dict:
    cache = cache or live_cache()
    man, fac = manual(), factors()
    return M.national({st: projection(st, cache, man, fac) for st in M.STATES})


def early_pace() -> list[tuple]:
    """(state, early votes, share of 2022 turnout, D-R registration gap or None) for every state the
    UF tracker covers, highest pace first."""
    uf = early_cache().get("uf") or {}
    out = []
    for st, r in uf.items():
        if r.get("voted") and r.get("turn22"):
            p = r["dem"] + r["rep"] + r["none"]
            out.append((st, r["voted"], r["voted"] / r["turn22"], (r["dem"] - r["rep"]) / p if p else None))
    return sorted(out, key=lambda x: -x[2])


# ------------------------------------------------------------------ alerts for the board
def alerts(cache: dict, states: list[str] | None = None) -> list[str]:
    man, fac = manual(), factors()          # may read the workbook: do it before taking the lock
    with _LOCK:
        return _alerts(cache, states, man, fac)


def _alerts(cache: dict, states, man, fac) -> list[str]:
    """Messages worth posting right away, one per state at most: projection milestones (first projection,
    confidence stepping up, the leader changing, or a move of a point or more) and key counties that just
    finished counting. Remembers what it already said in countywatch/alerts.json."""
    seen = _load("alerts.json", {})
    nm = names()
    rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
    post_projections = FD.load_sources().get("post_projection_alerts", True)
    out = []
    for st in states or list(M.STATES):
        p = projection(st, cache, man, fac)
        mem = seen.setdefault(st, {"done": [], "proj": None, "conf": "none", "at": 0})
        dl, rl = nm[st][0].split()[-1], nm[st][1].split()[-1]
        parts = []
        pm = p["proj_m"]
        if pm is not None:
            last = mem["proj"]
            leader_flip = last is not None and (last > 0) != (pm > 0)
            conf_up = rank[p["confidence"]] > rank.get(mem["conf"], 0)
            moved = last is not None and abs(pm - last) >= 0.01 and time.time() - mem["at"] >= 180
            if last is None or leader_flip or conf_up or moved:
                was = f" (was {M.fmt(last, dl, rl)})" if last is not None else ""
                tagb = " (bellwether: watch the swing, not the winner)" if st in M.BELLWETHER else ""
                if post_projections:
                    parts.append(f"📈 **{M.STATES[st]} projection: {M.fmt(pm, dl, rl)}**{was}{tagb}."
                                 + (" **Leader change.**" if leader_flip else "")
                                 + f" Confidence {p['confidence']}: {p['done']} {'county' if p['done'] == 1 else 'counties'} fully counted, "
                                 f"{p['done_share']:.0%} of the expected vote. Swing vs 2024 {M.swing_txt(p['state_swing'])}; "
                                 f"{dl} {M.need_txt(p['needed_swing'])}."
                                 + _turnout_tag(p, dl, rl))
                mem.update(proj=pm, conf=p["confidence"], at=time.time())
        finished = []
        for r in M.key_table(st, p):
            if r["complete"] and r["fips"] not in mem["done"]:
                mem["done"].append(r["fips"])
                gap = r["live_m"] - r["breakeven"]
                t = (f"**{r['name']}** {M.fmt(r['live_m'], dl, rl)} vs {M.fmt(r['breakeven'])} needed "
                     f"({'+' if gap >= 0 else '-'}{abs(gap) * 100:.1f}; swing {M.swing_txt(r['swing'])})")
                if r["t_index"] is not None:
                    ov, net = r["overall"], r["overall"] * (r["D"] + r["R"])
                    t += (f" · turnout {M.idx_txt(r['t_index'])} → needed {M.fmt(r['t_be'])} · with turnout "
                          f"{'+' if ov >= 0 else '-'}{abs(ov) * 100:.1f} pts ({'+' if net >= 0 else '-'}{abs(net):,.0f} net for {dl})")
                finished.append(t)
        if finished:
            parts.append(f"📍 Key {'county' if len(finished) == 1 else 'counties'} fully counted in {M.STATES[st]} "
                         f"(turnout index: 100 = state pace):\n" + "\n".join(f"• {f}" for f in finished))
        if parts:
            out += _chunks("\n".join(parts))
    n = M.national({st: projection(st, cache, man, fac) for st in M.STATES})
    mem = seen.setdefault("_national", {"swing": None, "counties": 0, "at": 0})
    if n["counties"] >= 20 and n["swing"] is not None and (
            mem["swing"] is None or (abs(n["swing"] - mem["swing"]) >= 0.01 and time.time() - mem["at"] >= 600)
            or n["counties"] >= 2 * max(mem["counties"], 20)):
        was = f" (was {M.swing_txt(mem['swing'])})" if mem["swing"] is not None else ""
        out.append("🌡️ **National read**" + was + "\n" + "\n".join(M.national_lines(n))[:1800])
        mem.update(swing=n["swing"], counties=n["counties"], at=time.time())
    _save("alerts.json", seen)
    return out


def _chunks(text: str, n: int = 1990) -> list[str]:
    """Split on line breaks so no Discord message passes the length limit."""
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > n:
            out.append(line[:n])
            line = line[n:]
        if cur and len(cur) + 1 + len(line) > n:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out


def _turnout_tag(p: dict, dl: str, rl: str) -> str:
    (di, dn), (ri, rn) = p["d_index"], p["r_index"]
    if di is None and ri is None:
        return ""
    parts = [f"{dl}-leaning counties {M.idx_txt(di)}" if di is not None else None,
             f"{rl}-leaning {M.idx_txt(ri)}" if ri is not None else None]
    return " Turnout index: " + ", ".join(x for x in parts if x) + " (100 = state pace)."


@_locked
def blocked_notices(cache: dict) -> list[str]:
    """One message per state whose site answered the bot with a bot check (said once per night), and one for the states
    that have been answering 429 or 403 for THROTTLE_WARN, with a follow-up when they answer again."""
    seen = _load("alerts.json", {})
    told = seen.setdefault("_blocked_told", [])
    slow_told = seen.setdefault("_throttled_told", [])
    out = []
    for st in sorted(cache.get("blocked", {})):
        if st not in told:
            told.append(st)
            out.append(f"🚧 {M.STATES[st]}'s results site answered the bot with a bot check, so it can't pull "
                       f"{st} automatically. Use the **County Watch grab** button on that site in your browser "
                       f"(one click saves the numbers and the bot loads them in seconds), or `/countyfile`.")
    throttled = cache.get("throttled", {})
    late = [st for st in sorted(throttled)
            if st not in slow_told and time.time() - throttled[st]["since"] >= THROTTLE_WARN]
    if late:
        slow_told.extend(late)
        why = "; ".join(f"{st}: {throttled[st]['why']}" for st in late)
        out.append(f"🐢 **Stale feed: {', '.join(M.STATES.get(st, st) for st in late)}.** The results site has been turning "
                   f"the bot away for at least {THROTTLE_WARN // 60} minutes ({why}). These feeds are stuck on their last "
                   "good numbers. The bot keeps trying, more slowly. To keep them moving, use the **County Watch grab** "
                   "button (OH, TX, KS), `/countyfile`, or `/countyset`.")
    back = [st for st in slow_told if st not in throttled and st not in cache.get("blocked", {})]
    if back:
        seen["_throttled_told"] = [st for st in slow_told if st not in back]
        out.append(f"✅ **{', '.join(M.STATES.get(st, st) for st in sorted(back))}:** the results site is answering the bot again.")
    if out:
        _save("alerts.json", seen)
    return out


_INPUTS = {"mtime": None, "keep": {}, "cloud": None}


def _inputs() -> dict:
    """The workbook's manual entries and turnout factors. With the shared workbook on, they're what the last sync
    read from it (no network here, so commands never wait on Microsoft); otherwise the local file, re-read only
    when it changes."""
    if _INPUTS["cloud"] is not None and CC.enabled():
        return _INPUTS["cloud"]
    try:
        mt = WORKBOOK.stat().st_mtime
    except OSError:
        return {}
    if _INPUTS["mtime"] != mt:
        try:
            _INPUTS["keep"], _INPUTS["mtime"] = W.read_inputs(WORKBOOK), mt
        except Exception:
            return _INPUTS["keep"]
    return _INPUTS["keep"]


def factors() -> dict:
    """Turnout factors from the workbook (Dashboard), so the bot and the sheet agree."""
    return _inputs().get("factor", {})


def discord_entries() -> dict:
    return _load("discord_entries.json", {})


@_locked
def set_entry(fips: str, d: int, r: int, other: int, done: bool, who: str):
    """A county result typed into Discord with /countyset. Overrides the feed and the workbook."""
    e = discord_entries()
    e[fips] = {"md": d, "mr": r, "mo": other, "mc": "Y" if done else "", "by": who, "at": time.time()}
    _save("discord_entries.json", e)


@_locked
def clear_entry(fips: str) -> bool:
    e = discord_entries()
    if e.pop(fips, None) is None:
        return False
    _save("discord_entries.json", e)
    return True


def manual() -> dict:
    """Hand-entered county numbers: the workbook's Manual columns, then /countyset entries on top.
    Both override feed values, as the workbook does."""
    keep = _inputs()
    out = {}
    for fips, m in keep.items():
        if fips in ("factor", "updated", "polls") or not isinstance(m, dict):
            continue
        out[fips] = m
    for fips, m in discord_entries().items():
        out[fips] = {k: m[k] for k in ("md", "mr", "mo", "mc")}
    return out


# ------------------------------------------------------------------ files you download yourself
INBOX = CW / "inbox"


def feed_view(cache: dict | None = None) -> dict:
    """Feed numbers with any loaded files laid over them, per state (what the workbook's Feed columns show)."""
    cache = cache or live_cache()
    out = {}
    for st in set(cache.get("states", {})) | set(cache.get("files", {})):
        rows = {k: dict(v) for k, v in (cache["states"].get(st) or {}).items()}
        for k, v in (cache.get("files", {}).get(st) or {}).items():
            rows[k] = dict(v)
        out[st] = rows
    return out


def load_file(raw: bytes, name: str, state: str | None = None) -> dict:
    """Read a results file (see cw_ingest) and use it for that state until a newer file or feed pull replaces it."""
    import cw_ingest as I
    import night as N
    st, counties, kind = I.ingest(raw, name, state)
    votes = sum(r["D"] + r["R"] + r["O"] for r in counties.values())
    done = sum(1 for r in counties.values() if r.get("complete") or (r.get("pct_in") or 0) >= 0.999)
    if name.startswith("countywatch-") and now_et() < N.first_close(st):
        # A grab before polls close is a test (states load practice numbers beforehand): report, don't use.
        return {"state": st, "kind": kind, "counties": len(counties), "of": len(M.load()[st]), "votes": votes,
                "complete": done, "changed": False, "test_only": True}
    with _LOCK:
        cache = live_cache()
        old = (cache.get("files", {}) or {}).get(st)
        cache.setdefault("files", {})[st] = counties
        cache.setdefault("file_meta", {})[st] = {"name": name, "kind": kind, "at": time.time()}
        cache["changed"] = [st]
        _save("live.json", cache)
    return {"state": st, "kind": kind, "counties": len(counties), "of": len(M.load()[st]), "votes": votes,
            "complete": done, "changed": counties != old}


def reset_night() -> str:
    """After a rehearsal: forget pulled and loaded results, /countyset entries and what alerts already said, so none
    of the practice numbers reach the real night. The shared workbook's Manual cells the bot filled get blanked on
    the next sync (cells people typed over are left alone)."""
    gone = []
    with _LOCK:
        for name in ("live.json", "discord_entries.json", "alerts.json"):
            try:
                (CW / name).unlink()
                gone.append(name)
            except FileNotFoundError:
                pass
    return ("cleared " + ", ".join(gone)) if gone else "nothing to clear"


def _download_dirs() -> list[Path]:
    if not FD.load_sources().get("watch_downloads", True):
        return []
    d = Path.home() / "Downloads"
    return [d] if d.is_dir() else []


def scan_inbox() -> list[dict]:
    """Load every file dropped into countywatch/inbox, plus countywatch-*.json files the grab button saved to
    Downloads, then move each to inbox/done."""
    INBOX.mkdir(exist_ok=True)
    (INBOX / "done").mkdir(exist_ok=True)
    out = []
    files = [f for f in INBOX.iterdir() if f.is_file()]
    for d in _download_dirs():
        files += [f for f in d.glob("countywatch-*.json") if f.is_file()]
    for f in sorted(files, key=lambda p: p.stat().st_mtime):
        if f.name.startswith((".", "~$")) or f.suffix.lower() in (".crdownload", ".part", ".tmp"):
            continue
        if time.time() - f.stat().st_mtime < 1:          # still being written
            continue
        try:
            res = load_file(f.read_bytes(), f.name)
            res["file"] = f.name
            res["grab"] = f.name.startswith("countywatch-")
        except Exception as exc:
            res = {"file": f.name, "error": str(exc)[:300]}
        out.append(res)
        try:
            shutil.move(str(f), str(INBOX / "done" / f"{datetime.now().strftime('%H%M%S')}_{f.name}"))
        except OSError:
            try:
                f.unlink()          # never load the same file twice
            except OSError:
                pass
    return out


def _vote(x):
    """A typed vote count: a number 0 or more, else None (text, blanks and negatives are ignored, as in Excel)."""
    if isinstance(x, bool) or not isinstance(x, (int, float)) or x != x or x < 0:
        return None
    return int(x)


def merged_live(st: str, cache: dict | None = None, man: dict | None = None) -> dict:
    cache = cache or live_cache()
    man = manual() if man is None else man
    live = feed_view(cache).get(st, {})
    for c in M.load()[st]:
        m = man.get(c["fips"])
        if not isinstance(m, dict) or not m:
            continue
        row = live.setdefault(c["fips"], {"D": 0, "R": 0, "O": 0, "pct_in": None})
        d, r = _vote(m.get("md")), _vote(m.get("mr"))
        if d is not None and r is not None:      # as the workbook: manual numbers count once both D and R are in
            row["D"], row["R"], row["O"] = d, r, _vote(m.get("mo")) or 0
            row["pct_in"] = None
            row.pop("rest", None)
        if str(m.get("mc") or "").strip().upper() == "Y":
            row["complete"] = True
    return live


def projection(st: str, cache=None, man=None, fac=None) -> dict:
    fac = factors() if fac is None else fac
    return M.project(st, merged_live(st, cache, man), fac.get(st, 1.0))


def reads(cache=None) -> dict:
    nm = names()
    man, fac = manual(), factors()
    out = {}
    for st in M.STATES:
        p = projection(st, cache, man, fac)
        out[st] = M.take(st, p, *nm[st])
    return out


# ------------------------------------------------------------------ early vote
async def refresh_early(nc_full: bool = True) -> dict:
    cache = early_cache()
    timeout = aiohttp.ClientTimeout(total=600)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        try:
            async with s.get(E.UF_URL) as r:
                r.raise_for_status()
                cache["uf"] = E.parse_uf(await r.read())
                cache["uf_fetched"] = datetime.now().strftime("%b %d %I:%M %p")
                cache.pop("uf_error", None)
        except Exception as exc:
            cache["uf_error"] = str(exc)[:200]
        if nc_full:
            try:
                async with s.get(E.NC_ABS_URL) as r:
                    r.raise_for_status()
                    raw = await r.read()
                agg = await asyncio.to_thread(E.aggregate_nc, raw, E.ELECTION_2026)
                cache["nc"] = nc_tables(agg)
                cache.pop("nc_error", None)
            except Exception as exc:
                cache["nc_error"] = str(exc)[:200]
    _save("early.json", cache)
    return cache


def nc_tables(agg: dict) -> dict:
    days = (E.ELECTION_2026 - agg["through"]).days
    off = E.load_offsets(CW / "nc_ev2022_by_day.csv")
    same = E.as_of(off, days)
    base = {c["name"].upper(): c for c in M.load()["NC"]}
    rows = []
    a22d = a22r = 0
    for cty in sorted(set(agg["by_county"]) | set(same)):
        c26 = agg["by_county"].get(cty, collections.Counter())
        b = base.get(cty)
        act = None
        if b and b["modes22"]:
            d = b["modes22"]["early"][0] + b["modes22"]["mail"][0]
            r = b["modes22"]["early"][1] + b["modes22"]["mail"][1]
            a22d, a22r = a22d + d, a22r + r
            act = M.m2(d, r)
        rows.append({"county": cty.title(), "g26": {g: c26.get(g, 0) for g in E.NC_GROUPS},
                     "g22": {g: same.get(cty, {}).get(g, 0) for g in E.NC_GROUPS}, "actual22": act,
                     "black26": (c26.get("race:BLACK", 0) / c26["all"]) if c26.get("all") else None,
                     "young26": (c26.get("age:18-29", 0) / c26["all"]) if c26.get("all") else None,
                     "old26": (c26.get("age:65+", 0) / c26["all"]) if c26.get("all") else None})
    return {"through": str(agg["through"]), "days_before": days, "rows": rows, "actual22_state": M.m2(a22d, a22r)}


def nc_summary(nc: dict) -> list[str]:
    if not nc or not nc.get("rows"):
        return []
    model = E.load_json("nc_ev_model.json") or {}
    sh = model.get("shares")
    t26, t22 = collections.Counter(), collections.Counter()
    for r in nc["rows"]:
        t26.update(r["g26"])
        t22.update(r["g22"])
    n26, n22 = sum(t26.values()), sum(t22.values())
    lines = [f"Through {nc['through']} ({nc['days_before']} days out): {n26:,} accepted ballots vs {n22:,} at the same "
             f"point in 2022 ({n26 / n22 - 1:+.0%})." if n22 else f"{n26:,} accepted ballots so far."]
    def share(c, n, keys):
        return sum(c[k] for k in keys) / n if n else 0
    dem = ["DEM_BLACK", "DEM_NONBLACK"]
    una = ["UNA_BLACK", "UNA_NONBLACK"]
    lines.append(f"Registered Democrats {share(t26, n26, dem):.0%} (2022: {share(t22, n22, dem):.0%}) · "
                 f"Republicans {share(t26, n26, ['REP']):.0%} ({share(t22, n22, ['REP']):.0%}) · "
                 f"unaffiliated {share(t26, n26, una):.0%} ({share(t22, n22, una):.0%}).")
    if sh:
        l26, l22 = E.lean(t26, sh), E.lean(t22, sh)
        lines.append(f"Modeled lean of the early vote: {M.fmt(l26)} vs {M.fmt(l22)} at this point in 2022 "
                     f"(2022's final early vote was {M.fmt(nc.get('actual22_state'))}; Budd still won by 3).")
        lines.append("That's who has voted, not how. It assumes each group splits the way it did in 2022, "
                     "and some Republicans may simply be waiting for Election Day.")
    return lines


# ------------------------------------------------------------------ workbook
WB_WARN = 3         # failed shared workbook updates in a row before the board hears about it
_WB = {"fails": 0, "error": None, "told": False, "partial": False}


def workbook_notices() -> list[str]:
    """One message once the shared workbook has missed WB_WARN updates in a row, and one when it's updating again.
    Updates that went through except for one part count as missed too, with their own wording."""
    if _WB["fails"] >= WB_WARN and not _WB["told"]:
        _WB["told"] = True
        if _WB.get("partial"):
            return [f"⚠️ **Part of the shared workbook has stopped updating.** The last {_WB['fails']} updates skipped it "
                    f"({_WB['error']}). Everything else is still updating. A 403 usually means a cell the bot writes is "
                    "locked on a protected tab."]
        return [f"⚠️ **The shared workbook has stopped updating.** The last {_WB['fails']} updates failed ({_WB['error']}). "
                "Anyone looking at it is seeing old numbers, and what's typed into it isn't reaching the bot. The bot keeps "
                "trying, and the feeds and board posts aren't affected. If it's the Microsoft sign-in, "
                "`python cw_cloud.py check` on the bot's PC will say so."]
    if _WB["fails"] == 0 and _WB["told"]:
        _WB["told"] = False
        return ["✅ **The shared workbook is updating again.**"]
    return []


def write_workbook(cache=None, *, local: bool = False) -> str:
    """Bring the workbook up to date: the shared copy when it's set up (cw_cloud), else the file in this folder.
    local=True always rebuilds the file here."""
    cache = cache or live_cache()
    early = early_cache()
    nc = dict(early.get("nc") or {})
    nc["uf_fetched"] = early.get("uf_fetched")
    try:
        extra = {f: {k: m[k] for k in ("md", "mr", "mo", "mc")} for f, m in discord_entries().items()}
        live, rd = feed_view(cache), reads(cache)
        if CC.enabled() and not local:
            try:
                _INPUTS["cloud"] = CC.sync(live=live, reads=rd, stamp=datetime.now().strftime("%b %d %I:%M %p"),
                                           uf=early.get("uf"), nc=nc, entries=extra)
                if CC.problems:     # the rest went through, but a part that keeps failing still needs someone to look
                    _WB.update(fails=_WB["fails"] + 1, error="; ".join(CC.problems)[:150], partial=True)
                else:
                    _WB.update(fails=0, error=None, partial=False)
                try:                # its own tab, so trouble here can't touch the rest of the workbook
                    import cw_mississippi as MS
                    MS.refresh(CC, cache)
                except Exception:
                    log.exception("Mississippi tab update failed")
                if CC.problems:
                    return f"updated the shared workbook except: {'; '.join(CC.problems)}"
                return "updated the shared workbook"
            except Exception as exc:
                log.exception("shared workbook update failed")
                _WB.update(fails=_WB["fails"] + 1, error=str(exc)[:150] if isinstance(exc, CC.CloudError) else type(exc).__name__,
                           partial=False)
                done = W.update(WORKBOOK, live=live, uf=early.get("uf"), nc=nc, reads=rd, extra_manual=extra)
                return f"shared workbook update failed ({exc}), so {done} on this PC instead"
        return W.update(WORKBOOK, live=live, uf=early.get("uf"), nc=nc, reads=rd, extra_manual=extra)
    except Exception as exc:
        log.exception("workbook update failed")
        return f"workbook update failed: {exc}"


# ------------------------------------------------------------------ private messages about what reached the workbook
def workbook_wrote(status: str) -> bool:
    """Whether write_workbook's answer means the numbers really went into the workbook. A failed shared update or a
    live.csv written instead doesn't count, so those numbers wait for the next update that works."""
    return status.startswith(("updated the shared workbook", "updated the open workbook", "rebuilt the workbook"))


def _snap_row(r: dict) -> list:
    """[D, R, other, share in, marked complete], then Alaska's votes still expected by vote type where there are any."""
    return [r.get("D") or 0, r.get("R") or 0, r.get("O") or 0, r.get("pct_in"), bool(r.get("complete"))] + ([r["rest"]] if r.get("rest") else [])


def result_snapshot(cache: dict | None = None) -> dict:
    """{state: {fips: [D, R, other, share of precincts in, marked complete]}}: every county's numbers as the workbook
    counts them, typed entries included."""
    cache = cache or live_cache()
    man = manual()
    out = {}
    for st in M.STATES:
        out[st] = {f: _snap_row(r) for f, r in merged_live(st, cache, man).items()}
    return out


@functools.lru_cache(maxsize=1)
def _county_districts() -> dict:
    """{fips: U.S. House districts, biggest share first}, built by tools/make_county_districts.py. Without the file the
    messages simply leave districts out."""
    try:
        return json.loads((CW / "county_districts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@functools.lru_cache(maxsize=None)
def _tracked_counties(st: str) -> frozenset:
    """The FIPS of a state's counties named on Key Counties or First Counties. Those lists can say 'Virginia Beach' where
    the baseline says 'Virginia Beach city', and an exact match wins, so 'Fairfax' never means Fairfax city."""
    keys = json.loads((CW / "key_counties.json").read_text(encoding="utf-8")).get(st, [])
    first = json.loads((CW / "first_counties.json").read_text(encoding="utf-8"))["states"]
    names = [k[0] for k in keys] + [n for e in first if e["state"] == st for n in e["counties"]]
    fips = {c["name"]: c["fips"] for c in M.load()[st]}
    return frozenset(filter(None, (fips.get(n) or fips.get(f"{n} city") for n in names)))


def _place_name(st: str, name: str) -> str:
    """The full name of a place on the map: Alaska reports by state House district, Virginia has independent cities."""
    if st == "AK":
        return name.replace("HD District", "State House District")
    return f"{name[:-5]} City" if name.endswith(" city") else f"{name} County"


def _result_block(st: str, c: dict, d_name: str, r_name: str, row: list) -> str:
    """One place's update, readable without any other message around it."""
    d, r, other, pct_in, complete = row[:5]
    total = d + r + other
    share = lambda v: f" ({100 * v / total:.1f}%)" if total else ""
    votes = f"{d_name} {d:,}{share(d)}, {r_name} {r:,}{share(r)}"
    if other:
        votes += f", other {other:,}{share(other)}"
    counted = "fully counted" if complete else f"{100 * pct_in:.0f}% in" if pct_in is not None else "% in not reported"
    districts = ["AK-AL"] if st == "AK" else _county_districts().get(c["fips"], [])
    where = f"{_place_name(st, c['name'])}, {M.STATES[st]}"
    return f"**{where}**" + (f" ({', '.join(districts)})" if districts else "") + f" · {counted}\n{votes}"


def result_messages(old: dict, new: dict, limit: int = 1900) -> list[str]:
    """What changed between two snapshots, by state and county, as messages short enough for Discord. Counties with no
    votes before or after are left out, since the feeds list every county from the moment polls close. Bellwether states
    aren't races we're following, so only the counties the model names there are sent."""
    import cw_ingest as I
    msgs, cur = [], ""
    for st in sorted(new, key=lambda s: M.STATES[s]):
        d_name, r_name = I._cands(st)
        d_name, r_name = (d_name.split() or ["D"])[-1], (r_name.split() or ["R"])[-1]
        for c in sorted(M.load()[st], key=lambda c: c["name"]):
            row, before = new[st].get(c["fips"]), (old.get(st) or {}).get(c["fips"])
            if row is None or row == before or not (sum(row[:3]) or (before and sum(before[:3]))):
                continue
            if st in M.BELLWETHER and c["fips"] not in _tracked_counties(st):
                continue
            block = _result_block(st, c, d_name, r_name, row)
            if cur and len(cur) + len(block) + 1 > limit:
                msgs.append(cur.rstrip())
                cur = ""
            cur += block + "\n"
    return msgs + ([cur.rstrip()] if cur else [])


def state_snapshot(st: str, cache: dict | None = None) -> dict:
    """One state's counties as they stand: {fips: [D, R, other, share of precincts in, marked complete]}."""
    return {f: _snap_row(r) for f, r in merged_live(st, cache or live_cache(), manual()).items()}


def _short_names(st: str) -> tuple[str, str]:
    import cw_ingest as I
    d_name, r_name = I._cands(st)
    return (d_name.split() or ["D"])[-1], (r_name.split() or ["R"])[-1]


def _counted(row: list | None) -> tuple:
    """What counts as a real change to a county: its votes, or someone marking it complete. Its % in doesn't, because NYT
    revising how many votes it expects moves that with no new votes at all."""
    return (row[0], row[1], row[2], row[4]) if row else (0, 0, 0, False)


def votes_moved(old: dict, new: dict) -> bool:
    return any(_counted(old.get(f)) != _counted(new.get(f)) for f in set(old) | set(new))


def _snap_project(st: str, snap: dict) -> dict:
    """The projection for a saved snapshot, so two moments can be compared on equal terms."""
    live = {f: {"D": r[0], "R": r[1], "O": r[2], "pct_in": r[3], "complete": r[4], "rest": r[5] if len(r) > 5 else None} for f, r in snap.items()}
    return M.project(st, live, factors().get(st, 1.0))


def _proj_totals(p: dict) -> tuple[float, float]:
    return sum(r["proj_D"] for r in p["rows"]), sum(r["proj_R"] for r in p["rows"])


def pct_moves(old: dict, new: dict) -> list[tuple]:
    """(fips, % in before, % in now) for counties whose whole-number % in changed, biggest change first. Counties with no
    votes yet are left out: their % in means nothing."""
    out = []
    for f, row in new.items():
        before = old.get(f)
        if not row or not before or row[3] is None or before[3] is None or not sum(row[:3]):
            continue
        a, b = round(100 * before[3]), round(100 * row[3])
        if a != b:
            out.append((f, a, b))
    return sorted(out, key=lambda m: -abs(m[2] - m[1]))


def _model_left(r: dict) -> float:
    """Two-party votes the projection still expects in a county. It sizes these from past turnout and tonight's pace."""
    return max(0.0, r["proj_D"] + r["proj_R"] - r["D"] - r["R"])


def _pct_left(r: dict) -> float:
    """Two-party votes still out if a county's % in is taken at face value. Places projected by vote type (Alaska) or with
    no % in keep the projection's number, and a finished county has none."""
    if r["complete"]:
        return 0.0
    if r["rest"] or not r["pct_in"]:
        return _model_left(r)
    return (r["D"] + r["R"]) * (1 / r["pct_in"] - 1)


MODEL_STALE = 6 * 3600    # seconds: NYT's model older than this is labeled old in the alerts


def _nyt_model(st: str) -> dict | None:
    """NYT's own estimate of the votes left, by county. Only Mississippi reads it so far. None if it can't be read."""
    if st != "MS":
        return None
    import cw_mississippi as MS
    return MS.fetch_nyt()


def pct_messages(st: str, old: dict, new: dict, moves: list[tuple], show: int = 8, limit: int = 1950) -> list[str]:
    """The update for when only % in moved: the votes still expected in each county by candidate, on the projection's
    numbers, on % in's and on NYT's model where we have it, and what the move did to the statewide projection."""
    d_name, r_name = _short_names(st)
    a, b = _snap_project(st, old), _snap_project(st, new)
    nyt = _nyt_model(st)
    asof, stale = "", False
    if nyt and nyt.get("model_at"):
        import cw_mississippi as MS
        asof = MS._clock(nyt["model_at"])
        try:
            age = datetime.now().astimezone() - datetime.fromisoformat(nyt["model_at"].replace("Z", "+00:00"))
            stale = age.total_seconds() > MODEL_STALE
        except ValueError:
            pass
    was, now = {r["fips"]: r for r in a["rows"]}, {r["fips"]: r for r in b["rows"]}
    moves = [m for m in moves if m[0] in now and m[0] in was]
    if not moves:
        return []
    who = lambda v: d_name if v > 0 else r_name
    net_txt = lambda v: f"{who(v)} +{abs(v):,}" if v else "even"
    by = lambda v: f"{who(v)} by {abs(v):,.0f} vote{'' if round(abs(v)) == 1 else 's'}" if round(v) else "Tied"

    def split(left: float, r: dict) -> tuple[int, int]:
        """(votes for D, votes for R) in what's left, at the margin the model gives this county's remaining votes."""
        total = round(left)
        d = round(left * (1 + r["proj_m"]) / 2)
        return d, total - d

    def net_of(left: float, r: dict) -> int:
        d, rr = split(left, r)
        return d - rr

    def line(label: str, left: float, left0: float, r: dict, r0: dict) -> str:
        n, n0 = net_of(left, r), net_of(left0, r0)
        if not round(left) and not round(left0):
            return f"   {label}: none left, no change"
        d, rr = split(left, r)
        votes = f"{left:,.0f} left. {d_name} {d:,}, {r_name} {rr:,}. Net {net_txt(n)}" if round(left) else "none left"
        if round(left) == round(left0) and n == n0:
            return f"   {label}: {votes}. No change"
        return f"   {label}: {votes}. Was {left0:,.0f} left, net {net_txt(n0)}"

    effect = {}
    for f, _, _ in moves:
        r, o = now[f], was[f]
        effect[f] = max(abs((r["proj_D"] - r["proj_R"]) - (o["proj_D"] - o["proj_R"])),
                        abs(net_of(_pct_left(r), r) - net_of(_pct_left(o), o)))
    moves = sorted(moves, key=lambda m: -effect[m[0]])

    # ---- the count, the projection, and which counties moved it
    cd, cr = sum(r["D"] for r in b["rows"]), sum(r["R"] for r in b["rows"])
    lead = f"{who(cd - cr)} ahead by {abs(cd - cr):,} vote{'' if abs(cd - cr) == 1 else 's'}" if cd != cr else "Tied"
    ad, ar = _proj_totals(a)
    pd, pr = _proj_totals(b)
    shift = (pd - pr) - (ad - ar)
    lines = [f"📊 **{M.STATES[st]} % in moved. No new votes.**", f"**Count:** {lead} ({d_name} {cd:,}, {r_name} {cr:,})"]
    if abs(shift) >= 1:
        lines.append(f"**Projected:** {by(pd - pr)}. Was {by(ad - ar)}. Moved {abs(shift):,.0f} to {who(shift)}")
        moved = {f for f, _, _ in moves}
        here = sum((now[f]["proj_D"] - now[f]["proj_R"]) - (was[f]["proj_D"] - was[f]["proj_R"]) for f in moved)
        elsewhere = shift - here
        if abs(here) >= 1:
            lines.append(f"• These {len(moved)} counties: {abs(here):,.0f} to {who(here)}" if len(moved) > 1 else f"• This county: {abs(here):,.0f} to {who(here)}")
        if abs(elsewhere) >= 1:
            why = f". Finished counties went {a['done']} → {b['done']}, which resets swing and turnout" if a["done"] != b["done"] else ""
            lines.append(f"• Other counties: {abs(elsewhere):,.0f} to {who(elsewhere)}{why}")
    else:
        lines.append(f"**Projected:** {by(pd - pr)}. No change")

    # ---- county by county
    lines += ["", "**Votes still expected**"]
    for f, x, y in moves[:show]:
        r, o = now[f], was[f]
        pm, pm0, im, im0 = _model_left(r), _model_left(o), _pct_left(r), _pct_left(o)
        at = f". Late votes at {M.fmt(r['proj_m'], d_name, r_name)} pts" if round(pm) or round(im) else ""
        block = [f"• **{r['name']}** {x}→{y}%{at}", line("Projection", pm, pm0, r, o)]
        if not r["rest"]:
            block.append(line("% in implies", im, im0, r, o))
        ny = ((nyt or {}).get("counties") or {}).get(f)
        if ny:
            block.append(f"   NYT's model{' (old)' if stale else ''}: {ny['left']:,.0f} left. {d_name} {ny['d']:,.0f}, {r_name} {ny['r']:,.0f}. Net {net_txt(round(ny['net']))}")
        lines.append("\n".join(block))          # one item, so a county never splits across two messages
    if len(moves) > show:
        rest = moves[show:]
        pn = sum(net_of(_model_left(now[m[0]]), now[m[0]]) - net_of(_model_left(was[m[0]]), was[m[0]]) for m in rest)
        inn = sum(net_of(_pct_left(now[m[0]]), now[m[0]]) - net_of(_pct_left(was[m[0]]), was[m[0]]) for m in rest)
        moved_txt = lambda v: f"{abs(v):,} to {who(v)}" if v else "no change"
        lines.append(f"• {len(rest)} more counties. Net moved: projection {moved_txt(pn)}, % in implies {moved_txt(inn)}")

    # ---- how to weigh those
    if nyt:
        when = f" as of {asof} ET" if asof else ""
        lines += ["", f"**NYT's model statewide{when}:** {nyt['left']:,.0f} left, net {net_txt(round(nyt['net']))}. Final {by(nyt['final'])}"]
        if stale:
            lines.append("NYT's model has stopped updating, so its numbers are older than the count")
    if not b["by_type"]:       # Alaska's projection works from vote types instead
        lines += ["", "The projection only uses % in when a county reaches or drops below 100%. \"% in implies\" scales up the votes counted, and isn't in the projection"]
    msgs, cur = [], ""
    for ln in lines:
        if cur and len(cur) + len(ln) + 1 > limit:
            msgs.append(cur.rstrip())
            cur = ""
        cur += ln + "\n"
    return msgs + ([cur.rstrip()] if cur.strip() else [])


def state_watch_messages(st: str, old: dict, new: dict, limit: int = 1900, prec: dict | None = None) -> list[str]:
    """A state's alert in short labeled lines: the count, new votes, the projection and why it moved, how to read it, and
    what to watch. old == new gives the same without the parts about a change."""
    d_name, r_name = _short_names(st)
    if prec is None:
        prec = {f: (r["prec_out"], r.get("prec_total") or 0) for f, r in feed_view(live_cache()).get(st, {}).items() if "prec_out" in r}
    p = _snap_project(st, new)
    rows = p["rows"]
    pd, pr = _proj_totals(p)
    cd, cr, co = sum(r["D"] for r in rows), sum(r["R"] for r in rows), sum(r["O"] for r in rows)
    total = cd + cr + co
    who = lambda v: d_name if v > 0 else r_name
    side = lambda v: f"{who(v)} +{abs(v):,.0f}" if round(v) else "Tied"
    margin = lambda v: M.fmt(v, d_name, r_name)
    pct = lambda v: f" ({100 * v / total:.1f}%)" if total else ""
    left = lambda r: max(0.0, r["proj_D"] + r["proj_R"] - r["D"] - r["R"])
    left_net = lambda r: (r["proj_D"] - r["proj_R"]) - (r["D"] - r["R"])
    net = lambda r: r["proj_D"] - r["proj_R"]
    changed = votes_moved(old, new)

    head = [f"🔔 **{M.STATES[st]} Senate: {side(cd - cr)}**",
            f"{d_name} {cd:,}{pct(cd)} · {r_name} {cr:,}{pct(cr)}" + (f" · other {co:,}{pct(co)}" if co else "")]

    # ---- new votes, one line each
    fresh = []
    for c in sorted(M.load()[st], key=lambda c: c["name"]):
        row, before = new.get(c["fips"]), old.get(c["fips"])
        if row is None or _counted(row) == _counted(before):
            continue
        was = before or [0, 0, 0]
        counted = "fully counted" if row[4] else f"{100 * row[3]:.0f}% in" if row[3] is not None else "% in unknown"
        fresh.append(f"• {c['name']}: {sum(row[:3]) - sum(was[:3]):+,}. {d_name} {row[0] - was[0]:+,}, {r_name} {row[1] - was[1]:+,}. Now {counted}")

    # ---- the projection and why it moved
    proj = [f"**Projected:** {side(pd - pr)}"]
    why = []
    if changed:
        a = _snap_project(st, old)
        ad, ar = _proj_totals(a)
        proj[0] += f" (was {side(ad - ar)})"
        shift = (pd - pr) - (ad - ar)
        if abs(shift) >= 1:
            proj[0] += f". Moved {abs(shift):,.0f} to {who(shift)}"
        proj.append(f"{d_name} {ad:,.0f} → {pd:,.0f} · {r_name} {ar:,.0f} → {pr:,.0f}")
        was = {r["fips"]: r for r in a["rows"]}
        items = []
        for r in rows:
            o = was[r["fips"]]
            v0, v1 = o["proj_D"] + o["proj_R"], r["proj_D"] + r["proj_R"]
            items.append((net(r) - net(o), r, (net(o) / v0 if v0 else 0.0), (net(r) / v1 if v1 else 0.0), v0, v1))
        reported = sorted((x for x in items if _counted(new.get(x[1]["fips"])) != _counted(old.get(x[1]["fips"])) and abs(x[0]) >= 1), key=lambda x: -abs(x[0]))
        for diff, r, m0, m1, v0, v1 in reported[:3]:
            still = "" if r["complete"] else " (still counting)"
            why.append(f"• {r['name']}{still}: {abs(diff):,.0f} to {who(diff)}. {margin(m1)}, expected {margin(m0)}. {v1:,.0f} votes, expected {v0:,.0f}")
        if len(reported) > 3:
            more = sum(x[0] for x in reported[3:])
            why.append(f"• {len(reported) - 3} smaller counties: {abs(more):,.0f} to {who(more)}")
        quiet = sum(x[0] for x in items if _counted(new.get(x[1]["fips"])) == _counted(old.get(x[1]["fips"])))
        if abs(quiet) >= 1:
            why.append(f"• Counties with no new votes: {abs(quiet):,.0f} to {who(quiet)}. Estimates updated")

    # ---- how to read it
    read = []
    if cd == cr or (cd > cr) != (pd > pr):
        read.append(f"• Count: {who(cd - cr) if cd != cr else 'tied'}. Projection: {who(pd - pr)}. They disagree")
    else:
        read.append(f"• Count and projection both say {who(pd - pr)}")
    if p.get("need_share") is not None and p.get("remaining"):
        trailer = d_name if p["trailer"] == "D" else r_name
        need, pace = round(100 * p["need_share"], 1), round(100 * p["pace_share"], 1)
        gap = need - pace
        call = "Can't catch up on these estimates" if need > 100 else "Still live" if gap < 5 else "Uphill" if gap <= 15 else "Nearly out of reach"
        read.append(f"• {trailer} needs {need:.1f}% of what's left. On pace for {pace:.1f}%. {call}")
    out = {r["fips"]: prec[r["fips"]][0] for r in rows if prec.get(r["fips"], (0, 0))[0]}
    remaining = sum(left(r) for r in rows)
    hard = [r for r in rows if r["fips"] in out]

    def normal_size(r):
        n_out, n_total = prec[r["fips"]]
        return (r["D"] + r["R"]) / (n_total - n_out) * n_out if n_total > n_out else left(r)
    if prec and remaining >= 1:
        if out:
            soft = remaining - sum(left(r) for r in hard)
            read.append(f"• About {remaining:,.0f} votes left. {soft:,.0f} of those may not exist (every precinct in)")
            high = (cd - cr) + sum(left_net(r) for r in hard)
            low = (cd - cr) + sum(left_net(r) * min(1.0, normal_size(r) / left(r)) for r in hard if left(r))
            a, b = sorted((abs(low), abs(high)))
            ends = (side(high) if round(abs(high - low)) < 50 else f"{who(high)} +{a:,.0f} to +{b:,.0f}" if (high > 0) == (low > 0)
                    else f"{side(low)} to {side(high)}")
            read.append(f"• Missing precincts alone: {ends}")
        else:
            read.append(f"• Every precinct is in. The {remaining:,.0f} left is only an estimate")
    if changed and why:
        top = reported[0] if reported else None
        if top:
            r = top[1]
            alike = sum(left(x) for x in rows if x["type"] == r["type"] and x["fips"] != r["fips"])
            if alike >= 100:
                read.append(f"• Watch if other {r['type']} counties do the same: about {alike:,.0f} votes left")

    # ---- what to watch
    watch = []
    for r in sorted(hard, key=lambda r: -left(r))[:3]:
        n = prec[r["fips"]][0]
        watch.append(f"• {r['name']}: {n} precinct{'s' if n > 1 else ''}, about {min(left(r), normal_size(r)) if left(r) else normal_size(r):,.0f} votes, leans {who(net(r))}")
    if not watch:
        for r in sorted((r for r in rows if left(r) >= 100), key=lambda r: -left(r))[:3]:
            watch.append(f"• {r['name']}: about {left(r):,.0f} votes, leans {who(left_net(r))}")
    empty = sorted(c["name"] for c in M.load()[st] if not sum((new.get(c["fips"]) or [0, 0, 0])[:3]))

    lines = list(head)
    trailing = []
    if fresh:
        if len(fresh) <= 6:
            lines += ["", "**New votes**"] + fresh
        else:
            lines += ["", f"**New votes in {len(fresh)} counties** (listed below)"]
            trailing = fresh
    lines += [""] + proj
    if why:
        lines += ["**Why**"] + why
    lines += ["", "**Read**"] + read
    if watch:
        lines += ["", "**Next**"] + watch
    if empty:
        lines.append(f"**Nothing in yet:** {', '.join(empty[:15])}" + (f" and {len(empty) - 15} more" if len(empty) > 15 else ""))
    msgs, cur = [], ""
    for line in lines + trailing:
        if cur and len(cur) + len(line) + 1 > limit:
            msgs.append(cur.rstrip())
            cur = ""
        cur += line + "\n"
    return msgs + ([cur.rstrip()] if cur.strip() else [])


async def main(argv):
    logging.basicConfig(level=logging.INFO)
    what = argv[1] if len(argv) > 1 else "build"
    if what == "early":
        c = await refresh_early()
        print("UF:", "ok" if c.get("uf") else c.get("uf_error"), "| NC:", "ok" if c.get("nc") else c.get("nc_error"))
    if what == "live":
        c = await refresh_live()
        print({st: len(v) for st, v in c["states"].items()}, c["errors"])
    print(write_workbook(local="--local" in argv))


if __name__ == "__main__":
    asyncio.run(main(sys.argv))
