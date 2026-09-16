"""Config, options, reconfigure and subentry flows (spec §3)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.components.cover import CoverEntityFeature
from homeassistant.components.weather.const import WeatherEntityFeature
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector
from homeassistant.util.unit_conversion import TemperatureConverter

from . import const
from .config_map import parse_time, quiet_from_data, rules_from_data
from .engine.model import ShadingRule, Target, WindAction
from .engine.schedule import Profile, TimeMode
from .engine.schedule import validate as validate_rules


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
        return {
            const.SUBENTRY_COVER: CoverSubentryFlow,
            const.SUBENTRY_PROFILE: ProfileSubentryFlow,
        }

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


# --- cover subentry -------------------------------------------------------------------

_SHADING_RULES = [r.value for r in ShadingRule]
_WIND_ACTIONS = [a.value for a in WindAction]


def _select(options: list[str], translation_key: str) -> selector.SelectSelector:
    return selector.SelectSelector(
        {
            "options": options,
            "mode": selector.SelectSelectorMode.DROPDOWN,
            "translation_key": translation_key,
        }
    )


def cover_schema(
    hass: HomeAssistant, entry: ConfigEntry, defaults: Mapping[str, Any], unit: str
) -> vol.Schema:
    d = defaults
    hub_has_wind = bool(entry.data.get(const.CONF_WIND_SENSOR))
    profiles = entry.get_subentries_of_type(const.SUBENTRY_PROFILE)
    profile_ids = {p.subentry_id for p in profiles}
    profile_options: list[selector.SelectOptionDict] = [
        {"value": const.PROFILE_NONE, "label": "—"},
        *({"value": p.subentry_id, "label": p.title} for p in profiles),
    ]

    def dflt(key: str, fallback: Any) -> Any:
        return d.get(key, fallback)

    fields: dict[Any, Any] = {
        vol.Required(
            const.CONF_COVER_ENTITY, default=dflt(const.CONF_COVER_ENTITY, vol.UNDEFINED)
        ): _entity("cover"),
        vol.Optional(
            const.CONF_NAME, description={"suggested_value": d.get(const.CONF_NAME)}
        ): selector.TextSelector(),
        vol.Required(const.CONF_AZIMUTH, default=dflt(const.CONF_AZIMUTH, 180)): _number(
            0, 359, 1, "°"
        ),
        vol.Required(
            const.CONF_TOLERANCE_LEFT,
            default=dflt(const.CONF_TOLERANCE_LEFT, const.DEFAULT_TOLERANCE_LEFT),
        ): _number(0, 180, 1, "°"),
        vol.Required(
            const.CONF_TOLERANCE_RIGHT,
            default=dflt(const.CONF_TOLERANCE_RIGHT, const.DEFAULT_TOLERANCE_RIGHT),
        ): _number(0, 180, 1, "°"),
        vol.Required(
            const.CONF_ELEVATION_MIN,
            default=dflt(const.CONF_ELEVATION_MIN, const.DEFAULT_ELEVATION_MIN),
        ): _number(0, 90, 1, "°"),
        vol.Required(
            const.CONF_ELEVATION_MAX,
            default=dflt(const.CONF_ELEVATION_MAX, const.DEFAULT_ELEVATION_MAX),
        ): _number(0, 90, 1, "°"),
        vol.Required(
            const.CONF_SHADING_RULE,
            default=dflt(const.CONF_SHADING_RULE, ShadingRule.FORECAST_WITH_ROOM.value),
        ): _select(_SHADING_RULES, "shading_rule"),
        vol.Optional(
            const.CONF_ROOM_SENSOR, description={"suggested_value": d.get(const.CONF_ROOM_SENSOR)}
        ): _entity("sensor", device_class="temperature"),
        vol.Required(
            const.CONF_COMFORT_FLOOR,
            default=dflt(
                const.CONF_COMFORT_FLOOR, _temperature_default(const.DEFAULT_COMFORT_FLOOR, unit)
            ),
        ): _temperature(-10, 40, unit),
        vol.Required(
            const.CONF_COMFORT_CEILING,
            default=dflt(
                const.CONF_COMFORT_CEILING,
                _temperature_default(const.DEFAULT_COMFORT_CEILING, unit),
            ),
        ): _temperature(-10, 40, unit),
        vol.Optional(
            const.CONF_DOOR_SENSOR, description={"suggested_value": d.get(const.CONF_DOOR_SENSOR)}
        ): _entity("binary_sensor"),
    }
    if hub_has_wind:
        wind_state = hass.states.get(str(entry.data[const.CONF_WIND_SENSOR]))
        wind_unit = str(wind_state.attributes.get("unit_of_measurement", "")) if wind_state else ""
        fields.update(
            {
                vol.Required(
                    const.CONF_WIND_ENABLED, default=dflt(const.CONF_WIND_ENABLED, False)
                ): selector.BooleanSelector(),
                vol.Required(
                    const.CONF_WIND_UPPER, default=dflt(const.CONF_WIND_UPPER, 60)
                ): _number(0, 300, 1, wind_unit or None),
                vol.Required(
                    const.CONF_WIND_LOWER, default=dflt(const.CONF_WIND_LOWER, 50)
                ): _number(0, 300, 1, wind_unit or None),
                vol.Required(
                    const.CONF_WIND_HOLD,
                    default=dflt(const.CONF_WIND_HOLD, const.DEFAULT_WIND_HOLD_MIN),
                ): _number(0, 240, 1, "min"),
                vol.Required(
                    const.CONF_WIND_ACTION,
                    default=dflt(const.CONF_WIND_ACTION, WindAction.OPEN.value),
                ): _select(_WIND_ACTIONS, "wind_action"),
            }
        )
    stored_profile = dflt(const.CONF_SCHEDULE_PROFILE, const.PROFILE_NONE)
    if stored_profile != const.PROFILE_NONE and stored_profile not in profile_ids:
        # The stored profile subentry was deleted; fall back to "none" so the selector
        # always has a valid, selectable default (spec §3).
        stored_profile = const.PROFILE_NONE
    fields.update(
        {
            vol.Required(
                const.CONF_SCHEDULE_PROFILE,
                default=stored_profile,
            ): selector.SelectSelector(
                {"options": profile_options, "mode": selector.SelectSelectorMode.DROPDOWN}
            ),
            vol.Required(
                const.CONF_MIN_MOVE_INTERVAL,
                default=dflt(const.CONF_MIN_MOVE_INTERVAL, const.DEFAULT_MIN_MOVE_INTERVAL_MIN),
            ): _number(0, 240, 1, "min"),
            vol.Required(
                const.CONF_CONFIRM_WINDOW,
                default=dflt(const.CONF_CONFIRM_WINDOW, const.DEFAULT_CONFIRM_WINDOW_S),
            ): _number(1, 900, 1, "s"),
        }
    )
    return vol.Schema(fields)


def validate_cover_input(
    hass: HomeAssistant,
    entry: ConfigEntry,
    data: Mapping[str, Any],
    editing_subentry_id: str | None,
) -> dict[str, str]:
    errors: dict[str, str] = {}
    cover_entity = str(data[const.CONF_COVER_ENTITY])
    for sub in entry.get_subentries_of_type(const.SUBENTRY_COVER):
        if (
            sub.subentry_id != editing_subentry_id
            and sub.data.get(const.CONF_COVER_ENTITY) == cover_entity
        ):
            errors[const.CONF_COVER_ENTITY] = "already_configured"
    state = hass.states.get(cover_entity)
    if (
        state is not None
        and state.state not in ("unavailable", "unknown")
        and const.CONF_COVER_ENTITY not in errors
    ):
        features = int(state.attributes.get("supported_features", 0) or 0)
        open_close = features & CoverEntityFeature.OPEN and features & CoverEntityFeature.CLOSE
        if not (open_close or features & CoverEntityFeature.SET_POSITION):
            errors[const.CONF_COVER_ENTITY] = "cover_unsupported"
    if float(data[const.CONF_COMFORT_FLOOR]) >= float(data[const.CONF_COMFORT_CEILING]):
        errors[const.CONF_COMFORT_FLOOR] = "floor_not_below_ceiling"
    if data.get(const.CONF_WIND_ENABLED) and float(data.get(const.CONF_WIND_LOWER, 0)) >= float(
        data.get(const.CONF_WIND_UPPER, 0)
    ):
        errors[const.CONF_WIND_LOWER] = "wind_lower_not_below_upper"
    if data[const.CONF_SHADING_RULE] == ShadingRule.ROOM_ONLY.value and not data.get(
        const.CONF_ROOM_SENSOR
    ):
        errors[const.CONF_ROOM_SENSOR] = "room_sensor_required"
    if int(data[const.CONF_CONFIRM_WINDOW]) < const.MIN_CONFIRM_WINDOW_S:
        errors[const.CONF_CONFIRM_WINDOW] = "confirm_window_too_short"
    return errors


class CoverSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure one cover."""

    async def _step(
        self, user_input: dict[str, Any] | None, *, reconfigure: bool
    ) -> SubentryFlowResult:
        entry = self._get_entry()
        current: ConfigSubentry | None = self._get_reconfigure_subentry() if reconfigure else None
        unit = str(
            current.data.get(const.CONF_TEMPERATURE_UNIT, self.hass.config.units.temperature_unit)
            if current is not None
            else self.hass.config.units.temperature_unit
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_cover_input(
                self.hass, entry, user_input, current.subentry_id if current else None
            )
            if not errors:
                data = _clean_optional_entities(
                    user_input, (const.CONF_ROOM_SENSOR, const.CONF_DOOR_SENSOR)
                )
                cover_state = self.hass.states.get(str(data[const.CONF_COVER_ENTITY]))
                friendly = cover_state.name if cover_state else str(data[const.CONF_COVER_ENTITY])
                title = str(data.get(const.CONF_NAME) or friendly)
                data[const.CONF_NAME] = title
                data[const.CONF_TEMPERATURE_UNIT] = unit
                wind_sensor = entry.data.get(const.CONF_WIND_SENSOR)
                wind_state = self.hass.states.get(str(wind_sensor)) if wind_sensor else None
                if wind_state is not None and wind_state.attributes.get("unit_of_measurement"):
                    data[const.CONF_WIND_UNIT] = str(wind_state.attributes["unit_of_measurement"])
                elif current is not None and current.data.get(const.CONF_WIND_UNIT):
                    data[const.CONF_WIND_UNIT] = current.data[const.CONF_WIND_UNIT]
                if current is not None:
                    return self.async_update_and_abort(entry, current, title=title, data=data)
                return self.async_create_entry(title=title, data=data)
        defaults: Mapping[str, Any] = user_input or (current.data if current else {})
        return self.async_show_form(
            step_id="reconfigure" if reconfigure else "user",
            data_schema=cover_schema(self.hass, entry, defaults, unit),
            errors=errors,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step(user_input, reconfigure=False)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._step(user_input, reconfigure=True)


# --- profile subentry -----------------------------------------------------------------

_RULE_KEYS = tuple(f"rule_{i}" for i in range(1, const.MAX_RULES + 1))
_TIME_MODES = [m.value for m in TimeMode]
_ACTIONS = [Target.CLOSED.value, Target.OPEN.value]


def _rule_section(defaults: Mapping[str, Any]) -> section:
    d = defaults
    return section(
        vol.Schema(
            {
                vol.Required(
                    const.CONF_RULE_ENABLED, default=d.get(const.CONF_RULE_ENABLED, False)
                ): selector.BooleanSelector(),
                vol.Required(
                    const.CONF_RULE_ACTION,
                    default=d.get(const.CONF_RULE_ACTION, Target.CLOSED.value),
                ): _select(_ACTIONS, "rule_action"),
                vol.Required(
                    const.CONF_RULE_TIME_MODE,
                    default=d.get(const.CONF_RULE_TIME_MODE, TimeMode.FIXED.value),
                ): _select(_TIME_MODES, "time_mode"),
                vol.Optional(
                    const.CONF_RULE_TIME,
                    description={"suggested_value": d.get(const.CONF_RULE_TIME)},
                ): selector.TimeSelector(),
                vol.Required(
                    const.CONF_RULE_OFFSET, default=d.get(const.CONF_RULE_OFFSET, 0)
                ): _number(-720, 720, 1, "min"),
                vol.Optional(
                    const.CONF_RULE_EARLIEST,
                    description={"suggested_value": d.get(const.CONF_RULE_EARLIEST)},
                ): selector.TimeSelector(),
                vol.Optional(
                    const.CONF_RULE_LATEST,
                    description={"suggested_value": d.get(const.CONF_RULE_LATEST)},
                ): selector.TimeSelector(),
            }
        ),
        {"collapsed": not bool(d.get(const.CONF_RULE_ENABLED, False))},
    )


def form_from_profile_data(data: Mapping[str, Any]) -> dict[str, Any]:
    """Stored profile data → form values (one section per rule slot)."""
    form: dict[str, Any] = {
        const.CONF_NAME: data.get(const.CONF_NAME),
        const.CONF_QUIET_START: data.get(const.CONF_QUIET_START),
        const.CONF_QUIET_END: data.get(const.CONF_QUIET_END),
    }
    rules = list(data.get(const.CONF_RULES, []))
    for index, key in enumerate(_RULE_KEYS):
        if index < len(rules):
            form[key] = {const.CONF_RULE_ENABLED: True, **rules[index]}
        else:
            form[key] = {const.CONF_RULE_ENABLED: False}
    return form


def profile_schema(form: Mapping[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required(
            const.CONF_NAME, description={"suggested_value": form.get(const.CONF_NAME)}
        ): selector.TextSelector(),
        vol.Optional(
            const.CONF_QUIET_START,
            description={"suggested_value": form.get(const.CONF_QUIET_START)},
        ): selector.TimeSelector(),
        vol.Optional(
            const.CONF_QUIET_END, description={"suggested_value": form.get(const.CONF_QUIET_END)}
        ): selector.TimeSelector(),
    }
    for key in _RULE_KEYS:
        fields[vol.Required(key)] = _rule_section(form.get(key) or {})
    return vol.Schema(fields)


def profile_data_from_form(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Form values → stored profile data: only enabled rule slots are kept, in slot order."""
    rules: list[dict[str, Any]] = []
    for key in _RULE_KEYS:
        raw = user_input.get(key) or {}
        if not raw.get(const.CONF_RULE_ENABLED):
            continue
        rules.append(
            {
                const.CONF_RULE_ACTION: raw[const.CONF_RULE_ACTION],
                const.CONF_RULE_TIME_MODE: raw[const.CONF_RULE_TIME_MODE],
                const.CONF_RULE_TIME: raw.get(const.CONF_RULE_TIME) or None,
                const.CONF_RULE_OFFSET: int(raw.get(const.CONF_RULE_OFFSET, 0) or 0),
                const.CONF_RULE_EARLIEST: raw.get(const.CONF_RULE_EARLIEST) or None,
                const.CONF_RULE_LATEST: raw.get(const.CONF_RULE_LATEST) or None,
            }
        )
    data: dict[str, Any] = {
        const.CONF_NAME: str(user_input[const.CONF_NAME]).strip(),
        const.CONF_RULES: rules,
    }
    start, end = user_input.get(const.CONF_QUIET_START), user_input.get(const.CONF_QUIET_END)
    if start or end:
        data[const.CONF_QUIET_START] = str(start) if start else None
        data[const.CONF_QUIET_END] = str(end) if end else None
    return data


def validate_profile(data: Mapping[str, Any]) -> list[str]:
    """Problems as human-readable strings (engine.schedule.validate plus time parsing)."""
    try:
        rules = rules_from_data(data)
        quiet = quiet_from_data(data)
    except ValueError as err:
        return [str(err)]
    problems = validate_rules(
        Profile(profile_id="draft", name=str(data[const.CONF_NAME]), rules=rules, quiet_hours=quiet)
    )
    start, end = (
        parse_time(data.get(const.CONF_QUIET_START)),
        parse_time(data.get(const.CONF_QUIET_END)),
    )
    if (start is None) != (end is None):
        problems.append("quiet hours need both a start and an end")
    return problems


class ProfileSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure one schedule profile."""

    async def _step(
        self, user_input: dict[str, Any] | None, *, reconfigure: bool
    ) -> SubentryFlowResult:
        entry = self._get_entry()
        current: ConfigSubentry | None = self._get_reconfigure_subentry() if reconfigure else None
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"problems": ""}
        if user_input is not None:
            data = profile_data_from_form(user_input)
            if not data[const.CONF_NAME]:
                errors[const.CONF_NAME] = "name_required"
            else:
                problems = validate_profile(data)
                if problems:
                    errors["base"] = "invalid_rules"
                    placeholders["problems"] = "; ".join(problems)
                else:
                    title = data[const.CONF_NAME]
                    if current is not None:
                        return self.async_update_and_abort(entry, current, title=title, data=data)
                    return self.async_create_entry(title=title, data=data)
        form = user_input or form_from_profile_data(current.data if current else {})
        return self.async_show_form(
            step_id="reconfigure" if reconfigure else "user",
            data_schema=profile_schema(form),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step(user_input, reconfigure=False)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._step(user_input, reconfigure=True)
