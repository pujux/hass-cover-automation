from __future__ import annotations

import dataclasses
from datetime import time
from types import MappingProxyType

import pytest
from custom_components.cover_automation import const
from custom_components.cover_automation.config_map import (
    cover_config,
    hub_config,
    parse_time,
    profile,
)
from custom_components.cover_automation.engine.model import ShadingRule, WindAction
from custom_components.cover_automation.engine.schedule import RuleAction, TimeMode
from homeassistant.config_entries import ConfigSubentry
from pytest_homeassistant_custom_component.common import MockConfigEntry


def make_hub_entry(**options) -> MockConfigEntry:
    opts = {
        const.CONF_FROST_THRESHOLD: 0.0,
        const.CONF_SUNNY_CONDITIONS: ["sunny", "partlycloudy"],
        const.CONF_SUNNY_ON_DELAY: 10,
        const.CONF_SUNNY_OFF_DELAY: 20,
        const.CONF_WEATHER_GRACE: 30,
        const.CONF_HOT_HIGH: 24.0,
        const.CONF_HOT_LOW: 13.0,
        const.CONF_HOT_LOW_ENABLED: True,
        const.CONF_SUN_RELEASE_MARGIN: 2.0,
        const.CONF_TOLERANCE: 5.0,
        const.CONF_OVERRIDE_DWELL: 30,
        const.CONF_TEMPERATURE_UNIT: "°C",
    }
    opts.update(options)
    return MockConfigEntry(
        domain=const.DOMAIN,
        data={const.CONF_WEATHER_ENTITY: "weather.home", const.CONF_WIND_SENSOR: "sensor.wind"},
        options=opts,
    )


def subentry(kind: str, data: dict, title: str = "x", subentry_id: str = "sub1") -> ConfigSubentry:
    return ConfigSubentry(
        data=MappingProxyType(data),
        subentry_type=kind,
        title=title,
        unique_id=None,
        subentry_id=subentry_id,
    )


def test_hub_config_converts_minutes_and_optional_low():
    hub = hub_config(make_hub_entry())
    assert hub.weather_entity == "weather.home" and hub.wind_sensor == "sensor.wind"
    assert hub.outdoor_temperature_sensor is None
    assert (hub.sunny_on_delay_s, hub.sunny_off_delay_s, hub.weather_grace_s) == (600, 1200, 1800)
    assert hub.override_dwell_s == 1800 and hub.hot_low == 13.0
    assert hub.sunny_conditions == frozenset({"sunny", "partlycloudy"})
    hub2 = hub_config(make_hub_entry(**{const.CONF_HOT_LOW_ENABLED: False}))
    assert hub2.hot_low is None


def test_hub_config_exposes_the_wind_override_entity():
    assert hub_config(make_hub_entry()).wind_override_entity is None
    hub = hub_config(
        make_hub_entry(**{const.CONF_WIND_OVERRIDE_ENTITY: "input_boolean.windschutz"})
    )
    assert hub.wind_override_entity == "input_boolean.windschutz"


def test_hub_config_uses_defaults_for_missing_options():
    entry = MockConfigEntry(
        domain=const.DOMAIN, data={const.CONF_WEATHER_ENTITY: "weather.home"}, options={}
    )
    hub = hub_config(entry)
    assert hub.hot_high == 24.0 and hub.tolerance == 5.0 and hub.temperature_unit == "°C"
    assert hub.wind_sensor is None


def test_cover_config_maps_all_fields_and_units():
    hub = hub_config(make_hub_entry())
    se = subentry(
        const.SUBENTRY_COVER,
        {
            const.CONF_COVER_ENTITY: "cover.bedroom",
            const.CONF_NAME: "Bedroom",
            const.CONF_AZIMUTH: 170,
            const.CONF_TOLERANCE_LEFT: 50,
            const.CONF_TOLERANCE_RIGHT: 70,
            const.CONF_ELEVATION_MIN: 5,
            const.CONF_ELEVATION_MAX: 80,
            const.CONF_SHADING_RULE: "room_only",
            const.CONF_ROOM_SENSOR: "sensor.bedroom_temp",
            const.CONF_COMFORT_FLOOR: 20,
            const.CONF_COMFORT_CEILING: 26,
            const.CONF_DOOR_SENSOR: "binary_sensor.terrace",
            const.CONF_WIND_ENABLED: True,
            const.CONF_WIND_UPPER: 60,
            const.CONF_WIND_LOWER: 50,
            const.CONF_WIND_HOLD: 20,
            const.CONF_WIND_ACTION: "hold",
            const.CONF_SCHEDULE_PROFILE: "prof1",
            const.CONF_MIN_MOVE_INTERVAL: 15,
            const.CONF_CONFIRM_WINDOW: 90,
            const.CONF_TEMPERATURE_UNIT: "°C",
            const.CONF_WIND_UNIT: "km/h",
        },
        title="Bedroom",
    )
    cfg, bind = cover_config(se, hub)
    assert cfg.cover_id == "sub1" and cfg.name == "Bedroom" and cfg.azimuth == 170.0
    assert (cfg.tolerance_left, cfg.tolerance_right, cfg.elevation_min, cfg.elevation_max) == (
        50.0,
        70.0,
        5.0,
        80.0,
    )
    assert cfg.shading_rule is ShadingRule.ROOM_ONLY and cfg.has_room_sensor and cfg.has_door_sensor
    assert (cfg.comfort_floor, cfg.comfort_ceiling) == (20.0, 26.0)
    assert cfg.wind_enabled and (cfg.wind_upper, cfg.wind_lower, cfg.wind_hold_s) == (
        60.0,
        50.0,
        1200,
    )
    assert cfg.wind_action is WindAction.HOLD and cfg.profile_ids == ("prof1",)
    assert (cfg.min_move_interval_s, cfg.confirm_window_s) == (900, 90)
    assert bind.cover_entity == "cover.bedroom" and bind.door_sensor == "binary_sensor.terrace"
    assert bind.room_sensor == "sensor.bedroom_temp" and bind.profile_ids == ("prof1",)
    assert (bind.temperature_unit, bind.wind_unit) == ("°C", "km/h")


def test_cover_config_defaults_and_none_profile():
    hub = hub_config(make_hub_entry())
    se = subentry(
        const.SUBENTRY_COVER,
        {
            const.CONF_COVER_ENTITY: "cover.x",
            const.CONF_AZIMUTH: 180,
            const.CONF_SCHEDULE_PROFILE: const.PROFILE_NONE,
        },
        title="X",
    )
    cfg, bind = cover_config(se, hub)
    assert (
        cfg.name == "X"
        and not cfg.has_room_sensor
        and not cfg.wind_enabled
        and cfg.profile_ids == ()
    )
    assert (
        cfg.tolerance_left,
        cfg.elevation_max,
        cfg.min_move_interval_s,
        cfg.confirm_window_s,
    ) == (
        60.0,
        90.0,
        600,
        120,
    )
    assert bind.profile_ids == () and bind.temperature_unit == hub.temperature_unit


def test_wind_disabled_when_hub_has_no_wind_sensor():
    hub = dataclasses.replace(hub_config(make_hub_entry()), wind_sensor=None)
    se = subentry(
        const.SUBENTRY_COVER,
        {
            const.CONF_COVER_ENTITY: "cover.x",
            const.CONF_AZIMUTH: 180,
            const.CONF_WIND_ENABLED: True,
            const.CONF_WIND_UPPER: 60,
            const.CONF_WIND_LOWER: 50,
        },
    )
    cfg, _ = cover_config(se, hub)
    assert cfg.wind_enabled is False


def test_parse_time():
    assert parse_time("21:30") == time(21, 30)
    assert parse_time("06:15:30") == time(6, 15, 30)
    assert parse_time(None) is None and parse_time("") is None


def test_profile_mapping_rules_and_quiet_hours():
    se = subentry(
        const.SUBENTRY_PROFILE,
        {
            const.CONF_NAME: "Bedroom",
            const.CONF_RULES: [
                {
                    const.CONF_RULE_ACTION: "closed",
                    const.CONF_RULE_TIME_MODE: "fixed",
                    const.CONF_RULE_TIME: "21:30:00",
                },
                {
                    const.CONF_RULE_ACTION: "open",
                    const.CONF_RULE_TIME_MODE: "sunrise",
                    const.CONF_RULE_OFFSET: 30,
                    const.CONF_RULE_EARLIEST: "07:00:00",
                    const.CONF_RULE_LATEST: None,
                },
            ],
            const.CONF_QUIET_START: "22:00:00",
            const.CONF_QUIET_END: "07:00:00",
        },
        title="Bedroom",
        subentry_id="prof1",
    )
    p = profile(se)
    assert p.profile_id == "prof1" and p.name == "Bedroom" and len(p.rules) == 2
    assert (
        p.rules[0].action is RuleAction.CLOSED
        and p.rules[0].time_mode is TimeMode.FIXED
        and p.rules[0].time == time(21, 30)
    )
    assert p.rules[1].action is RuleAction.OPEN and p.rules[1].time_mode is TimeMode.SUNRISE
    assert (
        p.rules[1].offset_minutes == 30
        and p.rules[1].earliest == time(7, 0)
        and p.rules[1].latest is None
    )
    assert p.quiet_hours is not None and (p.quiet_hours.start, p.quiet_hours.end) == (
        time(22, 0),
        time(7, 0),
    )


def test_profile_without_quiet_hours_or_rules():
    se = subentry(
        const.SUBENTRY_PROFILE,
        {const.CONF_NAME: "Empty", const.CONF_RULES: []},
        title="Empty",
        subentry_id="p",
    )
    p = profile(se)
    assert p.rules == () and p.quiet_hours is None


def test_invalid_shading_rule_raises():
    hub = hub_config(make_hub_entry())
    se = subentry(
        const.SUBENTRY_COVER,
        {
            const.CONF_COVER_ENTITY: "cover.x",
            const.CONF_AZIMUTH: 1,
            const.CONF_SHADING_RULE: "bogus",
        },
    )
    with pytest.raises(ValueError):
        cover_config(se, hub)


def _cover_with(**data) -> tuple:
    return cover_config(
        subentry(
            const.SUBENTRY_COVER,
            {const.CONF_COVER_ENTITY: "cover.x", const.CONF_AZIMUTH: 180, **data},
            title="X",
        ),
        hub_config(make_hub_entry()),
    )


def test_profile_list_is_read_in_priority_order():
    cfg, bind = _cover_with(**{const.CONF_SCHEDULE_PROFILES: ["p2", "p1", "p3"]})
    assert cfg.profile_ids == ("p2", "p1", "p3") and bind.profile_ids == cfg.profile_ids


def test_legacy_single_profile_key_still_loads(caplog):
    """The eleven production covers store `schedule_profile`; no migration may be needed."""
    cfg, _bind = _cover_with(**{const.CONF_SCHEDULE_PROFILE: "prof1"})
    assert cfg.profile_ids == ("prof1",)
    none_cfg, none_bind = _cover_with(**{const.CONF_SCHEDULE_PROFILE: const.PROFILE_NONE})
    assert none_cfg.profile_ids == () and none_bind.profile_ids == ()
    # a cover written before schedules existed at all
    assert _cover_with()[0].profile_ids == ()


def test_profile_list_wins_over_the_legacy_key():
    cfg, _bind = _cover_with(
        **{
            const.CONF_SCHEDULE_PROFILE: "prof1",
            const.CONF_SCHEDULE_PROFILES: ["prof2"],
        }
    )
    assert cfg.profile_ids == ("prof2",)
    # an explicitly emptied list means "no schedule", not "fall back to the legacy key"
    emptied, _ = _cover_with(
        **{const.CONF_SCHEDULE_PROFILE: "prof1", const.CONF_SCHEDULE_PROFILES: []}
    )
    assert emptied.profile_ids == ()


def test_profile_list_of_the_wrong_shape_is_ignored():
    """A hand-written scalar must not become one profile id per character."""
    assert _cover_with(**{const.CONF_SCHEDULE_PROFILES: "prof1"})[0].profile_ids == ()
    # ... and it falls back to the legacy key rather than to garbage
    cfg, _bind = _cover_with(
        **{const.CONF_SCHEDULE_PROFILES: "prof1", const.CONF_SCHEDULE_PROFILE: "prof2"}
    )
    assert cfg.profile_ids == ("prof2",)
