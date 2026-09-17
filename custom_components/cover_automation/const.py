"""Constants for the cover_automation integration (spec §3)."""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "cover_automation"
PLATFORMS: Final[list[Platform]] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

SUBENTRY_COVER: Final = "cover"
SUBENTRY_PROFILE: Final = "profile"
PROFILE_NONE: Final = "none"

# Hub entry data (immutable identity of the hub)
CONF_WEATHER_ENTITY: Final = "weather_entity"
CONF_WIND_SENSOR: Final = "wind_sensor"
CONF_OUTDOOR_TEMPERATURE_SENSOR: Final = "outdoor_temperature_sensor"

# Hub options (thresholds and behaviour)
CONF_FROST_THRESHOLD: Final = "frost_threshold"
CONF_SUNNY_CONDITIONS: Final = "sunny_conditions"
CONF_SUNNY_ON_DELAY: Final = "sunny_on_delay"  # minutes
CONF_SUNNY_OFF_DELAY: Final = "sunny_off_delay"  # minutes
CONF_WEATHER_GRACE: Final = "weather_grace"  # minutes
CONF_HOT_HIGH: Final = "hot_high"
CONF_HOT_LOW: Final = "hot_low"
CONF_HOT_LOW_ENABLED: Final = "hot_low_enabled"
CONF_SUNNY_OVERRIDE_ENTITY: Final = "sunny_override_entity"
CONF_HOT_OVERRIDE_ENTITY: Final = "hot_override_entity"
CONF_WIND_OVERRIDE_ENTITY: Final = "wind_override_entity"
CONF_SUN_RELEASE_MARGIN: Final = "sun_release_margin"
CONF_TOLERANCE: Final = "open_closed_tolerance"
CONF_OVERRIDE_DWELL: Final = "override_dwell"  # minutes
CONF_TEMPERATURE_UNIT: Final = "temperature_unit"
CONF_WIND_UNIT: Final = "wind_unit"

# Cover subentry data
CONF_COVER_ENTITY: Final = "cover_entity"
CONF_NAME: Final = "name"
CONF_AZIMUTH: Final = "azimuth"
CONF_TOLERANCE_LEFT: Final = "tolerance_left"
CONF_TOLERANCE_RIGHT: Final = "tolerance_right"
CONF_ELEVATION_MIN: Final = "elevation_min"
CONF_ELEVATION_MAX: Final = "elevation_max"
CONF_SHADING_RULE: Final = "shading_rule"
CONF_ROOM_SENSOR: Final = "room_temperature_sensor"
CONF_COMFORT_FLOOR: Final = "comfort_floor"
CONF_COMFORT_CEILING: Final = "comfort_ceiling"
CONF_DOOR_SENSOR: Final = "door_sensor"
CONF_WIND_ENABLED: Final = "wind_enabled"
CONF_WIND_UPPER: Final = "wind_upper"
CONF_WIND_LOWER: Final = "wind_lower"
CONF_WIND_HOLD: Final = "wind_hold"  # minutes
CONF_WIND_ACTION: Final = "wind_action"
CONF_SCHEDULE_PROFILE: Final = "schedule_profile"
CONF_MIN_MOVE_INTERVAL: Final = "min_move_interval"  # minutes
CONF_CONFIRM_WINDOW: Final = "confirm_window"  # seconds

# Profile subentry data
CONF_RULES: Final = "rules"
CONF_RULE_ACTION: Final = "action"
CONF_RULE_TIME_MODE: Final = "time_mode"
CONF_RULE_TIME: Final = "time"
CONF_RULE_OFFSET: Final = "offset_minutes"
CONF_RULE_EARLIEST: Final = "earliest"
CONF_RULE_LATEST: Final = "latest"
CONF_RULE_ENABLED: Final = "enabled"
CONF_QUIET_START: Final = "quiet_start"
CONF_QUIET_END: Final = "quiet_end"
MAX_RULES: Final = 4

# Defaults (spec §3)
DEFAULT_FROST_THRESHOLD: Final = 0.0
DEFAULT_SUNNY_CONDITIONS: Final[tuple[str, ...]] = ("sunny", "partlycloudy")
DEFAULT_SUNNY_ON_DELAY_MIN: Final = 10
DEFAULT_SUNNY_OFF_DELAY_MIN: Final = 20
DEFAULT_WEATHER_GRACE_MIN: Final = 30
DEFAULT_HOT_HIGH: Final = 24.0
DEFAULT_HOT_LOW: Final = 13.0
DEFAULT_HOT_LOW_ENABLED: Final = True
DEFAULT_SUN_RELEASE_MARGIN: Final = 2.0
DEFAULT_TOLERANCE: Final = 5.0
DEFAULT_OVERRIDE_DWELL_MIN: Final = 30
DEFAULT_AZIMUTH: Final = 180.0
DEFAULT_TOLERANCE_LEFT: Final = 60.0
DEFAULT_TOLERANCE_RIGHT: Final = 60.0
DEFAULT_ELEVATION_MIN: Final = 0.0
DEFAULT_ELEVATION_MAX: Final = 90.0
DEFAULT_COMFORT_FLOOR: Final = 21.0
DEFAULT_COMFORT_CEILING: Final = 25.0
DEFAULT_WIND_UPPER_KMH: Final = 60.0
DEFAULT_WIND_LOWER_KMH: Final = 50.0
DEFAULT_WIND_HOLD_MIN: Final = 15
DEFAULT_MIN_MOVE_INTERVAL_MIN: Final = 10
DEFAULT_CONFIRM_WINDOW_S: Final = 120
MIN_CONFIRM_WINDOW_S: Final = 10

WEATHER_CONDITIONS: Final[list[str]] = [
    "clear-night",
    "cloudy",
    "exceptional",
    "fog",
    "hail",
    "lightning",
    "lightning-rainy",
    "partlycloudy",
    "pouring",
    "rainy",
    "snowy",
    "snowy-rainy",
    "sunny",
    "windy",
    "windy-variant",
]

STORAGE_VERSION: Final = 1
STORAGE_MINOR_VERSION: Final = 2


def storage_key(entry_id: str) -> str:
    return f"{DOMAIN}.{entry_id}"
