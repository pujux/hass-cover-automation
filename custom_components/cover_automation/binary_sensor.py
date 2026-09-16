"""Binary sensor platform: hub-level conditions and per-cover flags (spec §4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import ControllerProtocol, CoverEntityBase, HubEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    controller: ControllerProtocol = entry.runtime_data.controller
    async_add_entities(
        [
            HotDaySensor(entry, controller),
            SunnySensor(entry, controller),
            FrostActiveSensor(entry, controller),
            AnyWindProtectionSensor(entry, controller),
            ProblemSensor(entry, controller),
        ]
    )
    for subentry_id in entry.runtime_data.covers:
        async_add_entities(
            [
                ManualOverrideSensor(entry, controller, subentry_id),
                SunHitsSensor(entry, controller, subentry_id),
                WindProtectionActiveSensor(entry, controller, subentry_id),
            ],
            config_subentry_id=subentry_id,
        )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class HotDaySensor(HubEntity, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol) -> None:
        super().__init__(entry, controller, "hot_day")

    @property
    def is_on(self) -> bool | None:
        return self.hub_view.hot_day


class SunnySensor(HubEntity, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol) -> None:
        super().__init__(entry, controller, "sunny")

    @property
    def is_on(self) -> bool | None:
        return self.hub_view.sunny


class FrostActiveSensor(HubEntity, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.COLD

    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol) -> None:
        super().__init__(entry, controller, "frost_active")

    @property
    def is_on(self) -> bool | None:
        return self.hub_view.frost


class AnyWindProtectionSensor(HubEntity, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol) -> None:
        super().__init__(entry, controller, "any_wind_protection_active")

    @property
    def is_on(self) -> bool | None:
        return self.hub_view.any_wind_active


class ProblemSensor(HubEntity, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol) -> None:
        super().__init__(entry, controller, "problem")

    @property
    def is_on(self) -> bool | None:
        return self.hub_view.problem


class ManualOverrideSensor(CoverEntityBase, BinarySensorEntity):
    def __init__(
        self, entry: ConfigEntry, controller: ControllerProtocol, subentry_id: str
    ) -> None:
        super().__init__(entry, controller, subentry_id, "manual_override")

    @property
    def is_on(self) -> bool | None:
        return self.view.override_active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        v = self.view
        return {"since": _iso(v.override_since), "overridden_desired": v.overridden_desired}


class SunHitsSensor(CoverEntityBase, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, entry: ConfigEntry, controller: ControllerProtocol, subentry_id: str
    ) -> None:
        super().__init__(entry, controller, subentry_id, "sun_hits")

    @property
    def is_on(self) -> bool | None:
        return self.view.sun_hits


class WindProtectionActiveSensor(CoverEntityBase, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, entry: ConfigEntry, controller: ControllerProtocol, subentry_id: str
    ) -> None:
        super().__init__(entry, controller, subentry_id, "wind_protection_active")

    @property
    def is_on(self) -> bool | None:
        return self.view.wind_active
