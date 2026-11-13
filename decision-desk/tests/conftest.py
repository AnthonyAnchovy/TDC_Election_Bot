"""Shared fixtures. The bot's modules sit flat in decision-desk/, so that folder goes on the path."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import clock  # noqa: E402
import desk as DESK  # noqa: E402
import replay as RP  # noqa: E402
from store import Store  # noqa: E402


@pytest.fixture(autouse=True)
def real_clock():
    """No test leaves the clock frozen for the next one."""
    yield
    clock.set(None)


@pytest.fixture
def store():
    return Store(":memory:")


@pytest.fixture
def five():
    """A desk of five role holders (1-5), so a call needs three votes. 9 is an admin without the role."""
    return DESK.Roster(frozenset({1, 2, 3, 4, 5}), frozenset({1, 2, 3, 4, 5, 9}))


@pytest.fixture(scope="session")
def night():
    return RP.load_night()


@pytest.fixture(scope="session")
def replayed(night):
    """The whole of 2026 run through desk.py once. Tests only read it."""
    return RP.replay(night)


@pytest.fixture(scope="session")
def field():
    return RP.FieldHistory.load()
