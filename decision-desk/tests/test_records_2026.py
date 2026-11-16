"""The real records of election night 2026, as fixtures: the bot's database and County Watch's county counts.

They sit in tests/fixtures/2026 under names that say ".fixture", which no running bot creates or looks for, with
people replaced by numbers. Every test here works on a copy: the fixtures stay as they were on the night.
What these check is that today's code still opens a real 2026 database and reads the same night out of it.
"""
import gzip
import hashlib
import json
import re
import sqlite3
import statistics
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import capture
import clock
import cw_model as M
import desk as DESK
import feeds as FEEDS
import field as F
import night as N
import packs
import remaining as REM
import replay as RP
import scoring as S
import tenancy as T
from store import Store
from web import app as WEB

FIXTURES = RP.NIGHT_2026
AFTER = N.first_close("AK").timestamp() + 5 * 86400


@pytest.fixture
def db(tmp_path):
    """A Store on a working copy of the night's database."""
    s = Store(str(RP.fixture_db(tmp_path)))
    yield s
    s.db.close()


def results(store: Store) -> dict:
    full = store.all_full()
    return S.truth({rid: c["party"] for rid, c in full.items()}, store.procalls(),
                   called_at={rid: c["called_at"] for rid, c in full.items()})


# ------------------------------------------------------------------ the fixtures themselves
def test_every_fixture_says_it_is_one_and_holds_nobody_real():
    names = sorted(p.name for p in FIXTURES.iterdir())
    assert names == ["countywatch.fixture.json", "desk.fixture.sqlite", "field.fixture.json.gz", "modules_snapshot.json",
                     "night.fixture.json"]
    raw = (FIXTURES / "desk.fixture.sqlite").read_bytes()
    assert not re.search(rb"(?<![0-9.])[0-9]{17,20}(?![0-9.])", raw)       # a Discord ID is 17 to 20 digits
    con = sqlite3.connect(f"file:{(FIXTURES / 'desk.fixture.sqlite').as_posix()}?mode=ro", uri=True)
    people = {u for (u,) in con.execute("SELECT user_id FROM desk_votes UNION SELECT user_id FROM predictions "
                                        "UNION SELECT logged_by FROM procalls")}
    assert people == {0, 1, 2, 3, 4, 5, 6, 7}                             # 0 is the bot's own outlet reads
    hows = {json.loads(d)["how"] for (d,) in con.execute("SELECT detail FROM history")}
    assert {h for h in hows if "user" in h} == {"Called by user1 (override)", "Retracted by user1"}
    assert json.loads(con.execute("SELECT value FROM settings WHERE key='names'").fetchone()[0])["2"] == "user2"
    con.close()


def test_tests_get_a_copy_and_the_fixture_is_left_alone(tmp_path):
    before = hashlib.sha256((FIXTURES / "desk.fixture.sqlite").read_bytes()).hexdigest()
    copy = RP.fixture_db(tmp_path)
    assert copy.name == "desk.db" and copy.parent == tmp_path
    s = Store(str(copy))
    s.call("AK-SEN", "D", {"how": "a test"})
    s.save_predictions(99, {"GA-SEN": "D"}, None)
    s.db.close()
    assert hashlib.sha256((FIXTURES / "desk.fixture.sqlite").read_bytes()).hexdigest() == before


# ------------------------------------------------------------------ the running program can't mistake them for its own
def test_the_bot_refuses_to_run_on_a_fixture(monkeypatch):
    monkeypatch.setenv("DESK_DB", str(FIXTURES / "desk.fixture.sqlite"))
    with pytest.raises(packs.PackError, match="test fixture"):
        packs.db_path()


def test_nothing_the_program_writes_lands_among_the_tests(monkeypatch):
    for name in ("DESK_DB", "DESK_DATA", "DESK_CAPTURE_DIR"):
        monkeypatch.delenv(name, raising=False)
    tests = Path(__file__).parent.resolve()
    capture.start(None)
    wrote = [packs.db_path(p["id"], server) for p in packs.available() for server in (None, 111, 222)]
    wrote += [F.history_file(), capture.HERE / "captures", packs.ROOT / "ACTIVE", FEEDS.cw_state("live.json")]
    for path in wrote:
        assert tests not in Path(path).resolve().parents, path
        assert ".fixture" not in Path(path).name
    runtime_names = {"desk.db", "field_history.json", "live.json", "early.json", "alerts.json", "discord_entries.json"}
    assert not runtime_names & {p.name for p in FIXTURES.iterdir()}       # and no fixture borrows a runtime name


# ------------------------------------------------------------------ the database, read with today's code
def test_a_2026_database_opens_and_holds_the_night(db):
    count = lambda table: db.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]     # noqa: E731
    assert [count(t) for t in ("calls", "history", "desk_votes", "procalls", "predictions", "prediction_meta")] == \
        [474, 482, 363, 494, 565, 6]
    calls = db.all()
    assert S.chamber_counts("senate", calls) == {"D": 55, "R": 44, "I": 0, "uncalled": 1}
    assert S.chamber_counts("house", calls) == {"D": 256, "R": 162, "I": 0, "uncalled": 17}
    assert S.clinched("senate", calls) == "D" and S.clinched("house", calls) == "D" and calls["US-HOUSE"] == "D"
    assert db.get("MS-SEN")["party"] == "D" and db.get("MN-01") is None            # after the retractions
    assert db.setting("card_theme") == "editorial" and db.setting("autocalls") is True
    assert len(db.setting("nyt_seen")) == 412


def test_the_database_scores_as_it_did(db, night):
    entries = {uid: db.user_predictions(uid) for uid in db.predictors()}
    board = S.leaderboard(entries, results(db), None, db.setting("final_house_dem"))
    assert [(r["user_id"], r["points"], r["flips"]) for r in board] == \
        [(x["user"], x["points"], x["flips"]) for x in night["expected"]["scores"]]
    assert [r["points"] for r in board] == [124, 123, 120, 63, 60, 48]
    left = S.open_items(results(db), db.setting("final_house_dem"), entries)
    assert left["senate"] == ["AK-SEN"] and len(left["governor"]) == 15 and left["house_seats"]
    assert len(left["no_tiebreak"]) == 5 and not left["closed"]


def test_the_database_and_the_replay_fixture_tell_the_same_night(db, night, replayed):
    rows = db.db.execute("SELECT action, race_id, party, detail, ts FROM history ORDER BY id").fetchall()
    told = [{"action": a, "race": r, "party": p, "how": json.loads(d)["how"], "outlet": json.loads(d).get("outlet"), "t": t}
            for a, r, p, d, t in rows]
    assert told == night["expected"]["history"]
    assert db.all() == replayed.store.all()
    standing = sorted([u, r, k, p] for u, r, k, p in db.db.execute(
        "SELECT user_id, race_id, kind, party FROM desk_votes WHERE active = 1"))
    assert standing == replayed.standing_votes()


def test_the_desk_was_well_ahead_of_the_outlets(db):
    """What /scoreboard reads: on races the desk called itself, how long before each outlet."""
    own = {rid: {"party": c["party"], "called_at": c["called_at"]} for rid, c in db.all_full().items() if not c["outlet"]}
    pros = db.procalls()
    lead = S.desk_vs_pros(own, pros)
    med = {outlet: (len(v), round(statistics.median(v))) for outlet, v in lead["by_outlet"].items()}
    assert med["NYT"] == (74, 105) and med["Decision Desk HQ"] == (30, 134)
    assert lead["disagree"] == [] and len(lead["desk_only"]) == 36
    stats = S.caller_stats(db.all_call_votes(), pros, results(db))
    assert max(stats, key=lambda u: stats[u]["calls"]) == 6 and len(stats) == 7
    assert (stats[1]["calls"], stats[1]["wrong"], stats[2]["wrong"]) == (103, 0, 2)


def test_the_rules_carry_on_from_where_the_night_stopped(db):
    """Alaska's Senate race was never called. Calling it now behaves as it would have then."""
    roster = DESK.Roster(frozenset({1, 2, 3, 4, 6}), frozenset(range(1, 8)))
    clock.set(AFTER)
    assert DESK.cast_call_vote(db, roster, 1, "GA-SEN", "D").status == "already_called"
    standing = db.active_votes("AK-SEN", "call")
    assert standing and set(standing.values()) == {"D"} and len(standing) < 3     # votes from the night are still there
    done = None
    for user in sorted(set(roster.counted) - set(standing)):
        done = DESK.cast_call_vote(db, roster, user, "AK-SEN", "D")
        assert done.support == len(db.active_votes("AK-SEN", "call")) and done.reached == (done.support >= 3)
        if done.reached:
            break
    assert done.reached and done.how == "Called by 3 of 5 Election Junkies"
    assert S.chamber_counts("senate", db.all())["uncalled"] == 0
    assert "AK-SEN" not in S.open_items(results(db), None)["senate"]


def test_the_website_reads_a_2026_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DESK_WEB_SECRET", "test-secret")
    monkeypatch.setenv("DESK_WEB_DEV", "1")
    monkeypatch.delenv("DESK_FAKE_NOW", raising=False)
    WEB.use_store(Store(str(RP.fixture_db(tmp_path)), check_same_thread=False))
    clock.set(AFTER)
    try:
        with TestClient(WEB.app, follow_redirects=False) as site:
            board = site.get("/api/leaderboard").json()
            assert [(r["name"], r["points"]) for r in board["rows"]] == \
                [("user2", 124), ("user1", 123), ("user6", 120), ("user3", 63), ("user4", 60), ("user5", 48)]
            assert board["open_races"] == 19 and board["final_house_dem"] is None
            n = site.get("/api/night").json()
            assert (n["senate"]["counts"], n["senate"]["clinched"]) == ({"D": 55, "R": 44}, "D")
            assert (n["house"]["counts"], n["house"]["control"]) == ({"D": 256, "R": 162}, "D")
            assert n["recent"][0]["race"] == "NC-08" and len(n["recent"]) == 40
            mine = site.get("/api/entry/2").json()
            assert len(mine["picks"]) == 122 and mine["house_dem_seats"] == 244 and mine["marks"]["US-HOUSE"] is True
            assert site.get("/login?as=2&name=x").status_code == 303
            state = site.get("/api/state").json()
            assert state["open"] is False and len(state["entry"]["picks"]) == 122      # locked, as it has been since
    finally:
        WEB.use_store(None)


def test_a_2026_database_can_sit_on_the_global_board(tmp_path, monkeypatch):
    monkeypatch.setenv("DESK_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("GUILD_ID", "111")
    monkeypatch.delenv("DESK_DB", raising=False)
    T.forget()
    try:
        home = packs.db_path("2026-general", 111)
        home.write_bytes((FIXTURES / "desk.fixture.sqlite").read_bytes())
        clock.set(AFTER)
        assert T.global_board() == []
        T.set_joined(111, True)
        rows = T.global_board()
        assert len(rows) == 6 and rows[0]["name"].startswith("user") and rows[0]["server"] == "111"
        assert all(r["points"] < 124 for r in rows)          # outlet calls only: the desk's own calls don't travel
    finally:
        T.forget()


# ------------------------------------------------------------------ County Watch's records
def test_county_watch_held_every_mississippi_county():
    held = RP.load_countywatch()
    assert list(held) == ["MS"] and len(held["MS"]) == 82
    fips = {c["fips"] for c in M.load()["MS"]}
    assert set(held["MS"]) == fips                           # the same 82 counties the model's baseline has
    assert sum(c["D"] for c in held["MS"].values()) == 467_930 and sum(c["R"] for c in held["MS"].values()) == 465_356
    assert all(0 <= c["pct_in"] <= 1 for c in held["MS"].values())


def test_the_model_and_the_vote_still_out_read_the_real_mississippi_count():
    """The closest race of the night, from the counties as County Watch last had them: Colom up 2,574."""
    live = RP.load_countywatch()["MS"]
    p = M.project("MS", live)
    assert p["counted"] == 467_930 + 465_356
    b = REM.breakdown(p)
    assert (b["D"], b["R"], b["lead"]) == (467_930, 465_356, 2_574)
    assert abs(b["lead"] / b["counted"]) < 0.003             # under three tenths of a point
    assert b["left"] >= 0 and b["final_margin"] is not None and abs(b["final_margin"]) < 0.02
    head = REM.headline(b, "Colom", "Hyde-Smith")
    assert head.startswith("Count: Colom +2,574 (Colom+0.3)")
    lines = REM.lines(b, "Colom", "Hyde-Smith")
    assert lines[0].startswith("**Counted:** Colom leads by 2,574 votes")
    assert all(c["name"] for c in b["counties"])


def test_the_field_last_agreed_it_was_inside_a_point(field):
    """The field's last look at Mississippi (6:51 AM) had it a tenth of a point the other way; the counties
    County Watch held days later had Colom ahead. Both are inside the margin a desk should not call."""
    last = field.race("MS-SEN", field.snaps[-1]["ts"])
    live = RP.load_countywatch()["MS"]
    count = sum(c["D"] - c["R"] for c in live.values()) / sum(c["D"] + c["R"] for c in live.values())
    assert -0.2 < last["margin"] < 0 < count * 100 < 0.5
    assert sum(c["D"] + c["R"] for c in live.values()) >= last["votes"]


# ------------------------------------------------------------------ the field's records
def test_the_field_fixture_is_the_recording_and_still_draws(field):
    with gzip.open(FIXTURES / "field.fixture.json.gz", "rt", encoding="utf-8") as f:
        raw = json.load(f)
    assert list(raw) == ["senate"] and len(raw["senate"]) == 580
    snap, earlier = field.snaps[300], field.snaps[280]
    assert F.changed(earlier, snap)
    buf, ext = F.render(snap, "paper")
    assert ext == "png" and len(buf.getvalue()) > 20_000
