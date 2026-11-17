"""The forecast (cw_forecast), the rule the desk's bot calls on, the outlook the website shows, and the feed check."""
import json
import time

import pytest
from fastapi.testclient import TestClient

import autocaller as AUTO
import backtest as BT
import clock
import countywatch_run as CWR
import cw_early as E
import cw_forecast as CF
import cw_model as M
import feedcheck as FC
import feeds as FEEDS
import outlook as OUT
import picks as P
import replay as RP
from store import Store
from web import app as WEB
from web import export as EXPORT


def all_states(live=None):
    live = live or {}
    return {st: M.project(st, live.get(st, {})) for st in M.STATES}


def finals(st, share=1.0, lean=0.0, pct=1.0, swing=0.10):
    """Every county of a state reporting `share` of a result `swing` better for the D side than its baseline."""
    out = {}
    for c in M.load()[st]:
        m = max(-0.95, min(0.95, c["m24"] + swing + lean))
        n = round(c["w"] * share)
        d = round(n * (1 + m) / 2)
        out[c["fips"]] = {"D": d, "R": n - d, "O": 0, "pct_in": pct}
    return out


# ------------------------------------------------------------------ before any votes
def test_before_any_votes_a_state_starts_from_its_poll_not_its_last_result():
    fc = CF.forecast(all_states(), {"GA": 0.10})
    ga = fc["GA"]
    assert ga["stage"] == "prior" and ga["legacy"]["proj_m"] is None        # project() alone had nothing to say
    assert ga["base_m"] < 0 < ga["proj_m"] < 0.10                           # between the baseline and the poll
    assert ga["range90"][0] < ga["proj_m"] < ga["range90"][1] and ga["sd"] > 0.03
    assert 0.5 < ga["win_prob"] < 0.99 and ga["data_weight"] == 0
    assert CF.forecast(all_states())["GA"]["proj_m"] == pytest.approx(ga["base_m"])     # no poll, no results: the baseline


def test_a_state_still_waiting_moves_with_the_states_that_have_reported():
    """Kentucky and Virginia finish 15 points bluer than their baselines; Georgia, with nothing counted, moves too."""
    live = {st: finals(st, swing=0.15) for st in ("KY", "VA")}
    alone, informed = CF.forecast(all_states())["GA"], CF.forecast(all_states(live))["GA"]
    assert informed["stage"] == "prior" and informed["proj_m"] - alone["proj_m"] > 0.08
    assert 0.09 < informed["prior"]["national_swing"] < 0.15         # two states' worth of evidence, not yet the full 15
    assert informed["sd"] < alone["sd"]                                     # and it's surer than it was
    ne = CF.forecast(all_states(live))["NE"]                                # Nebraska's baseline is Osborn's own race
    assert "national swing" not in ne["prior"]["parts"]


def test_polls_that_miss_everywhere_move_the_states_still_waiting():
    polls = {st: M.base(st) + 0.05 for st in ("KY", "VA", "GA", "NC")}       # every poll: 5 points bluer than baseline
    live = {st: finals(st, swing=0.13) for st in ("KY", "VA")}               # the first two states come in 8 better
    nc = CF.forecast(all_states(live), polls)["NC"]
    assert nc["prior"]["poll_miss"] > 0.03 and nc["proj_m"] > polls["NC"] + 0.03


# ------------------------------------------------------------------ during the count
def test_a_finished_state_is_its_count_and_certain():
    fc = CF.forecast(all_states({"GA": finals("GA", swing=0.19)}))["GA"]
    assert fc["stage"] == "live" and fc["proj_m"] == pytest.approx(fc["raw_m"], abs=1e-6)
    assert fc["sd"] < 0.001 and fc["win_prob"] == 1.0 and fc["data_weight"] > 0.95


def test_early_votes_that_lean_one_way_dont_leak_into_the_forecast():
    """Half of every Georgia county's vote is in and it runs 12 points bluer than the county will end; a third of
    the counties have finished at their true result. project() keeps the early votes at face value; the forecast
    sees that half-counted counties are running ahead of finished ones and discounts them."""
    truth, live = finals("GA", swing=0.10), {}
    for i, (fips, row) in enumerate(finals("GA", share=0.5, lean=0.12, pct=0.5, swing=0.10).items()):
        live[fips] = truth[fips] if i % 3 == 0 else row
    final_m = M.m2(sum(r["D"] for r in truth.values()), sum(r["R"] for r in truth.values()))
    projs = all_states({"GA": live})
    fc = CF.forecast(projs)["GA"]
    assert abs(fc["proj_m"] - final_m) < 0.012
    assert abs(projs["GA"]["proj_m"] - final_m) > 2 * abs(fc["proj_m"] - final_m)
    assert fc["early_lean"] > 0.08                                           # it found the lean, about 12 points


def test_a_county_whose_turnout_fell_still_finishes():
    """Every precinct in, 75% of the expected vote: project() waits for 85% and never calls it complete (Ohio ended
    the night with 1 of 88). A count that has stopped moving for an hour does the same without precinct numbers."""
    live = finals("OH", share=0.75)
    p = M.project("OH", live)
    assert p["done"] == 0
    fc = CF.forecast({**all_states(), "OH": p})["OH"]
    assert all(r["fin"] for r in fc["rows"]) and fc["sd"] < 0.001
    live = finals("OH", share=0.8)
    idle = {f: {**r, "pct_in": None, "idle": 90} for f, r in live.items()}
    moving = {f: {**r, "pct_in": None, "idle": 5} for f, r in live.items()}
    assert all(r["fin"] for r in CF.forecast({**all_states(), "OH": M.project("OH", idle)})["OH"]["rows"])
    assert not any(r["fin"] for r in CF.forecast({**all_states(), "OH": M.project("OH", moving)})["OH"]["rows"])


def test_a_feed_that_goes_quiet_doesnt_make_its_counties_look_finished():
    """A county's idle time runs up to the last time its feed answered, not up to now: an hour of a blocked or
    downed site is not an hour of a county with nothing left to count."""
    fips = M.load()["OH"][0]["fips"]
    now = time.time()
    cache = {"states": {"OH": {fips: {"D": 500, "R": 500, "O": 0, "pct_in": None}}},
             "moved": {"OH": {fips: now - 120 * 60}}, "answered": {"OH": now - 115 * 60}}
    assert CWR.merged_live("OH", cache, {})[fips]["idle"] == pytest.approx(5, abs=0.1)
    cache["answered"]["OH"] = now
    assert CWR.merged_live("OH", cache, {})[fips]["idle"] == pytest.approx(120, abs=0.1)
    del cache["answered"]
    assert "idle" not in CWR.merged_live("OH", cache, {})[fips]


def test_mississippi_as_the_bot_last_held_it_is_not_a_race_to_call():
    """The desk called Mississippi and took it back. On the same counties the forecast has Colom ahead by under a
    point and well short of the 99% its own rule needs."""
    live = RP.load_countywatch()["MS"]
    fc = CF.forecast(all_states({"MS": live}))["MS"]
    assert 0 < fc["proj_m"] < 0.01 and 0.6 < fc["win_prob"] < AUTO.CALL_AT
    assert BT.call_side(fc) is None


# ------------------------------------------------------------------ calling
def test_the_bot_calls_on_a_chance_that_has_held():
    t = 1_000_000.0
    held = [(t + 60 * i, 0.999, 0.9) for i in range(12)]
    call = AUTO.decide_prob(held, "GA-SEN")
    assert call and call.side == "D" and call.held >= AUTO.HOLD_MINUTES
    assert AUTO.decide_prob(held[:3]) is None                               # not held long enough yet
    assert AUTO.decide_prob([(ts, 0.999, 0.2) for ts, _wp, _w in held]) is None      # the prior, not results, says so
    wobble = held[:6] + [(t + 360, 0.97, 0.9)] + [(t + 420 + 60 * i, 0.999, 0.9) for i in range(4)]
    assert AUTO.decide_prob(wobble) is None                                 # a dip starts the wait again
    assert AUTO.decide_prob([(ts, 0.001, 0.9) for ts, _wp, _w in held]).side == "R"


def test_chamber_odds_add_fixed_seats_and_races_in_play():
    sure = CF.chamber_odds([{"m": 0.2, "sd": 0.01, "weight": 1.0}] * 3, fixed=48, majority=51)
    assert sure["chance"] == 1.0 and sure["median"] == 51
    coin = CF.chamber_odds([{"m": 0.0, "sd": 0.05, "weight": 0.0}], fixed=50, majority=51)
    assert 0.45 < coin["chance"] < 0.55
    # two toss-ups that both lean on the prior move together: both win far more often than two separate coins would
    pair = CF.chamber_odds([{"m": 0.0, "sd": 0.05, "weight": 0.0}] * 2, fixed=49, majority=51)
    assert pair["chance"] > 0.3


def test_win_chance_is_never_certain_while_votes_are_out():
    assert CF.win_prob(0.0, 0.03) == 0.5 and CF.win_prob(0.05, 0) == 1.0
    assert 0.99 < CF.win_prob(0.09, 0.03) < 1.0 and CF.win_prob(0.2, 0.03) > AUTO.CALL_AT


# ------------------------------------------------------------------ the replay
def test_on_replayed_elections_the_forecast_beats_the_projection_and_calls_nothing_wrong():
    res = BT.run((2022, 2024), nights=2, seed=5, steps=12)        # 2026 needs the night's live.json, which isn't in the repo
    rows = BT.table(res["rows"])
    assert len(rows) == len(BT.BINS)
    assert sum(r["new_miss"] for r in rows) < 0.85 * sum(r["old_miss"] for r in rows)
    assert all(r["new_in"] >= 0.80 for r in rows)                           # the 90% range holds up at every stage
    calls = BT.call_summary(res["calls"])
    assert calls["wrong"] == 0 and calls["called"] > 0.8 * calls["races"]


# ------------------------------------------------------------------ the outlook and the website
@pytest.fixture
def site(monkeypatch, tmp_path):
    monkeypatch.setenv("DESK_WEB_SECRET", "test-secret")
    monkeypatch.setattr(OUT, "_keep_file", lambda: tmp_path / "outlook.json")
    monkeypatch.setattr(CWR, "live_cache", lambda: {"states": {"MS": RP.load_countywatch()["MS"]}, "errors": {"TX": "HTTP 500"},
                                                    "fetched": time.time() - 30})
    monkeypatch.setattr(CWR, "manual", lambda: {})
    monkeypatch.setattr(CWR, "factors", lambda: {})
    monkeypatch.setattr(CWR, "early_pace", lambda: [])
    monkeypatch.setattr(CWR, "early_cache", lambda: {})
    WEB.use_store(Store(str(tmp_path / "desk.db"), check_same_thread=False))
    OUT._memo.clear()
    clock.set(P.deadline().timestamp() + 3 * 86400)
    with TestClient(WEB.app, follow_redirects=False) as client:
        yield client
    WEB.use_store(None)
    OUT._memo.clear()


def test_the_outlook_words_every_race_and_adds_up_the_senate(site):
    o = site.get("/api/outlook").json()
    assert o["has_model"] and not o.get("stale") and len(o["races"]) == len(M.STATES)
    ms = next(r for r in o["races"] if r["id"] == "MS-SEN")
    assert ms["stage"] == "live" and ms["counted"] == 467_930 + 465_356 and 0 < ms["margin"] < 0.01
    assert "Mississippi" in ms["plain"] and ms["keys"] and ms["types"] and ms["left"]
    ga = next(r for r in o["races"] if r["id"] == "GA-SEN")
    assert ga["stage"] == "prior" and ga["plain"].startswith("No results yet in Georgia") and ga["poll"] is not None
    senate = o["chambers"][0]
    assert senate["key"] == "senate" and senate["need"] == 51 and 0 < senate["chance_d"] <= 1
    assert abs(sum(senate["dist"].values()) - 1) < 0.01 and "Senate" in senate["plain"]
    assert o["feeds"]["errors"] == {"TX": "HTTP 500"} and o["feeds"]["age"] < 120
    WEB.store().call("MS-SEN", "D", {"how": "test"})
    OUT._memo.clear()
    called = next(r for r in site.get("/api/outlook").json()["races"] if r["id"] == "MS-SEN")
    assert called["called"] == "D" and called["plain"].startswith("Scott Colom has won Mississippi")


def test_the_outlook_keeps_answering_when_the_numbers_cant_be_worked_out(site, monkeypatch):
    good = site.get("/api/outlook").json()
    OUT._memo.clear()

    def broken(*_a, **_k):
        raise ValueError("live.json is half written")
    monkeypatch.setattr(CWR, "forecasts", broken)
    r = site.get("/api/outlook")
    o = r.json()
    assert r.status_code == 200 and o["stale"] and "half written" in o["stale_why"]
    assert len(o["races"]) == len(good["races"])                            # the last good outlook, from disk


def test_the_model_page_health_check_and_new_script_are_served(site):
    m = site.get("/api/model").json()
    assert m["settings"]["TAU"] == CF.HP["TAU"] and m["backtest"]["years"] == list(BT.all_years())
    assert m["backtest"]["calls"]["all"]["wrong"] == 0
    h = site.get("/healthz").json()
    assert h["ok"] and h["results_age"] < 120
    assert site.get("/static/outlook.js").status_code == 200 and "outlook.js?v=" in site.get("/").text
    assert site.get("/api/state").json()["has_model"] is True


def test_a_static_copy_of_the_site_stands_on_its_own(site, tmp_path):
    out = tmp_path / "site"
    assert EXPORT.export(out, site) == []
    assert {p.stem for p in (out / "data").glob("*.json")} == set(EXPORT.PAGES)
    assert json.loads((out / "data" / "outlook.json").read_text(encoding="utf-8"))["has_model"]
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "window.DESK_STATIC" in html and 'src="static/app.js' in html and '"/static/' not in html
    assert 'url("/fonts/' not in (out / "static" / "app.css").read_text(encoding="utf-8")
    assert (out / "fonts" / "DMSerifDisplay-Regular.ttf").exists()


# ------------------------------------------------------------------ the feed check
def test_the_feed_check_puts_what_is_broken_first_and_says_so():
    rows = [FC._row("county feed", "GA", "ok", "159 of 159 counties", 1.2), FC._row("county feed", "TX", "warn", "blocked", 0.4),
            FC._row("results file", "FL", "FAIL", "HTTP 404", 0.3)]
    assert FC.table(rows).splitlines()[0].startswith("FAIL") and "| ❌ FAIL | results file | FL |" in FC.table(rows, markdown=True)
    assert FC.summary(rows) == "1 ok, 1 couldn't be checked from here, 1 broken"


# ------------------------------------------------------------------ early vote, pack settings and past polls
EARLY = {"uf": {"IA": {"voted": 500, "turn22": 1000, "dem": 300, "rep": 150, "none": 50}},
         "nc": {"rows": [{"county": M.load()["NC"][0]["name"], "g26": {"REP": 1440}, "g22": {"REP": 1000}}]}}
BEFORE = {"IA": {"voted": 400, "turn22": 1000, "dem": 200, "rep": 150, "none": 50}}


def test_early_vote_gives_each_north_carolina_county_its_own_turnout_growth():
    ev = CF.early_inputs(EARLY, BEFORE)
    assert len(ev["NC"]["counties"]) == len(M.load()["NC"])
    assert all(x == pytest.approx(1.2) for x in ev["NC"]["counties"].values())


def test_a_states_early_vote_total_is_a_turnout_start_only_when_that_setting_is_switched_on():
    ev = CF.early_inputs(EARLY, BEFORE)
    assert ev["IA"]["growth"] == pytest.approx(1.25) and ev["IA"]["reg_shift"] == pytest.approx(0.175)
    assert ev["IA"]["turnout"] is None
    assert CF.early_inputs(EARLY, BEFORE, {"EV_STATE_TURNOUT": True})["IA"]["turnout"] == pytest.approx(1.25 ** 0.5)


def test_county_turnout_from_early_vote_scales_the_votes_a_state_is_expected_to_cast():
    ev = CF.early_inputs(EARLY, BEFORE)
    assert CF.turnout_start("NC", ev) == (1.0, ev["NC"]["counties"])
    assert CF.turnout_start("NC", ev, 1.1) == (1.1, None)             # a number typed by hand wins
    assert CF.turnout_start("IA", ev) == (1.0, None)
    plain, scaled = M.project("NC", {}), M.project("NC", {}, *CF.turnout_start("NC", ev))
    assert sum(r["exp2"] for r in scaled["rows"]) / sum(r["exp2"] for r in plain["rows"]) == pytest.approx(1.2, abs=1e-6)


def test_the_party_registration_term_moves_the_prior_only_when_it_is_switched_on():
    ev = CF.early_inputs(EARLY, BEFORE)
    projs = {st: M.project(st, {}) for st in M.STATES}
    off, on = CF.forecast(projs, {}, None, ev)["IA"], CF.forecast(projs, {}, {"EV_REG": True}, ev)["IA"]
    assert "early vote" not in off["prior"]["parts"]
    assert on["proj_m"] > off["proj_m"] + 0.02 and on["sd"] < off["sd"]


def test_last_cycles_early_vote_file_reads_in_full():
    before = E.load_before()
    assert before["GA"]["voted"] > 3_000_000 and before["GA"]["turn22"] > 4_000_000


def test_a_packs_forecast_block_changes_the_settings_and_a_bad_one_is_rejected(monkeypatch):
    block = {"states": ["GA"], "not_comparable": ["GA"], "forecast": {"SE": 0.09, "NOT_A_SETTING": 1}}
    monkeypatch.setattr(CF.FEEDS, "countywatch", lambda: block)
    try:
        CF._pack_settings(None)
        assert CF.settings()["SE"] == 0.09 and CF.settings({"SE": 0.2})["SE"] == 0.2
        assert "NOT_A_SETTING" not in CF.settings() and CF.NOT_COMPARABLE == {"GA"}
    finally:
        monkeypatch.undo()
        CF._pack_settings(None)
    assert CF.settings()["SE"] == CF.HP["SE"]
    bad = {"feeds": {"countywatch": {"states": ["GA"], "not_comparable": ["TX"], "forecast": {"SE": "wide"}}}}
    said = " ".join(CF.FEEDS.problems(bad))
    assert 'not_comparable "TX"' in said and "forecast has to be a block of named numbers" in said
    assert CF.FEEDS.problems({"feeds": {"countywatch": {"states": ["GA"], "forecast": {"SE": 0.05}}}}) == []


def test_past_polls_give_ten_states_for_each_earlier_election_and_skip_the_gaps():
    pp = BT.past_polls(2024)
    assert len(pp) == 10 and "KS" not in pp and -0.02 < pp["GA"] < 0
    assert len(BT.past_polls(2022)) == 10


# ------------------------------------------------------------------ a pack says which past contests it holds
def test_a_pack_that_says_nothing_about_its_contests_gets_2026s_meaning(monkeypatch):
    monkeypatch.setattr(FEEDS, "countywatch", lambda: {"states": ["GA"]})
    assert M.contests() == M.CONTESTS_2026 and M.baseline() == M.BASELINE_2026


def test_every_past_contest_is_kept_on_each_county_as_votes_for_each_side_and_everyone():
    counties = M.load()["GA"]
    assert set(counties[0]["past"]) == set(M.contests())
    c = counties[0]
    d, r, everyone = c["past"]["2024-pres"]
    assert d == c["d24"] and r == c["r24"] and everyone >= d + r


def test_the_pack_declares_the_elections_the_backtest_replays():
    assert BT.all_years() == (2022, 2024, 2018, 2020, 2026) and BT.live_year() == 2026
    with pytest.raises(KeyError):
        BT.election("1999")


def test_a_backtest_added_to_the_pack_with_its_own_turnout_contest_builds_and_runs(monkeypatch):
    block = dict(FEEDS.countywatch())
    extra = {"name": "alt", "result": "2024-pres", "split": "2020-pres", "turnout": "2022", "polls": "2024"}
    block["backtests"] = [*block["backtests"], extra]
    monkeypatch.setattr(FEEDS, "countywatch", lambda: block)
    assert BT.all_years()[-1] == "alt" and FEEDS.problems({"feeds": {"countywatch": block}}) == []
    rows, finals, _polls = BT.election("alt")
    assert rows and set(rows) == set(finals)
    res = BT.run(("alt",), nights=1, steps=6, snapshots=False)
    assert res["rows"] and {r["year"] for r in res["rows"]} <= {"alt"}


def _block(**changes):
    block = {"states": ["GA"], "contests": {"a": {"d": "d1", "r": "r1"}, "b": {"d": "d2", "r": "r2"}},
             "baseline": {"split": "a", "turnout": "b"}}
    return {"feeds": {"countywatch": {**block, **changes}}}


def test_a_contest_the_baseline_or_a_backtest_names_but_the_pack_doesnt_list_is_reported():
    said = " ".join(FEEDS.problems(_block(baseline={"split": "nope", "turnout": "b"})))
    assert '"nope" isn\'t one of its contests' in said
    said = " ".join(FEEDS.problems(_block(backtests=[{"name": "x", "result": "a", "split": "zzz"}])))
    assert '"zzz" as its split' in said


def test_a_contest_missing_one_of_its_two_sides_is_reported():
    said = " ".join(FEEDS.problems(_block(contests={"a": {"d": "d1"}, "b": {"d": "d2", "r": "r2"}})))
    assert '"a" needs the columns for its two sides' in said


def test_a_baseline_without_contests_is_reported_and_a_live_backtest_is_accepted():
    assert any("need \"contests\"" in p for p in FEEDS.problems({"feeds": {"countywatch": {"states": ["GA"], "baseline": {"split": "a"}}}}))
    assert FEEDS.problems(_block(backtests=[{"name": "now", "result": "live"}])) == []


def test_a_rating_reads_onto_the_history_scale_and_gives_a_prior():
    assert CF.rating_level("Lean D") == 1.0 and CF.rating_level("Likely R") == -2.0
    assert CF.rating_level("Toss-up") == 0.0 and CF.rating_level("Tilt R") == -0.5
    assert CF.rating_level("Leans Blue") is None
    model = {"ratings": {"lookup": {"1.0": {"mean": 7.0, "sd": 9.0}, "-2.0": {"mean": -16.0, "sd": 1.0}}}}
    assert CF.rating_prior("Lean D", model) == (0.07, 0.09)
    assert CF.rating_prior("Likely R", model) == (-0.16, 0.03)      # never narrower than 3 points


def test_a_race_known_by_its_totals_moves_from_its_rating_to_its_count():
    before = CF.from_totals(0.05, 0.06, None, 0.0)
    assert before["proj_m"] == 0.05 and before["data_weight"] == 0
    half = CF.from_totals(0.05, 0.06, -0.04, 0.5, 100_000)
    assert -0.04 < half["proj_m"] < 0.05 and 0 < half["data_weight"] < 1
    done = CF.from_totals(0.05, 0.06, -0.04, 1.0, 200_000)
    assert done["proj_m"] == pytest.approx(-0.04, abs=0.002) and done["win_prob"] < 0.01
    early = CF.from_totals(0.05, 0.06, -0.04, 0.0, 50_000)      # votes with no precincts in: early vote, weighed
    assert early["data_weight"] > 0 and early["proj_m"] > half["proj_m"]


def test_the_totals_replay_runs_and_its_finished_races_show_the_count():
    res = BT.totals_test(years=(2022,), nights=1, steps=6)
    done = [r for r in res["rows"] if r["share"] >= 0.999]
    assert res["rows"] and done and all(abs(r["new"] - r["truth"]) < 0.01 for r in done)
