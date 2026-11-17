"""The pre-election model (cw_pre) and its leave-one-year-out backtest. Offline, on made-up races."""
import copy
import random

import cw_forecast
import cw_pre as P
import pre_backtest as B

YEARS = [2002, 2004, 2006, 2008, 2010, 2012, 2014, 2016, 2018, 2020]
HP = {"MIN_CELL": 8}


def made_up(seed=1):
    """Ten years of Senate, governor and presidential races. Polls miss by more when few of them; ratings and
    the lean carry some signal; each year has its own shared miss."""
    rnd = random.Random(seed)
    rows, ratings = [], {}
    for y in YEARS:
        shared = rnd.gauss(0, 2.5)
        gb = rnd.gauss(0, 4)
        for office in P.OFFICES:
            for i in range(14):
                if office == "PRES" and y % 4:
                    continue
                st = f"S{i}"
                lean = rnd.gauss(0, 12)
                truth = lean + gb + rnd.gauss(0, 6)
                n = rnd.choice([None, 1.0, 1.5, 3.0, 4.0, 8.0, 12.0])
                avg = None if n is None else truth - shared - rnd.gauss(0, 2 + 6 / n ** 0.5)
                race = f"{y}-{st}-{office}"
                rows.append({"race": race, "year": y, "state": st, "office": office, "midterm": int(y % 4 == 2),
                             "result_margin": truth, "avg_margin": avg, "avg_n_eff": n, "lean": lean,
                             "generic_ballot": gb, "incumbent_running": rnd.choice(["D", "R", "open"])})
                level = max(-3, min(3, round((truth + rnd.gauss(0, 6)) / 6)))
                ratings[race] = [float(level), float(level)]
    return rows, ratings


def all_cells(model):
    for office, groups in model["weights"].items():
        for group, subsets in groups.items():
            for key, cell in subsets.items():
                yield office, group, key, cell


def test_fit_returns_every_field_and_the_weights_add_to_one_in_each_group():
    rows, ratings = made_up()
    m = P.fit(rows, ratings, YEARS, HP)
    for k in ("fitted_on", "poll", "c", "c_note", "ratings", "fund", "weights", "yardstick"):
        assert k in m
    for o in P.OFFICES:
        assert m["poll"][o]["A"] >= 0 and m["poll"][o]["B"] >= 0
        assert m["fund"]["by_office"][o]["coef"] and m["yardstick"][o]["width"] > 0
    assert set(m["c"]) == {"midterm", "presidential"}          # five or more of each kind of year
    assert m["ratings"]["lookup"]
    cells = list(all_cells(m))
    assert {g for _, g, _, _ in cells} >= {"lt2", "2to5", "5plus", "none"}
    for _, _, _, cell in cells:
        assert abs(sum(cell["weights"].values()) - 1) < 1e-9
        assert all(0 <= w <= 1 for w in cell["weights"].values())
        assert cell["own"] > 0


def test_one_shared_miss_value_when_there_are_too_few_midterm_years():
    rows, ratings = made_up()
    m = P.fit(rows, ratings, [2002, 2004, 2006, 2008, 2012, 2016, 2020], HP)
    assert set(m["c"]) == {"all"}
    assert "one value" in m["c_note"]


def test_the_model_can_be_saved_as_json():
    import json
    rows, ratings = made_up()
    assert json.loads(json.dumps(P.fit(rows, ratings, YEARS, HP)))["fitted_on"] == YEARS


def test_rating_levels_with_few_races_borrow_from_their_neighbours():
    rows, ratings = made_up()
    m = P.fit(rows, ratings, YEARS, {"MIN_LEVEL_N": 40})
    assert any(c["borrowed"] for c in m["ratings"]["lookup"].values())
    for c in m["ratings"]["lookup"].values():
        assert c["sd"] >= 0


def test_with_no_polls_the_polls_weight_is_zero():
    rows, ratings = made_up()
    m = P.fit(rows, ratings, YEARS, HP)
    row = dict(next(r for r in rows if r["office"] == "SEN"), avg_margin=None, avg_n_eff=None)
    f = P.forecast(row, ratings[row["race"]], m)
    assert f["parts"]["polls"] == (None, 0.0)
    assert f["parts"]["ratings"][1] > 0 or f["parts"]["fund"][1] > 0
    assert abs(sum(w for _, w in f["parts"].values()) - 1) < 1e-9


def test_a_race_with_many_polls_gets_a_smaller_own_width_than_one_with_one_poll():
    rows, ratings = made_up()
    m = P.fit(rows, ratings, YEARS, HP)
    base = next(r for r in rows if r["office"] == "SEN")
    many = P.forecast(dict(base, avg_margin=3.0, avg_n_eff=12.0), ratings[base["race"]], m)
    one = P.forecast(dict(base, avg_margin=3.0, avg_n_eff=1.0), ratings[base["race"]], m)
    assert many["sd_own"] < one["sd_own"]
    assert many["sd_shared"] == one["sd_shared"]
    assert one["sd"] ** 2 == one["sd_own"] ** 2 + one["sd_shared"] ** 2 or abs(one["sd"] ** 2 - one["sd_own"] ** 2 - one["sd_shared"] ** 2) < 1e-9
    polls_only = P.forecast(dict(base, avg_margin=3.0, avg_n_eff=12.0), None, m, layers=("polls",))
    polls_one = P.forecast(dict(base, avg_margin=3.0, avg_n_eff=1.0), None, m, layers=("polls",))
    assert polls_only["sd_own"] < polls_one["sd_own"]


def test_the_chance_is_one_half_at_a_margin_of_zero_and_uses_the_nights_win_prob():
    assert cw_forecast.win_prob(0.0, 5.0) == 0.5
    rows, ratings = made_up()
    m = P.fit(rows, ratings, YEARS, HP)
    old = P.fit(rows, ratings, YEARS, dict(HP, SHARED_DF=None))
    f = P.forecast(rows[0], ratings[rows[0]["race"]], old)
    assert f["win_prob"] == cw_forecast.win_prob(f["margin"], f["sd"]) and f["range90"] is None
    g = P.forecast(rows[0], ratings[rows[0]["race"]], m)               # the default: a fat-tailed shared miss
    assert g["range90"] > 0 and 0 < g["win_prob"] < 1


def test_a_forecast_with_nothing_to_go_on_says_so():
    rows, ratings = made_up()
    m = P.fit(rows, ratings, YEARS, HP)
    f = P.forecast({"race": "x", "year": 2022, "state": "ZZ", "office": "SEN", "midterm": 1}, None, m)
    assert f["margin"] is None and f["win_prob"] is None


def fc(race, year, state, margin, own, shared):
    return {"race": race, "year": year, "state": state, "margin": margin, "sd_own": own, "sd_shared": shared}


def test_the_chance_of_a_majority_is_one_when_every_race_is_certain():
    fs = [fc(f"r{i}", 2026, f"S{i}", 40.0, 0.5, 0.5) for i in range(5)]
    out = P.simulate(fs, draws=300, seed=1, chamber={"races": [f["race"] for f in fs], "held": 46, "majority": 50},
                     joint=["r0", "r1"])
    assert out["chamber"]["p_majority"] == 1.0
    assert all(p == 1.0 for p in out["win_prob"].values())
    assert out["joint"]["outcome"] == {"r0": "D", "r1": "D"} and out["joint"]["p"] == 1.0
    lost = [dict(f, margin=-40.0) for f in fs]
    assert P.simulate(lost, draws=300, seed=1, chamber={"races": [f["race"] for f in fs], "held": 40,
                                                         "majority": 50})["chamber"]["p_majority"] == 0.0


def test_a_chamber_with_races_that_move_together_has_a_wider_spread_of_seats():
    fs = [fc(f"r{i}", 2026, f"S{i}", 1.0, 1.0, 0.0) for i in range(20)]
    tied = [dict(f, sd_own=1.0, sd_shared=8.0) for f in fs]
    ch = {"races": [f["race"] for f in fs], "held": 0, "majority": 11}

    def spread(forecasts):
        seats = P.simulate(forecasts, draws=2000, seed=3, chamber=ch)["chamber"]["seats"]
        mean = sum(k * v for k, v in seats.items())
        return sum(v * (k - mean) ** 2 for k, v in seats.items())
    assert spread(tied) > spread(fs) * 3


def test_races_in_one_state_move_together_more_than_races_in_two_states():
    one = [fc("a", 2026, "GA", 0.0, 6.0, 0.0), fc("b", 2026, "GA", 0.0, 6.0, 0.0)]
    two = [fc("a", 2026, "GA", 0.0, 6.0, 0.0), fc("b", 2026, "AZ", 0.0, 6.0, 0.0)]

    def both(forecasts):
        j = P.simulate(forecasts, draws=4000, seed=5, joint=["a", "b"])["joint"]
        return j["p"]
    assert both(one) > both(two) + 0.05


def test_leave_one_year_out_never_uses_the_held_out_year():
    rows, ratings = made_up()
    held = 2012
    before = B.fold_model(rows, ratings, held, HP)
    absurd = copy.deepcopy(rows)
    for r in absurd:
        if r["year"] == held:
            r["result_margin"] = 5000.0
            r["avg_margin"] = -9000.0
            r["lean"] = 777.0
    after = B.fold_model(absurd, ratings, held, HP)
    assert held not in before["fitted_on"]
    assert before == after
    other = B.fold_model(absurd, ratings, 2004, HP)               # a different fold does see 2012, so differs
    assert other != B.fold_model(rows, ratings, 2004, HP)


def test_the_backtest_scores_every_layer_on_the_same_complete_races_and_each_year_once():
    rows, ratings = made_up()
    out = B.summarize(rows, ratings, 2008, HP)
    t = out["table"]["complete"]
    ns = {name: t[name]["ALL"]["n"] for name in ("polls", "ratings", "fund", "polls+ratings", "all three", "yardstick")}
    assert len(set(ns.values())) == 1 and ns["polls"] > 0
    assert sorted(out["per_year"]) == [2008, 2010, 2012, 2014, 2016, 2018, 2020]
    assert sum(b["n"] for b in out["calibration"]["polled"]["polls"]) > 0


def test_fundamentals_are_off_by_default_and_can_be_turned_on():
    rows, ratings = made_up()
    row = next(r for r in rows if r["office"] == "SEN" and r["avg_n_eff"] == 8.0)
    off = P.fit(rows, ratings, YEARS, HP)
    assert P.forecast(row, ratings[row["race"]], off)["parts"]["fund"] == (None, 0.0)
    on = P.fit(rows, ratings, YEARS, dict(HP, USE_FUNDAMENTALS=True))
    assert P.forecast(row, ratings[row["race"]], on)["parts"]["fund"][0] is not None
    assert P.forecast(row, ratings[row["race"]], off, layers=P.LAYERS)["parts"]["fund"][0] is not None  # still measurable


def test_an_unpolled_race_uses_ratings_only_when_fundamentals_are_off():
    rows, ratings = made_up()
    m = P.fit(rows, ratings, YEARS, HP)
    row = dict(next(r for r in rows if r["office"] == "GOV"), avg_margin=None, avg_n_eff=None)
    f = P.forecast(row, ratings[row["race"]], m)
    assert f["parts"]["ratings"][1] == 1.0 and f["parts"]["fund"] == (None, 0.0)


def test_one_shared_miss_for_all_years_when_the_split_is_turned_off():
    rows, ratings = made_up()
    m = P.fit(rows, ratings, YEARS, dict(HP, C_SPLIT=False))
    assert set(m["c"]) == {"all"} and "C_SPLIT is off" in m["c_note"]


def test_the_shared_miss_shapes_keep_their_spread_and_a_large_df_is_a_bell():
    for df in (3, 5, 10, float("inf")):
        nodes = P.shared_nodes(df)
        assert abs(sum(nodes)) < 1e-6
        assert 0.8 < (sum(t * t for t in nodes) / len(nodes)) ** 0.5 < 1.02
    assert abs(P.half_width90(1.0, 0.001, float("inf")) - 1.645) < 0.02
    wide = P.half_width90(3.0, 4.0, float("inf"))
    assert abs(wide - 1.645 * 5.0) < 0.15                      # a bell of own 3 and shared 4 has spread 5
    assert P.win_prob_t(20.0, 3.0, 4.0, 3) < P.win_prob_t(20.0, 3.0, 4.0, float("inf"))   # fatter tail: less sure


def test_the_year_block_bootstrap_keeps_the_stated_chance_and_gives_a_range():
    import pre_experiments as X
    recs = [{"year": 2006 + 2 * (i % 5), "win_prob": 0.75, "result": 1.0 if i % 4 else -1.0} for i in range(200)]
    rows = X.bootstrap_calibration(recs, 200)
    cell = next(r for r in rows if r["bin"] == "70-80")
    assert cell["n"] == 200 and cell["lo"] <= cell["won"] <= cell["hi"]


def test_c_gets_a_leave_one_year_out_range_and_a_bootstrap_interval():
    import pre_experiments as X
    rows, _ = made_up()
    ci, own2 = X.c_intervals(rows, 100)
    d = ci["all years"]
    assert d["leave_one_out_min"] <= d["C"] <= d["leave_one_out_max"] and d["boot_5"] <= d["boot_95"] and own2 > 0
