"""Select platform: hub shading/reopening modes, per-cover mode (spec §4)."""

from __future__ import annotations

from typing import ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .engine.model import Mode, ReopeningMode, ShadingMode
from .entity import ControllerProtocol, CoverEntityBase, HubEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    controller: ControllerProtocol = entry.runtime_data.controller
    async_add_entities(
        [ShadingModeSelect(entry, controller), ReopeningModeSelect(entry, controller)]
    )
    for subentry_id in entry.runtime_data.covers:
        entity = CoverModeSelect(entry, controller, subentry_id)
        async_add_entities([entity], config_subentry_id=subentry_id)


class ShadingModeSelect(HubEntity, SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options: ClassVar[list[str]] = [m.value for m in ShadingMode]

    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol) -> None:
        super().__init__(entry, controller, "shading_mode")

    @property
    def current_option(self) -> str:
        return self.hub_view.shading_mode.value

    async def async_select_option(self, option: str) -> None:
        await self._controller.async_set_shading_mode(ShadingMode(option))


class ReopeningModeSelect(HubEntity, SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options: ClassVar[list[str]] = [m.value for m in ReopeningMode]

    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol) -> None:
        super().__init__(entry, controller, "reopening_mode")

    @property
    def current_option(self) -> str:
        return self.hub_view.reopening_mode.value

    async def async_select_option(self, option: str) -> None:
        await self._controller.async_set_reopening_mode(ReopeningMode(option))


class CoverModeSelect(CoverEntityBase, SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options: ClassVar[list[str]] = [m.value for m in Mode]

    def __init__(
        self, entry: ConfigEntry, controller: ControllerProtocol, subentry_id: str
    ) -> None:
        super().__init__(entry, controller, subentry_id, "mode")

    @property
    def current_option(self) -> str:
        return self.view.mode.value

    async def async_select_option(self, option: str) -> None:
        await self._controller.async_set_mode(self.subentry_id, Mode(option))
