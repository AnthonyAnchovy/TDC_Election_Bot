"""County Watch orchestration: pulls feeds and early vote, writes caches and the workbook.
Used by the bot's background loop and its /counties, /earlyvote and /countytest commands.
Can also run alone:  python countywatch_run.py early   |   python countywatch_run.py live   |   python countywatch_run.py build
"""
from __future__ import annotations

import asyncio
import collections
import csv
import json
import logging
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import aiohttp

import cw_early as E
import cw_feeds as FD
import cw_model as M
import cw_workbook as W

HERE = Path(__file__).parent
CW = HERE / "countywatch"
WORKBOOK = HERE / "County Watch 2026.xlsx"
log = logging.getLogger("countywatch")
UA = FD.UA


def _load(name, default):
    try:
        return json.loads((CW / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save(name, data):
    (CW / name).write_text(json.dumps(data, default=str), encoding="utf-8")


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


async def refresh_live() -> dict:
    """Pull every feed state at once. A state's file is ignored until its first polls close, because several
    states load test numbers into the live file beforehand (Minnesota's did in late October).
    Sets cache['changed'] to the states whose numbers moved since the last pull."""
    import night as N
    cache = live_cache()
    cache["changed"] = []
    todo = []
    blocked = cache.setdefault("blocked", {})
    for st in FD.feed_states():
        if now_et() < N.first_close(st):
            cache["states"].pop(st, None)
            cache["errors"][st] = f"waiting for polls to close ({N.fmt(N.first_close(st), N.ET)})"
        elif time.time() - blocked.get(st, 0) < BLOCK_RETRY:
            continue            # its site answered with a bot check; don't keep knocking
        else:
            todo.append(st)
    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        results = await asyncio.gather(*(FD.fetch_state(st, session=s) for st in todo), return_exceptions=True)
    for st, res in zip(todo, results):
        if isinstance(res, Exception):          # one state's outage shouldn't stop the others
            cache["errors"][st] = str(res)[:200] or type(res).__name__
            if isinstance(res, FD.BotCheck):
                blocked[st] = time.time()
            continue
        blocked.pop(st, None)
        cache["errors"].pop(st, None)
        if res["changed"] and res["counties"] != cache["states"].get(st):
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
    """Messages worth posting right away, one per state at most: projection milestones (first projection,
    confidence stepping up, the leader changing, or a move of a point or more) and key counties that just
    finished counting. Remembers what it already said in countywatch/alerts.json."""
    seen = _load("alerts.json", {})
    nm = names()
    man, fac = manual(), factors()
    rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
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


def blocked_notices(cache: dict) -> list[str]:
    """One message per state whose site answered the bot with a bot check (said once per night)."""
    seen = _load("alerts.json", {})
    told = seen.setdefault("_blocked_told", [])
    out = []
    for st in sorted(cache.get("blocked", {})):
        if st not in told:
            told.append(st)
            out.append(f"🚧 {M.STATES[st]}'s results site answered the bot with a bot check, so it can't pull "
                       f"{st} automatically. Use the **County Watch grab** button on that site in your browser "
                       f"(one click saves the numbers and the bot loads them in seconds), or `/countyfile`.")
    if out:
        _save("alerts.json", seen)
    return out


_INPUTS = {"mtime": None, "keep": {}}


def _inputs() -> dict:
    """The workbook's manual entries and turnout factors, re-read only when the file changes."""
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


def set_entry(fips: str, d: int, r: int, other: int, done: bool, who: str):
    """A county result typed into Discord with /countyset. Overrides the feed and the workbook."""
    e = discord_entries()
    e[fips] = {"md": d, "mr": r, "mo": other, "mc": "Y" if done else "", "by": who, "at": time.time()}
    _save("discord_entries.json", e)


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
        if fips in ("factor", "updated") or not isinstance(m, dict):
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
    cache = live_cache()
    old = (cache.get("files", {}) or {}).get(st)
    cache.setdefault("files", {})[st] = counties
    cache.setdefault("file_meta", {})[st] = {"name": name, "kind": kind, "at": time.time()}
    cache["changed"] = [st]
    _save("live.json", cache)
    return {"state": st, "kind": kind, "counties": len(counties), "of": len(M.load()[st]), "votes": votes,
            "complete": done, "changed": counties != old}


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


def merged_live(st: str, cache: dict | None = None, man: dict | None = None) -> dict:
    cache = cache or live_cache()
    man = manual() if man is None else man
    live = feed_view(cache).get(st, {})
    for c in M.load()[st]:
        m = man.get(c["fips"])
        if not m:
            continue
        row = live.setdefault(c["fips"], {"D": 0, "R": 0, "O": 0, "pct_in": None})
        if m.get("md") not in (None, ""):
            row["D"], row["R"], row["O"] = m.get("md") or 0, m.get("mr") or 0, m.get("mo") or 0
            row["pct_in"] = None
        if str(m.get("mc", "")).upper() == "Y":
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
        out[st] = "\n".join(l.replace("**", "") for l in M.read_lines(st, p, *nm[st]))
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
def write_workbook(cache=None) -> str:
    cache = cache or live_cache()
    early = early_cache()
    nc = dict(early.get("nc") or {})
    nc["uf_fetched"] = early.get("uf_fetched")
    try:
        extra = {f: {k: m[k] for k in ("md", "mr", "mo", "mc")} for f, m in discord_entries().items()}
        return W.update(WORKBOOK, live=feed_view(cache), uf=early.get("uf"), nc=nc, reads=reads(cache), extra_manual=extra)
    except Exception as exc:
        log.exception("workbook update failed")
        return f"workbook update failed: {exc}"


async def main(argv):
    logging.basicConfig(level=logging.INFO)
    what = argv[1] if len(argv) > 1 else "build"
    if what == "early":
        c = await refresh_early()
        print("UF:", "ok" if c.get("uf") else c.get("uf_error"), "| NC:", "ok" if c.get("nc") else c.get("nc_error"))
    if what == "live":
        c = await refresh_live()
        print({st: len(v) for st, v in c["states"].items()}, c["errors"])
    print(write_workbook())


if __name__ == "__main__":
    asyncio.run(main(sys.argv))
