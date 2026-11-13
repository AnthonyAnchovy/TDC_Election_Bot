"""Finding a race from what someone types, and which chamber a race belongs to."""
import pytest

import races as R


def test_every_race_finds_itself_first():
    assert [rid for rid in R.RACES if R.search(rid, 1) != [rid]] == []


@pytest.mark.parametrize("typed, want", [
    ("florida gov", "FL-GOV"), ("fl gov", "FL-GOV"), ("FL-GOV", "FL-GOV"), ("fl-gov", "FL-GOV"),
    ("ohio governor", "OH-GOV"), ("oh gov", "OH-GOV"),          # the race nobody could find a command for in 2026
    ("tx 15", "TX-15"), ("texas 15", "TX-15"), ("TX-15", "TX-15"), ("tx15", None),
    ("ms sen", "MS-SEN"), ("mississippi senate", "MS-SEN"), ("montana sen", "MT-SEN"),
    ("ak al", "AK-AL"), ("alaska at-large", "AK-AL"),
    ("senate", "US-SENATE"), ("house", "US-HOUSE"), ("control of the senate", "US-SENATE"),
])
def test_what_people_type_finds_the_race(typed, want):
    found = R.search(typed)
    assert (found[0] if found else None) == want


def test_a_state_lists_its_statewide_races_before_its_districts():
    assert R.search("michigan")[:2] == ["MI-SEN", "MI-GOV"]
    assert all(rid.startswith("MI-") for rid in R.search("michigan"))


def test_nothing_typed_leads_with_the_control_races():
    assert R.search("")[:2] == ["US-HOUSE", "US-SENATE"]
    assert len(R.search("")) == 25 and len(R.search("", 5)) == 5


def test_nonsense_finds_nothing():
    assert R.search("zzzz") == []


def test_loose_ids_normalize():
    for typed, want in (("tx-sen", "TX-SEN"), ("TX 7", "TX-07"), ("ak al", "AK-AL"), ("house", "US-HOUSE"),
                        ("senate", "US-SENATE"), ("Senate Control", "US-SENATE"), ("ga governor", "GA-GOV")):
        assert R.normalize(typed) == want
    assert R.normalize("florida") is None


def test_each_race_counts_toward_one_chamber():
    assert (R.chamber("US-SENATE"), R.chamber("US-HOUSE")) == ("senate", "house")
    assert (R.chamber("GA-SEN"), R.chamber("GA-GOV"), R.chamber("TX-15")) == ("senate", "governor", "house")
    assert R.chamber_name("US-SENATE") == "Senate" and R.chamber_name("AK-AL") == "House"
    assert R.RACES["US-SENATE"]["type"] == R.RACES["US-HOUSE"]["type"] == "control"
    assert "US-SENATE" not in R.races_of("senate")      # the 35 seats and control of the chamber are separate races
