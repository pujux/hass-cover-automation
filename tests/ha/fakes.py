"""Test doubles shared by the entity, service and platform tests."""

from __future__ import annotations

from collections.abc import Iterable

from custom_components.cover_automation.engine.model import Mode, ReopeningMode, ShadingMode
from custom_components.cover_automation.views import CoverView, HubView, signal_update
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send


class FakeController:
    def __init__(self) -> None:
        self.hub_view = HubView()
        self.cover_views: dict[str, CoverView] = {}
        self.cover_names: dict[str, str] = {}
        self.calls: list[tuple] = []

    def notify(self, hass: HomeAssistant, entry_id: str) -> None:
        async_dispatcher_send(hass, signal_update(entry_id))

    async def async_set_enabled(self, cover_id: str, enabled: bool) -> None:
        self.calls.append(("set_enabled", cover_id, enabled))

    async def async_set_mode(self, cover_id: str, mode: Mode) -> None:
        self.calls.append(("set_mode", cover_id, mode))

    async def async_set_shading_mode(self, mode: ShadingMode) -> None:
        self.calls.append(("set_shading_mode", mode))

    async def async_set_reopening_mode(self, mode: ReopeningMode) -> None:
        self.calls.append(("set_reopening_mode", mode))

    async def async_set_simulation(self, on: bool) -> None:
        self.calls.append(("set_simulation", on))

    async def async_set_verbose(self, on: bool) -> None:
        self.calls.append(("set_verbose", on))

    async def async_reset_override(self, cover_id: str) -> None:
        self.calls.append(("reset_override", cover_id))

    async def async_evaluate_now(self, cover_ids: Iterable[str] | None = None) -> None:
        self.calls.append(("evaluate_now", tuple(cover_ids) if cover_ids is not None else None))
