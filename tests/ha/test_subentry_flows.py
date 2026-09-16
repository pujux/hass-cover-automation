from __future__ import annotations

from custom_components.cover_automation import const
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from tests.ha.conftest import (
    cover_subentry_data,
    profile_subentry_data,
    set_cover,
    set_sensor,
    set_weather,
)

COVER_INPUT = {
    const.CONF_COVER_ENTITY: "cover.bedroom",
    const.CONF_NAME: "Bedroom",
    const.CONF_AZIMUTH: 170,
    const.CONF_TOLERANCE_LEFT: 60,
    const.CONF_TOLERANCE_RIGHT: 60,
    const.CONF_ELEVATION_MIN: 0,
    const.CONF_ELEVATION_MAX: 90,
    const.CONF_SHADING_RULE: "forecast_with_room",
    const.CONF_COMFORT_FLOOR: 21,
    const.CONF_COMFORT_CEILING: 25,
    const.CONF_WIND_ENABLED: True,
    const.CONF_WIND_UPPER: 60,
    const.CONF_WIND_LOWER: 50,
    const.CONF_WIND_HOLD: 15,
    const.CONF_WIND_ACTION: "open",
    const.CONF_SCHEDULE_PROFILE: const.PROFILE_NONE,
    const.CONF_MIN_MOVE_INTERVAL: 10,
    const.CONF_CONFIRM_WINDOW: 120,
}


async def start(hass: HomeAssistant, entry, kind: str):
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, kind), context={"source": config_entries.SOURCE_USER}
    )


async def test_cover_subentry_created_with_units_and_profile_none(
    hass: HomeAssistant, hub_entry
) -> None:
    set_weather(hass)
    set_sensor(hass, "sensor.wind", 5, unit="km/h", device_class="wind_speed")
    set_cover(hass, "cover.bedroom", features=3)
    result = await start(hass, hub_entry, const.SUBENTRY_COVER)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], COVER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    sub = next(iter(hub_entry.subentries.values()))
    assert sub.subentry_type == const.SUBENTRY_COVER and sub.title == "Bedroom"
    assert (
        sub.data[const.CONF_TEMPERATURE_UNIT] == "°C" and sub.data[const.CONF_WIND_UNIT] == "km/h"
    )
    assert sub.data[const.CONF_SCHEDULE_PROFILE] == const.PROFILE_NONE


async def test_cover_subentry_validation_errors(hass: HomeAssistant, hub_entry) -> None:
    set_weather(hass)
    set_cover(hass, "cover.tiltonly", features=16)  # OPEN_TILT only
    result = await start(hass, hub_entry, const.SUBENTRY_COVER)
    bad = {
        **COVER_INPUT,
        const.CONF_COVER_ENTITY: "cover.tiltonly",
        const.CONF_COMFORT_FLOOR: 26,
        const.CONF_WIND_LOWER: 70,
        const.CONF_SHADING_RULE: "room_only",
        const.CONF_CONFIRM_WINDOW: 5,
    }
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], bad)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {
        const.CONF_COVER_ENTITY: "cover_unsupported",
        const.CONF_COMFORT_FLOOR: "floor_not_below_ceiling",
        const.CONF_WIND_LOWER: "wind_lower_not_below_upper",
        const.CONF_ROOM_SENSOR: "room_sensor_required",
        const.CONF_CONFIRM_WINDOW: "confirm_window_too_short",
    }


async def test_cover_without_state_is_accepted(hass: HomeAssistant, hub_entry) -> None:
    set_weather(hass)
    result = await start(hass, hub_entry, const.SUBENTRY_COVER)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**COVER_INPUT, const.CONF_COVER_ENTITY: "cover.asleep"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_same_cover_twice_is_rejected(hass: HomeAssistant, hub_entry) -> None:
    set_weather(hass)
    set_cover(hass, "cover.bedroom")
    hass.config_entries.async_add_subentry(
        hub_entry, config_entries.ConfigSubentry(**cover_subentry_data("cover.bedroom"))
    )
    result = await start(hass, hub_entry, const.SUBENTRY_COVER)
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], COVER_INPUT)
    assert result["errors"] == {const.CONF_COVER_ENTITY: "already_configured"}


async def test_cover_reconfigure_keeps_identity_and_offers_profiles(
    hass: HomeAssistant, hub_entry
) -> None:
    set_weather(hass)
    set_cover(hass, "cover.bedroom")
    hass.config_entries.async_add_subentry(
        hub_entry, config_entries.ConfigSubentry(**profile_subentry_data("Night"))
    )
    hass.config_entries.async_add_subentry(
        hub_entry, config_entries.ConfigSubentry(**cover_subentry_data("cover.bedroom"))
    )
    cover_sub = next(
        s for s in hub_entry.subentries.values() if s.subentry_type == const.SUBENTRY_COVER
    )
    prof_sub = next(
        s for s in hub_entry.subentries.values() if s.subentry_type == const.SUBENTRY_PROFILE
    )
    result = await hass.config_entries.subentries.async_init(
        (hub_entry.entry_id, const.SUBENTRY_COVER),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": cover_sub.subentry_id},
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            **COVER_INPUT,
            const.CONF_NAME: "Bedroom East",
            const.CONF_SCHEDULE_PROFILE: prof_sub.subentry_id,
        },
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    updated = hub_entry.subentries[cover_sub.subentry_id]
    assert (
        updated.title == "Bedroom East"
        and updated.data[const.CONF_SCHEDULE_PROFILE] == prof_sub.subentry_id
    )


async def test_cover_reconfigure_keeps_stored_temperature_unit_after_unit_system_change(
    hass: HomeAssistant, hub_entry
) -> None:
    """Reconfiguring after a household unit-system switch must render and re-save in the
    unit the cover was stored in, not the current unit system, so a stored °C comfort
    value is never silently relabelled °F without conversion."""
    set_weather(hass)
    set_cover(hass, "cover.bedroom")
    hass.config_entries.async_add_subentry(
        hub_entry, config_entries.ConfigSubentry(**cover_subentry_data("cover.bedroom"))
    )
    cover_sub = next(iter(hub_entry.subentries.values()))
    assert cover_sub.data[const.CONF_TEMPERATURE_UNIT] == "°C"
    hass.config.units = US_CUSTOMARY_SYSTEM
    result = await hass.config_entries.subentries.async_init(
        (hub_entry.entry_id, const.SUBENTRY_COVER),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": cover_sub.subentry_id},
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], COVER_INPUT)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    updated = hub_entry.subentries[cover_sub.subentry_id]
    assert updated.data[const.CONF_TEMPERATURE_UNIT] == "°C"
    assert (
        updated.data[const.CONF_COMFORT_FLOOR] == COVER_INPUT[const.CONF_COMFORT_FLOOR]
        and updated.data[const.CONF_COMFORT_CEILING] == COVER_INPUT[const.CONF_COMFORT_CEILING]
    )


async def test_cover_reconfigure_keeps_wind_unit_when_sensor_has_no_state(
    hass: HomeAssistant, hub_entry
) -> None:
    """A wind sensor that currently has no state (integration not loaded yet, entity
    renamed) must not wipe out the wind_unit already stored on the cover subentry."""
    set_weather(hass)
    set_cover(hass, "cover.bedroom")
    set_sensor(hass, "sensor.wind", 5, unit="km/h", device_class="wind_speed")
    hass.config_entries.async_add_subentry(
        hub_entry,
        config_entries.ConfigSubentry(**cover_subentry_data("cover.bedroom", wind_unit="km/h")),
    )
    cover_sub = next(iter(hub_entry.subentries.values()))
    hass.states.async_remove("sensor.wind")
    result = await hass.config_entries.subentries.async_init(
        (hub_entry.entry_id, const.SUBENTRY_COVER),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": cover_sub.subentry_id},
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], COVER_INPUT)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    updated = hub_entry.subentries[cover_sub.subentry_id]
    assert updated.data[const.CONF_WIND_UNIT] == "km/h"


async def test_wind_fields_hidden_without_hub_wind_sensor(hass: HomeAssistant) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from tests.ha.conftest import WEATHER, hub_options

    set_weather(hass)
    entry = MockConfigEntry(
        domain=const.DOMAIN, data={const.CONF_WEATHER_ENTITY: WEATHER}, options=hub_options()
    )
    entry.add_to_hass(hass)
    result = await start(hass, entry, const.SUBENTRY_COVER)
    keys = {str(k) for k in result["data_schema"].schema}
    assert const.CONF_WIND_UPPER not in keys and const.CONF_AZIMUTH in keys


async def test_profile_subentry_created_from_rule_sections(hass: HomeAssistant, hub_entry) -> None:
    result = await start(hass, hub_entry, const.SUBENTRY_PROFILE)
    assert result["step_id"] == "user"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            const.CONF_NAME: "Bedroom",
            const.CONF_QUIET_START: "22:00:00",
            const.CONF_QUIET_END: "07:00:00",
            "rule_1": {
                const.CONF_RULE_ENABLED: True,
                const.CONF_RULE_ACTION: "closed",
                const.CONF_RULE_TIME_MODE: "fixed",
                const.CONF_RULE_TIME: "21:30:00",
                const.CONF_RULE_OFFSET: 0,
            },
            "rule_2": {
                const.CONF_RULE_ENABLED: True,
                const.CONF_RULE_ACTION: "open",
                const.CONF_RULE_TIME_MODE: "sunrise",
                const.CONF_RULE_OFFSET: 30,
                const.CONF_RULE_EARLIEST: "07:00:00",
            },
            "rule_3": {
                const.CONF_RULE_ENABLED: False,
                const.CONF_RULE_ACTION: "closed",
                const.CONF_RULE_TIME_MODE: "fixed",
                const.CONF_RULE_OFFSET: 0,
            },
            "rule_4": {
                const.CONF_RULE_ENABLED: False,
                const.CONF_RULE_ACTION: "closed",
                const.CONF_RULE_TIME_MODE: "fixed",
                const.CONF_RULE_OFFSET: 0,
            },
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    sub = next(iter(hub_entry.subentries.values()))
    assert sub.title == "Bedroom" and len(sub.data[const.CONF_RULES]) == 2
    assert sub.data[const.CONF_RULES][0] == {
        const.CONF_RULE_ACTION: "closed",
        const.CONF_RULE_TIME_MODE: "fixed",
        const.CONF_RULE_TIME: "21:30:00",
        const.CONF_RULE_OFFSET: 0,
        const.CONF_RULE_EARLIEST: None,
        const.CONF_RULE_LATEST: None,
    }
    assert sub.data[const.CONF_RULES][1][const.CONF_RULE_EARLIEST] == "07:00:00"
    assert (sub.data[const.CONF_QUIET_START], sub.data[const.CONF_QUIET_END]) == (
        "22:00:00",
        "07:00:00",
    )


async def test_profile_validation_uses_engine(hass: HomeAssistant, hub_entry) -> None:
    result = await start(hass, hub_entry, const.SUBENTRY_PROFILE)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            const.CONF_NAME: "Bad",
            const.CONF_QUIET_START: "22:00:00",
            const.CONF_QUIET_END: "07:00:00",
            "rule_1": {
                const.CONF_RULE_ENABLED: True,
                const.CONF_RULE_ACTION: "closed",
                const.CONF_RULE_TIME_MODE: "fixed",
                const.CONF_RULE_TIME: "23:00:00",
                const.CONF_RULE_OFFSET: 0,
            },
            "rule_2": {
                const.CONF_RULE_ENABLED: True,
                const.CONF_RULE_ACTION: "open",
                const.CONF_RULE_TIME_MODE: "fixed",
                const.CONF_RULE_OFFSET: 0,
            },
            "rule_3": {
                const.CONF_RULE_ENABLED: False,
                const.CONF_RULE_ACTION: "closed",
                const.CONF_RULE_TIME_MODE: "fixed",
                const.CONF_RULE_OFFSET: 0,
            },
            "rule_4": {
                const.CONF_RULE_ENABLED: False,
                const.CONF_RULE_ACTION: "closed",
                const.CONF_RULE_TIME_MODE: "fixed",
                const.CONF_RULE_OFFSET: 0,
            },
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_rules"}
    assert "rule 1 fires inside quiet hours" in result["description_placeholders"]["problems"]
    assert "rule 2 needs a time" in result["description_placeholders"]["problems"]


async def test_profile_partial_quiet_hours_is_rejected(hass: HomeAssistant, hub_entry) -> None:
    """A quiet-hours start with no end (or vice versa) must be refused with feedback,
    not silently saved as 'no quiet hours'."""
    result = await start(hass, hub_entry, const.SUBENTRY_PROFILE)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            const.CONF_NAME: "Partial",
            const.CONF_QUIET_START: "22:00:00",
            "rule_1": {
                const.CONF_RULE_ENABLED: False,
                const.CONF_RULE_ACTION: "closed",
                const.CONF_RULE_TIME_MODE: "fixed",
                const.CONF_RULE_OFFSET: 0,
            },
            "rule_2": {
                const.CONF_RULE_ENABLED: False,
                const.CONF_RULE_ACTION: "closed",
                const.CONF_RULE_TIME_MODE: "fixed",
                const.CONF_RULE_OFFSET: 0,
            },
            "rule_3": {
                const.CONF_RULE_ENABLED: False,
                const.CONF_RULE_ACTION: "closed",
                const.CONF_RULE_TIME_MODE: "fixed",
                const.CONF_RULE_OFFSET: 0,
            },
            "rule_4": {
                const.CONF_RULE_ENABLED: False,
                const.CONF_RULE_ACTION: "closed",
                const.CONF_RULE_TIME_MODE: "fixed",
                const.CONF_RULE_OFFSET: 0,
            },
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_rules"}
    assert (
        "quiet hours need both a start and an end" in result["description_placeholders"]["problems"]
    )


async def test_profile_empty_name_is_rejected(hass: HomeAssistant, hub_entry) -> None:
    result = await start(hass, hub_entry, const.SUBENTRY_PROFILE)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            const.CONF_NAME: "   ",
            "rule_1": {
                const.CONF_RULE_ENABLED: False,
                const.CONF_RULE_ACTION: "closed",
                const.CONF_RULE_TIME_MODE: "fixed",
                const.CONF_RULE_OFFSET: 0,
            },
            "rule_2": {
                const.CONF_RULE_ENABLED: False,
                const.CONF_RULE_ACTION: "closed",
                const.CONF_RULE_TIME_MODE: "fixed",
                const.CONF_RULE_OFFSET: 0,
            },
            "rule_3": {
                const.CONF_RULE_ENABLED: False,
                const.CONF_RULE_ACTION: "closed",
                const.CONF_RULE_TIME_MODE: "fixed",
                const.CONF_RULE_OFFSET: 0,
            },
            "rule_4": {
                const.CONF_RULE_ENABLED: False,
                const.CONF_RULE_ACTION: "closed",
                const.CONF_RULE_TIME_MODE: "fixed",
                const.CONF_RULE_OFFSET: 0,
            },
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {const.CONF_NAME: "name_required"}


async def test_cover_reconfigure_offers_none_when_profile_deleted(
    hass: HomeAssistant, hub_entry
) -> None:
    """A cover referencing a schedule profile that was later deleted must fall back to
    'none' in the reconfigure form default instead of an unselectable stale value."""
    set_weather(hass)
    set_cover(hass, "cover.bedroom")
    hass.config_entries.async_add_subentry(
        hub_entry,
        config_entries.ConfigSubentry(
            **cover_subentry_data("cover.bedroom", schedule_profile="ghost_profile")
        ),
    )
    cover_sub = next(iter(hub_entry.subentries.values()))
    result = await hass.config_entries.subentries.async_init(
        (hub_entry.entry_id, const.SUBENTRY_COVER),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": cover_sub.subentry_id},
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
    marker = next(k for k in result["data_schema"].schema if str(k) == const.CONF_SCHEDULE_PROFILE)
    assert marker.default() == const.PROFILE_NONE

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**COVER_INPUT, const.CONF_SCHEDULE_PROFILE: marker.default()}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    updated = hub_entry.subentries[cover_sub.subentry_id]
    assert updated.data[const.CONF_SCHEDULE_PROFILE] == const.PROFILE_NONE


async def test_profile_reconfigure_prefills_and_updates(hass: HomeAssistant, hub_entry) -> None:
    hass.config_entries.async_add_subentry(
        hub_entry, config_entries.ConfigSubentry(**profile_subentry_data("Night"))
    )
    sub = next(iter(hub_entry.subentries.values()))
    result = await hass.config_entries.subentries.async_init(
        (hub_entry.entry_id, const.SUBENTRY_PROFILE),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": sub.subentry_id},
    )
    assert result["step_id"] == "reconfigure"
    schema = result["data_schema"]
    suggested = {
        str(k): k.description.get("suggested_value")
        for k in schema.schema
        if getattr(k, "description", None)
    }
    assert suggested[const.CONF_NAME] == "Night"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            const.CONF_NAME: "Night 2",
            "rule_1": {
                const.CONF_RULE_ENABLED: True,
                const.CONF_RULE_ACTION: "closed",
                const.CONF_RULE_TIME_MODE: "sunset",
                const.CONF_RULE_OFFSET: 15,
            },
            "rule_2": {
                const.CONF_RULE_ENABLED: False,
                const.CONF_RULE_ACTION: "closed",
                const.CONF_RULE_TIME_MODE: "fixed",
                const.CONF_RULE_OFFSET: 0,
            },
            "rule_3": {
                const.CONF_RULE_ENABLED: False,
                const.CONF_RULE_ACTION: "closed",
                const.CONF_RULE_TIME_MODE: "fixed",
                const.CONF_RULE_OFFSET: 0,
            },
            "rule_4": {
                const.CONF_RULE_ENABLED: False,
                const.CONF_RULE_ACTION: "closed",
                const.CONF_RULE_TIME_MODE: "fixed",
                const.CONF_RULE_OFFSET: 0,
            },
        },
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    updated = hub_entry.subentries[sub.subentry_id]
    assert (
        updated.title == "Night 2"
        and updated.data[const.CONF_RULES][0][const.CONF_RULE_TIME_MODE] == "sunset"
    )
    assert const.CONF_QUIET_START not in updated.data  # cleared quiet hours are dropped
