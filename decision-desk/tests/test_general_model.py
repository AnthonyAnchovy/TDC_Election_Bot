"""The forecast for elections unlike 2026's: features a pack chooses, learning the night's noise, and races with more than
two candidates (cw_multi). Offline; nothing here writes beside the code."""
import math
import random

import pytest

import backtest as BT
import cw_forecast as CF
import cw_model as M
import cw_multi as MU
import feeds as FEEDS
import intake as IN


def uniform(st, swing=0.10):
    """Every county of a state finished at a result `swing` bluer than its baseline."""
    out = {}
    for c in M.load()[st]:
        m = max(-0.95, min(0.95, c["m24"] + swing))
        n = round(c["w"])
        d = round(n * (1 + m) / 2)
        out[c["fips"]] = {"D": d, "R": n - d, "O": 0, "pct_in": 1.0}
    return out


def projections(live):
    return {st: M.project(st, live.get(st, {})) for st in M.STATES}


@pytest.fixture
def pack_block(monkeypatch):
    """Lets a test change the active pack's County Watch block, and puts everything back afterwards."""
    block = dict(FEEDS.countywatch())
    monkeypatch.setattr(FEEDS, "countywatch", lambda: block)
    M._CACHE.clear()
    CF._pack_settings(None)
    yield block
    monkeypatch.undo()
    M._CACHE.clear()
    CF._pack_settings(None)


# ------------------------------------------------------------------ features a pack chooses
def test_the_default_five_features_are_scaled_so_each_has_a_spread_of_about_one_within_states():
    states = M.load()
    feats = CF._features(states)
    names = [n for n, _s in CF.features()]
    assert len(names) == 5
    total = sum(r["w"] for rows in states.values() for r in rows)
    for i, name in enumerate(names):
        var = sum(r["w"] * feats[st][r["fips"]][i] ** 2 for st, rows in states.items() for r in rows) / total
        assert var == pytest.approx(1.0, abs=0.01), name


def test_a_pack_can_name_a_column_a_past_contest_and_a_hand_scaled_feature(pack_block):
    pack_block["features"] = ["black", "2022", {"name": "logpop", "scale": 0.6}]
    CF._pack_settings(None)
    assert CF.features() == (("black", None), ("2022", None), ("logpop", 0.6))
    c = M.load()["GA"][0]
    assert CF._value(c, "black") == c["black"]
    assert CF._value(c, "2022") == pytest.approx(M.m2(*c["past"]["2022"][:2]))
    feats = CF._features({"GA": M.load()["GA"]})["GA"]
    assert all(len(x) == 3 for x in feats.values())


def test_a_feature_that_matches_nothing_is_zero_everywhere():
    feats = CF._features({"GA": M.load()["GA"]}, (("no such thing", None),))["GA"]
    assert all(x == [0.0] for x in feats.values())


def test_a_bad_list_of_features_is_rejected_and_a_good_one_is_not():
    bad = {"feeds": {"countywatch": {"states": ["GA"], "features": [{"name": "black", "scale": 0}]}}}
    assert any("features has to be a list" in p for p in FEEDS.problems(bad))
    bad["feeds"]["countywatch"]["features"] = "black"
    assert any("features has to be a list" in p for p in FEEDS.problems(bad))
    good = {"feeds": {"countywatch": {"states": ["GA"], "features": ["black", {"name": "logpop", "scale": 0.6}]}}}
    assert FEEDS.problems(good) == []


# ------------------------------------------------------------------ learning the night's noise
def rough_states(sd, states=("GA", "TX", "OH")):
    out = {}
    for st in states:
        out[st] = BT.roughen({st: uniform(st)}, sd, random.Random(1))[st]
        for row in out[st].values():
            row["pct_in"] = 1.0
    return out


def test_places_that_stray_about_as_far_as_the_setting_expects_read_near_one():
    fc = CF.forecast(projections(rough_states(0.04)))
    assert 0.8 < fc["GA"]["noise"] < 1.5


def test_a_state_finished_at_exactly_a_uniform_swing_reads_at_the_lowest_the_formula_gives():
    """Nothing strays at all, so the reading falls toward its floor (the starting belief counts for 12 places)."""
    fc = CF.forecast(projections({"GA": uniform("GA"), "TX": uniform("TX")}))
    assert fc["GA"]["noise"] < 0.7


def test_places_that_stray_from_their_state_show_more_noise_and_a_wider_range():
    live = rough_states(0.06)
    live["GA"] = dict(list(live["GA"].items())[: len(live["GA"]) // 2])          # Georgia is half counted, the rest still out
    projs = projections(live)
    learning, fixed = CF.forecast(projs)["GA"], CF.forecast(projs, None, {"LEARN": False})["GA"]
    assert learning["noise"] > 1.5
    assert learning["sd"] > fixed["sd"]


def test_the_spread_between_states_is_never_learned_below_its_starting_setting():
    fc = CF.forecast(projections({st: uniform(st, 0.12) for st in ("KY", "VA")}))
    spreads = [f["prior"]["state_spread"] for f in fc.values() if f.get("prior")]
    assert spreads and all(s >= CF.HP["SS"] - 1e-12 for s in spreads)


def test_the_stress_report_scores_every_case_with_the_settings_fixed_and_learning():
    rows = BT.stress_report(1, years=(2024,))
    assert [r["case"] for r in rows] == [c[0] for c in BT.STRESS] and len(rows) == 6
    for r in rows:
        assert set(r) == {"case", "fixed", "learning"}
        assert r["fixed"]["miss"] > 0 and 0 <= r["learning"]["in_range"] <= 1


# ------------------------------------------------------------------ races with more than two candidates
@pytest.fixture(scope="module")
def city():
    places = M.load()["GA"]
    votes = BT.made_up_city(places, random.Random(3))
    return places, votes


def rows_of(votes, share=1.0, done=True):
    return {f: {"v": {k: round(n * share) for k, n in v.items()}, "pct_in": share, **({"complete": True} if done else {})}
            for f, v in votes.items()}


def test_with_every_place_in_the_shares_equal_the_count_and_the_winner_is_certain(city):
    places, votes = city
    keys = BT.MULTI_KEYS
    totals = {k: sum(v[k] for v in votes.values()) for k in keys}
    everyone = sum(totals.values())
    fc = MU.forecast("GA", rows_of(votes), keys, places=places, draws=400)
    for k in keys:
        assert fc["options"][k]["share"] == pytest.approx(totals[k] / everyone, abs=0.01)
    winner = max(totals, key=totals.get)
    assert fc["options"][winner]["first"] == pytest.approx(1.0) and MU.settled(fc)["first"] == winner


def test_with_nothing_in_the_shares_follow_the_poll_and_the_forecast_says_it_is_a_guess(city):
    places, _votes = city
    poll = {"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1}
    fc = MU.forecast("GA", {}, BT.MULTI_KEYS, polls=poll, places=places, draws=400)
    assert fc["stage"] == "prior" and fc["counted"] == 0
    assert sorted(fc["options"], key=lambda k: -fc["options"][k]["share"]) == ["A", "B", "C", "D"]
    for k, p in poll.items():
        assert fc["options"][k]["share"] == pytest.approx(p, abs=0.05)
    assert MU.settled(fc)["first"] is None


def test_shares_add_to_one_at_every_stage_and_ranges_stay_above_the_votes_already_counted(city):
    places, votes = city
    for share in (0.0, 0.3, 0.7, 1.0):
        live = {} if share == 0 else rows_of(dict(list(votes.items())[: int(len(votes) * share)]), 1.0, False)
        fc = MU.forecast("GA", live, BT.MULTI_KEYS, places=places, draws=400)
        assert sum(o["share"] for o in fc["options"].values()) == pytest.approx(1.0)
        for o in fc["options"].values():
            assert 0 <= o["lo"] <= o["share"] + 0.02 and o["hi"] >= o["share"] - 0.02
            assert o["lo"] >= o["counted"] / fc["expected"] * 0.9 - 1e-9


def test_a_place_where_one_candidates_count_fell_is_held_at_its_last_good_numbers(city):
    _places, votes = city
    prev = rows_of(votes, 0.5, False)
    big = max(prev, key=lambda f: IN._votes(prev[f]))
    now = {**prev, big: {**prev[big], "v": {**prev[big]["v"], "A": prev[big]["v"]["A"] // 3}}}
    v = IN.check("GA", now, prev)
    assert v.rejected is None and v.rows[big] == prev[big] and big in v.held


def test_many_candidate_rows_survive_the_snapshot_log_unchanged(tmp_path, city):
    _places, votes = city
    rows = rows_of(votes, 0.5, False)
    IN._last.clear()
    assert IN.record("CHI", rows, 100.0, "state", tmp_path) == len(rows)
    night = IN.replay("CHI", tmp_path)
    IN._last.clear()
    assert len(night) == 1 and night[0][1] == rows


def test_the_many_candidate_report_runs_over_made_up_races():
    rep = BT.multi_report(1, steps=3)
    assert rep["cities"] > 0 and rep["table"] and rep["calls"]["wrong"] <= rep["calls"]["made"]
    assert all(r["n"] > 0 for r in rep["table"])
