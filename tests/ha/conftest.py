"""Home Assistant test helpers for the cover_automation integration."""

from __future__ import annotations

from typing import Any

import pytest
from custom_components.cover_automation import const
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

WEATHER = "weather.home"
SUN = "sun.sun"


def set_weather(
    hass: HomeAssistant,
    entity_id: str = WEATHER,
    *,
    daily: bool = True,
    condition: str = "sunny",
    temperature: float = 20.0,
    unit: str = "°C",
) -> None:
    hass.states.async_set(
        entity_id,
        condition,
        {
            "supported_features": 1 if daily else 2,
            "temperature": temperature,
            "temperature_unit": unit,
            "friendly_name": "Home",
        },
    )


def set_sun(hass: HomeAssistant, *, elevation: float = 30.0, azimuth: float = 180.0) -> None:
    hass.states.async_set(
        SUN,
        "above_horizon" if elevation > 0 else "below_horizon",
        {"elevation": elevation, "azimuth": azimuth},
    )


def set_cover(
    hass: HomeAssistant,
    entity_id: str,
    *,
    state: str = "open",
    position: int | None = 100,
    features: int = 3,
    name: str | None = None,
) -> None:
    attrs: dict[str, Any] = {"supported_features": features, "friendly_name": name or entity_id}
    if position is not None:
        attrs["current_position"] = position
    hass.states.async_set(entity_id, state, attrs)


def set_sensor(
    hass: HomeAssistant,
    entity_id: str,
    value: float | str,
    *,
    unit: str | None = None,
    device_class: str | None = None,
) -> None:
    attrs: dict[str, Any] = {}
    if unit:
        attrs["unit_of_measurement"] = unit
    if device_class:
        attrs["device_class"] = device_class
    hass.states.async_set(entity_id, str(value), attrs)


def hub_options(**overrides: Any) -> dict[str, Any]:
    opts: dict[str, Any] = {
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
    opts.update(overrides)
    return opts


def cover_subentry_data(entity_id: str = "cover.bedroom", **overrides: Any) -> ConfigSubentryData:
    data: dict[str, Any] = {
        const.CONF_COVER_ENTITY: entity_id,
        const.CONF_NAME: "Bedroom",
        const.CONF_AZIMUTH: 180,
        const.CONF_SCHEDULE_PROFILE: const.PROFILE_NONE,
        const.CONF_TEMPERATURE_UNIT: "°C",
    }
    data.update(overrides)
    return ConfigSubentryData(
        data=data,
        subentry_type=const.SUBENTRY_COVER,
        title=str(data[const.CONF_NAME]),
        unique_id=None,
    )


def profile_subentry_data(
    name: str = "Bedroom",
    rules: list[dict[str, Any]] | None = None,
    quiet: tuple[str, str] | None = ("22:00:00", "07:00:00"),
) -> ConfigSubentryData:
    data: dict[str, Any] = {
        const.CONF_NAME: name,
        const.CONF_RULES: rules
        or [
            {
                const.CONF_RULE_ACTION: "closed",
                const.CONF_RULE_TIME_MODE: "fixed",
                const.CONF_RULE_TIME: "21:30:00",
            }
        ],
    }
    if quiet:
        data[const.CONF_QUIET_START], data[const.CONF_QUIET_END] = quiet
    return ConfigSubentryData(
        data=data, subentry_type=const.SUBENTRY_PROFILE, title=name, unique_id=None
    )


@pytest.fixture
def hub_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A hub entry with weather + wind sensor, added to hass but not set up."""
    entry = MockConfigEntry(
        domain=const.DOMAIN,
        title="Cover Automation",
        data={const.CONF_WEATHER_ENTITY: WEATHER, const.CONF_WIND_SENSOR: "sensor.wind"},
        options=hub_options(),
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)
    return entry
