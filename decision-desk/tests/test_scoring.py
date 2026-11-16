"""Scoring rules: what counts as a result, Senate control, and what's left before the game is final."""
import night as N
import races as R
import scoring as S

GA_CLOSE = N.first_close("GA").timestamp()
SENATE = sorted(R.races_of("senate"))


def pro(rid, party, ts, outlet="AP"):
    return {"race_id": rid, "outlet": outlet, "party": party, "ts": ts}


def senate_picks(d_seats: int, independents: int = 0) -> dict[str, str]:
    """Every Senate race picked: the first d_seats for D, then some independents, the rest R."""
    return {rid: ("D" if i < d_seats else "I" if i < d_seats + independents else "R") for i, rid in enumerate(SENATE)}


# ------------------------------------------------------------------ what counts as a result
def test_a_call_from_before_polls_close_does_not_count():
    """Nov 2 2026: a rehearsal Georgia call and an outlet call left in the database scored '1/1 right' a day early."""
    day_before = GA_CLOSE - 86400
    assert S.truth({"GA-SEN": "D"}, [], called_at={"GA-SEN": day_before}) == {}
    assert S.truth({}, [pro("GA-SEN", "D", day_before)]) == {}
    assert S.truth({"GA-SEN": "D"}, [pro("GA-SEN", "D", day_before)], called_at={"GA-SEN": day_before}) == {}
    assert S.truth({"GA-SEN": "D"}, [], called_at={"GA-SEN": GA_CLOSE + 60}) == {"GA-SEN": "D"}


def test_control_cannot_be_scored_before_the_first_polls_close_anywhere():
    first = min(N.first_close(s) for s in N.CLOSINGS).timestamp()
    assert S.opens("US-HOUSE") == S.opens("US-SENATE") == first
    assert S.truth({"US-HOUSE": "D"}, [], called_at={"US-HOUSE": first - 1}) == {}
    assert S.truth({"US-HOUSE": "D"}, [], called_at={"US-HOUSE": first}) == {"US-HOUSE": "D"}


def test_the_gate_can_be_lifted_for_a_rehearsal():
    assert S.truth({"GA-SEN": "D"}, [], called_at={"GA-SEN": 0}, gate=False) == {"GA-SEN": "D"}


def test_a_desk_call_with_no_time_is_taken_as_given():
    assert S.truth({"GA-SEN": "D"}, []) == {"GA-SEN": "D"}


def test_the_earliest_pro_call_beats_the_desk_unless_scoring_on_the_desk_alone():
    pros = [pro("GA-SEN", "R", GA_CLOSE + 500, "NBC"), pro("GA-SEN", "D", GA_CLOSE + 100)]
    assert S.truth({"GA-SEN": "R"}, pros) == {"GA-SEN": "D"}
    assert S.truth({"GA-SEN": "R"}, pros, "desk") == {"GA-SEN": "R"}


def test_a_final_result_outranks_every_call():
    pros = [pro("GA-SEN", "R", GA_CLOSE + 100)]
    assert S.truth({"GA-SEN": "R"}, pros, finals={"GA-SEN": "D", "AK-SEN": "R"}) == {"GA-SEN": "D", "AK-SEN": "R"}
    assert S.truth({}, [], "desk", finals={"GA-SEN": "D"}) == {"GA-SEN": "D"}
    assert S.truth({}, [], finals={"NOT-A-RACE": "D"}) == {}


# ------------------------------------------------------------------ Senate control
def test_senate_control_is_what_the_seat_picks_add_up_to():
    d_hold, r_hold = R.SENATE_HOLDOVER["D"], R.SENATE_HOLDOVER["R"]
    need_d = R.SENATE_MAJORITY - d_hold
    assert S.senate_control_pick(senate_picks(need_d)) == "D"
    assert S.senate_control_pick(senate_picks(need_d - 1)) == "R"        # 50-50: the Vice President's party
    assert len(SENATE) - (need_d - 1) + r_hold == 50
    assert S.senate_control_pick({rid: "D" for rid in SENATE[:-1]}) is None      # a seat unpicked
    assert S.senate_control_pick(senate_picks(need_d - 1, independents=1)) is None   # an independent holds the balance


def test_senate_control_scores_like_house_control():
    picks = senate_picks(R.SENATE_MAJORITY - R.SENATE_HOLDOVER["D"])
    before = S.grade(picks, {})
    assert before["senate_control"] == "D" and before["cats"]["senate_control"] == {"right": 0, "wrong": 0, "pending": 1, "picked": 1}
    won = S.grade(picks, {"US-SENATE": "D"})
    assert won["marks"]["US-SENATE"] is True
    assert won["points"] == S.DEFAULT_POINTS["senate_control"] + S.DEFAULT_POINTS["flip_bonus"]     # a takeover is a flip
    lost = S.grade(picks, {"US-SENATE": "R"})
    assert lost["points"] == 0 and lost["marks"]["US-SENATE"] is False
    assert S.grade(picks, {"US-SENATE": "D"}, {"senate_control": 9, "flip_bonus": 0})["points"] == 9


def test_a_stray_senate_control_pick_is_ignored_in_favour_of_the_seats():
    picks = {**senate_picks(0), "US-SENATE": "D"}
    assert S.grade(picks, {"US-SENATE": "R"})["marks"]["US-SENATE"] is True


def test_house_control_and_senate_control_are_separate_categories():
    assert S.category("US-HOUSE") == "house_control" and S.category("US-SENATE") == "senate_control"
    assert set(S.grade({}, {})["cats"]) == set(S.CATEGORIES)


def test_clinched_follows_the_path_to_majority():
    assert S.clinched("senate", {}) is None
    need_r = R.SENATE_MAJORITY - 1 - R.SENATE_HOLDOVER["R"]
    r_calls = {rid: "R" for rid in SENATE[:need_r]}
    assert S.clinched("senate", r_calls) == "R"
    assert S.clinched("senate", dict(list(r_calls.items())[:-1])) is None
    house = [rid for rid, r in R.RACES.items() if r["type"] == "house"]
    assert S.clinched("house", {rid: "D" for rid in house[:R.HOUSE_MAJORITY]}) == "D"
    assert S.clinched("house", {rid: "D" for rid in house[:R.HOUSE_MAJORITY - 1]}) is None
    assert S.clinched("governor", {}) is None


# ------------------------------------------------------------------ finishing the game
def test_completion_counts_every_part_and_the_tiebreaker():
    empty = S.completion({})
    assert empty["share"] == 0 and empty["missing"] == ["senate", "house_control", "governor", "battleground", "tiebreak"]
    picks = {**senate_picks(10), "US-HOUSE": "D"}
    some = S.completion(picks, {"house_dem_seats": 230})
    assert some["parts"]["senate"] == (len(SENATE), len(SENATE)) and some["parts"]["house_control"] == (1, 1)
    assert some["tiebreak"] and some["missing"] == ["governor", "battleground"]
    assert 0 < some["share"] < 1


def test_open_items_lists_what_still_has_no_result():
    left = S.open_items({}, None, {7: ({}, None), 8: ({}, {"house_dem_seats": 240})})
    assert left["control"] == ["US-HOUSE", "US-SENATE"] and len(left["senate"]) == len(SENATE)
    assert left["house_seats"] and left["no_tiebreak"] == [7] and not left["closed"]
    everything = {rid: "D" for rid in R.RACES}
    done = S.open_items(everything, 240)
    assert done["closed"] and not done["senate"] and not done["battleground"]
    assert not S.open_items(everything, None)["closed"]
