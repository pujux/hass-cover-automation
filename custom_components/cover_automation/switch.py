"""Switch platform: hub simulation/verbose, per-cover enabled (spec §4)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
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
        [SimulationModeSwitch(entry, controller), VerboseLoggingSwitch(entry, controller)]
    )
    for subentry_id in entry.runtime_data.covers:
        entity = CoverEnabledSwitch(entry, controller, subentry_id)
        async_add_entities([entity], config_subentry_id=subentry_id)


class SimulationModeSwitch(HubEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol) -> None:
        super().__init__(entry, controller, "simulation_mode")

    @property
    def is_on(self) -> bool:
        return self.hub_view.simulation

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._controller.async_set_simulation(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._controller.async_set_simulation(False)


class VerboseLoggingSwitch(HubEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol) -> None:
        super().__init__(entry, controller, "verbose_logging")

    @property
    def is_on(self) -> bool:
        return self.hub_view.verbose

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._controller.async_set_verbose(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._controller.async_set_verbose(False)


class CoverEnabledSwitch(CoverEntityBase, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, entry: ConfigEntry, controller: ControllerProtocol, subentry_id: str
    ) -> None:
        super().__init__(entry, controller, subentry_id, "enabled")

    @property
    def is_on(self) -> bool:
        return self.view.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._controller.async_set_enabled(self.subentry_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._controller.async_set_enabled(self.subentry_id, False)
