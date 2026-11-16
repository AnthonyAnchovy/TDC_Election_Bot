"""The time the desk's records are stamped with. It's the real time unless a replay or a test has set it.

store.py and desk.py ask here instead of time.time(), so a recorded night can be fed back through them at its own
timestamps. The bot never sets it. (DESK_FAKE_NOW in bot.py is a different thing: it moves the rehearsal's wall
clock for poll closings, and leaves these timestamps real.)
"""
from __future__ import annotations

import time as _time
from contextlib import contextmanager

_fixed: float | None = None


def time() -> float:
    return _fixed if _fixed is not None else _time.time()


def set(ts: float | None):
    """Freeze the clock at ts (Unix seconds). None goes back to real time."""
    global _fixed
    _fixed = ts


@contextmanager
def at(ts: float):
    """Run a block with the clock frozen at ts, then put it back as it was."""
    before = _fixed
    set(ts)
    try:
        yield
    finally:
        set(before)
