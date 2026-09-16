"""Entity bases: views over controller state, written through the controller (decision 17)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from . import const
from .engine.model import Mode, ReopeningMode, ShadingMode
from .views import CoverView, HubView, signal_update


class ControllerProtocol(Protocol):
    hub_view: HubView
    cover_views: Mapping[str, CoverView]
    cover_names: Mapping[str, str]

    async def async_set_enabled(self, cover_id: str, enabled: bool) -> None: ...
    async def async_set_mode(self, cover_id: str, mode: Mode) -> None: ...
    async def async_set_shading_mode(self, mode: ShadingMode) -> None: ...
    async def async_set_reopening_mode(self, mode: ReopeningMode) -> None: ...
    async def async_set_simulation(self, on: bool) -> None: ...
    async def async_set_verbose(self, on: bool) -> None: ...
    async def async_reset_override(self, cover_id: str) -> None: ...
    async def async_evaluate_now(self, cover_ids: Iterable[str] | None = None) -> None: ...


class _BaseEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol, key: str) -> None:
        self._entry = entry
        self._controller = controller
        self._key = key
        self._attr_translation_key = key

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_update(self._entry.entry_id), self._handle_update
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def hub_view(self) -> HubView:
        return self._controller.hub_view


class HubEntity(_BaseEntity):
    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol, key: str) -> None:
        super().__init__(entry, controller, key)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(const.DOMAIN, entry.entry_id)})


class CoverEntityBase(_BaseEntity):
    def __init__(
        self, entry: ConfigEntry, controller: ControllerProtocol, subentry_id: str, key: str
    ) -> None:
        super().__init__(entry, controller, key)
        self.subentry_id = subentry_id
        self._attr_unique_id = f"{subentry_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(const.DOMAIN, subentry_id)})

    @property
    def view(self) -> CoverView:
        return self._controller.cover_views[self.subentry_id]
