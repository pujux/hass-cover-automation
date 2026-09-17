"""End-to-end tests of the seam between the config/subentry flows and config_map (F5).

Each test runs a real flow to CREATE_ENTRY, then feeds the resulting entry/subentry
straight into `config_map` and asserts the engine-facing values it produces, so a
schema default drifting out of sync with its `config_map` counterpart is caught here.
"""

from __future__ import annotations

from datetime import time

from custom_components.cover_automation import config_map, const
from custom_components.cover_automation.engine.model import ShadingRule, WindAction
from custom_components.cover_automation.engine.schedule import QuietHours, RuleAction
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from tests.ha.conftest import WEATHER, set_sensor, set_weather


async def test_cover_subentry_defaults_map_to_engine_values(hass: HomeAssistant, hub_entry) -> None:
    set_weather(hass)
    set_sensor(hass, "sensor.wind", 5, unit="km/h", device_class="wind_speed")
    result = await hass.config_entries.subentries.async_init(
        (hub_entry.entry_id, const.SUBENTRY_COVER),
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {const.CONF_COVER_ENTITY: "cover.bedroom"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    subentry = next(iter(hub_entry.subentries.values()))
    cfg, bind = config_map.cover_config(subentry, config_map.hub_config(hub_entry))

    assert cfg.cover_id == subentry.subentry_id
    assert cfg.wind_hold_s == 900
    assert cfg.min_move_interval_s == 600
    assert cfg.confirm_window_s == 120
    assert cfg.profile_ids == ()
    assert cfg.comfort_floor == 21.0
    assert cfg.comfort_ceiling == 25.0
    assert cfg.shading_rule is ShadingRule.FORECAST_WITH_ROOM
    assert cfg.wind_action is WindAction.OPEN
    assert bind.temperature_unit == "°C"
    assert bind.wind_unit == "km/h"


async def test_profile_subentry_defaults_map_to_engine_values(
    hass: HomeAssistant, hub_entry
) -> None:
    result = await hass.config_entries.subentries.async_init(
        (hub_entry.entry_id, const.SUBENTRY_PROFILE),
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            const.CONF_NAME: "Evening",
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
    assert result["type"] is FlowResultType.CREATE_ENTRY

    subentry = next(iter(hub_entry.subentries.values()))
    prof = config_map.profile(subentry)

    assert prof.rules[0].action is RuleAction.CLOSED
    assert prof.rules[0].time == time(21, 30)
    assert prof.quiet_hours == QuietHours(time(22, 0), time(7, 0))


async def test_hub_flow_defaults_map_to_engine_values(hass: HomeAssistant) -> None:
    set_weather(hass)
    result = await hass.config_entries.flow.async_init(
        const.DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {const.CONF_WEATHER_ENTITY: WEATHER}
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "thresholds"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY

    entry = result["result"]
    hub = config_map.hub_config(entry)

    assert hub.sunny_on_delay_s == 600
    assert hub.weather_grace_s == 1800
    assert hub.override_dwell_s == 1800
    assert hub.hot_low == 13.0
    assert hub.temperature_unit == "°C"
