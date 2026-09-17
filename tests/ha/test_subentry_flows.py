from __future__ import annotations

from custom_components.cover_automation import config_map, const
from custom_components.cover_automation.engine.schedule import RuleAction
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
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
    const.CONF_SCHEDULE_PROFILES: [],
    const.CONF_MIN_MOVE_INTERVAL: 10,
    const.CONF_CONFIRM_WINDOW: 120,
}


def register_profile_switch(hass: HomeAssistant, entry, subentry_id: str) -> str:
    """The enable switch the cover picker selects profiles by, as the switch platform adds it."""
    return (
        er.async_get(hass)
        .async_get_or_create(
            "switch",
            const.DOMAIN,
            f"{subentry_id}_{const.PROFILE_ENABLED_KEY}",
            config_entry=entry,
            config_subentry_id=subentry_id,
            suggested_object_id=f"profile_{subentry_id}",
        )
        .entity_id
    )


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
    assert sub.data[const.CONF_SCHEDULE_PROFILES] == []
    assert const.CONF_SCHEDULE_PROFILE not in sub.data


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
    switch_id = register_profile_switch(hass, hub_entry, prof_sub.subentry_id)
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
            const.CONF_SCHEDULE_PROFILES: [switch_id],
        },
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    updated = hub_entry.subentries[cover_sub.subentry_id]
    assert updated.title == "Bedroom East" and updated.data[const.CONF_SCHEDULE_PROFILES] == [
        prof_sub.subentry_id
    ]


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


async def test_profile_with_a_release_rule_is_stored(hass: HomeAssistant, hub_entry) -> None:
    """Decision 33: close at sunset+60, hand back to the automation at 08:00."""
    result = await start(hass, hub_entry, const.SUBENTRY_PROFILE)
    disabled = {
        const.CONF_RULE_ENABLED: False,
        const.CONF_RULE_ACTION: "closed",
        const.CONF_RULE_TIME_MODE: "fixed",
        const.CONF_RULE_OFFSET: 0,
    }
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            const.CONF_NAME: "Bedroom",
            "rule_1": {
                const.CONF_RULE_ENABLED: True,
                const.CONF_RULE_ACTION: "closed",
                const.CONF_RULE_TIME_MODE: "sunset",
                const.CONF_RULE_OFFSET: 60,
            },
            "rule_2": {
                const.CONF_RULE_ENABLED: True,
                const.CONF_RULE_ACTION: "release",
                const.CONF_RULE_TIME_MODE: "fixed",
                const.CONF_RULE_TIME: "08:00:00",
                const.CONF_RULE_OFFSET: 0,
            },
            "rule_3": disabled,
            "rule_4": disabled,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    sub = next(iter(hub_entry.subentries.values()))
    assert sub.data[const.CONF_RULES][1][const.CONF_RULE_ACTION] == "release"
    assert (
        config_map.profile(
            config_entries.ConfigSubentry(
                data=sub.data,
                subentry_type=const.SUBENTRY_PROFILE,
                title=sub.title,
                unique_id=None,
            )
        )
        .rules[1]
        .action
        is RuleAction.RELEASE
    )


async def test_profile_with_a_release_rule_but_no_close_rule_is_rejected(
    hass: HomeAssistant, hub_entry
) -> None:
    result = await start(hass, hub_entry, const.SUBENTRY_PROFILE)
    disabled = {
        const.CONF_RULE_ENABLED: False,
        const.CONF_RULE_ACTION: "closed",
        const.CONF_RULE_TIME_MODE: "fixed",
        const.CONF_RULE_OFFSET: 0,
    }
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            const.CONF_NAME: "Lonely",
            "rule_1": {
                const.CONF_RULE_ENABLED: True,
                const.CONF_RULE_ACTION: "release",
                const.CONF_RULE_TIME_MODE: "fixed",
                const.CONF_RULE_TIME: "08:00:00",
                const.CONF_RULE_OFFSET: 0,
            },
            "rule_2": disabled,
            "rule_3": disabled,
            "rule_4": disabled,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_rules"}
    assert (
        "rule 1 (release) has no close rule in this profile to release"
        in result["description_placeholders"]["problems"]
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


def _suggested(result, key: str):
    marker = next(k for k in result["data_schema"].schema if str(k) == key)
    return marker.description["suggested_value"]


async def test_cover_reconfigure_drops_a_deleted_profile_from_the_picker(
    hass: HomeAssistant, hub_entry
) -> None:
    """A cover referencing a schedule profile that was later deleted must not render an
    unselectable stale value; the reference is simply dropped."""
    set_weather(hass)
    set_cover(hass, "cover.bedroom")
    hass.config_entries.async_add_subentry(
        hub_entry,
        config_entries.ConfigSubentry(
            **cover_subentry_data("cover.bedroom", schedule_profiles=["ghost_profile"])
        ),
    )
    cover_sub = next(iter(hub_entry.subentries.values()))
    result = await hass.config_entries.subentries.async_init(
        (hub_entry.entry_id, const.SUBENTRY_COVER),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": cover_sub.subentry_id},
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
    assert _suggested(result, const.CONF_SCHEDULE_PROFILES) == []

    result = await hass.config_entries.subentries.async_configure(result["flow_id"], COVER_INPUT)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    updated = hub_entry.subentries[cover_sub.subentry_id]
    assert updated.data[const.CONF_SCHEDULE_PROFILES] == []


async def test_cover_stores_two_profiles_in_picker_order(hass: HomeAssistant, hub_entry) -> None:
    """The picker is ordered: what the user dragged into first place is stored first."""
    set_weather(hass)
    set_cover(hass, "cover.bedroom")
    for name in ("Night", "Vacation"):
        hass.config_entries.async_add_subentry(
            hub_entry, config_entries.ConfigSubentry(**profile_subentry_data(name))
        )
    night, vacation = (
        s for s in hub_entry.subentries.values() if s.subentry_type == const.SUBENTRY_PROFILE
    )
    night_switch = register_profile_switch(hass, hub_entry, night.subentry_id)
    vacation_switch = register_profile_switch(hass, hub_entry, vacation.subentry_id)

    result = await start(hass, hub_entry, const.SUBENTRY_COVER)
    schema_key = next(
        k for k in result["data_schema"].schema if str(k) == const.CONF_SCHEDULE_PROFILES
    )
    config = result["data_schema"].schema[schema_key].config
    assert config["multiple"] is True and config["reorder"] is True
    assert set(config["include_entities"]) == {night_switch, vacation_switch}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {**COVER_INPUT, const.CONF_SCHEDULE_PROFILES: [vacation_switch, night_switch]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    cover_sub = next(
        s for s in hub_entry.subentries.values() if s.subentry_type == const.SUBENTRY_COVER
    )
    assert cover_sub.data[const.CONF_SCHEDULE_PROFILES] == [
        vacation.subentry_id,
        night.subentry_id,
    ]
    cfg, _bind = config_map.cover_config(cover_sub, config_map.hub_config(hub_entry))
    assert cfg.profile_ids == (vacation.subentry_id, night.subentry_id)


async def test_reconfiguring_a_legacy_cover_writes_the_new_key(
    hass: HomeAssistant, hub_entry
) -> None:
    """A pre-0.5 cover renders its single profile in the picker and is rewritten as a list."""
    set_weather(hass)
    set_cover(hass, "cover.bedroom")
    hass.config_entries.async_add_subentry(
        hub_entry, config_entries.ConfigSubentry(**profile_subentry_data("Night"))
    )
    night = next(
        s for s in hub_entry.subentries.values() if s.subentry_type == const.SUBENTRY_PROFILE
    )
    night_switch = register_profile_switch(hass, hub_entry, night.subentry_id)
    hass.config_entries.async_add_subentry(
        hub_entry,
        config_entries.ConfigSubentry(
            **cover_subentry_data("cover.bedroom", schedule_profile=night.subentry_id)
        ),
    )
    cover_sub = next(
        s for s in hub_entry.subentries.values() if s.subentry_type == const.SUBENTRY_COVER
    )
    # read backwards compatibly before anything is rewritten
    legacy_cfg, _ = config_map.cover_config(cover_sub, config_map.hub_config(hub_entry))
    assert legacy_cfg.profile_ids == (night.subentry_id,)

    result = await hass.config_entries.subentries.async_init(
        (hub_entry.entry_id, const.SUBENTRY_COVER),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": cover_sub.subentry_id},
    )
    assert _suggested(result, const.CONF_SCHEDULE_PROFILES) == [night_switch]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**COVER_INPUT, const.CONF_SCHEDULE_PROFILES: [night_switch]}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    updated = hub_entry.subentries[cover_sub.subentry_id]
    assert updated.data[const.CONF_SCHEDULE_PROFILES] == [night.subentry_id]
    assert const.CONF_SCHEDULE_PROFILE not in updated.data


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


async def test_profile_reconfigure_prefills_rules_and_quiet_hours(
    hass: HomeAssistant, hub_entry
) -> None:
    hass.config_entries.async_add_subentry(
        hub_entry, config_entries.ConfigSubentry(**profile_subentry_data("Night"))
    )
    sub = next(iter(hub_entry.subentries.values()))
    result = await hass.config_entries.subentries.async_init(
        (hub_entry.entry_id, const.SUBENTRY_PROFILE),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": sub.subentry_id},
    )
    schema = result["data_schema"].schema
    suggested = {
        str(k): k.description.get("suggested_value")
        for k in schema
        if getattr(k, "description", None)
    }
    assert (
        suggested[const.CONF_QUIET_START] == "22:00:00"
        and suggested[const.CONF_QUIET_END] == "07:00:00"
    )
    rule_1 = next(v for k, v in schema.items() if str(k) == "rule_1")
    inner = {str(k): k for k in rule_1.schema.schema}
    assert inner[const.CONF_RULE_ENABLED].default() is True
    assert inner[const.CONF_RULE_TIME].description["suggested_value"] == "21:30:00"
    rule_2 = next(v for k, v in schema.items() if str(k) == "rule_2")
    assert {str(k): k for k in rule_2.schema.schema}[const.CONF_RULE_ENABLED].default() is False


async def test_cover_elevation_min_must_be_below_max(hass: HomeAssistant, hub_entry) -> None:
    set_weather(hass)
    set_cover(hass, "cover.bedroom")
    result = await start(hass, hub_entry, const.SUBENTRY_COVER)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {**COVER_INPUT, const.CONF_ELEVATION_MIN: 50, const.CONF_ELEVATION_MAX: 40},
    )
    assert result["errors"] == {const.CONF_ELEVATION_MIN: "elevation_min_not_below_max"}


async def test_validation_error_keeps_the_picked_profiles(hass: HomeAssistant, hub_entry) -> None:
    """Re-rendering the form after an error must not silently drop the picker's selection."""
    set_weather(hass)
    set_cover(hass, "cover.bedroom")
    hass.config_entries.async_add_subentry(
        hub_entry, config_entries.ConfigSubentry(**profile_subentry_data("Night"))
    )
    night = next(iter(hub_entry.subentries.values()))
    night_switch = register_profile_switch(hass, hub_entry, night.subentry_id)
    result = await start(hass, hub_entry, const.SUBENTRY_COVER)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            **COVER_INPUT,
            const.CONF_COMFORT_FLOOR: 26,  # floor above ceiling: one error, form comes back
            const.CONF_SCHEDULE_PROFILES: [night_switch],
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {const.CONF_COMFORT_FLOOR: "floor_not_below_ceiling"}
    assert _suggested(result, const.CONF_SCHEDULE_PROFILES) == [night_switch]


async def test_reconfigure_keeps_a_profile_the_picker_cannot_offer(
    hass: HomeAssistant, hub_entry
) -> None:
    """F3: a profile with no switch entity (misconfigured, so skipped at setup) cannot appear
    in the picker; saving the form must not silently drop the reference."""
    set_weather(hass)
    set_cover(hass, "cover.bedroom")
    for name in ("Broken", "Night"):
        hass.config_entries.async_add_subentry(
            hub_entry, config_entries.ConfigSubentry(**profile_subentry_data(name))
        )
    broken, night = (
        s for s in hub_entry.subentries.values() if s.subentry_type == const.SUBENTRY_PROFILE
    )
    night_switch = register_profile_switch(hass, hub_entry, night.subentry_id)  # Broken has none
    hass.config_entries.async_add_subentry(
        hub_entry,
        config_entries.ConfigSubentry(
            **cover_subentry_data(
                "cover.bedroom",
                schedule_profiles=[broken.subentry_id, night.subentry_id],
            )
        ),
    )
    cover_sub = next(
        s for s in hub_entry.subentries.values() if s.subentry_type == const.SUBENTRY_COVER
    )
    result = await hass.config_entries.subentries.async_init(
        (hub_entry.entry_id, const.SUBENTRY_COVER),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": cover_sub.subentry_id},
    )
    # only the resolvable profile is offered, at its own position
    assert _suggested(result, const.CONF_SCHEDULE_PROFILES) == [night_switch]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**COVER_INPUT, const.CONF_SCHEDULE_PROFILES: [night_switch]}
    )
    assert result["type"] is FlowResultType.ABORT
    updated = hub_entry.subentries[cover_sub.subentry_id]
    assert updated.data[const.CONF_SCHEDULE_PROFILES] == [
        broken.subentry_id,
        night.subentry_id,
    ]


async def test_reconfigure_still_drops_a_profile_whose_subentry_is_gone(
    hass: HomeAssistant, hub_entry
) -> None:
    """The F3 rescue is scoped to profiles that still exist: a deleted one stays dropped."""
    set_weather(hass)
    set_cover(hass, "cover.bedroom")
    hass.config_entries.async_add_subentry(
        hub_entry,
        config_entries.ConfigSubentry(
            **cover_subentry_data("cover.bedroom", schedule_profiles=["ghost_profile"])
        ),
    )
    cover_sub = next(iter(hub_entry.subentries.values()))
    result = await hass.config_entries.subentries.async_init(
        (hub_entry.entry_id, const.SUBENTRY_COVER),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": cover_sub.subentry_id},
    )
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], COVER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert hub_entry.subentries[cover_sub.subentry_id].data[const.CONF_SCHEDULE_PROFILES] == []
