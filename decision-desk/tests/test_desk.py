"""The desk's rules one at a time, on an empty database."""
import time

import clock
import desk as DESK

RATED, SAFE = "TX-15", "VT-AL"       # a seat the ratings have as competitive, and one they don't


def test_the_test_seats_are_what_they_say():
    assert not DESK.is_safe_seat(RATED) and DESK.is_safe_seat(SAFE)
    assert not DESK.is_safe_seat("GA-SEN")      # only House seats can be Safe seats


# ------------------------------------------------------------------ votes
def test_a_majority_of_role_holders_makes_the_call(store, five):
    assert DESK.cast_call_vote(store, five, 1, "GA-SEN", "D").reached is False
    second = DESK.cast_call_vote(store, five, 2, "GA-SEN", "D")
    assert (second.support, second.need, second.reached) == (2, 3, False)
    assert store.get("GA-SEN") is None
    third = DESK.cast_call_vote(store, five, 3, "GA-SEN", "D")
    assert third.reached and third.how == "Called by 3 of 5 Election Junkies"
    assert store.get("GA-SEN")["party"] == "D"


def test_votes_for_different_winners_do_not_add_up(store, five):
    DESK.cast_call_vote(store, five, 1, "GA-SEN", "D")
    DESK.cast_call_vote(store, five, 2, "GA-SEN", "D")
    assert DESK.cast_call_vote(store, five, 3, "GA-SEN", "R").reached is False
    assert store.get("GA-SEN") is None


def test_switching_a_vote_replaces_it(store, five):
    DESK.cast_call_vote(store, five, 1, "GA-SEN", "R")
    switched = DESK.cast_call_vote(store, five, 1, "GA-SEN", "D")
    assert switched.status == "recorded" and switched.prev["party"] == "R" and switched.support == 1
    assert store.active_votes("GA-SEN", "call") == {1: "D"}


def test_the_same_vote_twice_is_a_duplicate(store, five):
    DESK.cast_call_vote(store, five, 1, "GA-SEN", "D")
    assert DESK.cast_call_vote(store, five, 1, "GA-SEN", "D").status == "duplicate"


def test_a_vote_on_a_called_race_is_turned_away(store, five):
    DESK.realcall(store, 1, "Ann", "GA-SEN", "D")
    late = DESK.cast_call_vote(store, five, 2, "GA-SEN", "R")
    assert late.status == "already_called" and late.official["party"] == "D"
    assert store.active_votes("GA-SEN", "call") == {1: "D"}


def test_an_admin_without_the_role_counts_but_does_not_raise_the_bar(store, five):
    DESK.cast_call_vote(store, five, 1, "GA-SEN", "D")
    DESK.cast_call_vote(store, five, 2, "GA-SEN", "D")
    third = DESK.cast_call_vote(store, five, 9, "GA-SEN", "D")
    assert third.reached and third.need == 3 and third.how == "Called by 3 of 5 Election Junkies"


def test_a_vote_from_someone_not_on_the_desk_is_kept_but_not_counted(store, five):
    DESK.cast_call_vote(store, five, 1, "GA-SEN", "D")
    DESK.cast_call_vote(store, five, 2, "GA-SEN", "D")
    outsider = DESK.cast_call_vote(store, five, 77, "GA-SEN", "D")
    assert (outsider.support, outsider.reached) == (2, False)


def test_a_fixed_threshold_replaces_the_majority(store):
    two = DESK.Roster(frozenset({1, 2, 3, 4, 5}), frozenset({1, 2, 3, 4, 5}), threshold=2)
    assert two.need() == 2
    assert DESK.Roster(frozenset({1, 2}), frozenset({1, 2}), threshold=9).need() == 2    # never more than the desk has
    DESK.cast_call_vote(store, two, 1, "GA-SEN", "D")
    assert DESK.cast_call_vote(store, two, 2, "GA-SEN", "D").reached


def test_settle_calls_a_race_whose_standing_votes_became_a_majority(store, five):
    before = DESK.Roster(five.junkies, five.junkies)     # the admin's vote doesn't count yet
    for user in (1, 2, 9):
        assert DESK.cast_call_vote(store, before, user, "GA-SEN", "D").reached is False
    assert DESK.settle(store, before, "GA-SEN") is None
    settled = DESK.settle(store, five, "GA-SEN")
    assert settled.reached and settled.how == "Called by 3 of 5 Election Junkies"
    assert DESK.settle(store, five, "GA-SEN") is None    # already called


# ------------------------------------------------------------------ overrides and retractions
def test_realcall_is_official_at_once_and_counts_as_a_vote(store):
    assert DESK.realcall(store, 1, "Ann", "GA-SEN", "D") == "Called by Ann (override)"
    assert store.get("GA-SEN")["party"] == "D"
    assert store.active_votes("GA-SEN", "call") == {1: "D"}
    assert DESK.realcall(store, 2, "Bo", "GA-SEN", "R") is None     # the first call stands
    assert store.get("GA-SEN")["party"] == "D"


def test_a_majority_retracts_and_the_race_starts_fresh(store, five):
    DESK.realcall(store, 1, "Ann", "GA-SEN", "D")
    assert DESK.cast_uncall_vote(store, five, 1, "GA-SEN").reached is False
    assert DESK.cast_uncall_vote(store, five, 1, "GA-SEN").status == "duplicate"
    DESK.cast_uncall_vote(store, five, 2, "GA-SEN")
    third = DESK.cast_uncall_vote(store, five, 3, "GA-SEN")
    assert third.reached and third.how == "Retracted by 3 of 5 Election Junkies"
    assert DESK.retract(store, "GA-SEN", third.how)["party"] == "D"
    assert store.get("GA-SEN") is None
    assert store.active_votes("GA-SEN", "call") == {}       # old call votes don't carry over
    assert DESK.retract(store, "GA-SEN", "again") is None
    assert DESK.cast_uncall_vote(store, five, 4, "GA-SEN").status == "not_called"


# ------------------------------------------------------------------ outlet calls
def test_a_trusted_outlet_makes_a_house_seat_official(store):
    got = DESK.outlet_call(store, RATED, "D", "NYT", 0)
    assert got.projected and got.how == "Projected by NYT"
    call = store.get(RATED)
    assert call["party"] == "D" and call["detail"]["outlet"] == "NYT"


def test_an_outlet_never_makes_a_senate_race_official_by_itself(store):
    assert DESK.outlet_call(store, "GA-SEN", "D", "NYT", 0).projected is False
    assert store.get("GA-SEN") is None
    assert [p["outlet"] for p in store.procalls("GA-SEN")] == ["NYT"]


def test_an_untrusted_outlet_is_only_logged(store):
    assert DESK.outlet_call(store, RATED, "D", "NBC", 5).projected is False
    assert store.get(RATED) is None


def test_trusted_outlets_that_disagree_leave_it_to_the_desk(store):
    store.procall(RATED, "Decision Desk HQ", "R", 0)        # logged while the seat was still open
    got = DESK.outlet_call(store, RATED, "D", "NYT", 0)
    assert got.rival == "Decision Desk HQ" and got.projected is False
    assert store.get(RATED) is None


def test_outlet_names_are_matched_the_way_people_type_them():
    for name in ("AP", "the A.P.", "ddhq", "Decision Desk HQ", "New York Times", " wasserman "):
        assert DESK.is_house_caller(name), name
    assert not DESK.is_house_caller("NBC")


def test_auto_calls_queue_safe_seats_and_post_rated_ones(store):
    clock.set(1000)
    assert DESK.auto_call(store, SAFE, "D", "NYT") == "queued"
    assert store.get(SAFE)["detail"] == {"how": "Projected by NYT", "outlet": "NYT"}
    assert store.setting("safe_batch") == [[SAFE, "D", 1000]]
    assert DESK.auto_call(store, SAFE, "D", "NYT") == "duplicate"
    assert DESK.auto_call(store, SAFE, "D", "Decision Desk HQ") == "logged"     # agrees with a call already made
    assert DESK.auto_call(store, SAFE, "R", "Dave Wasserman") == "post"         # disagrees: a person should see it
    assert DESK.auto_call(store, RATED, "D", "NYT") == "post"
    assert store.get(RATED) is None                                             # posting it is what records it


def test_follow_outlet_makes_any_race_official(store):
    assert DESK.follow_outlet(store, "GA-SEN", "D", "NYT", 1) == "Projected by NYT"
    assert store.get("GA-SEN")["detail"]["outlet"] == "NYT"
    assert DESK.follow_outlet(store, "GA-SEN", "D", "NYT", 1) is None


# ------------------------------------------------------------------ the clock
def test_records_are_stamped_with_the_desk_clock(store, five):
    with clock.at(1_793_750_000):
        DESK.realcall(store, 1, "Ann", "GA-SEN", "D")
    assert store.get("GA-SEN")["called_at"] == 1_793_750_000
    assert store.all_call_votes()[0][3] == 1_793_750_000
    assert abs(clock.time() - time.time()) < 5      # and it's back on real time afterwards
