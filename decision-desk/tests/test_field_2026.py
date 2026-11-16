"""The Senate field through election night 2026: what the model said about each race, minute by minute.

This is the data a feature that shows margins beside a call, or a bot that makes its own calls, gets tested against.
The margin is the model's projection (D side positive), not the raw count.
"""
from datetime import datetime

import field as F
import races as R
import replay as RP

LIVE = {"AK", "FL", "GA", "IA", "KS", "KY", "ME", "MI", "MN", "MS", "NC", "NE", "NH", "OH", "SC", "TX", "VA"}


def ts(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()


def test_the_series_covers_the_night(field):
    assert len(field.snaps) == 580
    assert ts("2026-11-03T17:00-06:00") <= field.snaps[0]["ts"] < ts("2026-11-03T17:01-06:00")
    assert ts("2026-11-04T06:50-06:00") < field.snaps[-1]["ts"] < ts("2026-11-04T06:52-06:00")
    assert all(set(s["races"]) == set(R.races_of("senate")) for s in field.snaps)
    busy = [b["ts"] - a["ts"] for a, b in zip(field.snaps, field.snaps[1:]) if b["ts"] < ts("2026-11-04T00:00-06:00")]
    assert sorted(busy)[len(busy) // 2] < 90        # about one a minute while results were coming in


def test_which_races_ever_had_numbers(field):
    live = {rid.split("-")[0] for rid in field.snaps[0]["races"] if any(st != "wait" for _t, _m, _v, st in field.series(rid))}
    assert live == LIVE


def test_lookup_by_time(field):
    assert field.at(field.snaps[0]["ts"] - 1) is None
    assert field.race("GA-SEN", field.snaps[0]["ts"] - 1) is None
    start = field.race("GA-SEN", ts("2026-11-03T17:30-06:00"))
    assert start["state"] == "wait" and start["votes"] == 0 and start["called"] is None
    later = field.race("GA-SEN", ts("2026-11-03T18:21-06:00"))
    assert later["state"] == "proj" and later["called"] == "D" and later["margin"] > 15 and 0 <= later["age"] < 120


def test_the_field_agrees_with_the_replayed_desk(night, field):
    """At a sample of moments, the call the field shows for each Senate race is the call the replay has."""
    for snap in field.snaps[::40]:
        calls = RP.replay(night, snap["ts"]).store.all()
        shown = {rid: v[3] for rid, v in snap["races"].items() if v[3]}
        assert shown == {rid: p for rid, p in calls.items() if rid in snap["races"]}, snap["clock"]


def test_the_bots_own_totals_still_read_every_snapshot(field):
    """field.py's seat arithmetic runs on the recorded format, so a format change shows up here."""
    last = F._totals(field.snaps[-1])
    assert last["have"] == {"D": 54, "R": 44, "I": 0}
    for snap in field.snaps[::25]:
        t = F._totals(snap)
        assert sum(t["proj"].values()) <= 100 and t["proj"]["D"] >= t["have"]["D"]


# ------------------------------------------------------------------ what a margin beside the call would have shown
def test_mississippi_was_inside_a_point_when_the_desk_called_it(field):
    called = field.race("MS-SEN", ts("2026-11-03T22:54:56-06:00"))
    assert called["state"] == "proj" and -1 < called["margin"] < 0 and called["votes"] > 850_000
    final = field.race("MS-SEN", field.snaps[-1]["ts"])
    assert -0.2 < final["margin"] < 0       # the model ended a tenth of a point on the wrong side


def test_some_calls_were_made_with_nothing_counted(night, field):
    first_call = {}
    for h in night["expected"]["history"]:
        if h["action"] == "call":
            first_call.setdefault(h["race"], h["t"])
    blind = sorted(rid for rid in ("VA-SEN", "MN-SEN", "IA-SEN", "ME-SEN", "NH-SEN", "GA-SEN", "NC-SEN")
                   if field.race(rid, first_call[rid])["state"] != "proj")
    assert blind == ["IA-SEN", "ME-SEN", "MN-SEN", "NH-SEN", "VA-SEN"]
    assert field.race("VA-SEN", first_call["VA-SEN"])["votes"] == 0


def test_first_votes_that_went_against_the_model(night, field):
    first_vote = {}
    for e in night["events"]:
        if e["do"] == "vote":
            first_vote.setdefault(e["race"], e)
    ne, fl = first_vote["NE-SEN"], first_vote["FL-SEN"]
    assert ne["party"] == "I" and field.race("NE-SEN", ne["t"])["margin"] < -12
    assert fl["party"] == "D" and field.race("FL-SEN", fl["t"])["margin"] < -14


def test_early_projections_swing_widely(field):
    """Why a bot that calls races needs more than a margin threshold."""
    def swing(rid):
        ms = [m for _t, m, _v, st in field.series(rid) if st == "proj"]
        return max(ms) - min(ms)
    assert swing("VA-SEN") > 20 and swing("FL-SEN") > 15
    assert swing("IA-SEN") < 1


def test_counted_votes_sometimes_go_down(field):
    """Feeds correct themselves. Anything built on 'votes so far' has to cope with the number falling."""
    drops = [(rid, a["races"][rid][1] - b["races"][rid][1]) for a, b in zip(field.snaps, field.snaps[1:])
             for rid in a["races"] if b["races"][rid][1] < a["races"][rid][1]]
    assert len(drops) == 15
