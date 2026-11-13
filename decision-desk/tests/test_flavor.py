"""Flavor lines: the right kind for the result, never the same one over and over, and every line in the file usable.

The last test replays election night 2026's calls through the picker. On the night, 118 of 133 cards drew from seven
lines and one line appeared 28 times.
"""
import collections
import random
import string

import pytest

import desk as DESK
import flavor as FL
import packs
import picks as P
import races as R


def names(rid):
    cands = P.candidates()
    return {k: P.name_of(rid, k, cands) for k in P.options_for(rid, cands)}


@pytest.fixture
def rng():
    return random.Random(2026)


# ------------------------------------------------------------------ the deck
def test_a_pool_is_used_up_before_any_line_comes_round_again(store, rng):
    pool = [f"line {i}" for i in range(6)]
    first = [FL.draw(store, "p", pool, rng=rng) for _ in range(6)]
    assert sorted(first) == sorted(pool)
    second = [FL.draw(store, "p", pool, rng=rng) for _ in range(6)]
    assert sorted(second) == sorted(pool) and second[0] != first[-1]       # and no back-to-back repeat at the turn


def test_the_deck_survives_a_restart(store, rng, tmp_path):
    from store import Store
    path = str(tmp_path / "desk.db")
    a = Store(path)
    used = {FL.draw(a, "p", ["one", "two", "three"], rng=rng) for _ in range(2)}
    a.db.close()
    assert FL.draw(Store(path), "p", ["one", "two", "three"], rng=rng) not in used


def test_pools_keep_separate_decks(store, rng):
    assert FL.draw(store, "a", ["x"], rng=rng) == "x" and FL.draw(store, "b", ["x"], rng=rng) == "x"


def test_a_line_that_cannot_be_filled_is_skipped(store, rng):
    lines = ["{winner} beats {loser}.", "{winner} wins."]
    assert FL.draw(store, "p", lines, {"winner": "Ann", "loser": None}, rng) == "Ann wins."
    assert FL.draw(store, "q", ["{winner} beats {loser}."], {"winner": "Ann", "loser": None}, rng) is None
    assert FL.draw(store, "r", ["broken {brace"], {"winner": "Ann"}, rng) is None
    assert FL.draw(store, "s", [], {}, rng) is None and FL.draw(store, "s", ["", "  "], {}, rng) is None


def test_without_a_store_it_still_picks(rng):
    assert FL.draw(None, "p", ["only"], rng=rng) == "only"


def test_memory_does_not_grow_without_limit(store, rng, monkeypatch):
    monkeypatch.setattr(FL, "KEEP", 5)
    for i in range(12):
        FL.draw(store, f"pool{i}", ["x"], rng=rng)
    assert len(store.setting(FL.MEMORY)) == 5 and "pool11" in store.setting(FL.MEMORY)


# ------------------------------------------------------------------ the right kind of line
def test_what_kind_of_result_a_call_is():
    assert FL.situation("GA-SEN", "D") == "hold" and FL.situation("GA-SEN", "R") == "flip"      # D-held, unrated
    assert FL.situation("US-HOUSE", "D") == "control"
    assert FL.situation("TX-15", "D") == "flip"          # a toss-up nobody is favored in, R-held
    assert FL.situation("VA-02", "R") == "upset"         # rated Tilt D
    assert FL.situation("VA-02", "D") == "flip"          # as rated, and it changes hands
    assert FL.situation("VT-AL", "D") == "call"          # a safe seat with no holder on file


def test_what_a_line_can_mention():
    c = FL.context("VA-02", "R", names("VA-02"))
    assert c["seat"] == "Virginia 2nd District" and c["place"] == "Virginia" and c["rating"] == "Tilt D" and c["party"] == "Republican"
    assert c["winner"] == P.name_of("VA-02", "R") and c["loser"] == P.name_of("VA-02", "D")
    house = FL.context("US-HOUSE", "D", names("US-HOUSE"))
    assert (house["winner"], house["loser"], house["place"]) == ("Democrats", "Republicans", "Control of the House")
    blank = FL.context("VT-AL", "D", names("VT-AL"))
    assert blank["winner"] == "The Democrat"             # no nominee on file: still a sentence


def test_a_race_with_its_own_lines_uses_them_first(store, rng):
    own = FL._lines()["races"]["GA-SEN"]["D"]
    got = [FL.call_line(store, "GA-SEN", "D", names("GA-SEN"), rng) for _ in range(len(own))]
    assert sorted(got) == sorted(own)


def test_a_race_without_its_own_lines_gets_one_for_its_kind_naming_names(store, rng):
    assert not FL._lines()["races"].get("VA-02", {}).get("R")
    text = FL.call_line(store, "VA-02", "R", names("VA-02"), rng)
    assert "Tilt D" in text                              # every upset line names the rating
    hold = FL.call_line(store, "CO-SEN", "D", names("CO-SEN"), rng)
    assert hold and ("Colorado" in hold or P.name_of("CO-SEN", "D") in hold)


def test_a_race_can_have_its_own_retraction_line(store, rng):
    assert FL.retract_line(store, "TX-SEN", rng) == "Pause the Tala-Freak-Out."
    assert FL.retract_line(store, "GA-SEN", rng) in FL._lines()["retract"]


def test_other_moments_fill_in_their_blanks(store, rng):
    text = FL.line(store, "desk_ahead", rng, outlet="AP", lead="14 min")
    assert "AP" in text and "14 min" in text
    assert FL.line(store, "desk_wrong", rng).startswith("⚠️")
    assert FL.line(store, "no_such_moment", rng) is None


# ------------------------------------------------------------------ the file itself
ALLOWED = {"call": {"winner", "loser", "place", "seat", "rating", "party"}, "desk": {"outlet", "lead"}}


def test_every_line_in_flavor_json_is_usable():
    data = FL._lines()
    cov = FL.coverage()
    assert cov["unknown_races"] == []
    for kind in ("call", "hold", "flip", "upset", "control", "retract", "desk_ahead", "desk_behind", "desk_wrong"):
        lines = data[kind]
        assert len(lines) >= 4 and len(set(lines)) == len(lines), kind
        ok = ALLOWED["desk"] if kind.startswith("desk_") else set() if kind == "retract" else ALLOWED["call"]
        for text in lines:
            used = {n for _l, n, _s, _c in string.Formatter().parse(text) if n}
            assert used <= ok, f"{kind}: {text!r} uses {used - ok}"
    for rid, by in data["races"].items():
        for key, lines in by.items():
            assert key == "retract" or key in P.options_for(rid), f"{rid} has lines for {key}, which it can't elect"
            for text in lines:
                assert isinstance(text, str) and text.strip() and "{" not in text, f"{rid}/{key}: {text!r}"
    assert all(any(not FL._needs(t) or FL._needs(t) <= {"winner", "seat", "place"} for t in data[k]) for k in ("call", "hold", "flip", "control"))


def test_built_in_lines_cover_every_moment_if_the_file_is_gone(store, rng, monkeypatch):
    monkeypatch.setattr(FL, "_lines", lambda: {})
    assert FL.call_line(store, "GA-SEN", "D", names("GA-SEN"), rng) and FL.retract_line(store, "GA-SEN", rng)
    assert FL.line(store, "desk_behind", rng, outlet="AP", lead="2 min")


def test_a_pack_with_no_parties_and_no_flavor_file_still_gets_lines(store, rng):
    packs.activate("2027-chicago-mayor")
    try:
        text = FL.call_line(store, "CHI-MAYOR", "B", names("CHI-MAYOR"), rng)
        assert "Candidate B" in text or "Mayor of Chicago" in text
    finally:
        packs.activate(packs.DEFAULT)


# ------------------------------------------------------------------ election night 2026 again
def test_replaying_2026_gives_every_card_a_line_without_the_repeats(night, store, rng):
    """The same 137 calls on rated and statewide races, in order, through the picker."""
    calls = [(h["race"], h["party"]) for h in night["expected"]["history"]
             if h["action"] == "call" and not DESK.is_safe_seat(h["race"])]
    lines = [FL.call_line(store, rid, key, names(rid), rng) for rid, key in calls]
    assert len(lines) == 137 and all(lines)
    assert all("{" not in t for t in lines)
    counts = collections.Counter(lines)
    assert max(counts.values()) <= 6                     # on the night the most-used line appeared 28 times
    assert len(counts) >= 100                            # on the night: 22 different lines across 133 cards
    for i in range(len(lines) - 5):
        assert len(set(lines[i:i + 5])) == 5, lines[i:i + 5]      # never the same line twice in any five cards
    own = FL._lines()["races"]
    with_own = [(rid, key, t) for (rid, key), t in zip(calls, lines) if own.get(rid, {}).get(key)]
    assert len(with_own) >= 15 and all(t in own[rid][key] for rid, key, t in with_own)   # race lines still win
    named = sum(1 for (rid, key), t in zip(calls, lines)
                if any(x in t for x in (R.RACES[rid]["label"], R.STATE_NAMES.get(R.RACES[rid]["state"]) or "~", names(rid)[key])))
    assert named >= 100                                  # most lines now say who or where
