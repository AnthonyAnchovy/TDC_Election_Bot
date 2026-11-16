"""The colour a card draws an option in, for options that aren't one of the three parties the themes were drawn for.

Every theme has tables keyed by party (D, R, I, and X for "nobody": a retraction). A candidate in a nonpartisan
race, or a pack's fourth party, has a colour of its own in the pack, and each table needs it in that table's tone:
the wire theme's field is a dark version, its dot a light one, a gradient runs from dark to full.

style(race, option) gives the key to look tables up by: the party letter when the option is a party, and the
option's own colour ("#0f766e") when it isn't. That also keeps "Candidate D" in a mayoral race from being drawn,
and worded, as the Democrat.
"""
from __future__ import annotations

import packs
import races as R

NEUTRAL = "X"


def rgb(text: str) -> tuple[int, int, int]:
    text = text.lstrip("#")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def _mix(c, toward, t: float) -> tuple[int, int, int]:
    return tuple(round(a + (b - a) * t) for a, b in zip(c, toward))


def dark(c, t: float) -> tuple[int, int, int]:
    return _mix(c, (0, 0, 0), t)


def light(c, t: float) -> tuple[int, int, int]:
    return _mix(c, (255, 255, 255), t)


TONES = {
    "base": lambda c: c,
    "field": lambda c: dark(c, 0.30),               # a whole card's background
    "dot": lambda c: light(c, 0.45),                # small marks on a dark field
    "panel": lambda c: dark(c, 0.70),
    "gradient": lambda c: (dark(c, 0.50), c),       # top to bottom
}


def style(race_id: str, key: str | None) -> str | None:
    """What to look colour tables up by for this option of this race."""
    if not key:
        return key
    own = next((o for o in R.RACES.get(race_id, {}).get("options") or [] if o["key"] == key), None)
    if own is None:                                 # one of the pack's shared options: a party
        return key
    if own.get("party"):                            # a named candidate who runs as a party's
        return own["party"]
    return own.get("color") or NEUTRAL


def is_party(race_id: str, key: str | None) -> bool:
    """Whether the option is one of the pack's shared ones, so party words ("Democratic hold") fit it."""
    return bool(key) and style(race_id, key) in packs.active().options


class Table(dict):
    """A theme's table keyed by party. Asked for anything else, it works the entry out: from a colour written as
    "#rrggbb", or from the colour the pack gives a shared option the theme has no entry for. Failing both, the
    neutral entry (or the independent's, in a table with no neutral)."""

    def __init__(self, entries: dict, tone: str = "base"):
        super().__init__(entries)
        self.tone = TONES[tone]

    def __missing__(self, key):
        text = key if isinstance(key, str) and key.startswith("#") else \
            (packs.active().options.get(key) or {}).get("color") if isinstance(key, str) else None
        try:
            return self.tone(rgb(text)) if text else self._neutral()
        except (ValueError, IndexError):
            return self._neutral()

    def _neutral(self):
        return dict.__getitem__(self, NEUTRAL if NEUTRAL in self else "I")
