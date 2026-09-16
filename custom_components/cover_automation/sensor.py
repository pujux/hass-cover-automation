"""Sensor platform: forecast values, next scheduled event, per-cover status (spec §4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .engine.model import Status
from .entity import ControllerProtocol, CoverEntityBase, HubEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    controller: ControllerProtocol = entry.runtime_data.controller
    async_add_entities(
        [
            ForecastMaxSensor(entry, controller),
            ForecastMinSensor(entry, controller),
            NextScheduledEventSensor(entry, controller),
        ]
    )
    for subentry_id in entry.runtime_data.covers:
        async_add_entities(
            [CoverStatusSensor(entry, controller, subentry_id)], config_subentry_id=subentry_id
        )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class ForecastMaxSensor(HubEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1

    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol) -> None:
        super().__init__(entry, controller, "forecast_max_today")

    @property
    def native_value(self) -> float | None:
        return self.hub_view.forecast_max_c


class ForecastMinSensor(HubEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1

    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol) -> None:
        super().__init__(entry, controller, "forecast_min_today")

    @property
    def native_value(self) -> float | None:
        return self.hub_view.forecast_min_c


class NextScheduledEventSensor(HubEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol) -> None:
        super().__init__(entry, controller, "next_scheduled_event")

    @property
    def native_value(self) -> datetime | None:
        return self.hub_view.next_event_at

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        view = self.hub_view
        return {
            "profile": view.next_event_profile,
            "action": view.next_event_action,
            "covers": list(view.next_event_covers),
        }


class CoverStatusSensor(CoverEntityBase, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options: ClassVar[list[str]] = [s.value for s in Status]
    _unrecorded_attributes = frozenset(
        {
            "winning_layer",
            "sun_hits",
            "sunny",
            "hot_day",
            "room_state",
            "wind_state",
            "active_rule",
            "next_planned_action",
            "next_planned_at",
            "last_engine_move",
            "owner",
            "degraded",
        }
    )

    def __init__(
        self, entry: ConfigEntry, controller: ControllerProtocol, subentry_id: str
    ) -> None:
        super().__init__(entry, controller, subentry_id, "status")

    @property
    def native_value(self) -> str:
        return self.view.status.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        v = self.view
        return {
            "desired_state": v.desired_state,
            "actual_state": v.actual_state,
            "winning_layer": v.winning_layer,
            "reason": v.reason,
            "sun_hits": v.sun_hits,
            "sunny": v.sunny,
            "hot_day": v.hot_day,
            "room_state": v.room_state,
            "wind_state": v.wind_state,
            "active_rule": v.active_rule,
            "next_planned_action": v.next_planned_action,
            "next_planned_at": _iso(v.next_planned_at),
            "last_engine_move": _iso(v.last_engine_move),
            "owner": v.owner,
            "degraded": v.degraded,
        }
