"""Map config entry / subentry dictionaries onto engine models (spec §3)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import UnitOfTemperature

from . import const
from .engine.model import CoverConfig, ShadingRule, WindAction
from .engine.schedule import Profile, QuietHours, Rule, RuleAction, TimeMode


@dataclass(frozen=True, slots=True)
class HubConfig:
    weather_entity: str
    wind_sensor: str | None
    outdoor_temperature_sensor: str | None
    frost_threshold: float
    sunny_conditions: frozenset[str]
    sunny_on_delay_s: int
    sunny_off_delay_s: int
    weather_grace_s: int
    hot_high: float
    hot_low: float | None
    sunny_override_entity: str | None
    hot_override_entity: str | None
    wind_override_entity: str | None
    sun_release_margin: float
    tolerance: float
    override_dwell_s: int
    temperature_unit: str


@dataclass(frozen=True, slots=True)
class CoverBindings:
    """Home Assistant entity references and units for one cover subentry."""

    subentry_id: str
    name: str
    cover_entity: str
    door_sensor: str | None
    room_sensor: str | None
    profile_ids: tuple[str, ...]
    temperature_unit: str
    wind_unit: str | None


def _opt_str(data: Mapping[str, Any], key: str, *, none_sentinel: bool = False) -> str | None:
    """Read an optional string field; `none_sentinel` also treats `const.PROFILE_NONE` as unset.

    Only the schedule-profile field uses the "none" sentinel value; every other optional
    field (entity ids, units) is unset by being absent or empty, never by the literal string.
    """
    value = data.get(key)
    if value in (None, "") or (none_sentinel and value == const.PROFILE_NONE):
        return None
    return str(value)


def profile_ids(data: Mapping[str, Any]) -> tuple[str, ...]:
    """The cover's schedule profiles in priority order, highest first.

    Backwards compatible on purpose (no migration): covers written before v0.5.0 carry the
    single `schedule_profile` key -- "none" or one subentry id -- and are read as a zero- or
    one-element tuple. The list key wins whenever it is present, even when it is empty.
    """
    raw = data.get(const.CONF_SCHEDULE_PROFILES)
    # A bare string is iterable: without the type check a hand-written `schedule_profiles:
    # "abc"` would become three one-character profile ids instead of being ignored.
    if isinstance(raw, list | tuple):
        return tuple(str(value) for value in raw if str(value))
    legacy = _opt_str(data, const.CONF_SCHEDULE_PROFILE, none_sentinel=True)
    return (legacy,) if legacy is not None else ()


def _minutes(data: Mapping[str, Any], key: str, default_min: int) -> int:
    return round(float(data.get(key, default_min)) * 60)


def parse_time(value: str | None) -> time | None:
    if value in (None, ""):
        return None
    parts = [int(p) for p in str(value).split(":")]
    if len(parts) == 2:
        return time(parts[0], parts[1])
    if len(parts) == 3:
        return time(parts[0], parts[1], parts[2])
    msg = f"invalid time: {value!r}"
    raise ValueError(msg)


def hub_config(entry: ConfigEntry) -> HubConfig:
    data, opts = entry.data, entry.options
    hot_low_enabled = bool(opts.get(const.CONF_HOT_LOW_ENABLED, const.DEFAULT_HOT_LOW_ENABLED))
    return HubConfig(
        weather_entity=str(data[const.CONF_WEATHER_ENTITY]),
        wind_sensor=_opt_str(data, const.CONF_WIND_SENSOR),
        outdoor_temperature_sensor=_opt_str(data, const.CONF_OUTDOOR_TEMPERATURE_SENSOR),
        frost_threshold=float(opts.get(const.CONF_FROST_THRESHOLD, const.DEFAULT_FROST_THRESHOLD)),
        sunny_conditions=frozenset(
            opts.get(const.CONF_SUNNY_CONDITIONS, const.DEFAULT_SUNNY_CONDITIONS)
        ),
        sunny_on_delay_s=_minutes(
            opts, const.CONF_SUNNY_ON_DELAY, const.DEFAULT_SUNNY_ON_DELAY_MIN
        ),
        sunny_off_delay_s=_minutes(
            opts, const.CONF_SUNNY_OFF_DELAY, const.DEFAULT_SUNNY_OFF_DELAY_MIN
        ),
        weather_grace_s=_minutes(opts, const.CONF_WEATHER_GRACE, const.DEFAULT_WEATHER_GRACE_MIN),
        hot_high=float(opts.get(const.CONF_HOT_HIGH, const.DEFAULT_HOT_HIGH)),
        hot_low=float(opts.get(const.CONF_HOT_LOW, const.DEFAULT_HOT_LOW))
        if hot_low_enabled
        else None,
        sunny_override_entity=_opt_str(opts, const.CONF_SUNNY_OVERRIDE_ENTITY),
        hot_override_entity=_opt_str(opts, const.CONF_HOT_OVERRIDE_ENTITY),
        wind_override_entity=_opt_str(opts, const.CONF_WIND_OVERRIDE_ENTITY),
        sun_release_margin=float(
            opts.get(const.CONF_SUN_RELEASE_MARGIN, const.DEFAULT_SUN_RELEASE_MARGIN)
        ),
        tolerance=float(opts.get(const.CONF_TOLERANCE, const.DEFAULT_TOLERANCE)),
        override_dwell_s=_minutes(
            opts, const.CONF_OVERRIDE_DWELL, const.DEFAULT_OVERRIDE_DWELL_MIN
        ),
        # hand-written entries only; flows always stamp the unit
        temperature_unit=str(opts.get(const.CONF_TEMPERATURE_UNIT, UnitOfTemperature.CELSIUS)),
    )


def cover_config(subentry: ConfigSubentry, hub: HubConfig) -> tuple[CoverConfig, CoverBindings]:
    d = subentry.data
    room_sensor = _opt_str(d, const.CONF_ROOM_SENSOR)
    door_sensor = _opt_str(d, const.CONF_DOOR_SENSOR)
    profiles = profile_ids(d)
    wind_enabled = bool(d.get(const.CONF_WIND_ENABLED, False)) and hub.wind_sensor is not None
    name = str(d.get(const.CONF_NAME) or subentry.title)
    cfg = CoverConfig(
        cover_id=subentry.subentry_id,
        name=name,
        azimuth=float(d[const.CONF_AZIMUTH]),
        tolerance_left=float(d.get(const.CONF_TOLERANCE_LEFT, const.DEFAULT_TOLERANCE_LEFT)),
        tolerance_right=float(d.get(const.CONF_TOLERANCE_RIGHT, const.DEFAULT_TOLERANCE_RIGHT)),
        elevation_min=float(d.get(const.CONF_ELEVATION_MIN, const.DEFAULT_ELEVATION_MIN)),
        elevation_max=float(d.get(const.CONF_ELEVATION_MAX, const.DEFAULT_ELEVATION_MAX)),
        shading_rule=ShadingRule(
            d.get(const.CONF_SHADING_RULE, ShadingRule.FORECAST_WITH_ROOM.value)
        ),
        has_room_sensor=room_sensor is not None,
        comfort_floor=float(d.get(const.CONF_COMFORT_FLOOR, const.DEFAULT_COMFORT_FLOOR)),
        comfort_ceiling=float(d.get(const.CONF_COMFORT_CEILING, const.DEFAULT_COMFORT_CEILING)),
        has_door_sensor=door_sensor is not None,
        wind_enabled=wind_enabled,
        wind_upper=float(d.get(const.CONF_WIND_UPPER, 0.0)),
        wind_lower=float(d.get(const.CONF_WIND_LOWER, 0.0)),
        wind_hold_s=_minutes(d, const.CONF_WIND_HOLD, const.DEFAULT_WIND_HOLD_MIN),
        wind_action=WindAction(d.get(const.CONF_WIND_ACTION, WindAction.OPEN.value)),
        profile_ids=profiles,
        min_move_interval_s=_minutes(
            d, const.CONF_MIN_MOVE_INTERVAL, const.DEFAULT_MIN_MOVE_INTERVAL_MIN
        ),
        confirm_window_s=int(d.get(const.CONF_CONFIRM_WINDOW, const.DEFAULT_CONFIRM_WINDOW_S)),
    )
    bindings = CoverBindings(
        subentry_id=subentry.subentry_id,
        name=name,
        cover_entity=str(d[const.CONF_COVER_ENTITY]),
        door_sensor=door_sensor,
        room_sensor=room_sensor,
        profile_ids=profiles,
        temperature_unit=str(d.get(const.CONF_TEMPERATURE_UNIT, hub.temperature_unit)),
        wind_unit=_opt_str(d, const.CONF_WIND_UNIT),
    )
    return cfg, bindings


def rules_from_data(data: Mapping[str, Any]) -> tuple[Rule, ...]:
    rules: list[Rule] = []
    for raw in data.get(const.CONF_RULES, []):
        rules.append(
            Rule(
                action=RuleAction(raw[const.CONF_RULE_ACTION]),
                time_mode=TimeMode(raw[const.CONF_RULE_TIME_MODE]),
                time=parse_time(raw.get(const.CONF_RULE_TIME)),
                offset_minutes=int(raw.get(const.CONF_RULE_OFFSET, 0) or 0),
                earliest=parse_time(raw.get(const.CONF_RULE_EARLIEST)),
                latest=parse_time(raw.get(const.CONF_RULE_LATEST)),
            )
        )
    return tuple(rules)


def quiet_from_data(data: Mapping[str, Any]) -> QuietHours | None:
    start = parse_time(data.get(const.CONF_QUIET_START))
    end = parse_time(data.get(const.CONF_QUIET_END))
    if start is None or end is None:
        return None
    return QuietHours(start=start, end=end)


def profile(subentry: ConfigSubentry) -> Profile:
    return Profile(
        profile_id=subentry.subentry_id,
        name=str(subentry.data.get(const.CONF_NAME) or subentry.title),
        rules=rules_from_data(subentry.data),
        quiet_hours=quiet_from_data(subentry.data),
    )
