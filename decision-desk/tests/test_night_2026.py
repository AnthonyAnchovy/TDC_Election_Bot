"""Election night 2026, replayed: the same votes, overrides and outlet calls must give the same night.

The fixture comes from the bot's own database and the Discord export (tools/build_night_fixture.py). Two sources
check the replay: the database's record of every call, and the tally the bot posted to the board after every vote.
"""
from datetime import datetime

import desk as DESK
import races as R
import replay as RP
import scoring as S


def ts(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()


# ------------------------------------------------------------------ the fixture itself
def test_the_fixture_holds_no_discord_ids_or_names(night):
    import json
    import re
    text = json.dumps(night)
    assert not re.search(r"\d{15,}", text)                      # Discord IDs are 17-20 digits
    hows = {h["how"] for h in night["expected"]["history"]}
    assert all(re.fullmatch(r"(Called by \d of \d Election Junkies|Projected by .+|(Called|Retracted) by user\d+( \(override\))?)", h)
               for h in hows), hows


def test_the_night_has_the_events_we_think_it_has(night):
    kinds = {}
    for e in night["events"]:
        kinds[e["do"]] = kinds.get(e["do"], 0) + 1
    assert kinds == {"vote": 348, "outlet": 494, "realcall": 18, "unvote": 6, "forceuncall": 4, "uncallvote": 3,
                     "roster": 3, "settle": 1}
    assert all(e["race"] in R.RACES for e in night["events"] if "race" in e)


# ------------------------------------------------------------------ the replay
def test_replay_reproduces_every_call_and_retraction_in_order(night, replayed):
    assert RP.differences(night, replayed, most=None) == []
    assert len(replayed.history()) == 482


def test_replay_ends_where_the_night_did(night, replayed):
    calls = replayed.store.all()
    assert calls == night["expected"]["calls"] and len(calls) == 474
    assert S.chamber_counts("house", calls) == {"D": 256, "R": 162, "I": 0, "uncalled": 17}
    assert S.chamber_counts("senate", calls) == {"D": 55, "R": 44, "I": 0, "uncalled": 1}
    assert S.path_text("senate", calls) == "Senate: **Democrats clinch control** (55 seats)"
    assert calls["US-HOUSE"] == "D"


def test_every_vote_shows_the_tally_the_board_showed(replayed):
    """The second source: '2 of 3 votes needed' on each of the 348 board posts."""
    votes = [(e, out) for e, out in replayed.log if e["do"] == "vote"]
    assert len(votes) == 348 and all(e.get("posted") for e, _ in votes)
    wrong = [(e, out) for e, out in votes
             if out.status != "recorded" or [min(out.support, out.need), out.need] != e["posted"]]
    assert wrong == []


def test_how_each_call_was_made(replayed):
    made = {}
    for h in replayed.history():
        if h["action"] == "call":
            key = "majority" if "Election Junkies" in h["how"] else "override" if "(override)" in h["how"] else h["outlet"]
            made[key] = made.get(key, 0) + 1
    assert made == {"NYT": 340, "majority": 96, "Decision Desk HQ": 24, "override": 18}


def test_the_four_retractions(replayed):
    gone = [(h["race"], h["party"]) for h in replayed.history() if h["action"] == "uncall"]
    assert gone == [("MT-02", "D"), ("MS-SEN", "R"), ("MT-01", "D"), ("MN-01", "R")]
    calls = replayed.store.all()
    assert (calls["MT-02"], calls["MS-SEN"], calls["MT-01"]) == ("R", "D", "D")     # called again, two the other way
    assert "MN-01" not in calls                                                    # never called again


def test_safe_seats_went_to_roll_ups_and_the_rest_got_cards(night, replayed):
    """Against what was posted in #decision-desk: 341 seats in roll-ups, and a card for every other call but four."""
    seen = night["expected"]["discord"]
    calls = [h["race"] for h in replayed.history() if h["action"] == "call"]
    assert sum(DESK.is_safe_seat(r) for r in calls) == seen["safe_seats_rolled_up"] == 341
    rated = [r for r in calls if not DESK.is_safe_seat(r)]
    assert len(rated) - len(seen["called_without_a_card"]) == seen["projection_cards"] == 133
    # A bug from the night, kept visible: these four were made official and no card was ever posted for them.
    assert seen["called_without_a_card"] == ["CA-22", "CA-48", "DE-SEN", "TX-15"]


def test_admin_votes_only_started_counting_late_in_the_evening(night):
    """Two admins without the role could vote all night, but their votes counted for nothing until 9:59 PM CT.
    Florida Governor shows it: the third vote left it at '2 of 3', and the fourth made the call."""
    change = [e for e in night["events"] if e["do"] == "roster" and "counted" in e]
    assert len(change) == 1 and ts("2026-11-03T21:45-06:00") < change[0]["t"] < ts("2026-11-03T22:00-06:00")
    fl = [e["posted"] for e in night["events"] if e["do"] == "vote" and e["race"] == "FL-GOV"
          and e["t"] > ts("2026-11-03T20:00-06:00")]
    assert fl == [[1, 3], [2, 3], [2, 3], [3, 3]]


def test_replay_can_stop_partway(night):
    """Mississippi: called Republican at 10:54 PM CT, retracted at 11:40, called Democratic three days later."""
    assert "MS-SEN" not in RP.replay(night, ts("2026-11-03T22:54:00-06:00")).store.all()
    assert RP.replay(night, ts("2026-11-03T22:55:00-06:00")).store.all()["MS-SEN"] == "R"
    assert "MS-SEN" not in RP.replay(night, ts("2026-11-03T23:41:00-06:00")).store.all()
    assert RP.replay(night, ts("2026-11-06T15:00:00-06:00")).store.all()["MS-SEN"] == "D"


# ------------------------------------------------------------------ scoring
def test_the_leaderboard_scores_as_it_did(night, replayed):
    """Under the 2026 rules. A change to scoring that moves these has changed the result of the real game."""
    board = RP.scores(night, replayed.results())
    assert [(r["user_id"], r["points"], r["flips"]) for r in board] == \
        [(r["user"], r["points"], r["flips"]) for r in night["expected"]["scores"]]
    assert [r["points"] for r in board] == [124, 123, 120, 63, 60, 48]


def test_house_picks_and_flip_bonuses_decided_it(night, replayed):
    board = RP.scores(night, replayed.results())
    top = board[0]
    cats = top["cats"]
    assert (cats["senate"]["right"], cats["house_control"]["right"], cats["governor"]["right"],
            cats["battleground"]["right"], top["flips"]) == (31, 1, 21, 35, 32)
    assert all(r["cats"]["battleground"]["picked"] == 0 for r in board[3:])     # the bottom three skipped the House
    assert min(r["points"] for r in board[:3]) - max(r["points"] for r in board[3:]) == 57


def test_nothing_is_scored_before_anything_is_called(night):
    """What should have been true on Nov 2, when a rehearsal call left in the database scored '1/1 right'."""
    board = RP.scores(night, {})
    assert all(r["points"] == 0 and not r["marks"] for r in board)


def test_races_nobody_called_stay_pending(night, replayed):
    """The night was never closed out: Alaska Senate and 15 governor races still have no result."""
    results = replayed.results()
    open_races = [rid for rid, r in R.RACES.items() if r["type"] in ("senate", "governor") and rid not in results]
    assert len(open_races) == 16 and "AK-SEN" in open_races
    assert all(p["house_dem_seats"] is None for n, p in night["predictions"].items() if n != "2")
