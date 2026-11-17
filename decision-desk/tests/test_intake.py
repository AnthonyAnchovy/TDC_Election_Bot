"""The data intake: the check on every county pull, the snapshot log, the pull path, sources in order, and a recorded
night replayed by the backtest. Everything here is offline: no test reaches the network or writes beside the code."""
import asyncio

import pytest

import backtest as BT
import countywatch_run as CWR
import cw_feeds as FD
import cw_model as M
import intake as IN


@pytest.fixture(autouse=True)
def private_records(monkeypatch, tmp_path):
    monkeypatch.setenv("DESK_DATA", str(tmp_path))
    monkeypatch.setenv("DESK_CAPTURE", "0")
    FD._STATUS.clear()
    IN._last.clear()
    yield
    FD._STATUS.clear()
    IN._last.clear()


def finals(st, share=1.0, swing=0.10, pct=1.0):
    out = {}
    for c in M.load()[st]:
        m = max(-0.95, min(0.95, c["m24"] + swing))
        n = round(c["w"] * share)
        d = round(n * (1 + m) / 2)
        out[c["fips"]] = {"D": d, "R": n - d, "O": 0, "pct_in": pct}
    return out


@pytest.fixture
def ga():
    return finals("GA", 0.5, pct=0.5)


@pytest.fixture
def big(ga):
    return max(ga, key=lambda f: IN._votes(ga[f]))


@pytest.fixture
def tiny(ga):
    return min(ga, key=lambda f: IN._votes(ga[f]))


# ------------------------------------------------------------------ the check
def test_a_first_pull_with_nothing_to_compare_against_goes_through(ga):
    v = IN.check("GA", ga, None)
    assert v.rejected is None and v.rows == ga


def test_an_empty_pull_is_refused(ga):
    assert "empty" in IN.check("GA", {}, ga).rejected


def test_a_pull_missing_most_counties_is_refused_from_a_feed_that_had_listed_them_all(ga):
    few = dict(list(ga.items())[:20])
    assert "only 20" in IN.check("GA", few, ga).rejected


def test_a_short_first_pull_from_a_feed_that_never_listed_every_county_is_let_through_with_a_note(ga):
    few = dict(list(ga.items())[:20])
    v = IN.check("GA", few, None)
    assert v.rejected is None and v.rows == few and "20 of the state's 159" in v.notes[-1]


def test_finished_numbers_before_the_polls_have_closed_are_taken_for_test_data():
    done = finals("GA", 1.0)
    assert "test data" in IN.check("GA", done, None, minutes_since_close=3).rejected
    assert IN.check("GA", done, None, minutes_since_close=30).rejected is None


def test_a_pull_where_many_counties_went_to_zero_is_refused(ga):
    zeros = {f: {**r, "D": 0, "R": 0} if i % 3 else r for i, (f, r) in enumerate(ga.items())}
    assert "down from" in IN.check("GA", zeros, ga).rejected


def test_a_county_that_falls_a_long_way_is_held_twice_and_believed_the_third_time(ga, big):
    fell = {**ga, big: {**ga[big], "D": ga[big]["D"] // 2}}
    held, rows = None, ga
    for n in (1, 2):
        v = IN.check("GA", fell, rows, held=held)
        assert v.rejected is None and v.rows[big] == ga[big] and "fell" in v.notes[0] and v.held[big]["seen"] == n
        held, rows = v.held, v.rows
    v = IN.check("GA", fell, rows, held=held)
    assert v.rows[big] == fell[big] and not v.held and not v.notes


def test_a_held_county_starts_its_count_again_when_the_number_comes_back_up(ga, big):
    v = IN.check("GA", ga, ga, held={big: {"why": "fell", "seen": 2}})
    assert not v.held and v.rows == ga


def test_a_small_correction_goes_straight_through(ga, big):
    small = {**ga, big: {**ga[big], "D": ga[big]["D"] - 20}}
    assert IN.check("GA", small, ga).rows[big]["D"] == ga[big]["D"] - 20


def test_a_county_far_past_its_expected_vote_is_held_and_a_missing_county_is_noted(ga, big, tiny):
    odd = {f: r for f, r in ga.items() if f != big}
    odd[tiny] = {**ga[tiny], "D": ga[tiny]["D"] + 500_000}
    v = IN.check("GA", odd, ga)
    assert v.rows[tiny] == ga[tiny] and v.rows[big] == ga[big]
    assert v.held[tiny]["why"] == "jumped" and big not in v.held
    assert any("missing" in n for n in v.notes) and any("far past" in n for n in v.notes)


def test_a_statewide_total_that_falls_is_refused_until_it_has_been_seen_enough_times_in_a_row(ga):
    low = {f: {**r, "D": r["D"] // 2, "R": r["R"] // 2} for f, r in ga.items()}
    held = None
    for n in range(1, IN.CONFIRM_TOTAL):
        v = IN.check("GA", low, ga, held=held)
        assert "fell from" in v.rejected and v.rows == ga and v.held[IN.STATE]["seen"] == n
        held = v.held
    v = IN.check("GA", low, ga, held=held)
    assert v.rejected is None and v.rows == low and not v.held


# ------------------------------------------------------------------ the snapshot log
def test_the_snapshot_log_keeps_only_what_changed_and_reads_back_as_a_night(tmp_path, ga, big, tiny):
    logs = tmp_path / "logs"
    assert IN.record("GA", ga, 100.0, "state", logs) == len(ga)
    assert IN.record("GA", ga, 130.0, "state", logs) == 0
    step2 = {**ga, big: {**ga[big], "D": ga[big]["D"] + 10}}
    del step2[tiny]
    assert IN.record("GA", step2, 160.0, "nyt", logs) == 2
    IN._last.clear()                                  # as after a restart: the log itself says where it stood
    assert IN.record("GA", step2, 190.0, "nyt", logs) == 0
    night = IN.replay("GA", logs)
    assert [t for t, _r, _m in night] == [100.0, 160.0]
    assert tiny not in night[1][1] and night[1][1][big]["D"] == ga[big]["D"] + 10
    quiet = next(f for f in ga if f not in (big, tiny))
    assert night[1][2][big] == 160.0 and night[1][2][quiet] == 100.0
    assert IN.recorded(logs) == ["GA"] and len((logs / "GA.jsonl").read_text().splitlines()) == 2


def test_setting_the_logs_aside_after_a_rehearsal_leaves_none_recorded(tmp_path, ga):
    logs = tmp_path / "logs"
    IN.record("GA", ga, 100.0, "state", logs)
    assert IN.set_aside(logs) == 1 and IN.recorded(logs) == []
    assert list(logs.glob("rehearsal-*/GA.jsonl"))


# ------------------------------------------------------------------ the pull path
def test_a_good_pull_is_applied_and_an_empty_one_leaves_the_numbers_alone(ga):
    src = [{"name": "Georgia SOS", "order": 1, "in_use": True}]
    c = CWR._apply_pulls(["GA"], [{"counties": ga, "source": "Georgia SOS", "sources": src}], [])
    assert c["states"]["GA"] == ga and c["changed"] == ["GA"] and "GA" not in c["errors"]
    c = CWR._apply_pulls(["GA"], [{"counties": {}}], [])
    assert "empty" in c["errors"]["GA"] and c["states"]["GA"] == ga and c["changed"] == []
    assert CWR.source_report(c)["GA"][0]["name"] == "Georgia SOS"


def test_a_pulled_county_that_fell_is_held_and_a_falling_total_is_taken_after_enough_pulls(ga, big):
    CWR._apply_pulls(["GA"], [{"counties": ga}], [])
    fell = {**ga, big: {**ga[big], "D": ga[big]["D"] // 2}}
    c = CWR._apply_pulls(["GA"], [{"counties": fell}], [])
    assert c["states"]["GA"][big] == ga[big] and "fell" in c["notes"]["GA"]
    assert c["held"]["GA"][big]["seen"] == 1 and "GA" not in c["errors"]
    low = {f: {**r, "D": r["D"] // 2, "R": r["R"] // 2} for f, r in ga.items()}
    for _ in range(IN.CONFIRM_TOTAL - 1):
        c = CWR._apply_pulls(["GA"], [{"counties": low}], [])
        assert "fell from" in c["errors"]["GA"] and c["states"]["GA"][big] == ga[big]
    c = CWR._apply_pulls(["GA"], [{"counties": low}], [])
    assert c["states"]["GA"] == low and "GA" not in c["errors"] and "GA" not in c["held"]


def test_a_county_a_little_behind_on_the_new_source_isnt_held_when_the_state_switches_sites(ga, big):
    CWR._apply_pulls(["GA"], [{"counties": ga, "source": "Georgia SOS"}], [])
    behind = {**ga, big: {**ga[big], "D": ga[big]["D"] - 500}}
    c = CWR._apply_pulls(["GA"], [{"counties": behind, "source": "New York Times"}], [])
    assert c["states"]["GA"][big] == behind[big] and "GA" not in c["held"] and c["using"]["GA"] == "New York Times"
    fell = {**behind, big: {**behind[big], "D": behind[big]["D"] // 2}}
    c = CWR._apply_pulls(["GA"], [{"counties": fell, "source": "New York Times"}], [])
    assert c["states"]["GA"][big] == behind[big] and c["held"]["GA"][big]["seen"] == 1


def test_accepted_pulls_are_logged_beside_the_packs_records_and_a_new_night_sets_them_aside(tmp_path, ga):
    CWR._apply_pulls(["GA"], [{"counties": ga}], [])
    assert IN.log_file("GA").parent.parent == tmp_path / "2026-general" / "countywatch"
    log = IN.replay("GA")
    assert len(log) == 1 and log[0][1] == {f: IN.unpack_row(IN.pack_row(r)) for f, r in ga.items()}
    assert "snapshot" in CWR.reset_night() and IN.recorded() == []


# ------------------------------------------------------------------ sources in order
@pytest.fixture
def sources(monkeypatch):
    clock = {"t": 1000.0}
    cfg = {"states": {
        "OH": {"format": "oh_api", "source": "State site (its own)", "url": "a", "fallback": [
            {"format": "nyt_json", "source": "New York Times (unofficial)", "url": "b"},
            {"format": "cw_json", "source": "Third", "url": "c"}]},
        "GA": {"format": "ga_item", "source": "Only one"}}}
    feed = {"a": {"39001": {"D": 60, "R": 40}}, "b": {"39001": {"D": 50, "R": 40}}, "c": {}}
    asked = []

    async def fake_pull(src, test, session):
        asked.append(src["url"])
        got = feed[src["url"]]
        if isinstance(got, Exception):
            raise got
        return {"counties": got, "url": src["url"], "changed": True}

    monkeypatch.setattr(FD.time, "time", lambda: clock["t"])
    monkeypatch.setattr(FD, "load_sources", lambda: cfg)
    monkeypatch.setattr(FD, "_pull", fake_pull)

    def pull(at):
        clock["t"] = 1000.0 + at
        asked.clear()
        return asyncio.run(FD.fetch_state("OH"))

    return cfg, feed, asked, pull


def test_a_fallback_can_be_one_source_or_a_list_and_names_drop_their_brackets(sources):
    cfg = sources[0]
    assert [FD.source_name(s) for s in FD.source_list(cfg["states"]["OH"])] == ["State site", "New York Times", "Third"]
    assert FD.has_fallback("OH") and not FD.has_fallback("GA")


def test_while_the_first_source_has_votes_the_others_are_not_asked(sources):
    _cfg, _feed, asked, pull = sources
    r = pull(0)
    assert r["source"] == "State site" and asked == ["a"]
    pull(200)
    assert asked == ["a"]


def test_a_first_source_stale_for_ten_minutes_makes_the_bot_look_at_the_others(sources):
    _cfg, feed, asked, pull = sources
    pull(0)
    pull(200)
    r = pull(400)
    assert asked == ["a", "b", "c"] and r["source"] == "State site"
    feed["b"] = {"39001": {"D": 70, "R": 50}}
    r = pull(700)
    assert r["source"] == "New York Times" and r["counties"]["39001"]["D"] == 70
    rep = {s["name"]: s for s in r["sources"]}
    assert rep["New York Times"]["in_use"] and rep["New York Times"]["changed"] == 1700.0
    assert rep["State site"]["changed"] is None


def test_the_bot_goes_back_to_the_first_source_when_it_moves_again(sources):
    _cfg, feed, asked, pull = sources
    pull(0)
    pull(200)
    pull(400)
    feed["b"] = {"39001": {"D": 70, "R": 50}}
    pull(700)
    r = pull(730)
    assert r["source"] == "New York Times" and "b" in asked
    feed["a"] = {"39001": {"D": 80, "R": 60}}
    assert pull(760)["source"] == "State site"


def test_a_blocked_source_is_left_alone_a_while_and_the_next_one_is_used(sources):
    _cfg, feed, asked, pull = sources
    pull(0)
    feed["a"] = FD.BotCheck("blocked")
    r = pull(30)
    assert r["source"] == "New York Times" and "blocked" in r["sources"][0]["error"]
    pull(60)
    assert asked[0] == "b"


def test_when_every_source_fails_the_pull_raises(sources):
    _cfg, feed, _asked, pull = sources
    feed["a"] = RuntimeError("HTTP 500")
    feed["b"] = RuntimeError("HTTP 500")
    feed["c"] = RuntimeError("HTTP 404")
    with pytest.raises(RuntimeError, match="500|404"):
        pull(0)


# ------------------------------------------------------------------ a recorded night replayed by the backtest
@pytest.fixture
def one_night(monkeypatch, tmp_path):
    rows = {st: M.load()[st] for st in ("GA", "NC", "OH")}
    fin = {st: finals(st) for st in rows}
    monkeypatch.setattr(BT, "election", lambda year: (rows, fin, {}))
    monkeypatch.setattr(BT, "early_files", lambda year: None)
    rec = tmp_path / "rec"
    order = sorted(fin["GA"], key=lambda f: IN._votes(fin["GA"][f]))
    state = {}
    for i, f in enumerate(order):                      # small counties first, one a minute
        state[f] = fin["GA"][f]
        IN.record("GA", state, 5000.0 + 60 * i, "state", rec)
    return rec


def test_the_backtest_replays_a_recorded_night_in_the_order_it_happened(one_night):
    res = BT.run((2026,), nights=3, seed=1, steps=8, snapshots=one_night)
    assert res["recorded"] == {2026: ["GA"]} and {r["st"] for r in res["rows"]} == {"GA"} and len(res["rows"]) == 9
    shares = [r["share"] for r in res["rows"]]
    assert shares == sorted(shares) and shares[0] < 0.01 and shares[-1] == pytest.approx(1)


def test_with_snapshots_switched_off_the_backtest_simulates_every_state(one_night):
    sim = BT.run((2026,), nights=2, seed=1, steps=8, snapshots=False)
    assert sim["recorded"] == {} and {r["st"] for r in sim["rows"]} == {"GA", "NC", "OH"}
