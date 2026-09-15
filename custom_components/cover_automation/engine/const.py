"""Engine defaults (spec §2, §3). No Home Assistant imports in this package."""

from __future__ import annotations

from typing import Final

OPEN_CLOSED_TOLERANCE: Final[float] = 5.0
SUN_RELEASE_MARGIN: Final[float] = 2.0
SUNNY_ON_DELAY_S: Final[int] = 600
SUNNY_OFF_DELAY_S: Final[int] = 1200
WEATHER_GRACE_S: Final[int] = 1800
HOT_HIGH_C: Final[float] = 24.0
HOT_LOW_C: Final[float] = 13.0
ROOM_HYSTERESIS_K: Final[float] = 0.5
ROOM_DWELL_S: Final[int] = 600
COMFORT_FLOOR_C: Final[float] = 21.0
COMFORT_CEILING_C: Final[float] = 25.0
WIND_HOLD_S: Final[int] = 900
FROST_THRESHOLD_C: Final[float] = 0.0
FROST_RELEASE_K: Final[float] = 1.0
OVERRIDE_DWELL_S: Final[int] = 1800
MIN_MOVE_INTERVAL_S: Final[int] = 600
CONFIRM_WINDOW_S: Final[int] = 120
CONTRARY_PERSIST_S: Final[int] = 10
RESTORING_WINDOW_S: Final[int] = 900
OPEN_RULE_WINDOW_S: Final[int] = 900
BACKOFF_MAX_S: Final[int] = 3600
COMMAND_RETRY_S: Final[int] = 30
