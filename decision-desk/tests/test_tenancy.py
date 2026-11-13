"""Many servers on one bot: separate records, and a global scoreboard only for those who join it."""
import pytest

import clock
import desk as DESK
import night as N
import packs
import races as R
import tenancy as T

AFTER = N.first_close("AK").timestamp() + 86400         # every poll in the country has closed


@pytest.fixture(autouse=True)
def two_servers(tmp_path, monkeypatch):
    monkeypatch.setattr(packs, "DATA", tmp_path / "data")
    monkeypatch.setattr(packs, "HERE", tmp_path)
    monkeypatch.delenv("DESK_DB", raising=False)
    monkeypatch.setenv("GUILD_ID", "111")
    T.forget()
    clock.set(AFTER)
    yield
    T.forget()


def senate(d_seats):
    return {rid: ("D" if i < d_seats else "R") for i, rid in enumerate(sorted(R.races_of("senate")))}


def test_each_server_has_its_own_desk():
    roster = DESK.Roster(frozenset({1, 2, 3}), frozenset({1, 2, 3}))
    with T.scope(111):
        DESK.realcall(T.store, 1, "Ann", "GA-SEN", "D")
        T.store.save_predictions(1, {"GA-SEN": "D"}, None)
    with T.scope(222):
        assert T.store.get("GA-SEN") is None and T.store.predictors() == []
        assert DESK.cast_call_vote(T.store, roster, 1, "GA-SEN", "R").status == "recorded"     # same person, other server
        DESK.realcall(T.store, 2, "Bo", "GA-SEN", "R")
    assert T.store_for(111).get("GA-SEN")["party"] == "D" and T.store_for(222).get("GA-SEN")["party"] == "R"
    assert T.store_for(111).all_call_votes()[0][0] == 1 and len(T.store_for(222).all_call_votes()) == 2


def test_outside_any_scope_the_store_is_the_home_servers():
    T.store.set_setting("x", 1)
    assert T.store_for(111).setting("x") == 1 and T.store_for(222).setting("x") is None
    with T.scope(222):
        assert T.store.setting("x") is None
        with T.scope(111):
            assert T.store.setting("x") == 1
        assert T.store.setting("x") is None
    assert T.store.setting("x") == 1
    assert T.enter(222) is T.store_for(222) and T.current() is T.store_for(222)


def test_each_pack_has_its_own_records_too():
    T.store_for(111, "2026-general").set_setting("x", "midterms")
    assert T.store_for(111, "2028-general").setting("x") is None


def test_nothing_leaves_a_server_that_has_not_joined():
    T.store_for(111).save_predictions(1, senate(20), None)
    T.store_for(222).save_predictions(2, senate(10), None)
    assert T.global_board() == [] and T.joined_servers() == []
    T.set_joined(111, True)
    assert [r["user_id"] for r in T.global_board()] == [1]
    T.set_joined(222, True)
    assert sorted(r["user_id"] for r in T.global_board()) == [1, 2]
    T.set_joined(111, False)
    assert [r["user_id"] for r in T.global_board()] == [2]


def test_a_person_can_stay_off_the_global_board():
    for sv in (111, 222):
        T.set_joined(sv, True)
    T.store_for(111).save_predictions(1, senate(20), None)
    T.store_for(111).save_predictions(3, senate(5), None)
    T.set_opted_out(111, 3, True)
    assert T.opted_out(111, 3) and not T.opted_out(111, 1)
    assert [r["user_id"] for r in T.global_board()] == [1]
    T.set_opted_out(111, 3, False)
    assert sorted(r["user_id"] for r in T.global_board()) == [1, 3]


def test_the_global_board_scores_on_shared_results_not_any_servers_own_calls():
    for sv in (111, 222):
        T.set_joined(sv, True)
    T.store_for(111).set_setting("names", {"1": "Ann"})
    T.store_for(111).save_predictions(1, {"GA-SEN": "D", "TX-SEN": "D"}, None)
    T.store_for(222).save_predictions(2, {"GA-SEN": "R", "TX-SEN": "R"}, None)
    T.store_for(222).call("GA-SEN", "R", {"how": "this server's desk"})       # an opinion: stays home
    assert all(r["points"] == 0 for r in T.global_board())
    T.store_for(222).procall("GA-SEN", "AP", "D", 0)                            # an outlet's call: shared
    T.store_for(111).set_setting("final_results", {"TX-SEN": "R"})             # a final result: shared
    assert T.global_results() == {"GA-SEN": "D", "TX-SEN": "R"}
    rows = T.global_board()
    assert [(r["name"], r["server"], r["points"]) for r in rows] == [("Ann", "111", 1), ("user 2", "222", 1)]
    T.store_for(333).procall("GA-SEN", "AP", "R", 0)                            # a server that hasn't joined: ignored
    assert T.global_results()["GA-SEN"] == "D"


def test_someone_in_two_joined_servers_appears_once_with_their_latest_entry():
    for sv in (111, 222):
        T.set_joined(sv, True)
    clock.set(AFTER)
    T.store_for(111).save_predictions(1, {"GA-SEN": "D"}, None)
    clock.set(AFTER + 60)
    T.store_for(222).save_predictions(1, {"GA-SEN": "R"}, None)
    rows = T.global_board()
    assert len(rows) == 1 and rows[0]["server"] == "222"
