"""Election-night timing for the active data pack: when polls close, in Eastern Time for display.

The pack's "closings" block gives each jurisdiction (most polls closed, last polls closed) as clock times in the
pack's timezone on election day; times past midnight are written 24:00, 25:00. "*" is the default for a place the
block doesn't list, and a pack with no closings at all closes at 8 PM. 2026's came from The Green Papers' polling
hours, checked Oct 21, 2026. Edit the pack if a state changes its hours.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import packs

ET = ZoneInfo("America/New_York")
ELECTION_DAY = datetime(2026, 11, 3, tzinfo=ET)     # replaced by the active pack's
CLOSINGS: dict = {}
DEFAULT_CLOSE = ("20:00", "20:00")
CLOSINGS_DEFAULT = [DEFAULT_CLOSE]      # what a place the pack doesn't list closes at; set from the pack's "*"


@packs.on_activate
def _fill(pack: packs.Pack):
    global ELECTION_DAY, CLOSINGS
    ELECTION_DAY = pack.election_day()
    CLOSINGS = {st: tuple(v) for st, v in (pack.data.get("closings") or {}).items() if st != "*"}
    CLOSINGS_DEFAULT[0] = tuple((pack.data.get("closings") or {}).get("*") or DEFAULT_CLOSE)


def _at(hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return ELECTION_DAY + timedelta(hours=h, minutes=m)


def _times(state: str | None) -> tuple[str, str]:
    return CLOSINGS.get(state) or CLOSINGS_DEFAULT[0]


def first_close(state: str | None) -> datetime:
    return _at(_times(state)[0])


def last_close(state: str | None) -> datetime:
    return _at(_times(state)[1])


def first_anywhere() -> datetime:
    """The first polls to close in any place the pack covers."""
    return min((first_close(st) for st in CLOSINGS), default=first_close(None))


def morning_after(hour: int = 8) -> datetime:
    """The day after election day at this hour, in the pack's timezone: when the night's posts and pulls stop."""
    return (ELECTION_DAY + timedelta(days=1)).replace(hour=hour)


def is_closed(state: str | None, now: datetime) -> bool:
    return now >= first_close(state)


def waves() -> list[tuple[datetime, list[str]]]:
    """Closing waves: (time most polls close, [states]) in time order."""
    by_time: dict[datetime, list[str]] = {}
    for st in CLOSINGS:
        by_time.setdefault(first_close(st), []).append(st)
    return sorted((t, sorted(sts)) for t, sts in by_time.items())


def fmt(dt: datetime, tz: ZoneInfo) -> str:
    """'7:00 PM ET / 6:00 PM CT' style label (second zone only if it differs)."""
    et = dt.astimezone(ET).strftime("%I:%M %p").lstrip("0") + " ET"
    local = dt.astimezone(tz)
    if local.utcoffset() == dt.astimezone(ET).utcoffset():
        return et
    return f"{et} / {local.strftime('%I:%M %p').lstrip('0')} {local.tzname()}"
