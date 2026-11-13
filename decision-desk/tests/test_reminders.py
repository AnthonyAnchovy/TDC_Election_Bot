"""When a picks reminder goes out, and what the daily early-vote post says."""
import cw_early as CE
import scoring as S

DEADLINE = 1_000_000_000.0
DAY, HOUR = 86400, 3600


def test_reminders_go_out_a_week_a_day_and_three_hours_before():
    assert S.reminder_due(DEADLINE - 8 * DAY, DEADLINE, set()) is None
    assert S.reminder_due(DEADLINE - 7 * DAY, DEADLINE, set()) == (f"picks-reminder-{7 * DAY}", "a week")
    assert S.reminder_due(DEADLINE - 2 * DAY, DEADLINE, {f"picks-reminder-{7 * DAY}"}) is None
    assert S.reminder_due(DEADLINE - DAY + 60, DEADLINE, {f"picks-reminder-{7 * DAY}"}) == (f"picks-reminder-{DAY}", "a day")
    assert S.reminder_due(DEADLINE - HOUR, DEADLINE, {f"picks-reminder-{DAY}"}) == (f"picks-reminder-{3 * HOUR}", "three hours")


def test_each_reminder_is_sent_once_and_none_after_picks_close():
    key = f"picks-reminder-{3 * HOUR}"
    assert S.reminder_due(DEADLINE - HOUR, DEADLINE, {key}) is None
    assert S.reminder_due(DEADLINE, DEADLINE, set()) is None
    assert S.reminder_due(DEADLINE + DAY, DEADLINE, set()) is None


def test_a_bot_started_late_sends_only_the_latest_reminder():
    assert S.reminder_due(DEADLINE - 2 * HOUR, DEADLINE, set()) == (f"picks-reminder-{3 * HOUR}", "three hours")


def test_every_missing_part_has_a_name():
    assert set(S.completion({})["missing"]) <= set(S.PART_NAMES)


def test_the_early_vote_post_ranks_states_and_shows_the_change():
    pace = [("GA", 1_200_000, 0.30, 0.04), ("NC", 2_000_000, 0.53, -0.012), ("MI", 500_000, 0.11, None)]
    lines = CE.digest(pace, {"GA": 1_000_000, "NC": 2_000_000}, {"GA": "Georgia", "NC": "North Carolina"})
    assert lines == [
        "**North Carolina** 2,000,000 · 53% of 2022's turnout · registered R+1",
        "**Georgia** 1,200,000 · 30% of 2022's turnout · +200,000 since the last post · registered D+4",
        "**MI** 500,000 · 11% of 2022's turnout",
    ]
    assert CE.digest([]) == []
