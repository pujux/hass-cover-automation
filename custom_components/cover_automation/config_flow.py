"""Config, options, reconfigure and subentry flows (spec §3)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.components.weather.const import WeatherEntityFeature
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.util.unit_conversion import TemperatureConverter

from . import const


def _number(
    minimum: float, maximum: float, step: float, unit: str | None = None
) -> selector.NumberSelector:
    config: selector.NumberSelectorConfig = {
        "min": minimum,
        "max": maximum,
        "step": step,
        "mode": selector.NumberSelectorMode.BOX,
    }
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


def _entity(domain: str, *, device_class: str | None = None) -> selector.EntitySelector:
    config: selector.EntitySelectorConfig = {"domain": domain}
    if device_class:
        config["device_class"] = device_class
    return selector.EntitySelector(config)


def _temperature_default(value_c: float, unit: str) -> float:
    """Convert a °C value to `unit`, rounded to the nearest 0.5.

    Used for schema bounds and for the built-in DEFAULT_* fallbacks, which are always
    expressed in °C. A stored option value is already in its recorded unit and must
    never go through this conversion.
    """
    if unit == UnitOfTemperature.CELSIUS:
        return value_c
    converted = TemperatureConverter.convert(value_c, UnitOfTemperature.CELSIUS, unit)
    return round(converted * 2) / 2


def _temperature(min_c: float, max_c: float, unit: str) -> selector.NumberSelector:
    """A temperature NumberSelector; bounds are given in °C and converted to `unit`."""
    return _number(_temperature_default(min_c, unit), _temperature_default(max_c, unit), 0.5, unit)


def hub_data_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                const.CONF_WEATHER_ENTITY, default=d.get(const.CONF_WEATHER_ENTITY, vol.UNDEFINED)
            ): _entity("weather"),
            vol.Optional(
                const.CONF_WIND_SENSOR,
                description={"suggested_value": d.get(const.CONF_WIND_SENSOR)},
            ): _entity("sensor", device_class="wind_speed"),
            vol.Optional(
                const.CONF_OUTDOOR_TEMPERATURE_SENSOR,
                description={"suggested_value": d.get(const.CONF_OUTDOOR_TEMPERATURE_SENSOR)},
            ): _entity("sensor", device_class="temperature"),
        }
    )


def thresholds_schema(defaults: Mapping[str, Any], temperature_unit: str) -> vol.Schema:
    d = defaults

    def dflt(key: str, fallback: Any) -> Any:
        return d.get(key, fallback)

    return vol.Schema(
        {
            vol.Required(
                const.CONF_FROST_THRESHOLD,
                default=dflt(
                    const.CONF_FROST_THRESHOLD,
                    _temperature_default(const.DEFAULT_FROST_THRESHOLD, temperature_unit),
                ),
            ): _temperature(-30, 30, temperature_unit),
            vol.Required(
                const.CONF_HOT_HIGH,
                default=dflt(
                    const.CONF_HOT_HIGH,
                    _temperature_default(const.DEFAULT_HOT_HIGH, temperature_unit),
                ),
            ): _temperature(-30, 60, temperature_unit),
            vol.Required(
                const.CONF_HOT_LOW_ENABLED,
                default=dflt(const.CONF_HOT_LOW_ENABLED, const.DEFAULT_HOT_LOW_ENABLED),
            ): selector.BooleanSelector(),
            vol.Required(
                const.CONF_HOT_LOW,
                default=dflt(
                    const.CONF_HOT_LOW,
                    _temperature_default(const.DEFAULT_HOT_LOW, temperature_unit),
                ),
            ): _temperature(-30, 60, temperature_unit),
            vol.Required(
                const.CONF_SUNNY_CONDITIONS,
                default=dflt(const.CONF_SUNNY_CONDITIONS, const.DEFAULT_SUNNY_CONDITIONS),
            ): selector.SelectSelector(
                {
                    "options": const.WEATHER_CONDITIONS,
                    "multiple": True,
                    "mode": selector.SelectSelectorMode.LIST,
                    "translation_key": "weather_condition",
                }
            ),
            vol.Required(
                const.CONF_SUNNY_ON_DELAY,
                default=dflt(const.CONF_SUNNY_ON_DELAY, const.DEFAULT_SUNNY_ON_DELAY_MIN),
            ): _number(0, 120, 1, "min"),
            vol.Required(
                const.CONF_SUNNY_OFF_DELAY,
                default=dflt(const.CONF_SUNNY_OFF_DELAY, const.DEFAULT_SUNNY_OFF_DELAY_MIN),
            ): _number(0, 240, 1, "min"),
            vol.Required(
                const.CONF_WEATHER_GRACE,
                default=dflt(const.CONF_WEATHER_GRACE, const.DEFAULT_WEATHER_GRACE_MIN),
            ): _number(1, 720, 1, "min"),
            vol.Required(
                const.CONF_SUN_RELEASE_MARGIN,
                default=dflt(const.CONF_SUN_RELEASE_MARGIN, const.DEFAULT_SUN_RELEASE_MARGIN),
            ): _number(0, 15, 0.5, "°"),
            vol.Required(
                const.CONF_TOLERANCE, default=dflt(const.CONF_TOLERANCE, const.DEFAULT_TOLERANCE)
            ): _number(0, 30, 1, "%"),
            vol.Required(
                const.CONF_OVERRIDE_DWELL,
                default=dflt(const.CONF_OVERRIDE_DWELL, const.DEFAULT_OVERRIDE_DWELL_MIN),
            ): _number(1, 720, 1, "min"),
            vol.Optional(
                const.CONF_SUNNY_OVERRIDE_ENTITY,
                description={"suggested_value": d.get(const.CONF_SUNNY_OVERRIDE_ENTITY)},
            ): _entity("binary_sensor"),
            vol.Optional(
                const.CONF_HOT_OVERRIDE_ENTITY,
                description={"suggested_value": d.get(const.CONF_HOT_OVERRIDE_ENTITY)},
            ): _entity("binary_sensor"),
        }
    )


_HUB_OPTION_KEYS: tuple[str, ...] = tuple(
    str(key) for key in thresholds_schema({}, UnitOfTemperature.CELSIUS).schema
)


def validate_weather(hass: HomeAssistant, entity_id: str) -> str | None:
    """Return an error key, or None when the weather entity is usable."""
    state = hass.states.get(entity_id)
    if state is None:
        return "entity_not_found"
    features = int(state.attributes.get("supported_features", 0) or 0)
    if not features & WeatherEntityFeature.FORECAST_DAILY:
        return "weather_no_daily"
    return None


def _clean_optional_entities(user_input: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Drop optional entity keys the user cleared so they read as 'not configured'."""
    cleaned = dict(user_input)
    for key in keys:
        if cleaned.get(key) in (None, ""):
            cleaned.pop(key, None)
    return cleaned


def _complete_options(user_input: Mapping[str, Any], unit: str) -> dict[str, Any]:
    """Fill defaults for every hub option and record the unit thresholds were entered in.

    `unit` must be the unit the caller rendered/interpreted the thresholds form in (the
    current unit system for the config flow, the entry's already-stored unit for the
    options flow) so values are never silently relabelled without conversion.
    """
    filled = {
        const.CONF_FROST_THRESHOLD: const.DEFAULT_FROST_THRESHOLD,
        const.CONF_SUNNY_CONDITIONS: list(const.DEFAULT_SUNNY_CONDITIONS),
        const.CONF_SUNNY_ON_DELAY: const.DEFAULT_SUNNY_ON_DELAY_MIN,
        const.CONF_SUNNY_OFF_DELAY: const.DEFAULT_SUNNY_OFF_DELAY_MIN,
        const.CONF_WEATHER_GRACE: const.DEFAULT_WEATHER_GRACE_MIN,
        const.CONF_HOT_HIGH: const.DEFAULT_HOT_HIGH,
        const.CONF_HOT_LOW: const.DEFAULT_HOT_LOW,
        const.CONF_HOT_LOW_ENABLED: const.DEFAULT_HOT_LOW_ENABLED,
        const.CONF_SUN_RELEASE_MARGIN: const.DEFAULT_SUN_RELEASE_MARGIN,
        const.CONF_TOLERANCE: const.DEFAULT_TOLERANCE,
        const.CONF_OVERRIDE_DWELL: const.DEFAULT_OVERRIDE_DWELL_MIN,
    }
    filled.update({k: v for k, v in user_input.items() if k in _HUB_OPTION_KEYS})
    filled = _clean_optional_entities(
        filled, (const.CONF_SUNNY_OVERRIDE_ENTITY, const.CONF_HOT_OVERRIDE_ENTITY)
    )
    filled[const.CONF_TEMPERATURE_UNIT] = unit
    return filled


class CoverAutomationConfigFlow(ConfigFlow, domain=const.DOMAIN):
    """Hub config flow: entities, then thresholds."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._hub_data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CoverAutomationOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {}  # filled in Task 5

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors: dict[str, str] = {}
        if user_input is not None:
            error = validate_weather(self.hass, user_input[const.CONF_WEATHER_ENTITY])
            if error:
                errors[const.CONF_WEATHER_ENTITY] = error
            else:
                self._hub_data = _clean_optional_entities(
                    user_input, (const.CONF_WIND_SENSOR, const.CONF_OUTDOOR_TEMPERATURE_SENSOR)
                )
                return await self.async_step_thresholds()
        return self.async_show_form(
            step_id="user", data_schema=hub_data_schema(user_input), errors=errors
        )

    async def async_step_thresholds(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        unit = str(self.hass.config.units.temperature_unit)
        if user_input is not None:
            return self.async_create_entry(
                title="Cover Automation",
                data=self._hub_data,
                options=_complete_options(user_input, unit),
            )
        return self.async_show_form(step_id="thresholds", data_schema=thresholds_schema({}, unit))

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            error = validate_weather(self.hass, user_input[const.CONF_WEATHER_ENTITY])
            if error:
                errors[const.CONF_WEATHER_ENTITY] = error
            else:
                data = _clean_optional_entities(
                    user_input, (const.CONF_WIND_SENSOR, const.CONF_OUTDOOR_TEMPERATURE_SENSOR)
                )
                return self.async_update_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=hub_data_schema(user_input or entry.data),
            errors=errors,
        )


class CoverAutomationOptionsFlow(OptionsFlow):
    """Thresholds and behaviour; a plain OptionsFlow because the entry has an update listener."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        unit = str(
            self.config_entry.options.get(
                const.CONF_TEMPERATURE_UNIT, self.hass.config.units.temperature_unit
            )
        )
        if user_input is not None:
            return self.async_create_entry(data=_complete_options(user_input, unit))
        return self.async_show_form(
            step_id="init", data_schema=thresholds_schema(self.config_entry.options, unit)
        )
