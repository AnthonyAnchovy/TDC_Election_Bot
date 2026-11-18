"""Which polls to trust (cw_quality) and how the night model uses them. Offline: the archive and ratings are files."""
import pytest

import cw_forecast as F
import cw_quality as Q


def poll(pollster, margin, days=3, n=800, method="Live Phone", aapor=True, partisan="", race="2026-XX-SEN", result=0.0):
    return {"race": race, "pollster": pollster, "pollster_id": "", "margin": margin, "days_out": days, "n": n,
            "methodology": method, "aapor_roper": aapor, "partisan": partisan, "result": result, "year": 2026}


def test_names_meet_across_the_three_spellings_of_a_joint_poll():
    assert Q.norm("The New York Times/Siena College") == Q.norm("New York Times / Siena")
    assert "siena" in Q._parts("NYT/Siena") and "siena" in Q._parts("New York Times/Siena University")


def test_the_times_rule_needs_two_of_three_and_never_takes_a_partisan_poll():
    good = {"pm": -0.5, "n": 40}
    assert Q.is_select(good, poll("A", 0, method="Online Panel", aapor=True))            # record + AAPOR
    assert Q.is_select({}, poll("B", 0, method="Live Phone", aapor=True))                # phone + AAPOR
    assert not Q.is_select({}, poll("C", 0, method="Online Panel", aapor=True))          # one of three
    assert not Q.is_select(good, poll("D", 0, method="Live Phone", aapor=True, partisan="REP"))


def test_a_select_pollster_with_a_good_record_pulls_the_average_its_way():
    polls = [poll("Good", 6.0, method="Live Phone", aapor=True), poll("Cheap", 0.0, method="Online Panel", aapor=False)]
    plain = Q.average(polls, {}, {})
    rated = Q.average(polls, {"Good": {"pm": -0.8, "n": 50}, "Cheap": {"pm": 1.5, "n": 50}}, {"TAU": 2.0, "SELECT": 2.0})
    assert plain["margin"] == pytest.approx(3.0) and rated["margin"] > 4.5


def test_polls_that_disagree_make_a_wider_range():
    model = {"widths": {"A": 0.0, "B": 7.0, "K": 0.6}}
    agree = Q.average([poll("A", 5), poll("B", 5.5), poll("C", 4.5)])
    split = Q.average([poll("A", 12), poll("B", 5), poll("C", -1)])
    assert Q.own_sd(split, model) > Q.own_sd(agree, model) + 1
    assert Q.own_sd(agree, model, gap=5.0) > Q.own_sd(agree, model)          # two weightings 5 points apart


def test_pollsters_are_rated_against_other_polls_of_the_same_race_and_shrunk():
    races = []
    for i in range(30):
        res = (i % 7) - 3.0
        races += [poll("Close", res + 0.5, race=f"r{i}", result=res), poll("Far", res + 6, race=f"r{i}", result=res)]
    rec = Q.track_records(races)
    assert rec["Close"]["pm"] < 0 < rec["Far"]["pm"]
    assert abs(rec["Far"]["pm"]) < 5.5            # shrunk toward the average pollster


def test_the_archive_replay_and_the_fit_on_file_hold_together():
    polls = Q.load()
    years = {p["year"] for p in polls}
    assert {2000, 2016, 2024, 2026} <= years
    fit = Q.load_fit()
    assert fit and fit["widths"]["B"] > 0 and 1.0 < fit["shared"]["midterm"] < fit["shared"]["presidential"] + 2
    rows = Q.replay(polls, [2024], fit["hp"])
    assert len(rows) > 50 and Q.score(rows)["miss"] < 6


def test_the_live_ratings_come_from_silver_bulletin_with_the_archives_method():
    rec = Q.live_record("The New York Times/Siena College")
    assert rec and rec["pm"] < 0 and rec["rated_as"]
    assert Q.live_record("A Pollster Nobody Has Heard Of") is None


def test_the_night_model_takes_each_states_number_and_width_from_its_own_polls():
    import packs
    with packs.using("2026-general"):
        bare = F.poll_margins()
        out, hp = F.night_inputs(bare)
        off, _ = F.night_inputs(bare, {"POLL_QUALITY": False})
    assert out["MI"]["sd"] < off["MI"]["sd"]                 # many polls that agree: narrower than the old width
    assert out["KS"] == off["KS"]                            # no late polls of its own: unchanged
    assert hp["SD0"] == pytest.approx(Q.load_fit()["shared"]["midterm"] / 100)
