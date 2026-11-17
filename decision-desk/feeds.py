"""Where the active data pack's numbers come from (no Discord here, and nothing is fetched here).

A pack names its own sources in a "feeds" block. Anything it leaves out is simply off: a pack with no "feeds" at
all runs on what people enter by hand, which is all a one-off city race needs.

  "feeds": {
    "results":     "results_sources.json",      the file of official state results files that /results reads
    "nyt":         {"house": url, "senate": url, "governor": url},     the NYT results files read for calls
    "bluesky":     {"since": "2026-11-03T17:00:00-05:00", "accounts": {"did:plc:...": "Decision Desk HQ"}},
    "early_vote":  {"uf": url, "nc_absentee": url},
    "countywatch": {"folder": "countywatch", "race": "{state}-SEN", "workbook": "County Watch 2026.xlsx",
                    "states": ["GA", "NC", ...], "bellwether": ["VA", "KY"], "night_ends": "08:00",
                    "not_comparable": ["NE"],          places whose swing says nothing about the others
                    "contests": {"2024-pres": {"d": "d24", "r": "r24", "total": "t24"}, ...},
                                                       the past contests in baseline.csv, by column (cw_model)
                    "baseline": {"split": "2024-pres", "turnout": "2022"},
                                                       what the night is measured from
                    "backtests": [{"name": "2024", "result": "2024-pres", "split": "2020-pres",
                                   "turnout": "2020-pres", "polls": "2024"}, {"name": "2026", "result": "live"}],
                                                       the elections backtest.py replays
                    "forecast": {"SE": 0.05},          the forecast's settings for this election (cw_forecast.HP)
                    "features": ["black", "ba", "2023-runoff"]}
                                                       what a place is like, for the forecast's tilts: columns of
                                                       baseline.csv, past contests, "logpop" or "split"
  }

Two kinds of County Watch file are kept apart. What the pack ships (baselines, key counties, feeds.json) is read
from the pack's own folder. What the night writes (pulled results, what alerts already said, the field's history)
goes under data/<pack>/countywatch, next to the servers' records, so a pack's folder never changes while it runs.
2026 is the exception: its files have always lived together in decision-desk/countywatch, and still do.

Everything a feed pulls is public and the same for every Discord server, so it is fetched once and each server acts
on it for itself (bot.py).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import packs

HERE = Path(__file__).parent
KINDS = ("results", "nyt", "bluesky", "early_vote", "countywatch")
NYT_FILES = ("house", "senate", "governor")
BLUESKY_READERS = ("Decision Desk HQ", "Dave Wasserman")       # the accounts bluesky.py knows how to read


def cfg(pack: packs.Pack | None = None) -> dict:
    return (pack or packs.active()).data.get("feeds") or {}


def declared(pack: packs.Pack | None = None) -> list[str]:
    """The kinds of feed a pack has, for telling people what will and won't run by itself."""
    return [k for k in KINDS if cfg(pack).get(k)]


# ------------------------------------------------------------------ state results files, outlets, early vote
def results_file() -> Path | None:
    name = cfg().get("results")
    return packs.active().path(name) if name else None


def nyt() -> dict:
    """{"house": url, "senate": url, "governor": url} with only the files the pack names."""
    c = cfg().get("nyt") or {}
    return {k: c[k] for k in NYT_FILES if c.get(k)}


def bluesky_accounts() -> dict[str, str]:
    """{account ID: outlet name} for the accounts whose posts are read for calls."""
    return dict((cfg().get("bluesky") or {}).get("accounts") or {})


def reading_since() -> datetime:
    """Outlets' calls count from here on, so nothing left over from a primary or a test can be read as a call. The
    pack's bluesky "since", or 5 PM on election day in the pack's timezone."""
    text = (cfg().get("bluesky") or {}).get("since")
    return datetime.fromisoformat(text) if text else packs.active().election_day().replace(hour=17)


def early_vote() -> dict:
    return dict(cfg().get("early_vote") or {})


# ------------------------------------------------------------------ County Watch
def countywatch(pack: packs.Pack | None = None) -> dict:
    """The pack's County Watch setup, or {} when it has none."""
    return dict(cfg(pack).get("countywatch") or {})


def cw_states() -> dict[str, str]:
    """{state: name} for the states County Watch models, in the pack's order."""
    places = packs.active().places
    return {st: places.get(st, st) for st in countywatch().get("states") or []}


def cw_bellwether() -> set[str]:
    return set(countywatch().get("bellwether") or [])


def cw_race(state: str) -> str:
    """The race County Watch follows in a state: GA-SEN in 2026, GA-PRES in a presidential pack."""
    return (countywatch().get("race") or "{state}-SEN").replace("{state}", state)


def cw_state_of(rid: str) -> str | None:
    """The state whose County Watch projection is about this race, if any."""
    return next((st for st in cw_states() if cw_race(st) == rid), None)


def _legacy(pack: packs.Pack) -> bool:
    """2026's County Watch files and its night's records share decision-desk/countywatch, as they always have.
    Not when records are kept somewhere else with DESK_DATA (tests, a second copy of the bot)."""
    return bool(pack.data.get("shared_files")) and not os.getenv("DESK_DATA")


def cw_folder() -> Path:
    """Where the pack's own County Watch files are."""
    pack = packs.active()
    name = countywatch().get("folder") or "countywatch"
    own = pack.dir / name
    return own if own.exists() or not pack.data.get("shared_files") else HERE / name


def cw_file(name: str) -> Path:
    """A file the pack ships: baseline.csv, key_counties.json, feeds.json."""
    return cw_folder() / name


def cw_state(name: str = "") -> Path:
    """A file the night writes: live.json, alerts.json, field_history.json. The folder is made if it isn't there."""
    pack = packs.active()
    folder = cw_folder() if _legacy(pack) else packs.data_root() / pack.id / "countywatch"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / name if name else folder


def cw_workbook() -> Path:
    """The Excel workbook County Watch writes, kept beside the pack's records (beside the code for 2026)."""
    pack = packs.active()
    name = countywatch().get("workbook") or f"County Watch {pack.election_day().year}.xlsx"
    return (HERE if _legacy(pack) else packs.data_root() / pack.id) / name


def night_window() -> tuple[datetime, datetime]:
    """When live county pulls run: from 6 PM on election day to the morning after (8 AM unless the pack says)."""
    day = packs.active().election_day()
    h, m = map(int, (countywatch().get("night_ends") or "08:00").split(":"))
    return day.replace(hour=18), (day + timedelta(days=1)).replace(hour=h, minute=m)


# ------------------------------------------------------------------ checking a pack's feeds
def problems(data: dict) -> list[str]:
    """What's wrong with a pack's "feeds" block, in words."""
    feeds = data.get("feeds")
    if feeds is None:
        return []
    if not isinstance(feeds, dict):
        return ['"feeds" has to be a block of named sources']
    out = [f'feeds: "{k}" isn\'t a kind of feed (use {", ".join(KINDS)})' for k in feeds if k not in KINDS]
    link = lambda v: isinstance(v, str) and v.startswith(("https://", "http://"))      # noqa: E731
    paper = feeds.get("nyt") or {}
    for k, v in paper.items():
        if k not in NYT_FILES:
            out.append(f'feeds.nyt: "{k}" isn\'t a file it reads (use {", ".join(NYT_FILES)})')
        elif not link(v):
            out.append(f"feeds.nyt.{k} has to be a web address")
    sky = feeds.get("bluesky") or {}
    for did, outlet in (sky.get("accounts") or {}).items():
        if not str(did).startswith("did:"):
            out.append(f'feeds.bluesky: "{did}" has to be an account\'s permanent ID (did:plc:...), not its handle')
        if outlet not in BLUESKY_READERS:
            out.append(f'feeds.bluesky: nothing reads "{outlet}" posts (it reads {", ".join(BLUESKY_READERS)})')
    if sky.get("since"):
        try:
            if datetime.fromisoformat(sky["since"]).tzinfo is None:
                out.append("feeds.bluesky.since needs a time zone, like 2028-11-07T17:00:00-05:00")
        except (TypeError, ValueError):
            out.append("feeds.bluesky.since has to look like 2028-11-07T17:00:00-05:00")
    for k, v in (feeds.get("early_vote") or {}).items():
        if not link(v):
            out.append(f"feeds.early_vote.{k} has to be a web address")
    cw = feeds.get("countywatch") or {}
    places = {**packs.STATE_NAMES, **(data.get("jurisdictions") or {})}
    for st in cw.get("states") or []:
        if st not in places:
            out.append(f'feeds.countywatch: "{st}" isn\'t a state or one of the pack\'s jurisdictions')
    for st in cw.get("bellwether") or []:
        if st not in (cw.get("states") or []):
            out.append(f'feeds.countywatch: bellwether "{st}" isn\'t one of its states')
    if cw and not cw.get("states"):
        out.append('feeds.countywatch needs its "states"')
    for st in cw.get("not_comparable") or []:
        if st not in (cw.get("states") or []):
            out.append(f'feeds.countywatch: not_comparable "{st}" isn\'t one of its states')
    past = cw.get("contests")
    if past is not None:
        if not isinstance(past, dict) or not past:
            out.append('feeds.countywatch.contests has to be a block of named contests')
            past = {}
        for name, cols in past.items():
            if not isinstance(cols, dict) or not cols.get("d") or not cols.get("r"):
                out.append(f'feeds.countywatch.contests: "{name}" needs the columns for its two sides, "d" and "r"')
        base = cw.get("baseline") or {}
        if not base.get("split"):
            out.append('feeds.countywatch: with "contests" it needs "baseline": {"split": a contest, "turnout": a contest}')
        for key in ("split", "turnout"):
            if base.get(key) and base[key] not in past:
                out.append(f'feeds.countywatch.baseline: "{base[key]}" isn\'t one of its contests')
        for b in cw.get("backtests") or []:
            if not isinstance(b, dict) or not b.get("name") or not b.get("result"):
                out.append('feeds.countywatch.backtests: each one needs a "name" and a "result"')
                continue
            if b["result"] == "live":           # the pack's own election, from its own baseline
                continue
            for key in ("result", "split", "turnout"):
                if (key != "turnout" or b.get(key)) and b.get(key) not in past:
                    out.append(f'feeds.countywatch.backtests: "{b["name"]}" names "{b.get(key)}" as its {key}, which isn\'t one of its contests')
    elif cw.get("backtests") or cw.get("baseline"):
        out.append('feeds.countywatch: "baseline" and "backtests" need "contests" to say which columns they mean')
    feats = cw.get("features")
    if feats is not None and (not isinstance(feats, list) or not all(
            isinstance(f, str) or (isinstance(f, dict) and isinstance(f.get("name"), str)
                                   and isinstance(f.get("scale", 1), (int, float)) and f.get("scale", 1) > 0) for f in feats)):
        out.append('feeds.countywatch.features has to be a list of names, or of {"name": ..., "scale": a number above 0}')
    extra = cw.get("forecast")
    if extra is not None and (not isinstance(extra, dict) or not all(isinstance(v, (int, float)) for v in extra.values())):
        out.append("feeds.countywatch.forecast has to be a block of named numbers, like {\"SE\": 0.05}")
    if cw.get("race") and "{state}" not in cw["race"]:
        out.append('feeds.countywatch.race has to say where the state goes, like "{state}-SEN"')
    return out
