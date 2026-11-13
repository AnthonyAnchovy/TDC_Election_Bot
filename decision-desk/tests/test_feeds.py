"""Where a data pack's numbers come from: each pack names its own sources, a pack that names none fetches nothing,
and what the night writes never lands in a pack's folder.
"""
import asyncio
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

import bluesky as BSKY
import countywatch_run as CWR
import cw_feeds as CF
import cw_model as M
import feeds as FEEDS
import field as F
import night as N
import nyt_house as NYT
import packs
import results as LIVE

DESK = Path(__file__).parent.parent


@pytest.fixture
def on():
    """Switch packs inside a test, and always end back on the default."""
    yield packs.activate
    packs.activate(packs.DEFAULT)


def boot(pack_id: str, code: str, **env) -> str:
    env = {**__import__("os").environ, "DESK_PACK": pack_id, "DESK_CAPTURE": "0", "PYTHONIOENCODING": "utf-8", **env}
    out = subprocess.run([sys.executable, "-X", "utf8", "-c", "import bot, json\n" + code], capture_output=True,
                         text=True, encoding="utf-8", cwd=DESK, env=env)
    assert out.returncode == 0, out.stderr[-800:]
    return out.stdout.strip().splitlines()[-1]


# ------------------------------------------------------------------ 2026 reads what it always read
def test_2026_names_every_source_it_used():
    assert FEEDS.declared() == ["results", "nyt", "bluesky", "early_vote", "countywatch"]
    assert LIVE.supported() and LIVE.state_cfg("FL")["format"] == "fl_txt"
    assert NYT.urls()["house"].endswith("/2026-11-03/results-house.json")
    assert NYT.urls()["senate"].endswith("results-senate.json") and NYT.urls()["governor"].endswith("results-governor.json")
    assert (NYT.enough("house"), NYT.enough("senate"), NYT.enough("governor")) == (391, 31, 32)
    assert sorted(BSKY.SOURCES.values()) == ["Dave Wasserman", "Decision Desk HQ"]
    assert FEEDS.reading_since() == datetime.fromisoformat("2026-11-03T17:00:00-05:00")


def test_2026_county_watch_is_where_it_was(monkeypatch):
    monkeypatch.delenv("DESK_DATA", raising=False)
    assert list(M.STATES)[:3] == ["AK", "GA", "IA"] and len(M.STATES) == 17 and M.BELLWETHER == {"VA", "KY"}
    assert M.STATES["NC"] == "North Carolina"
    assert FEEDS.cw_folder() == DESK / "countywatch" == M.CW_DIR
    assert FEEDS.cw_state("live.json") == DESK / "countywatch" / "live.json"
    assert F.history_file() == DESK / "countywatch" / "field_history.json"
    assert CWR.workbook() == DESK / "County Watch 2026.xlsx"
    assert FEEDS.cw_race("GA") == "GA-SEN" and FEEDS.cw_state_of("GA-SEN") == "GA" and FEEDS.cw_state_of("GA-GOV") is None
    assert len(M.load()["MS"]) == 82 and "GA" in CF.feed_states()
    start, end = FEEDS.night_window()
    assert (start.day, start.hour, end.day, end.hour) == (3, 18, 4, 8)


def test_records_kept_elsewhere_take_the_nights_files_with_them(tmp_path, monkeypatch):
    """With DESK_DATA set (tests, a second copy of the bot), nothing is written beside the code."""
    monkeypatch.setenv("DESK_DATA", str(tmp_path))
    assert FEEDS.cw_folder() == DESK / "countywatch"                       # the pack's own files are still read
    assert FEEDS.cw_state("live.json") == tmp_path / "2026-general" / "countywatch" / "live.json"
    assert CWR.workbook() == tmp_path / "2026-general" / "County Watch 2026.xlsx"
    assert CF.overrides_file().parent == tmp_path / "2026-general" / "countywatch"


# ------------------------------------------------------------------ a pack that names no sources
def test_a_pack_with_no_feeds_fetches_nothing(on, tmp_path, monkeypatch):
    monkeypatch.setenv("DESK_DATA", str(tmp_path))
    on("2027-chicago-mayor")
    assert FEEDS.declared() == [] and packs.active().summary()["feeds"] == []
    assert LIVE.supported() == [] and LIVE.sources() == {"states": {}} and LIVE.page_link("IL") is None
    assert NYT.urls() == {} and BSKY.SOURCES == {}
    assert M.STATES == {} and M.BELLWETHER == set() and M.load() == {}
    assert CF.feed_states() == [] and CF.fips_for("IL", "Cook") is None
    assert CWR.scan_inbox() == [] and FEEDS.cw_state_of("CHI-MAYOR") is None
    with pytest.raises(LookupError):
        asyncio.run(NYT.fetch())
    found, problems = asyncio.run(NYT.fetch_all())
    assert (found, problems) == ({}, [])
    # 5 PM on its own election day, in its own timezone
    assert FEEDS.reading_since() == packs.active().election_day().replace(hour=17)
    assert not list((packs.active().dir).glob("countywatch*"))             # and its folder was left alone


def test_the_sources_follow_the_pack(on):
    on("2028-general")
    assert FEEDS.declared() == ["bluesky"]                                 # the accounts carry over; nothing else is known yet
    assert sorted(BSKY.SOURCES.values()) == ["Dave Wasserman", "Decision Desk HQ"]
    assert FEEDS.reading_since() == datetime.fromisoformat("2028-11-07T17:00:00-05:00")
    assert M.STATES == {} and NYT.urls() == {}
    on("2026-general")
    assert len(M.STATES) == 17 and len(M.load()["MS"]) == 82 and NYT.urls()["house"]


def test_the_morning_after_is_the_next_day_whatever_the_date(on):
    assert N.morning_after() == N.ELECTION_DAY.replace(day=4, hour=8)
    on("2028-general")                                                     # November 7: "the 4th" would be before it
    assert (N.morning_after().day, N.morning_after().hour) == (8, 8) and N.morning_after(12).hour == 12
    start, end = FEEDS.night_window()
    assert start < end and (end - start).total_seconds() == 14 * 3600


def test_a_pack_can_point_county_watch_at_another_race(tmp_path, monkeypatch):
    """A made-up presidential pack with its own County Watch folder: the states, the race followed and every file
    come from it, and the night's files go under data/."""
    data = json.loads((packs.ROOT / "2028-general" / "pack.json").read_text(encoding="utf-8"))
    data.update(id="test-pres", feeds={"countywatch": {"states": ["GA", "NC"], "race": "{state}-PRES", "bellwether": ["NC"],
                                                        "night_ends": "10:30"}})
    root = tmp_path / "packs"
    (root / "test-pres" / "countywatch").mkdir(parents=True)
    (root / "test-pres" / "pack.json").write_text(json.dumps(data), encoding="utf-8")
    rows = (DESK / "countywatch" / "baseline.csv").read_text(encoding="utf-8").splitlines()
    keep = [rows[0]] + [r for r in rows[1:] if r.split(",")[0] in ("GA", "NC")]
    (root / "test-pres" / "countywatch" / "baseline.csv").write_text("\n".join(keep), encoding="utf-8")
    (root / "test-pres" / "countywatch" / "feeds.json").write_text(json.dumps(
        {"states": {"GA": {"format": "ga_item", "contest": "President of the US"}}}), encoding="utf-8")
    monkeypatch.setattr(packs, "ROOT", root)
    monkeypatch.setenv("DESK_DATA", str(tmp_path / "data"))
    try:
        packs.activate("test-pres")
        assert M.STATES == {"GA": "Georgia", "NC": "North Carolina"} and M.BELLWETHER == {"NC"}
        assert FEEDS.cw_race("GA") == "GA-PRES" and FEEDS.cw_state_of("NC-PRES") == "NC" and FEEDS.cw_state_of("GA-SEN") is None
        assert sorted(M.load()) == ["GA", "NC"] and len(M.load()["GA"]) == 159
        assert CF.feed_states() == ["GA"] and CF.fips_for("GA", "Fulton County") == "13121"
        assert FEEDS.cw_file("baseline.csv").parent == root / "test-pres" / "countywatch"
        assert FEEDS.cw_state("live.json") == tmp_path / "data" / "test-pres" / "countywatch" / "live.json"
        assert CWR.workbook() == tmp_path / "data" / "test-pres" / "County Watch 2028.xlsx"
        assert FEEDS.night_window()[1].strftime("%d %H:%M") == "08 10:30"
        p = CWR.projection("GA", {"states": {}}, {}, {})                  # the model runs on the pack's baseline
        assert p["counted"] == 0 and p["base_m"] is not None
        CF.set_override("GA", {"contest": "President"})                    # a change made on the night
        assert CF.load_sources()["states"]["GA"]["contest"] == "President"
        assert not (root / "test-pres" / "countywatch" / "feed_overrides.json").exists()      # ...isn't written into the pack
    finally:
        monkeypatch.undo()
        packs._loaded.pop("test-pres", None)
        packs.activate(packs.DEFAULT)
    assert len(M.STATES) == 17 and CF.fips_for("MS", "Hinds County")


# ------------------------------------------------------------------ a pack's feeds are checked when it loads
@pytest.mark.parametrize("feeds, said", [
    ({"twitter": {}}, '"twitter" isn\'t a kind of feed'),
    ({"nyt": {"house": "results-house.json"}}, "feeds.nyt.house has to be a web address"),
    ({"nyt": {"mayor": "https://x.test/a.json"}}, '"mayor" isn\'t a file it reads'),
    ({"bluesky": {"accounts": {"ddhq.bsky.social": "Decision Desk HQ"}}}, "permanent ID"),
    ({"bluesky": {"accounts": {"did:plc:abc": "Some Pundit"}}}, 'nothing reads "Some Pundit" posts'),
    ({"bluesky": {"since": "2028-11-07T17:00:00"}}, "needs a time zone"),
    ({"bluesky": {"since": "election night"}}, "has to look like"),
    ({"early_vote": {"uf": "US.csv"}}, "feeds.early_vote.uf has to be a web address"),
    ({"countywatch": {"states": ["GA", "ZZ"]}}, '"ZZ" isn\'t a state'),
    ({"countywatch": {"states": ["GA"], "bellwether": ["VA"]}}, 'bellwether "VA" isn\'t one of its states'),
    ({"countywatch": {"folder": "cw"}}, 'needs its "states"'),
    ({"countywatch": {"states": ["GA"], "race": "GA-SEN"}}, "has to say where the state goes"),
    ("results_sources.json", "has to be a block"),
])
def test_a_wrong_feeds_block_is_explained(feeds, said):
    data = {"id": "x", "name": "X", "election_day": "2028-11-07", "feeds": feeds}
    assert any(said in p for p in packs.problems(data)), packs.problems(data)


def test_every_pack_in_the_repo_has_sound_feeds():
    for p in packs.available():
        data = packs.load(p["id"]).data
        assert FEEDS.problems(data) == [], p["id"]
        assert p["feeds"] == [k for k in FEEDS.KINDS if (data.get("feeds") or {}).get(k)] or set(p["feeds"]) == set(data.get("feeds") or {})


# ------------------------------------------------------------------ the bot under each pack
def test_the_bot_starts_with_no_feeds_and_says_so(tmp_path):
    got = json.loads(boot("2027-chicago-mayor", """
import asyncio
from types import SimpleNamespace
said = []
class R:
    async def send_message(self, text, ephemeral=False): said.append(text)
def use(name, kind=bot.discord.InteractionType.application_command):
    return SimpleNamespace(guild_id=1, command=SimpleNamespace(name=name), type=kind, response=R())
ok = [asyncio.run(bot._enter_server(use(n))) for n in ("counties", "call", "trend")]
print(json.dumps({"ok": ok, "said": said, "states": len(bot.CW_STATE_CHOICES), "night": bot.cw_night(),
                  "known": sorted(bot.CW_COMMANDS - {c.name for c in bot.tree.get_commands()})}))
""", DESK_DATA=str(tmp_path)))
    assert got["ok"] == [False, True, False] and got["states"] == 0 and got["known"] == []
    assert got["said"][0].startswith("**Chicago mayoral election") and "has no County Watch, so `/counties`" in got["said"][0]
    assert not (tmp_path / "2027-chicago-mayor" / "countywatch").exists()      # and nothing was made for it


def test_the_bot_reads_shared_feeds_once_for_every_server(tmp_path):
    """Two servers, one read of each outlet: each server acts on what it hasn't dealt with, and a server with
    automatic calls off is left out."""
    got = json.loads(boot("2026-general", """
import asyncio, time
from datetime import datetime, timezone
from types import SimpleNamespace
A, B, C = (SimpleNamespace(id=i, name=n) for i, n in ((1, "a"), (2, "b"), (3, "c")))
bot.client._connection._guilds = {g.id: g for g in (A, B, C)}
with bot.TEN.scope(3): bot.store.set_setting("autocalls", False)
with bot.TEN.scope(1): bot.store.set_setting("bsky_seen", ["at://x/1"])
with bot.TEN.scope(2): bot.store.set_setting("bsky_seen", ["at://x/1", "at://x/2"])
reads, acted = {"nyt": 0, "bsky": []}, []
async def nyt_fetch():
    reads["nyt"] += 1
    return {"TX-15": {"call": True, "rid": "TX-15", "party": "R", "who": "X"}}
did = next(iter(bot.BSKY.SOURCES))
async def bsky_fetch(d, known, **kw):
    reads["bsky"].append(sorted(known))
    return [{"uri": u, "did": did, "when": datetime.now(timezone.utc), "text": "hello"} for u in ("at://x/2", "at://x/3")] if d == did else []
async def take(guild, rid, party, outlet, source): acted.append([guild.id, rid, party, outlet])
bot.NYT.fetch, bot.BSKY.fetch, bot.take_auto_call = nyt_fetch, bsky_fetch, take
bot.FEEDS.reading_since = lambda: datetime(2020, 1, 1, tzinfo=timezone.utc)
bot.now = lambda: bot.N.ELECTION_DAY.replace(hour=23)
async def run():
    await bot.nyt_loop.coro()
    await bot.nyt_loop.coro()
    await bot.bluesky_loop.coro()
asyncio.run(run())
seen = {}
for g in (1, 2, 3):
    with bot.TEN.scope(g): seen[g] = [bot.store.setting("bsky_seen", []), bot.store.setting("nyt_seen", {})]
print(json.dumps({"reads": reads, "acted": acted, "seen": seen, "listening": [g.id for g in bot.listening()]}))
""", DESK_DATA=str(tmp_path), GUILD_ID="1"))
    assert got["listening"] == [1, 2]
    assert got["reads"]["nyt"] == 2                                    # once a pass, not once a server
    assert got["reads"]["bsky"] == [["at://x/1"], ["at://x/1"]]        # two accounts, each read once; only what all have seen is skipped
    assert got["acted"] == [[1, "TX-15", "R", "NYT"], [2, "TX-15", "R", "NYT"]]       # each server once, and not again on the second read
    assert got["seen"]["1"] == [["at://x/1", "at://x/2", "at://x/3"], {"TX-15": "R"}]
    assert got["seen"]["2"] == [["at://x/1", "at://x/2", "at://x/3"], {"TX-15": "R"}]
    assert got["seen"]["3"] == [[], {}]                                # automatic calls are off there
