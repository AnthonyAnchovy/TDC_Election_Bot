"""Data packs: 2026 as a pack is exactly what the hard-coded modules were, and a pack can be any election at all."""
import json
from pathlib import Path

import pytest

import competitive as CMP
import night as N
import packs
import races as R

SNAP = json.loads((Path(__file__).parent / "fixtures" / "2026" / "modules_snapshot.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def back_to_2026():
    yield
    packs.activate(packs.DEFAULT)


def write_pack(root: Path, data: dict) -> Path:
    folder = root / data["id"]
    folder.mkdir(parents=True)
    (folder / "pack.json").write_text(json.dumps(data), encoding="utf-8")
    return folder


# ------------------------------------------------------------------ 2026 is unchanged
def test_the_2026_pack_gives_the_same_races_as_the_old_module():
    """The snapshot was taken from races.py while it still had 2026 written into it."""
    assert list(R.RACES) == SNAP["RACE_ORDER"]
    for rid, old in SNAP["RACES"].items():
        assert {k: R.RACES[rid][k] for k in old} == old, rid
    for name in ("SENATE", "SENATE_HOLDOVER", "SENATE_MAJORITY", "GOVERNOR", "GOVERNOR_NOT_UP", "HOUSE_SEATS",
                 "HOUSE_TOTAL", "HOUSE_MAJORITY", "STATE_NAMES"):
        assert getattr(R, name) == SNAP[name], name
    assert sorted(R.SENATE_SPECIAL) == SNAP["SENATE_SPECIAL"]
    assert dict(R.PARTY_NAMES) == SNAP["PARTY_NAMES"]


def test_the_2026_pack_gives_the_same_ratings_and_closings():
    for name in ("RATING_OF", "HOLDER_OF", "RATING", "HOLDER", "D_BASELINE", "EARLY_WATCH", "SAFE"):
        assert getattr(CMP, name) == SNAP[name], name
    assert [list(x) for x in CMP.RATINGS] == SNAP["RATINGS"]
    assert [list(x) for x in CMP.BATTLEGROUND] == SNAP["BATTLEGROUND"]
    assert {k: list(v) for k, v in N.CLOSINGS.items()} == SNAP["CLOSINGS"]
    assert N.ELECTION_DAY.isoformat() == SNAP["ELECTION_DAY"]
    assert {st: N.first_close(st).isoformat() for st in N.CLOSINGS} == SNAP["first_close"]
    assert {st: N.last_close(st).isoformat() for st in N.CLOSINGS} == SNAP["last_close"]
    assert [[t.isoformat(), sts] for t, sts in N.waves()] == SNAP["waves"]


def test_every_shipped_pack_loads_and_has_no_problems():
    found = packs.available()
    assert packs.DEFAULT in [p["id"] for p in found]
    for p in found:
        pack = packs.load(p["id"], fresh=True)
        assert packs.problems(pack.data) == [] and p["races"] == len(pack.races) > 0
        packs.activate(p["id"])                 # every module can refill from it without falling over
        assert R.RACES is pack.races
        for rid in pack.races:
            assert R.search(rid, 1) == [rid] and R.chamber_name(rid)


# ------------------------------------------------------------------ any election
MAYOR = {
    "id": "test-mayor", "name": "A city's mayoral election", "election_day": "2027-02-23", "timezone": "America/Chicago",
    "jurisdictions": {"SPR": "Springfield"},
    "options": {},
    "races": [{"id": "SPR-MAYOR", "type": "mayor", "jurisdiction": "SPR", "level": "local", "holder": "QUIMBY",
               "options": [{"key": "quimby", "name": "Joe Quimby"}, {"key": "terwilliger", "name": "Bob Terwilliger"},
                           {"key": "hutz", "name": "Lionel Hutz", "color": "#333333"}]}],
    "closings": {"SPR": ["19:00", "19:00"]},
    "game": {"categories": [{"key": "mayor", "name": "Mayor", "points": 10, "required": True, "races": {"type": "mayor"}}]},
}


def test_a_pack_can_be_one_nonpartisan_contest(tmp_path, monkeypatch):
    monkeypatch.setattr(packs, "ROOT", tmp_path)
    write_pack(tmp_path, MAYOR)
    pack = packs.activate("test-mayor")
    assert list(R.RACES) == ["SPR-MAYOR"]
    race = R.RACES["SPR-MAYOR"]
    assert race["label"] == "Springfield Mayor" and race["state"] == "SPR" and race["holder"] == "QUIMBY"
    assert [o["key"] for o in race["options"]] == ["QUIMBY", "TERWILLIGER", "HUTZ"]
    assert race["options"][2]["color"] == "#333333" and race["options"][0]["color"] == packs.PALETTE[0]
    assert R.STATE_NAMES["SPR"] == "Springfield" and R.chamber("SPR-MAYOR") == "mayor"
    assert R.search("springfield mayor") == ["SPR-MAYOR"] and R.normalize("spr mayor") == "SPR-MAYOR"
    assert (R.SENATE, R.SENATE_MAJORITY, R.HOUSE_TOTAL, CMP.RATINGS, CMP.SAFE) == ({}, 0, 0, [], {})
    assert N.ELECTION_DAY.isoformat() == "2027-02-23T00:00:00-06:00"
    assert N.first_close("SPR").hour == 19 and N.first_anywhere() == N.first_close("SPR")
    assert R.PARTY_NAMES["QUIMBY"] == "Quimby"          # no party table, so a key reads as itself
    assert pack.summary()["types"] == {"mayor": 1}


def test_races_can_carry_weight_toward_a_body(tmp_path, monkeypatch):
    """A presidential race: each state is a race worth its electoral votes, adding up to 270."""
    monkeypatch.setattr(packs, "ROOT", tmp_path)
    write_pack(tmp_path, {
        "id": "test-president", "name": "President", "election_day": "2028-11-07",
        "bodies": {"electoral_college": {"name": "Electoral College", "seats": 538, "majority": 270, "control": "US-PRES"}},
        "races": [{"id": "CA-PRES", "type": "president", "jurisdiction": "CA", "body": "electoral_college", "weight": 54},
                  {"id": "WY-PRES", "type": "president", "jurisdiction": "WY", "body": "electoral_college", "weight": 3},
                  {"id": "US-PRES", "type": "control", "body": "electoral_college", "label": "The presidency"}]})
    packs.activate("test-president")
    assert (R.RACES["CA-PRES"]["weight"], R.RACES["CA-PRES"]["body"], R.RACES["CA-PRES"]["label"]) == (54, "electoral_college", "California President")
    assert list(R.RACES)[-1] == "US-PRES" and R.chamber("US-PRES") == "electoral_college"
    assert R.chamber_name("US-PRES") == "Electoral College"
    assert R.normalize("electoral_college") == "US-PRES"
    assert N.first_close("CA").hour == 20               # no closings in the pack: the 8 PM default


def test_switching_packs_and_back(tmp_path, monkeypatch):
    real = packs.ROOT
    write_pack(tmp_path, MAYOR)
    monkeypatch.setattr(packs, "folder_of", lambda pid: (tmp_path if pid == "test-mayor" else real) / pid)
    assert len(R.RACES) == 508
    with packs.using("test-mayor"):
        assert len(R.RACES) == 1 and packs.active().id == "test-mayor"
    assert len(R.RACES) == 508 and packs.active().id == packs.DEFAULT and CMP.D_BASELINE == 223


@pytest.mark.parametrize("change, problem", [
    ({"id": "Bad ID"}, "lower-case"),
    ({"election_day": "Nov 7"}, "has to look like"),
    ({"races": [{"id": "X-1", "type": "mayor"}]}, 'needs a "label" or a "jurisdiction"'),
    ({"races": [{"id": "X-1", "type": "mayor", "jurisdiction": "ZZ"}]}, "isn't a state or one of the pack's jurisdictions"),
    ({"races": MAYOR["races"] * 2}, "listed twice"),
    ({"races": [{**MAYOR["races"][0], "options": [{"key": "a", "name": "A"}]}]}, "at least two options"),
    ({"races": [{**MAYOR["races"][0], "holder": "NOBODY"}]}, "isn't one of its options"),
    ({"races": [{**MAYOR["races"][0], "body": "council"}]}, 'no body called "council"'),
    ({"bodies": {"council": {"control": "SPR-COUNCIL"}}}, "control race SPR-COUNCIL isn't in the races"),
    ({"house_seats": {"ZZ": 3}}, '"ZZ" isn\'t a state'),
    ({"game": {"categories": [{"key": "a", "races": {}}, {"key": "a", "races": {}}]}}, "its own key"),
])
def test_a_broken_pack_says_what_is_wrong(change, problem):
    assert any(problem in p for p in packs.problems({**MAYOR, **change})), packs.problems({**MAYOR, **change})


def test_a_broken_pack_is_refused_with_every_problem_listed(tmp_path, monkeypatch):
    monkeypatch.setattr(packs, "ROOT", tmp_path)
    write_pack(tmp_path, {**MAYOR, "id": "broken", "election_day": "soon", "races": MAYOR["races"] * 2})
    with pytest.raises(packs.PackError) as err:
        packs.load("broken")
    assert "has to look like" in str(err.value) and "listed twice" in str(err.value)
    with pytest.raises(packs.PackError, match="no data pack called"):
        packs.load("nowhere")
    assert packs.available() == []                      # a broken pack is left out of the list, not fatal


# ------------------------------------------------------------------ where records live
def test_each_pack_and_each_server_has_its_own_database(tmp_path, monkeypatch):
    monkeypatch.setattr(packs, "DATA", tmp_path / "data")
    monkeypatch.setattr(packs, "HERE", tmp_path)        # no legacy desk.db here
    monkeypatch.delenv("DESK_DB", raising=False)
    monkeypatch.setenv("GUILD_ID", "111")
    a = packs.db_path("2026-general", 111)
    b = packs.db_path("2026-general", 222)
    c = packs.db_path("2028-general", 111)
    assert len({a, b, c}) == 3 and a == tmp_path / "data" / "2026-general" / "111" / "desk.db"
    assert packs.db_path("2026-general") == a           # no server given: the home server's
    for p in (a, b):
        p.write_bytes(b"")
    assert packs.servers("2026-general") == ["111", "222"] and packs.servers("2028-general") == []


def test_the_first_servers_2026_records_stay_where_they_were(tmp_path, monkeypatch):
    monkeypatch.setattr(packs, "DATA", tmp_path / "data")
    monkeypatch.setattr(packs, "HERE", tmp_path)
    monkeypatch.delenv("DESK_DB", raising=False)
    monkeypatch.setenv("GUILD_ID", "111")
    (tmp_path / "desk.db").write_bytes(b"")
    assert packs.db_path("2026-general", 111) == tmp_path / "desk.db"
    assert packs.db_path("2026-general", 222) != tmp_path / "desk.db"
    assert packs.servers("2026-general") == ["111"]
    monkeypatch.setenv("DESK_DB", str(tmp_path / "elsewhere.db"))
    assert packs.db_path("2026-general", 222) == tmp_path / "elsewhere.db"


def test_the_starting_pack_comes_from_the_environment_or_the_active_file(tmp_path, monkeypatch):
    monkeypatch.setattr(packs, "ROOT", tmp_path)
    monkeypatch.delenv("DESK_PACK", raising=False)
    assert packs.default_id() == packs.DEFAULT
    write_pack(tmp_path, MAYOR)
    packs.set_default("test-mayor")
    assert packs.default_id() == "test-mayor"
    monkeypatch.setenv("DESK_PACK", "something-else")
    assert packs.default_id() == "something-else"


def test_modules_can_be_imported_in_any_order(tmp_path):
    """night.py once fell over when it was imported after a pack was already active, which is how the bot starts."""
    import subprocess
    import sys
    code = "import packs; packs.activate(); import night, competitive, races, scoring; print(len(races.RACES), night.first_close('GA').hour)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=Path(__file__).parent.parent)
    assert out.returncode == 0 and out.stdout.split() == ["508", "19"], out.stderr[-400:]
