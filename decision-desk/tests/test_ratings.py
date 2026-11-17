"""The expert-ratings table (tools/build_ratings.py). Everything here is offline: small saved
snippets of Wikipedia's wikitext live in tests/fixtures/ratings/, one per table layout."""
import csv
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_ratings as R  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "ratings")


def rows_of(name, year, office, monkeypatch):
    """Run one fixture through the page reader as if it were that year's page."""
    monkeypatch.setitem(R.PAGES, "fixture-" + name, (year, office))
    with open(os.path.join(FIX, name + ".txt"), encoding="utf-8") as f:
        out, unmapped, skipped, notes = R.page_rows("fixture-" + name, f.read())
    return {(r[0], r[1]): list(r[2:]) for r in out}, unmapped, skipped, notes


# ---- a single rating

@pytest.mark.parametrize("cell,rating,level", [
    ("{{USRaceRating|Safe|R}}", "Safe R", -3),
    ("{{USRaceRating|Solid|D}}", "Solid D", 3),
    ("{{USRaceRating|Likely|R}}", "Likely R", -2),
    ("{{USRaceRating|Lean|D}}", "Lean D", 1),
    ("{{USRaceRating|Leans|R|flip}}", "Leans R (flip)", -1),
    ("{{USRaceRating|Tilt|D}}", "Tilt D", 0.5),
    ("{{USRaceRating|Tilt|R|flip}}", "Tilt R (flip)", -0.5),
    ("{{USRaceRating|Tossup}}", "Tossup", 0),
    ("{{sort|108|Safe R}}", "Safe R", -3),
    ("Likely D {{Small|(flip)}}", "Likely D (flip)", 2),
    ("Toss-up", "Tossup", 0),
    ("SafeR", "Safe R", -3),
])
def test_a_rating_cell_reads_as_its_words_and_a_level_from_minus_3_to_plus_3(cell, rating, level):
    assert R.read_rating(cell) == (rating, level)


def test_an_independent_seat_keeps_its_words_but_gets_no_level():
    assert R.read_rating("{{sort|098|Lean I {{small|(flip)}}}}") == ("Lean I (flip)", "")


@pytest.mark.parametrize("cell", ["", "n/a", "—", "Not rated"])
def test_an_empty_cell_is_no_rating_not_a_toss_up(cell):
    assert R.read_rating(cell) == (None, None)


@pytest.mark.parametrize("cell", ["Possible", "D: 290 R: 125 Tossup: 123", "Safe", "Leaning R maybe"])
def test_text_that_is_not_a_rating_is_flagged_not_guessed(cell):
    assert R.read_rating(cell)[0] == "?"


# ---- headings

def test_a_heading_gives_the_rater_and_the_date_in_it():
    head = "[[Cook Political Report|Cook]]<br />{{Small|Oct 28,<br />2022}}<ref>{{cite web|url=x}}</ref>"
    head = R.prepare(head)
    assert R.rater_name(head) == "Cook"
    assert R.header_date(head) == "2022-10-28"


@pytest.mark.parametrize("head,name", [
    ("[[Rothenberg Political Report|Rothenberg]]", "Inside"),
    ("[[Inside Elections|IE]]", "Inside"),
    ("[[Sabato's Crystal Ball|Sabato]]", "Sabato"),
    ("[[RealClearPolitics|RCP]]", "RCP"),
    ("[[FiveThirtyEight|538]]", "538"),
    ("[[CQ Politics]]", "CQ"),
    ("[[Niskanen Center]]", "Niskanen Center"),
])
def test_the_same_rater_gets_one_name_across_years(head, name):
    assert R.rater_name(head) == name


def test_a_heading_with_no_date_gives_a_blank_date():
    assert R.header_date("[[Politico]]") == ""


# ---- layout 1: USRaceRating template, two-row heading with a colspan wider than the table

def test_template_layout_reads_every_rater_column_with_its_date(monkeypatch):
    got, unmapped, skipped, notes = rows_of("template_senate", 2022, "SEN", monkeypatch)
    assert got[("2022-GA-SEN", "Cook")] == ["Tossup", 0, "2022-10-28"]
    assert got[("2022-GA-SEN", "Inside")] == ["Tossup", 0, "2022-11-03"]
    assert got[("2022-GA-SEN", "Sabato")] == ["Lean D", 1, "2022-11-07"]
    assert got[("2022-VT-SEN", "Cook")][:2] == ["Solid D", 3]
    assert got[("2022-WI-SEN", "Inside")][:2] == ["Tilt R", -0.5]
    assert got[("2022-WI-SEN", "Sabato")][:2] == ["Lean R (flip)", -1]
    assert len(got) == 9 and not unmapped and not skipped


def test_the_pvi_incumbent_and_result_columns_are_not_taken_for_raters(monkeypatch):
    got, *_ = rows_of("template_senate", 2022, "SEN", monkeypatch)
    assert {rater for _, rater in got} == {"Cook", "Inside", "Sabato"}


# ---- layout 2: plain words in the cells

def test_text_layout_of_senate_2014_reads_sorted_words(monkeypatch):
    got, unmapped, skipped, notes = rows_of("text_senate_2014", 2014, "SEN", monkeypatch)
    assert got[("2014-AL-SEN", "Cook")] == ["Safe R", -3, "2014-10-29"]
    assert got[("2014-AL-SEN", "Inside")][:2] == ["Safe R", -3]  # Rothenberg is Inside Elections
    assert got[("2014-AL-SEN", "538")][:2] == ["Safe R", -3]  # written "SafeR" in the table
    assert got[("2014-KS-SEN", "Inside")][:2] == ["Tossup", 0]
    assert not unmapped and not skipped


def test_an_independent_in_the_table_is_kept_with_a_blank_level_and_listed(monkeypatch):
    got, *_ = rows_of("text_senate_2014", 2014, "SEN", monkeypatch)
    assert got[("2014-KS-SEN", "538")] == ["Lean I (flip)", "", "2014-10-29"]


def test_an_empty_or_n_a_cell_leaves_that_rater_out_of_the_race(monkeypatch):
    got, *_ = rows_of("text_senate_2014", 2014, "SEN", monkeypatch)
    assert ("2014-VT-SEN", "Inside") not in got and ("2014-VT-SEN", "538") not in got
    assert got[("2014-VT-SEN", "Cook")][:2] == ["Safe D", 3]


def test_text_layout_of_governor_2018_reads_flip_marks_and_trailing_symbols(monkeypatch):
    got, unmapped, skipped, notes = rows_of("text_governor_2018", 2018, "GOV", monkeypatch)
    assert got[("2018-AL-GOV", "Sabato")] == ["Likely R", -2, "2018-11-05"]  # the "^" is dropped
    assert got[("2018-AK-GOV", "Cook")][:2] == ["Lean R (flip)", -1]
    assert got[("2018-AK-GOV", "Inside")] == ["Tilt R (flip)", -0.5, "2018-11-01"]
    assert got[("2018-AK-GOV", "Sabato")][:2] == ["Tossup", 0]


def test_words_that_are_not_a_rating_are_listed_never_guessed(monkeypatch):
    got, unmapped, skipped, notes = rows_of("text_governor_2018", 2018, "GOV", monkeypatch)
    assert {u[4] for u in unmapped} == {"?? something odd", "Possible"}
    assert ("2018-ME-GOV", "Cook") not in got and ("2018-ME-GOV", "Sabato") not in got
    assert got[("2018-ME-GOV", "Inside")][:2] == ["Tossup D", 0]


# ---- president, special races, broken tables

def test_president_by_state_skips_comment_markers_and_districts_and_national_rows(monkeypatch):
    got, unmapped, skipped, notes = rows_of("president_2012", 2012, "PRES", monkeypatch)
    assert got[("2012-FL-PRES", "RCP")] == ["Tossup", 0, "2012-11-06"]
    assert got[("2012-FL-PRES", "Sabato")][:2] == ["Lean R (flip)", -1]
    assert got[("2012-FL-PRES", "538")][:2] == ["Lean D", 1]
    assert got[("2012-AZ-PRES", "538")][:2] == ["Safe R", -3]
    assert not any(r.startswith("2012-NE") for r, _ in got)  # Nebraska's 2nd is a district
    assert any("Nebraska's 2nd" in n for n in notes)
    assert not unmapped  # the national-totals row is not a state, so it is not listed either


def test_a_second_senate_race_in_a_state_is_sen2_and_a_lone_special_is_sen(monkeypatch):
    got, *_ = rows_of("special_senate", 2020, "SEN", monkeypatch)
    assert got[("2020-GA-SEN", "Sabato")][:2] == ["Lean R", -1]
    assert got[("2020-GA-SEN2", "Cook")][:2] == ["Tossup", 0]
    assert got[("2020-AZ-SEN", "Cook")][:2] == ["Lean D (flip)", 1]
    assert not any(r == "2020-AZ-SEN2" for r, _ in got)


def test_a_table_missing_its_closing_bar_ends_at_the_collapse_box(monkeypatch):
    got, *_ = rows_of("unclosed_table", 2022, "SEN", monkeypatch)
    assert got == {("2022-OH-SEN", "Cook"): ["Likely R", -2, "2022-11-07"]}


# ---- the table reader on its own

def test_cells_split_at_double_bars_but_not_inside_links_or_templates():
    rows = R.read_table("{|\n|-\n| a || [[x|y]] || {{t|1|2}}\n|}")
    assert [c["text"] for c in rows[0]] == ["a", "[[x|y]]", "{{t|1|2}}"]


def test_cell_attributes_are_kept_apart_from_the_text_and_spans_are_read():
    rows = R.read_table('{|\n|-\n! colspan="3" rowspan=2 | Head\n|}')
    assert rows[0][0]["text"] == "Head"
    assert (rows[0][0]["colspan"], rows[0][0]["rowspan"]) == (3, 2)


def test_comments_and_footnotes_are_removed_before_reading():
    text = R.prepare("a<!-- hidden | cell -->b<ref name=x>note</ref>c<ref name=y />d{{efn|note {{x}} more}}e")
    assert text == "abcde"


# ---- the CSV

def test_the_csv_has_the_agreed_columns_and_writes_blank_for_no_level(tmp_path):
    path = tmp_path / "ratings.csv"
    R.write_csv([("2014-KS-SEN", "538", "Lean I (flip)", "", "2014-10-29"),
                 ("2022-GA-SEN", "Cook", "Tossup", 0.0, "2022-10-28"),
                 ("2022-WI-SEN", "Inside", "Tilt R", -0.5, "")], str(path))
    with open(path, newline="", encoding="utf-8") as f:
        got = list(csv.reader(f))
    assert got[0] == ["race", "rater", "rating", "level", "as_of"]
    assert got[1] == ["2014-KS-SEN", "538", "Lean I (flip)", "", "2014-10-29"]
    assert got[2] == ["2022-GA-SEN", "Cook", "Tossup", "0", "2022-10-28"]
    assert got[3] == ["2022-WI-SEN", "Inside", "Tilt R", "-0.5", ""]


LEVELS = {-3, -2, -1, -0.5, 0, 0.5, 1, 2, 3}
BUILT = os.path.join(ROOT, "history", "ratings.csv")


@pytest.mark.skipif(not os.path.exists(BUILT), reason="history/ratings.csv has not been built here")
def test_the_built_file_has_one_row_per_race_and_rater_and_only_allowed_levels():
    with open(BUILT, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows and list(rows[0]) == ["race", "rater", "rating", "level", "as_of"]
    assert len({(r["race"], r["rater"]) for r in rows}) == len(rows)
    for r in rows:
        assert re.fullmatch(r"\d{4}-[A-Z]{2}-(PRES|SEN2?|GOV)", r["race"]), r
        assert r["rater"].strip() == r["rater"] and r["rater"]
        assert r["level"] == "" or float(r["level"]) in LEVELS, r
        assert r["as_of"] == "" or re.fullmatch(r"\d{4}-\d\d-\d\d", r["as_of"]), r
        # the sign of the level agrees with the party in the words
        if r["level"] != "" and float(r["level"]) != 0:
            assert (" D" in " " + r["rating"]) == (float(r["level"]) > 0), r
        if r["level"] == "":
            assert " I" in r["rating"], r
