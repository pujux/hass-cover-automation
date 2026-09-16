"""Button platform: hub evaluate-now, per-cover reset-override (spec §4)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import ControllerProtocol, CoverEntityBase, HubEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    controller: ControllerProtocol = entry.runtime_data.controller
    async_add_entities([EvaluateNowButton(entry, controller)])
    for subentry_id in entry.runtime_data.covers:
        entity = ResetOverrideButton(entry, controller, subentry_id)
        async_add_entities([entity], config_subentry_id=subentry_id)


class EvaluateNowButton(HubEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol) -> None:
        super().__init__(entry, controller, "evaluate_now")

    async def async_press(self) -> None:
        await self._controller.async_evaluate_now()


class ResetOverrideButton(CoverEntityBase, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, entry: ConfigEntry, controller: ControllerProtocol, subentry_id: str
    ) -> None:
        super().__init__(entry, controller, subentry_id, "reset_override")

    async def async_press(self) -> None:
        await self._controller.async_reset_override(self.subentry_id)
