"""The polls file and the polling average (cw_polls). Everything here is offline."""
import datetime as dt
import random

import pytest

import cw_polls as P

TODAY = "2026-11-02"
AS_OF = dt.date(2026, 11, 2)
HEADER = "race,pollster,start,end,n,population,sponsor,option,share\n"


def poll(pollster, end, d, r, n=800, pop="lv", sponsor="", start=None, race="GA-SEN"):
    end = dt.date.fromisoformat(end)
    start = dt.date.fromisoformat(start) if start else end - dt.timedelta(days=2)
    return {"race": race, "pollster": pollster, "start": start, "end": end, "n": n, "population": pop,
            "sponsor": sponsor, "shares": {"D": d, "R": r}}


def write(tmp_path, *rows):
    f = tmp_path / "polls.csv"
    f.write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return f


GOOD = ["GA-SEN,Acme,2026-10-20,2026-10-22,800,lv,,D,48", "GA-SEN,Acme,2026-10-20,2026-10-22,800,lv,,R,44"]
RACES = {"GA-SEN": ["D", "R"], "CHI-MAYOR": ["A", "B", "C", "D4"]}


def test_a_clean_file_loads_into_one_record_per_poll(tmp_path):
    polls = P.load(write(tmp_path, *GOOD), RACES, today=TODAY)
    assert len(polls) == 1
    assert polls[0]["shares"] == {"D": 48.0, "R": 44.0}
    assert polls[0]["end"] == dt.date(2026, 10, 22)


def test_an_old_poll_counts_less_than_a_new_one_of_the_same_size():
    polls = [poll("A", "2026-10-31", 50, 40), poll("B", "2026-09-20", 40, 50)]
    avg = P.average(polls, "GA-SEN", AS_OF)
    assert avg["margin"] > 0          # the new D-leading poll wins
    assert avg["shares"]["D"] > 45


def test_a_poll_loses_half_its_weight_after_the_half_life():
    polls = [poll("A", "2026-10-31", 50, 40, start="2026-10-31"), poll("B", "2026-10-17", 40, 50, start="2026-10-17")]
    avg = P.average(polls, "GA-SEN", AS_OF)
    # ages are 2 and 16 days: 14 days apart, so the older weighs half
    assert avg["shares"]["D"] == pytest.approx((50 * 1 + 40 * 0.5) / 1.5, abs=1e-9)


def test_a_huge_poll_cannot_swamp_the_rest():
    small = [poll(f"P{i}", "2026-10-30", 50, 44, n=1000) for i in range(4)]
    huge = poll("Big", "2026-10-30", 30, 60, n=1_000_000)
    avg = P.average(small + [huge], "GA-SEN", AS_OF)
    # capped at 1500 against 1000 each: the huge one weighs about 1.22 polls, not 1000
    assert avg["shares"]["D"] > 45


def test_one_pollsters_five_polls_count_about_like_one():
    five = [poll("Same", f"2026-10-{20 + i}", 60, 30, start=f"2026-10-{20 + i}") for i in range(5)]
    other = poll("Other", "2026-10-30", 40, 50)
    many = P.average(five + [other], "GA-SEN", AS_OF)
    one = P.average(five[-1:] + [other], "GA-SEN", AS_OF)
    assert abs(many["shares"]["D"] - one["shares"]["D"]) < 4
    assert many["shares"]["D"] < 54   # counted as five equal polls it would be about 57


def test_reordering_the_file_changes_nothing():
    polls = [poll("A", "2026-10-30", 50, 40), poll("A", "2026-10-30", 47, 43, n=900),
             poll("B", "2026-10-25", 45, 47), poll("B", "2026-10-25", 44, 46, pop="rv"),
             poll("C", "2026-10-28", 49, 45, n=None)]
    want = P.average(polls, "GA-SEN", AS_OF)
    for seed in range(10):
        shuffled = polls[:]
        random.Random(seed).shuffle(shuffled)
        assert P.average(shuffled, "GA-SEN", AS_OF) == want


def test_sponsored_polls_are_dropped_and_counted():
    polls = [poll("A", "2026-10-30", 48, 44), poll("Camp", "2026-10-30", 60, 30, sponsor="Smith for Senate")]
    avg = P.average(polls, "GA-SEN", AS_OF, hp={"SPONSORED": "drop"})
    assert avg["shares"]["D"] == pytest.approx(48) and avg["sponsored_dropped"] == 1 and avg["polls_used"] == 1
    kept = P.average(polls, "GA-SEN", AS_OF, hp={"SPONSORED": "keep"})
    assert kept["shares"]["D"] == pytest.approx(54) and kept["sponsored_dropped"] == 0


def test_by_default_a_sponsored_poll_is_kept_at_two_thirds_weight_and_moved_away_from_its_sponsor():
    polls = [poll("A", "2026-10-30", 48, 44), poll("Camp", "2026-10-30", 54, 40, sponsor="D")]
    avg = P.average(polls, "GA-SEN", AS_OF)
    # the Democratic internal reads D+14 and becomes D+10 (52 to 42), at weight 2/3 against 1
    assert avg["sponsored_shifted"] == 1 and avg["sponsored_dropped"] == 0
    assert avg["shares"]["D"] == pytest.approx((48 * 1 + 52 * 2 / 3) / (5 / 3))
    assert avg["margin"] == pytest.approx((4 * 1 + 10 * 2 / 3) / (5 / 3))


def test_a_republican_sponsors_poll_is_moved_toward_the_democrat():
    avg = P.average([poll("Camp", "2026-10-30", 44, 48, sponsor="Republican National Committee")], "GA-SEN", AS_OF)
    assert avg["margin"] == pytest.approx(0)      # R+4 becomes even


def test_a_sponsor_that_is_a_candidate_has_its_own_share_moved_down():
    p = {"race": "CHI-MAYOR", "pollster": "X", "start": dt.date(2026, 10, 28), "end": dt.date(2026, 10, 30),
         "n": 800, "population": "lv", "sponsor": "Smith for Mayor", "shares": {"A": 30, "B": 25, "C": 15}}
    avg = P.average([p], "CHI-MAYOR", AS_OF, sponsors={"Smith for Mayor": "A"})
    assert avg["shares"]["A"] == pytest.approx(26) and avg["shares"]["B"] == 25
    assert avg["undecided"] == pytest.approx(34)


def test_a_sponsor_we_cannot_place_is_dropped_even_when_shifting():
    avg = P.average([poll("Camp", "2026-10-30", 60, 30, sponsor="Friends of Liberty")], "GA-SEN", AS_OF)
    assert avg["polls_used"] == 0 and avg["sponsored_dropped"] == 1 and avg["sponsored_shifted"] == 0


def test_the_sponsor_shift_is_a_setting():
    polls = [poll("Camp", "2026-10-30", 54, 40, sponsor="D")]
    avg = P.average(polls, "GA-SEN", AS_OF, hp={"SPONSOR_SHIFT": 10})
    assert avg["margin"] == pytest.approx(4)


def test_the_stale_poll_switch_is_off_unless_asked_for():
    mood = {"2026-09-01": 0.0, "2026-11-01": 6.0}
    avg = P.average([poll("A", "2026-09-02", 48, 44)], "GA-SEN", AS_OF, national=mood)
    assert avg["margin"] == pytest.approx(4) and avg["stale_moved"] == 0


def test_an_old_poll_moves_by_the_share_of_the_national_mood_shift_since_it_was_taken():
    mood = {"2026-09-01": 0.0, "2026-11-01": 6.0}
    avg = P.average([poll("A", "2026-09-02", 48, 44)], "GA-SEN", AS_OF, hp={"STALE_SHIFT": 0.5}, national=mood)
    assert avg["margin"] == pytest.approx(4 + 3) and avg["stale_moved"] == 1


def test_the_stale_switch_leaves_a_poll_alone_when_the_mood_series_does_not_reach_back():
    mood = {"2026-10-15": 0.0, "2026-11-01": 6.0}
    avg = P.average([poll("A", "2026-09-02", 48, 44)], "GA-SEN", AS_OF, hp={"STALE_SHIFT": 1}, national=mood)
    assert avg["margin"] == pytest.approx(4) and avg["stale_moved"] == 0


def test_the_stale_switch_leaves_races_with_more_than_two_sides_alone():
    mood = {"2026-09-01": 0.0, "2026-11-01": 6.0}
    p = {"race": "CHI-MAYOR", "pollster": "X", "start": dt.date(2026, 9, 1), "end": dt.date(2026, 9, 3),
         "n": 800, "population": "lv", "sponsor": "", "shares": {"A": 30, "B": 25, "C": 15}}
    avg = P.average([p], "CHI-MAYOR", AS_OF, hp={"STALE_SHIFT": 1}, national=mood)
    assert avg["shares"]["A"] == 30 and avg["stale_moved"] == 0


def test_the_sample_size_cap_is_a_setting():
    polls = [poll("A", "2026-10-30", 50, 40, n=1500), poll("B", "2026-10-30", 30, 60, n=6000)]
    low = P.average(polls, "GA-SEN", AS_OF, hp={"N_CAP": 1500})
    high = P.average(polls, "GA-SEN", AS_OF, hp={"N_CAP": 6000})
    assert low["shares"]["D"] == pytest.approx(40) and high["shares"]["D"] < 38


def test_a_poll_after_the_as_of_date_is_ignored():
    polls = [poll("A", "2026-10-20", 48, 44), poll("B", "2026-10-30", 30, 60)]
    avg = P.average(polls, "GA-SEN", dt.date(2026, 10, 25))
    assert avg["shares"]["D"] == 48 and avg["polls_used"] == 1


def test_an_as_of_before_any_poll_gives_an_empty_result_not_an_error():
    avg = P.average([poll("A", "2026-10-20", 48, 44)], "GA-SEN", dt.date(2026, 1, 1))
    assert avg["shares"] == {} and avg["margin"] is None and avg["effective_polls"] == 0
    assert avg["newest_age"] is None and avg["undecided"] is None


def test_the_margin_is_democrat_minus_republican_and_the_age_is_the_newest_polls():
    avg = P.average([poll("A", "2026-10-30", 48, 44)], "GA-SEN", AS_OF)
    assert avg["margin"] == pytest.approx(4) and avg["newest_age"] == 3
    assert avg["undecided"] == pytest.approx(8)


def test_a_pollsters_likely_voter_version_beats_its_registered_voter_version():
    polls = [poll("A", "2026-10-30", 50, 40, pop="lv"), poll("A", "2026-10-30", 30, 60, pop="rv")]
    avg = P.average(polls, "GA-SEN", AS_OF)
    assert avg["shares"]["D"] == 50 and avg["polls_used"] == 1


def test_a_four_candidate_race_averages_each_candidate():
    def four(pollster, a, b, c, d):
        return {"race": "CHI-MAYOR", "pollster": pollster, "start": dt.date(2026, 10, 28),
                "end": dt.date(2026, 10, 30), "n": 800, "population": "lv", "sponsor": "",
                "shares": {"A": a, "B": b, "C": c, "D4": d}}
    avg = P.average([four("X", 30, 25, 15, 10), four("Y", 20, 25, 25, 10)], "CHI-MAYOR", AS_OF)
    assert avg["shares"] == pytest.approx({"A": 25, "B": 25, "C": 20, "D4": 10})
    assert avg["undecided"] == pytest.approx(20) and avg["margin"] is None


def test_polls_for_other_races_are_left_out():
    avg = P.average([poll("A", "2026-10-30", 48, 44, race="MI-SEN")], "GA-SEN", AS_OF)
    assert avg["polls_used"] == 0


def test_a_bad_sponsor_setting_is_refused():
    with pytest.raises(ValueError):
        P.average([], "GA-SEN", AS_OF, hp={"SPONSORED": "maybe"})


# ---- the checker ----

def problems(tmp_path, *rows, races=RACES):
    return P.check(write(tmp_path, *rows), races, today=TODAY)


def test_the_checker_names_an_unknown_race(tmp_path):
    out = problems(tmp_path, "XX-SEN,Acme,2026-10-20,2026-10-22,800,lv,,D,48")
    assert len(out) == 1 and "XX-SEN" in out[0] and "Line 2" in out[0]


def test_the_checker_names_an_unknown_option_for_that_race(tmp_path):
    out = problems(tmp_path, "GA-SEN,Acme,2026-10-20,2026-10-22,800,lv,,Z,48")
    assert len(out) == 1 and "option 'Z'" in out[0]


def test_the_checker_refuses_shares_over_100_for_one_poll(tmp_path):
    out = problems(tmp_path, "GA-SEN,Acme,2026-10-20,2026-10-22,800,lv,,D,60",
                   "GA-SEN,Acme,2026-10-20,2026-10-22,800,lv,,R,50")
    assert len(out) == 1 and "over 100" in out[0]


def test_the_checker_refuses_a_poll_that_ends_before_it_starts(tmp_path):
    out = problems(tmp_path, "GA-SEN,Acme,2026-10-22,2026-10-20,800,lv,,D,48")
    assert len(out) == 1 and "ends before it starts" in out[0]


def test_the_checker_refuses_a_date_in_the_future(tmp_path):
    out = problems(tmp_path, "GA-SEN,Acme,2026-11-01,2026-11-20,800,lv,,D,48")
    assert len(out) == 1 and "after today" in out[0]


def test_the_checker_refuses_a_duplicate_poll(tmp_path):
    out = problems(tmp_path, *GOOD, GOOD[0])
    assert len(out) == 1 and "listed twice" in out[0] and "Line 4" in out[0]


def test_the_checker_refuses_numbers_that_are_not_numbers(tmp_path):
    out = problems(tmp_path, "GA-SEN,Acme,2026-10-20,2026-10-22,lots,lv,,D,48",
                   "GA-SEN,Beta,2026-10-20,2026-10-22,800,lv,,D,many")
    assert len(out) == 2 and "sample size" in out[0] and "share" in out[1]


def test_the_checker_refuses_a_bad_date_and_a_bad_population(tmp_path):
    out = problems(tmp_path, "GA-SEN,Acme,Oct 20,2026-10-22,800,lv,,D,48",
                   "GA-SEN,Beta,2026-10-20,2026-10-22,800,voters,,D,48")
    assert len(out) == 2 and "not a date" in out[0] and "population" in out[1]


def test_the_checker_accepts_a_blank_sample_size_and_population(tmp_path):
    assert problems(tmp_path, "GA-SEN,Acme,2026-10-20,2026-10-22,,,,D,48") == []


def test_the_checker_without_a_race_list_skips_race_checks(tmp_path):
    assert P.check(write(tmp_path, "XX-SEN,Acme,2026-10-20,2026-10-22,800,lv,,Q,48"), today=TODAY) == []


def test_a_bad_file_is_refused_with_every_reason_and_nothing_is_read(tmp_path):
    f = write(tmp_path, *GOOD, "XX-SEN,Acme,2026-10-20,2026-10-22,800,lv,,D,48",
              "GA-SEN,Beta,2026-10-22,2026-10-20,800,lv,,D,48")
    with pytest.raises(P.PollFileError) as e:
        P.load(f, RACES, today=TODAY)
    assert len(e.value.problems) == 2
    assert "XX-SEN" in str(e.value) and "before it starts" in str(e.value)


def test_a_file_missing_a_column_is_refused_in_words(tmp_path):
    f = tmp_path / "polls.csv"
    f.write_text("race,pollster\nGA-SEN,Acme\n", encoding="utf-8")
    out = P.check(f, today=TODAY)
    assert len(out) == 1 and "no column" in out[0]
