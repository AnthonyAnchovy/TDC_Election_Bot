"""Election-night timing: poll closing times for Nov 3, 2026, in Eastern Time.

Source: The Green Papers 2026 polling hours, checked Oct 21, 2026. Nov 3 is after
daylight saving ends, so ET is UTC-5. Each state has (most polls closed, last polls closed).
Times past midnight are written as 24:00, 25:00 (i.e. early Nov 4).
Edit this table if a state changes its hours.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ELECTION_DAY = datetime(2026, 11, 3, tzinfo=ET)

CLOSINGS = {
    "AL": ("20:00", "20:00"), "AK": ("24:00", "25:00"), "AZ": ("21:00", "21:00"),
    "AR": ("20:30", "20:30"), "CA": ("23:00", "23:00"), "CO": ("21:00", "21:00"),
    "CT": ("20:00", "20:00"), "DE": ("20:00", "20:00"), "FL": ("19:00", "20:00"),
    "GA": ("19:00", "19:00"), "HI": ("24:00", "24:00"), "ID": ("22:00", "23:00"),
    "IL": ("20:00", "20:00"), "IN": ("18:00", "19:00"), "IA": ("21:00", "21:00"),
    "KS": ("20:00", "21:00"), "KY": ("18:00", "19:00"), "LA": ("21:00", "21:00"),
    "ME": ("20:00", "20:00"), "MD": ("20:00", "20:00"), "MA": ("20:00", "20:00"),
    "MI": ("20:00", "21:00"), "MN": ("21:00", "21:00"), "MS": ("20:00", "20:00"),
    "MO": ("20:00", "20:00"), "MT": ("22:00", "22:00"), "NE": ("21:00", "21:00"),
    "NV": ("22:00", "22:00"), "NH": ("19:00", "20:00"), "NJ": ("20:00", "20:00"),
    "NM": ("21:00", "21:00"), "NY": ("21:00", "21:00"), "NC": ("19:30", "19:30"),
    "ND": ("22:00", "23:00"), "OH": ("19:30", "19:30"), "OK": ("20:00", "20:00"),
    "OR": ("23:00", "23:00"), "PA": ("20:00", "20:00"), "RI": ("20:00", "20:00"),
    "SC": ("19:00", "19:00"), "SD": ("20:00", "21:00"), "TN": ("20:00", "20:00"),
    "TX": ("20:00", "21:00"), "UT": ("22:00", "22:00"), "VT": ("19:00", "19:00"),
    "VA": ("19:00", "19:00"), "WA": ("23:00", "23:00"), "WV": ("19:30", "19:30"),
    "WI": ("21:00", "21:00"), "WY": ("21:00", "21:00"),
}


def _at(hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return ELECTION_DAY + timedelta(hours=h, minutes=m)


def first_close(state: str) -> datetime:
    return _at(CLOSINGS[state][0])


def last_close(state: str) -> datetime:
    return _at(CLOSINGS[state][1])


def is_closed(state: str, now: datetime) -> bool:
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
