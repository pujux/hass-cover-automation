from __future__ import annotations

from custom_components.cover_automation import config_flow, const
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from tests.ha.conftest import WEATHER, hub_options, set_sensor, set_weather


async def test_user_flow_two_steps_creates_entry(hass: HomeAssistant) -> None:
    set_weather(hass)
    set_sensor(hass, "sensor.wind", 12, unit="km/h", device_class="wind_speed")
    result = await hass.config_entries.flow.async_init(
        const.DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {const.CONF_WEATHER_ENTITY: WEATHER, const.CONF_WIND_SENSOR: "sensor.wind"},
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "thresholds"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {const.CONF_HOT_HIGH: 26.0, const.CONF_SUNNY_ON_DELAY: 5}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.title == "Cover Automation"
    assert entry.data == {const.CONF_WEATHER_ENTITY: WEATHER, const.CONF_WIND_SENSOR: "sensor.wind"}
    assert (
        entry.options[const.CONF_HOT_HIGH] == 26.0 and entry.options[const.CONF_SUNNY_ON_DELAY] == 5
    )
    assert entry.options[const.CONF_HOT_LOW] == 13.0  # default filled in
    assert entry.options[const.CONF_TEMPERATURE_UNIT] == "°C"


async def test_user_flow_rejects_weather_without_daily_forecast(hass: HomeAssistant) -> None:
    set_weather(hass, daily=False)
    result = await hass.config_entries.flow.async_init(
        const.DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {const.CONF_WEATHER_ENTITY: WEATHER}
    )
    assert result["type"] is FlowResultType.FORM and result["errors"] == {
        const.CONF_WEATHER_ENTITY: "weather_no_daily"
    }


async def test_user_flow_rejects_missing_weather_entity(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        const.DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {const.CONF_WEATHER_ENTITY: "weather.nope"}
    )
    assert result["errors"] == {const.CONF_WEATHER_ENTITY: "entity_not_found"}


async def test_single_instance(hass: HomeAssistant, hub_entry) -> None:
    result = await hass.config_entries.flow.async_init(
        const.DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "single_instance_allowed"


async def test_options_flow_updates_thresholds(hass: HomeAssistant, hub_entry) -> None:
    set_weather(hass)
    result = await hass.config_entries.options.async_init(hub_entry.entry_id)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "init"
    posted = {k: v for k, v in hub_options().items() if k != const.CONF_TEMPERATURE_UNIT}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**posted, const.CONF_FROST_THRESHOLD: -1.0, const.CONF_HOT_LOW_ENABLED: False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert hub_entry.options[const.CONF_FROST_THRESHOLD] == -1.0
    assert hub_entry.options[const.CONF_HOT_LOW_ENABLED] is False
    assert set(hub_entry.options) == set(hub_options())


async def test_options_flow_keeps_stored_temperature_unit_after_unit_system_change(
    hass: HomeAssistant, hub_entry
) -> None:
    """Options are rendered and re-saved in the unit they were stored in, not the
    household's current unit system, so switching units never silently relabels a
    value without converting it."""
    set_weather(hass)
    hass.config.units = US_CUSTOMARY_SYSTEM
    result = await hass.config_entries.options.async_init(hub_entry.entry_id)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "init"
    posted = {k: v for k, v in hub_options().items() if k != const.CONF_TEMPERATURE_UNIT}
    result = await hass.config_entries.options.async_configure(result["flow_id"], posted)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert hub_entry.options[const.CONF_TEMPERATURE_UNIT] == "°C"


def test_thresholds_schema_converts_temperature_bounds_for_fahrenheit() -> None:
    """Temperature bounds and defaults are stated in °C in the code and must be
    converted to the household's unit before being handed to the NumberSelector."""
    schema = config_flow.thresholds_schema({}, "°F")
    fields = {str(key): (key, value) for key, value in schema.schema.items()}
    _, frost_selector = fields[const.CONF_FROST_THRESHOLD]
    assert frost_selector(32) == 32  # 0 °C == 32 °F; would raise if bounds stayed in °C

    hot_high_marker, _ = fields[const.CONF_HOT_HIGH]
    assert hot_high_marker.default() == 75.0  # 24 °C -> 75.2 °F -> rounds to 75.0


async def test_options_flow_clears_override_entities(hass: HomeAssistant, hub_entry) -> None:
    hass.config_entries.async_update_entry(
        hub_entry,
        options={**hub_entry.options, const.CONF_SUNNY_OVERRIDE_ENTITY: "binary_sensor.x"},
    )
    result = await hass.config_entries.options.async_init(hub_entry.entry_id)
    posted = {k: v for k, v in hub_options().items() if k != const.CONF_TEMPERATURE_UNIT}
    result = await hass.config_entries.options.async_configure(result["flow_id"], posted)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert const.CONF_SUNNY_OVERRIDE_ENTITY not in hub_entry.options


async def test_reconfigure_flow_changes_hub_entities(hass: HomeAssistant, hub_entry) -> None:
    set_weather(hass)
    set_weather(hass, "weather.other")
    result = await hub_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {const.CONF_WEATHER_ENTITY: "weather.other"}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    assert hub_entry.data[const.CONF_WEATHER_ENTITY] == "weather.other"
    assert const.CONF_WIND_SENSOR not in hub_entry.data or hub_entry.data[
        const.CONF_WIND_SENSOR
    ] in (None, "")
