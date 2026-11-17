"""The pre-election history table (history/pre_history.csv) and the helpers that build it. Everything is offline:
the table is read as it sits in the repo, and the builder's small functions are tested on made-up rows."""
import csv
import datetime as dt
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_pre_history as B  # noqa: E402

TABLE = os.path.join(ROOT, "history", "pre_history.csv")
RACE_ID = re.compile(r"^(\d{4})-([A-Z]{2})-(PRES|SEN2?|GOV)$")


@pytest.fixture(scope="module")
def rows():
    with open(TABLE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == B.COLUMNS, "the table's columns changed; rebuild it with tools/build_pre_history.py"
        return list(reader)


def num(text):
    return float(text) if text != "" else None


def test_every_column_the_builder_writes_is_in_the_table(rows):
    assert rows and set(rows[0]) == set(B.COLUMNS)


def test_race_ids_are_unique_and_well_formed(rows):
    ids = [r["race"] for r in rows]
    assert len(ids) == len(set(ids))
    for r in rows:
        m = RACE_ID.match(r["race"])
        assert m, r["race"]
        assert (int(m.group(1)), m.group(2), m.group(3)) == (int(r["year"]), r["state"], r["office"])


def test_years_are_even_election_years_from_2000_to_2024(rows):
    years = {int(r["year"]) for r in rows}
    assert years == set(range(2000, 2025, 2))


def test_president_rows_are_only_in_presidential_years_and_midterm_follows_the_year(rows):
    for r in rows:
        year = int(r["year"])
        assert r["midterm"] == ("0" if year % 4 == 0 else "1")
        if r["office"] == "PRES":
            assert year % 4 == 0


def test_a_second_senate_race_only_exists_beside_a_first_one_except_lone_specials_are_plain_sen(rows):
    have = {r["race"] for r in rows}
    for r in rows:
        if r["office"] == "SEN2":
            assert r["race"].replace("SEN2", "SEN") in have


def test_margins_are_within_minus_100_and_100(rows):
    for r in rows:
        for col in ("result_margin", "avg_margin"):
            v = num(r[col])
            assert v is None or -100 <= v <= 100, (r["race"], col, v)


def test_lean_and_the_national_numbers_are_plausible_percentage_points(rows):
    for r in rows:
        v = num(r["lean"])
        assert v is None or -100 <= v <= 100, (r["race"], "lean", v)
        for col in ("generic_ballot", "national_house_vote"):
            v = num(r[col])
            assert v is None or -30 <= v <= 30, (r["race"], col, v)


def test_there_is_no_average_without_a_poll_in_the_last_21_days(rows):
    for r in rows:
        polls = num(r["polls_final21"])
        if r["avg_margin"] != "":
            assert polls is not None and polls > 0, r["race"]
            assert num(r["avg_n_eff"]) > 0
            assert 0 <= num(r["avg_newest_days"]) <= 21
        else:
            assert r["avg_n_eff"] == "" and r["avg_newest_days"] == "", r["race"]


def test_a_result_margin_always_names_its_source_and_a_blank_one_does_not(rows):
    for r in rows:
        assert (r["result_margin"] == "") == (r["result_source"] == ""), r["race"]


def test_incumbent_is_d_r_i_open_or_blank_and_has_a_source_exactly_when_filled(rows):
    for r in rows:
        assert r["incumbent_running"] in ("", "D", "R", "I", "open")
        assert (r["incumbent_running"] == "") == (r["incumbent_source"] == ""), r["race"]


def test_the_president_is_the_same_incumbent_in_every_state_of_a_year(rows):
    by_year = {}
    for r in rows:
        if r["office"] == "PRES":
            by_year.setdefault(r["year"], set()).add(r["incumbent_running"])
    assert by_year == {"2000": {"open"}, "2004": {"R"}, "2008": {"open"}, "2012": {"D"}, "2016": {"open"},
                       "2020": {"R"}, "2024": {"open"}}


def test_every_presidential_state_has_a_lean(rows):
    assert all(r["lean"] != "" for r in rows if r["office"] == "PRES")


def test_no_race_appears_with_the_same_state_year_and_office_twice(rows):
    keys = [(r["year"], r["state"], r["office"]) for r in rows]
    assert len(keys) == len(set(keys))


# ---- the builder's helpers, on made-up numbers

def test_two_party_margin_is_d_minus_r_over_the_two_parties_votes():
    assert B.two_party_margin(60, 40) == pytest.approx(20.0)
    assert B.two_party_margin(40, 60) == pytest.approx(-20.0)
    assert B.two_party_margin(0, 50) is None
    assert B.two_party_margin(50, None) is None


def test_election_day_is_the_tuesday_after_the_first_monday_in_november():
    assert B.election_day(2000) == dt.date(2000, 11, 7)
    assert B.election_day(2020) == dt.date(2020, 11, 3)
    assert B.election_day(2022) == dt.date(2022, 11, 8)
    assert B.election_day(2024) == dt.date(2024, 11, 5)


def test_a_candidate_on_two_party_lines_has_the_votes_added_up():
    lines = [("SMITH", "D", 600.0, False), ("SMITH", "other", 100.0, False), ("JONES", "R", 500.0, False)]
    merged = B.merge_by_name(lines)
    d, r, why = B.pick_sides(merged)
    assert why == "" and d[1] == 700.0 and r[1] == 500.0


def test_two_real_republicans_leave_the_margin_blank_but_a_stray_line_does_not():
    two_real = [("A", "D", 500.0, False), ("B", "R", 300.0, False), ("C", "R", 250.0, False)]
    d, r, why = B.pick_sides(two_real)
    assert d is None and r is None and "Republican" in why
    stray = [("A", "D", 500.0, False), ("B", "R", 300.0, False), ("C", "R", 2.0, False)]
    d, r, why = B.pick_sides(stray)
    assert why == "" and r[0] == "B"


def test_a_race_with_no_democrat_has_no_margin_and_says_why():
    d, r, why = B.pick_sides([("B", "R", 300.0, False), ("C", "other", 50.0, False)])
    assert d is None and r is None and "Democratic" in why


def test_party_labels_in_the_mit_files_are_read_whatever_their_spelling():
    assert B.party_of("DEMOCRATIC-FARMER-LABOR", "OTHER") == "D"
    assert B.party_of("DEMOCRAT", "DEMOCRAT") == "D"
    assert B.party_of("REPUBLICAN", "REPUBLICAN") == "R"
    assert B.party_of("", "OTHER") == "other"


def test_write_ins_are_dropped_unless_they_are_all_there_is():
    both = [("A", "D", 10.0, False, False), ("B", "R", 9.0, False, False), ("C", "R", 8.0, False, True)]
    assert [c[0] for c in B.without_writeins(both)] == ["A", "B"]
    only = [("A", "D", 10.0, False, True), ("B", "R", 9.0, False, True)]
    assert len(B.without_writeins(only)) == 2


def test_lean_is_the_mean_of_the_last_two_elections_state_margin_minus_the_nations():
    state = {(2012, "XX"): 10.0, (2016, "XX"): 6.0, (2008, "XX"): 99.0}
    nation = {2008: 0.0, 2012: 4.0, 2016: 2.0}
    assert B.lean_for(2018, "XX", state, nation) == pytest.approx(5.0)   # (10-4 + 6-2) / 2
    assert B.lean_for(2014, "XX", state, nation) == pytest.approx(52.5)  # 2008 and 2012: (99-0 + 10-4) / 2
    assert B.lean_for(2010, "XX", state, nation) is None                # only one election before: no lean


def test_final_polls_are_those_ending_within_21_days_up_to_election_day():
    day = dt.date(2022, 11, 8)
    p = lambda off: {"end": day - dt.timedelta(days=off)}
    got = B.final_polls([p(0), p(21), p(22), {"end": day + dt.timedelta(days=1)}], day)
    assert len(got) == 2
