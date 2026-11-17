"""The Wikipedia poll importer (cw_polls_wiki). Everything here is offline: the pages are saved wikitext in
tests/fixtures/wiki/ and the network is never touched."""
import csv
import datetime as dt
from pathlib import Path

import pytest

import cw_polls as P
import cw_polls_wiki as W

FIX = Path(__file__).parent / "fixtures" / "wiki"
TODAY = "2026-11-17"
GA = {"Ossoff": "D", "Collins": "R"}
CHICAGO = {"Buckner": "A", "García": "B", "Green": "C", "Johnson": "D", "King": "E", "Lightfoot": "F",
           "Lopez": "G", "Sawyer": "H", "Vallas": "I", "Wilson": "J"}


def page(name):
    return (FIX / name).read_text(encoding="utf-8")


def read(name, race, options, **kw):
    return W.parse(page(name), race, options, today=TODAY, **kw)


def polls_of(rows):
    seen = {}
    for r in rows:
        seen.setdefault((r["pollster"], r["start"], r["end"], r["population"]), {})[r["option"]] = r["share"]
    return seen


def assert_passes_check(rows, tmp_path, races=None):
    assert rows
    path = tmp_path / "polls.csv"
    assert W.write_csv(rows, path, races=races, today=TODAY) == []
    assert P.check(path, races=races, today=TODAY) == []
    return path


# ---- a 2026 Senate race page

def test_the_2026_georgia_senate_page_gives_rows_that_pass_the_polls_check(tmp_path):
    rows = read("senate_2026_georgia.wiki", "GA-SEN", GA, under="General")
    assert_passes_check(rows, tmp_path, races={"GA-SEN": ["D", "R"]})
    assert {r["option"] for r in rows} == {"D", "R"}
    assert len(polls_of(rows)) == 26


def test_a_pollster_name_has_no_links_references_or_party_tag(tmp_path):
    rows = read("senate_2026_georgia.wiki", "GA-SEN", GA, under="General")
    names = {r["pollster"] for r in rows}
    assert "InsiderAdvantage" in names and "Research Co." in names
    assert not any(set("[]<>{}|") & set(n) or n.endswith(")") for n in names)


def test_a_party_tag_after_the_pollster_becomes_its_sponsor():
    rows = read("senate_2026_georgia.wiki", "GA-SEN", GA, under="General")
    sponsors = {r["pollster"]: r["sponsor"] for r in rows}
    assert sponsors["InsiderAdvantage"] == "R"
    assert sponsors["Research Co."] == ""


def test_sample_size_and_kind_of_voter_are_read_from_one_cell():
    rows = read("senate_2026_georgia.wiki", "GA-SEN", GA, under="General")
    first = next(r for r in rows if r["pollster"] == "InsiderAdvantage")
    assert (first["n"], first["population"]) == (2500, "lv")
    assert (first["start"], first["end"]) == ("2026-10-31", "2026-11-02")
    assert (first["option"], first["share"]) == ("D", 53.0)


def test_a_table_of_polling_averages_is_not_read_as_polls():
    rows = read("senate_2026_georgia.wiki", "GA-SEN", GA, under="General")
    assert not {"270toWin", "RealClearPolitics", "Silver Bulletin", "Average"} & {r["pollster"] for r in rows}
    assert any("polling averages" in n for n in rows.notes)


def test_head_to_head_tables_against_other_candidates_are_skipped_with_a_reason():
    rows = read("senate_2026_georgia.wiki", "GA-SEN", GA, under="General")
    notes = " ".join(rows.notes)
    assert "no column for Collins" in notes and "Raffensperger" in notes
    cygnal = [r for r in rows if r["pollster"] == "Cygnal" and r["start"] == "2025-05-15"]
    assert {r["share"] for r in cygnal} == {46.0, 43.0}  # the Collins table, not the 44-44 Raffensperger one


def test_extra_versions_of_one_poll_keep_only_the_first_row():
    rows = read("senate_2026_georgia.wiki", "GA-SEN", GA, under="General")
    quantus = [r for r in rows if r["pollster"] == "Quantus Insights" and r["start"] == "2026-10-15"]
    assert len(quantus) == 2  # one poll, two options
    assert any("second row for Quantus Insights" in n for n in rows.notes)


def test_a_primary_page_section_can_be_left_out_with_under():
    everything = read("senate_2026_georgia.wiki", "GA-SEN", GA)
    nothing = read("senate_2026_georgia.wiki", "GA-SEN", GA, under="Runoff")
    assert len(everything) > 0 and nothing == []


# ---- the 2028 national presidential page

def test_the_2028_presidential_page_matches_last_names_with_party_tags(tmp_path):
    rows = read("president_2028_national.wiki", "US-PRES", {"Vance": "R", "Harris": "D"})
    assert_passes_check(rows, tmp_path)
    assert len(polls_of(rows)) == 8
    emerson = polls_of(rows)[("Emerson College", "2026-08-16", "2026-08-17", "lv")]
    assert emerson == {"R": 45.0, "D": 49.0}


def test_a_three_way_hypothetical_is_skipped_even_when_the_two_mapped_candidates_are_in_it():
    rows = read("president_2028_national.wiki", "US-PRES", {"Vance": "R", "Newsom": "D"})
    assert len(polls_of(rows)) == 19
    assert any("Rodgers" in n and "different match-up" in n for n in rows.notes)
    assert any("Smith" in n and "different match-up" in n for n in rows.notes)


def test_a_table_missing_a_mapped_candidate_is_skipped_with_a_note():
    rows = read("president_2028_national.wiki", "US-PRES", {"Vance": "R", "Harris": "D"})
    assert any("Skipped" in n and "no column for Harris" in n and "Newsom" in n for n in rows.notes)


# ---- the 2023 Chicago mayoral race: nonpartisan, many candidates

def test_chicago_first_round_polls_give_a_share_for_each_candidate_and_pass_the_check(tmp_path):
    rows = read("chicago_2023_mayor.wiki", "CHI-MAYOR", CHICAGO, under="First round")
    races = {"CHI-MAYOR": list("ABCDEFGHIJ")}
    assert_passes_check(rows, tmp_path, races=races)
    assert len(polls_of(rows)) == 26
    victory = polls_of(rows)[("Victory Research", "2023-02-23", "2023-02-26", "lv")]
    assert victory == {"B": 15.6, "D": 20.2, "F": 18.7, "I": 26.8, "J": 11.4}


def test_other_and_undecided_columns_are_never_options():
    rows = read("chicago_2023_mayor.wiki", "CHI-MAYOR", CHICAGO, under="First round")
    assert {r["option"] for r in rows} <= set("ABCDEFGHIJ")


def test_a_dash_in_a_candidates_cell_leaves_that_candidate_out_of_the_poll():
    rows = read("chicago_2023_mayor.wiki", "CHI-MAYOR", CHICAGO, under="First round")
    victory = polls_of(rows)[("Victory Research", "2023-02-23", "2023-02-26", "lv")]
    assert "A" not in victory and "C" not in victory


def test_mapping_only_some_chicago_candidates_skips_the_many_candidate_table_unless_allowed():
    two = {"Vallas": "I", "Johnson": "D"}
    strict = read("chicago_2023_mayor.wiki", "CHI-MAYOR", two, under="First round")
    loose = read("chicago_2023_mayor.wiki", "CHI-MAYOR", two, under="First round", allow_extra=True)
    assert strict == [] and any("other candidates" in n for n in strict.notes)
    assert len(polls_of(loose)) == 25


def test_the_runoff_table_is_read_on_its_own_with_the_two_runoff_candidates():
    rows = read("chicago_2023_mayor.wiki", "CHI-RUNOFF", {"Vallas": "I", "Johnson": "D"}, under="Runoff")
    assert len(polls_of(rows)) == 12
    assert all(sum(v.values()) <= 100 for v in polls_of(rows).values())


# ---- an older page with a plainer table

def test_the_2008_minnesota_page_reads_single_day_polls_with_no_sample_size(tmp_path):
    rows = read("senate_2008_minnesota.wiki", "MN-SEN", {"Coleman": "R", "Franken": "D", "Barkley": "I"})
    assert_passes_check(rows, tmp_path)
    first = rows[0]
    assert (first["pollster"], first["start"], first["end"]) == ("Survey USA", "2008-11-01", "2008-11-01")
    assert first["n"] == "" and first["population"] == ""
    assert polls_of(rows)[("Survey USA", "2008-11-01", "2008-11-01", "")] == {"R": 44.0, "D": 39.0, "I": 16.0}


def test_a_candidate_whose_name_contains_a_column_word_is_still_a_candidate():
    # "Franken" holds the letters of "rank"; it must not be taken for a ranking column
    assert W._kind("Al Franken (DFL)") == "candidate"
    assert W._kind("Pollster's rating from 538") == "rating"


# ---- dates

@pytest.mark.parametrize("text, year, start, end", [
    ("Oct 1–5, 2026", None, "2026-10-01", "2026-10-05"),
    ("September 28 – October 2, 2026", None, "2026-09-28", "2026-10-02"),
    ("May 21, 2026", None, "2026-05-21", "2026-05-21"),
    ("Oct. 28 - Nov. 2, 2026", None, "2026-10-28", "2026-11-02"),
    ("1–5 October 2026", None, "2026-10-01", "2026-10-05"),
    ("2026-10-05", None, "2026-10-05", "2026-10-05"),
    ("Oct 1–5", 2026, "2026-10-01", "2026-10-05"),
    ("Sep 28 – Oct 2", 2026, "2026-09-28", "2026-10-02"),
    ("Oct 7", 2026, "2026-10-07", "2026-10-07"),
    ("December 28 – January 3, 2026", None, "2025-12-28", "2026-01-03"),
    ("Dec 28 – Jan 3", 2026, "2025-12-28", "2026-01-03"),
    ("December 28, 2025 – January 3, 2026", None, "2025-12-28", "2026-01-03"),
])
def test_dates_are_read_the_way_wikipedia_writes_them(text, year, start, end):
    assert W.parse_date(text, year) == (dt.date.fromisoformat(start), dt.date.fromisoformat(end))


@pytest.mark.parametrize("text", ["Oct 1–5", "Mid-November", "October 2026", "", "–"])
def test_a_date_that_cannot_be_read_is_none_not_a_guess(text):
    assert W.parse_date(text, None) is None


def test_dates_without_a_year_take_the_year_heading_they_sit_under():
    rows = read("year_headings_made_up.wiki", "X", {"Smith": "D", "Jones": "R"})
    by = polls_of(rows)
    assert ("Alpha Research", "2026-10-01", "2026-10-05", "lv") in by
    assert ("Beta Poll", "2026-09-28", "2026-10-02", "rv") in by
    assert ("Delta Group", "2026-10-07", "2026-10-07", "") in by


def test_a_range_across_new_year_under_a_year_heading_starts_in_the_year_before():
    rows = read("year_headings_made_up.wiki", "X", {"Smith": "D", "Jones": "R"})
    assert ("Gamma Survey", "2025-12-28", "2026-01-03", "a") in polls_of(rows)


def test_a_year_row_inside_a_table_sets_the_year_for_the_rows_below_it():
    rows = read("year_headings_made_up.wiki", "X", {"Smith": "D", "Jones": "R"})
    assert ("Epsilon", "2025-11-03", "2025-11-06", "lv") in polls_of(rows)


def test_a_date_with_no_year_and_no_heading_is_skipped_with_a_note():
    rows = read("year_headings_made_up.wiki", "X", {"Smith": "D", "Jones": "R"})
    assert not any(r["pollster"] == "Zeta" for r in rows)
    assert any("Zeta" in n and "could not be read" in n for n in rows.notes)
    text = "{| class=wikitable\n! Poll source !! Dates !! Smith !! Jones\n|-\n| Alpha || Oct 1–5 || 48% || 44%\n|}"
    assert W.parse(text, "X", {"Smith": "D", "Jones": "R"}, today=TODAY) == []
    assert len(W.parse(text, "X", {"Smith": "D", "Jones": "R"}, default_year=2026, today=TODAY)) == 2


def test_a_poll_dated_after_today_is_left_out():
    rows = W.parse(page("year_headings_made_up.wiki"), "X", {"Smith": "D", "Jones": "R"}, today="2026-09-30")
    assert not any(r["pollster"] == "Alpha Research" for r in rows)
    assert any("after today" in n for n in rows.notes)


# ---- hypothetical match-ups, sponsors, pages with no polls

def test_hypothetical_tables_are_skipped_unless_every_mapped_candidate_is_in_them():
    rows = read("year_headings_made_up.wiki", "X", {"Smith": "D", "Jones": "R"})
    assert len([r for r in rows if r["pollster"] == "Alpha Research"]) == 2
    assert any("no column for Jones" in n for n in rows.notes)
    only_smith = read("year_headings_made_up.wiki", "X", {"Smith": "D", "Doe": "R"})
    assert len(only_smith) == 2 and only_smith[1]["share"] == 43.0


def test_a_footnote_naming_the_campaign_that_paid_gives_the_sponsor():
    rows = read("year_headings_made_up.wiki", "X", {"Smith": "D", "Jones": "R"})
    alpha = next(r for r in rows if r["pollster"] == "Alpha Research")
    assert alpha["sponsor"] == "D"


def test_a_page_with_no_poll_table_returns_no_rows_and_says_why():
    rows = read("no_polls_senate_2026.wiki", "GA-SEN", GA)
    assert rows == [] and rows.tables == 0
    assert rows.notes and "no opinion-poll table" in rows.notes[0]
    empty = W.parse("Just some words, no tables.", "GA-SEN", GA, today=TODAY)
    assert empty == [] and "no opinion-poll table" in empty.notes[0]


def test_a_page_with_poll_tables_for_other_candidates_says_none_could_be_used():
    rows = read("chicago_2023_mayor.wiki", "CHI-MAYOR", {"Nobody": "Z"})
    assert rows == [] and "none could be used" in rows.notes[0]


def test_shares_that_add_up_to_over_a_hundred_are_dropped():
    text = "{| class=wikitable\n! Poll source !! Dates !! Smith !! Jones\n|-\n| Alpha || Oct 1–5, 2026 || 70% || 60%\n|}"
    rows = W.parse(text, "X", {"Smith": "D", "Jones": "R"}, today=TODAY)
    assert rows == [] and any("over 100" in n for n in rows.notes)


# ---- writing the file

def one_poll(**kw):
    row = {"race": "GA-SEN", "pollster": "Acme", "start": "2026-10-20", "end": "2026-10-22", "n": 800,
           "population": "lv", "sponsor": "", "option": "D", "share": 48.0}
    row.update(kw)
    return row


def test_write_csv_makes_the_file_with_the_polls_header(tmp_path):
    path = tmp_path / "polls.csv"
    assert W.write_csv([one_poll(), one_poll(option="R", share=44.0)], path, today=TODAY) == []
    assert path.read_text(encoding="utf-8").splitlines()[0] == ",".join(P.COLUMNS)
    assert len(P.load(path, today=TODAY)) == 1


def test_write_csv_appends_to_a_file_that_is_there(tmp_path):
    path = tmp_path / "polls.csv"
    W.write_csv([one_poll(), one_poll(option="R", share=44.0)], path, today=TODAY)
    more = [one_poll(pollster="Beta"), one_poll(pollster="Beta", option="R", share=40.0)]
    assert W.write_csv(more, path, today=TODAY) == []
    assert {p["pollster"] for p in P.load(path, today=TODAY)} == {"Acme", "Beta"}


def test_running_the_same_import_twice_does_not_list_a_poll_twice(tmp_path):
    path = tmp_path / "polls.csv"
    rows = [one_poll(), one_poll(option="R", share=44.0)]
    W.write_csv(rows, path, today=TODAY)
    before = path.read_text(encoding="utf-8")
    assert W.write_csv(rows, path, today=TODAY) == []
    assert path.read_text(encoding="utf-8") == before


def test_a_result_that_fails_the_check_is_not_written_and_the_reasons_come_back(tmp_path):
    path = tmp_path / "polls.csv"
    W.write_csv([one_poll()], path, today=TODAY)
    before = path.read_bytes()
    problems = W.write_csv([one_poll(pollster="Late", end="2027-01-01", start="2026-12-30")], path, today=TODAY)
    assert problems and "after today" in problems[0]
    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["polls.csv"]  # no half-made file left behind


def test_a_new_file_that_fails_the_check_is_never_created(tmp_path):
    path = tmp_path / "polls.csv"
    problems = W.write_csv([one_poll(race="NOWHERE")], path, races={"GA-SEN": ["D", "R"]}, today=TODAY)
    assert problems and "NOWHERE" in problems[0]
    assert not path.exists()


def test_writing_no_rows_is_refused_with_a_reason(tmp_path):
    assert W.write_csv([], tmp_path / "polls.csv")
    assert not (tmp_path / "polls.csv").exists()


# ---- the command line and the network (the network is faked)

def test_the_command_line_reads_a_saved_page_writes_the_file_and_the_credit(tmp_path, capsys):
    out = tmp_path / "polls.csv"
    code = W.main(["2026 United States Senate election in Georgia", "--race", "GA-SEN", "--map", "Ossoff=D,Collins=R",
                   "--out", str(out), "--under", "General", "--today", TODAY,
                   "--from-file", str(FIX / "senate_2026_georgia.wiki")])
    printed = capsys.readouterr().out
    assert code == 0 and "Wrote 26 polls" in printed and "CC BY-SA 4.0" in printed
    assert len(P.load(out, today=TODAY)) == 26
    assert "CC BY-SA 4.0" in (tmp_path / "polls_credit.txt").read_text(encoding="utf-8")


def test_the_command_line_says_so_and_writes_nothing_when_the_page_has_no_polls(tmp_path, capsys):
    out = tmp_path / "polls.csv"
    code = W.main(["x", "--race", "GA-SEN", "--map", "Ossoff=D", "--out", str(out), "--today", TODAY,
                   "--from-file", str(FIX / "no_polls_senate_2026.wiki")])
    assert code == 1 and "no opinion-poll table" in capsys.readouterr().out and not out.exists()


def test_the_credit_line_names_the_page_and_the_licence():
    line = W.credit("2023 Chicago mayoral election")
    assert "https://en.wikipedia.org/wiki/2023_Chicago_mayoral_election" in line and "CC BY-SA 4.0" in line


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.text.encode("utf-8")


def test_fetch_asks_for_the_raw_wikitext_with_a_plain_user_agent_and_waits(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"], seen["agent"] = req.full_url, req.get_header("User-agent")
        return FakeResponse("{| wikitext |}")
    slept = []
    monkeypatch.setattr(W.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(W.time, "sleep", slept.append)
    monkeypatch.setattr(W, "_last_request", [W.time.monotonic()])
    assert W.fetch("2026 United States Senate election in Georgia") == "{| wikitext |}"
    assert seen["url"] == ("https://en.wikipedia.org/w/index.php?title=2026_United_States_Senate_election_in_Georgia"
                           "&action=raw")
    assert seen["agent"] == W.USER_AGENT and "@" not in seen["agent"]
    assert slept and slept[0] > 0


def test_fetch_turns_a_missing_page_into_a_plain_error(monkeypatch):
    def gone(req, timeout=None):
        raise W.urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    monkeypatch.setattr(W.urllib.request, "urlopen", gone)
    monkeypatch.setattr(W.time, "sleep", lambda s: None)
    with pytest.raises(W.FetchError, match="no such page"):
        W.fetch("No such page at all")


def test_the_tests_never_reach_the_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the network was used")
    monkeypatch.setattr(W.urllib.request, "urlopen", refuse)
    read("president_2028_national.wiki", "US-PRES", {"Vance": "R", "Harris": "D"})
