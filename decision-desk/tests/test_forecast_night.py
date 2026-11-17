"""How the forecast reads an election night: sizing unfinished counties, when a county is finished, the outlier-resistant
fit, the limit on early-vote lean, messy precinct counts in the replay, and the older elections. Offline."""
import random

import pytest

import backtest as BT
import cw_forecast as CF
import cw_model as M
import cw_multi as MU

HP = CF.settings()


def county(**kw):
    return {"exp_v": 1000.0, "pct_in": None, "idle": 0.0, **kw}


# ------------------------------------------------------------------ sizing a county that is still counting
def test_a_county_is_sized_between_its_turnout_reading_and_its_precinct_reading():
    got, doubt = CF._size(county(pct_in=0.9), 450, 0.1, HP)          # precincts say 500, turnout says 1000
    assert 500 < got < 1000 and doubt > 0


def test_a_county_is_never_sized_below_the_votes_already_counted():
    for pct in (None, 0.3, 0.9, 0.999):
        got, _doubt = CF._size(county(pct_in=pct), 2000, 0.1, HP)
        assert got >= 2000


def test_a_county_with_no_precinct_count_past_its_expected_vote_still_expects_a_little_more():
    got, doubt = CF._size(county(), 1100, 0.1, HP)
    assert got > 1100 and doubt > 0
    far, _ = CF._size(county(), 3000, 0.1, HP)
    assert far == pytest.approx(3000, rel=0.02)         # far past it, the allowance is small


def test_a_county_with_nothing_to_size_it_by_is_left_to_the_old_estimate():
    assert CF._size(county(idle=None), 500, 0.1, HP) is None
    assert CF._size({"pct_in": 0.5}, 500, 0.1, HP) is None


# ------------------------------------------------------------------ when a county is finished
def row(n, exp, **kw):
    return {"D": n // 2, "R": n - n // 2, "exp2": exp, "complete": False, **kw}


def test_a_county_with_no_precinct_count_is_not_finished_while_its_count_is_still_moving_even_past_its_expected_vote():
    assert not CF.finished(row(1200, 1000, idle=10.0))
    assert not CF.finished(row(1200, 1000, idle=HP["FIN_IDLE"] - 1))


def test_a_county_with_no_precinct_count_is_finished_once_the_count_has_stopped_with_most_of_the_vote_in():
    assert CF.finished(row(1200, 1000, idle=HP["FIN_IDLE"] + 1))
    assert not CF.finished(row(300, 1000, idle=HP["FIN_IDLE"] + 1))


def test_a_county_with_every_precinct_in_and_most_of_its_vote_is_finished():
    assert CF.finished(row(1000, 1000, pct_in=1.0))


# ------------------------------------------------------------------ the forecast's range and national tilts
def half_counted_georgia():
    rows = M.load()["GA"]
    live = {}
    for c in rows[: len(rows) // 2]:
        n = round(c["w"])
        d = round(n * (1 + c["m24"] + 0.1) / 2)
        live[c["fips"]] = {"D": d, "R": n - d, "O": 0, "pct_in": 1.0}
    projs = {st: M.project(st, live if st == "GA" else {}) for st in M.STATES}
    return projs


def test_the_forecast_says_what_its_range_is_made_of_in_four_parts_that_are_never_negative():
    ga = CF.forecast(half_counted_georgia())["GA"]
    assert set(ga["sd_parts"]) == {"fit", "counties", "size", "allowance"}
    assert all(v >= 0 for v in ga["sd_parts"].values())
    assert set(ga["national_tilts"]) == {n for n, _s in CF.features()}


# ------------------------------------------------------------------ the outlier-resistant fit
def line(wild: bool):
    obs = [([1.0, x / 10 - 1.5], 0.02 + 0.01 * (x / 10 - 1.5), 0.0004) for x in range(30)]
    if wild:
        obs.append(([1.0, 1.4], 0.9, 0.0004))
    return obs


MEAN0, SD0 = [0.0, 0.0], [0.1, 0.1]


def test_the_outlier_resistant_fit_with_a_limit_of_zero_is_the_plain_fit():
    obs = line(True)
    assert CF._robust(obs, MEAN0, SD0, 0) == CF._posterior(obs, MEAN0, SD0)


def test_one_wild_place_barely_moves_the_outlier_resistant_fit_but_moves_the_plain_one_a_lot():
    clean = CF._posterior(line(False), MEAN0, SD0)[0]
    plain = CF._posterior(line(True), MEAN0, SD0)[0]
    robust = CF._robust(line(True), MEAN0, SD0, HP["ROBUST"])[0]
    moved = lambda m: max(abs(a - b) for a, b in zip(m, clean))      # noqa: E731
    assert moved(plain) > 0.03 and moved(robust) < moved(plain) / 5


# ------------------------------------------------------------------ the limit on early-vote lean
def test_a_states_early_vote_lean_is_held_within_its_limit():
    rows = M.load()["GA"]
    live = {}
    for i, c in enumerate(rows[:60]):
        n = round(c["w"] * (1.0 if i % 2 else 0.2))                    # half finished, half just begun and far too blue
        d = round(n * (1 + (c["m24"] if i % 2 else 0.95)) / 2)
        live[c["fips"]] = {"D": d, "R": n - d, "O": 0, "pct_in": 1.0 if i % 2 else 0.2}
    projs = {st: M.project(st, live if st == "GA" else {}) for st in M.STATES}
    assert abs(CF.forecast(projs)["GA"]["early_lean"]) <= CF.HP["LEAN_MAX"] + 1e-4
    assert abs(CF.forecast(projs, None, {"LEAN_MAX": 0.05})["GA"]["early_lean"]) <= 0.05 + 1e-4


# ------------------------------------------------------------------ messier simulated precinct counts
def test_a_county_can_read_zero_percent_of_precincts_in_with_votes_counted_when_its_state_posts_early_vote_first():
    plan = {"early": 0.5}
    assert BT.MESSY and BT.precincts_in(plan, {"bend": 1.0}, 0.4) == 0.0
    assert 0 < BT.precincts_in(plan, {"bend": 1.0}, 0.8) < 0.8 + 1e-9


def test_with_the_tidy_setting_the_share_of_precincts_is_the_share_of_votes(monkeypatch):
    monkeypatch.setattr(BT, "MESSY", False)
    assert BT.precincts_in({"early": 0.5}, {"bend": 2.0}, 0.4) == 0.4


def test_about_half_of_simulated_states_post_an_early_block_of_30_to_65_percent_first():
    places = {st: M.load()[st] for st in ("GA", "TX", "OH", "NC", "MI", "VA", "KY", "SC")}
    plans = [BT.night(places, {}, random.Random(s)) for s in range(6)]
    early = [pl["early"] for p in plans for pl in p.values()]
    assert any(e == 0.0 for e in early) and any(0.30 <= e <= 0.65 for e in early)
    assert all(e == 0.0 or 0.30 <= e <= 0.65 for e in early)


# ------------------------------------------------------------------ races with many candidates: the leader against each rival
def fake(ahead, weight=1.0):
    options = {k: {"first": 1.0 if k == "A" else 0.0, "top_two": 1.0, "data_weight": weight} for k in "ABC"}
    return {"stage": "live", "leader": "A", "options": options, "runoff": 0.0, "outright": 1.0,
            "head_to_head": {k: {"ahead": ahead, "data_weight": weight} for k in "BC"}}


def test_settled_names_no_first_place_while_the_leader_may_still_lose_to_one_rival():
    assert MU.settled(fake(0.999))["first"] == "A"
    unsure = fake(0.999)
    unsure["head_to_head"]["C"]["ahead"] = 0.9
    assert MU.settled(unsure)["first"] is None


def test_a_many_candidate_forecast_carries_the_leader_against_every_rival():
    places = M.load()["GA"]
    votes = BT.made_up_city(places, random.Random(5))
    live = {f: {"v": {k: round(n * 0.6) for k, n in v.items()}, "pct_in": 0.6} for f, v in votes.items()}
    fc = MU.forecast("GA", live, BT.MULTI_KEYS, places=places, draws=300)
    assert set(fc["head_to_head"]) == set(BT.MULTI_KEYS) - {fc["leader"]}
    assert all(0 <= h["ahead"] <= 1 for h in fc["head_to_head"].values())


# ------------------------------------------------------------------ the five elections
def test_the_backtest_replays_five_elections_including_2018_and_2020():
    assert {2018, 2020, 2022, 2024, 2026} <= set(BT.all_years())


def test_the_2018_election_is_built_from_the_2016_columns_and_has_no_polls():
    rows, finals, polls = BT.election(2018)
    assert polls == {} and rows and set(rows) == set(finals)
    c = next(iter(rows.values()))[0]
    assert c["d24"] == c["past"]["2016-pres"][0] and c["past"]["2016-pres"][0] > 0
