from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

TZ = ZoneInfo("Europe/Vienna")


def at(day: str, clock: str) -> datetime:
    """Local, timezone-aware datetime from 'YYYY-MM-DD' and 'HH:MM[:SS]'."""
    hh, mm, *rest = (int(x) for x in clock.split(":"))
    ss = rest[0] if rest else 0
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, hh, mm, ss, tzinfo=TZ)


def t(clock: str) -> time:
    hh, mm, *rest = (int(x) for x in clock.split(":"))
    return time(hh, mm, rest[0] if rest else 0)


@pytest.fixture
def tz() -> ZoneInfo:
    return TZ
