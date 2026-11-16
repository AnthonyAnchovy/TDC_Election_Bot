"""The desk's own bot, the race-through-the-night chart, and drinking-game mode."""
import io
import random
import statistics

from PIL import Image

import autocaller as A
import drinking as DR
import trend as TR

MIN = 60


def run(*steps, start=0.0, every=5):
    """A series from (minutes, margin) steps: each margin held for that many minutes, sampled every few minutes."""
    out, t = [], start
    for minutes, margin in steps:
        for _ in range(int(minutes / every)):
            out.append((t, margin, 1000, "proj"))
            t += every * MIN
    return out


# ------------------------------------------------------------------ the rule
def test_a_wide_lead_is_called_sooner_than_a_narrow_one():
    assert A.wait_for(20) == 20 and A.wait_for(9) == 40 and A.wait_for(4) == 75 and A.wait_for(3.9) is None
    wide, narrow = A.decide(run((120, 18.0))), A.decide(run((120, 5.0)))
    assert wide.side == "D" and 20 <= wide.held < 30
    assert narrow.side == "D" and 75 <= narrow.held < 85
    assert A.decide(run((120, -18.0))).side == "R"


def test_a_lead_inside_the_floor_is_never_called():
    assert A.decide(run((600, 3.5))) is None and A.decide(run((600, -3.9))) is None
    assert A.standing(run((600, 3.5))) == "inside 4 points"


def test_nothing_is_called_before_a_county_finishes():
    early = [(t, m, v, "count") for t, m, v, _s in run((300, 20.0))]
    assert A.decide(early) is None and A.standing(early) == "no county has finished counting"
    assert A.decide([]) is None and A.decide_now([]) is None


def test_a_change_of_leader_starts_the_wait_again():
    series = run((15, 18.0), (15, -18.0), (15, 18.0))
    assert A.decide(series) is None                     # never 20 minutes on one side
    assert A.decide(run((15, 18.0), (15, -18.0), (30, 18.0))).at >= 45 * MIN


def test_a_lurching_projection_is_not_trusted():
    lurching = run((10, 25.0), (10, 16.0), (10, 25.0), (10, 16.0), (10, 25.0), (10, 16.0))
    assert A.decide(lurching) is None                   # every reading is a 15-point lead, and it swings 9
    assert A.decide(lurching + run((30, 16.0), start=lurching[-1][0] + 5 * MIN)) is not None


def test_one_reading_cannot_call_anything():
    assert A.decide([(0, 30.0, 1, "proj"), (3600, 30.0, 1, "proj")]) is None


def test_asking_now_agrees_with_looking_back(field):
    for rid in ("GA-SEN", "MS-SEN", "TX-SEN", "FL-SEN", "NE-SEN"):
        series = field.series(rid)
        first = A.decide(series, rid)
        seen = next((A.decide_now(series[:i + 1], rid) for i in range(len(series)) if A.decide_now(series[:i + 1], rid)), None)
        assert first == seen


# ------------------------------------------------------------------ the rule against election night 2026
def test_the_bot_called_14_senate_races_in_2026_and_none_wrong(night, field):
    final = night["expected"]["calls"]
    live = [rid for rid in field.snaps[0]["races"] if any(st != "wait" for *_x, st in field.series(rid))]
    calls = {rid: A.decide(field.series(rid), rid) for rid in live}
    made = {rid: c for rid, c in calls.items() if c}
    assert len(live) == 17 and len(made) == 14
    side = {"D": "D", "I": "D", "R": "R"}
    assert [rid for rid, c in made.items() if final.get(rid) and side[final[rid]] != c.side] == []
    assert sorted(set(live) - set(made)) == ["AK-SEN", "FL-SEN", "MS-SEN"]      # the three closest, left alone
    assert A.standing(field.series("MS-SEN")) == "inside 4 points"              # the one the desk had to retract


def test_the_bot_was_slower_than_the_desk_in_2026(night, field):
    """Worth knowing before anyone trusts it for speed: it was safe, not fast."""
    first = {}
    for h in night["expected"]["history"]:
        if h["action"] == "call":
            first.setdefault(h["race"], h["t"])
    leads = [(first[rid] - c.at) / 60 for rid in first if rid in field.snaps[0]["races"]
             for c in [A.decide(field.series(rid), rid)] if c]
    assert round(statistics.median(leads)) == -83 and sum(1 for x in leads if x > 0) == 2


# ------------------------------------------------------------------ the chart
def test_the_chart_draws_a_real_race(night, field):
    points = TR.series(field.snaps, "MS-SEN")
    marks = [(h["t"], h["action"], None if h["action"] == "uncall" else "D" if h["party"] == "D" else "R")
             for h in night["expected"]["history"] if h["race"] == "MS-SEN"]
    buf = TR.render("Mississippi Senate", points, "Colom", "Hyde-Smith", marks)
    img = Image.open(io.BytesIO(buf.getvalue()))
    assert img.size == (TR.W, TR.H) and len(buf.getvalue()) > 30_000
    info = TR.summary(points)
    assert info["changed_sides"] and -4 < info["low"] < 0 < info["high"] < 3 and info["votes"] == 925_599
    assert TR._lean(info["last"], "Colom", "Hyde-Smith") == "Hyde-Smith +0.1"


def test_the_chart_marks_the_bots_call_and_handles_a_blowout(field):
    points = TR.series(field.snaps, "GA-SEN")
    bot = A.decide(points, "GA-SEN")
    assert TR.render("Georgia Senate", points, "Ossoff", "Collins", [], (bot.at, bot.side)) is not None
    assert not TR.summary(points)["changed_sides"]


def test_a_race_with_no_numbers_has_no_chart(field):
    assert TR.render("Alabama Senate", TR.series(field.snaps, "AL-SEN")) is None
    assert TR.summary(TR.series(field.snaps, "AL-SEN")) is None
    assert TR.series(field.snaps, "NOT-A-RACE") == []


# ------------------------------------------------------------------ drinking-game mode
def test_the_game_is_off_until_a_server_turns_it_on(store):
    assert DR.line(store, "flip", seat="Georgia Senate") is None and DR.line(None, "flip") is None
    store.set_setting(DR.SETTING, True)
    assert DR.line(store, "flip", random.Random(1), seat="Georgia Senate").startswith("🍺")
    assert DR.line(store, "no_such_event") is None


def test_rules_fill_in_what_happened_and_do_not_repeat(store):
    store.set_setting(DR.SETTING, True)
    rng = random.Random(7)
    assert "NYT" in DR.line(store, "beaten", rng, outlet="NYT", seat="Ohio Senate")
    two = {DR.line(store, "retract", rng), DR.line(store, "retract", rng)}
    assert two == set(DR.RULES["retract"])
    assert DR.line(store, "first", rng, seat="Ohio Senate", lead=None) is None      # a blank it can't fill: no line


def test_which_events_a_call_sets_off():
    assert DR.events_for_call("flip", None, None) == ["flip"]
    assert DR.events_for_call("hold", None, "NYT") == ["beaten"]
    assert DR.events_for_call("upset", None, "The Bot") == ["upset", "bot"]
    assert DR.events_for_call("call", None, None) == []


def test_every_built_in_rule_can_be_filled(store):
    store.set_setting(DR.SETTING, True)
    fmt = dict(winner="Ann", seat="the seat", place="the state", outlet="AP", lead="14 min")
    for event, lines in DR.RULES.items():
        for _ in lines:
            assert DR.line(store, event, **fmt), event
