"""Diagnostics download (spec §4): hub config, subentries, engine snapshot, Store."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    data = entry.runtime_data
    hub = asdict(data.hub)
    hub["sunny_conditions"] = sorted(hub["sunny_conditions"])
    controller = getattr(data, "controller", None)
    started = getattr(controller, "started", False)
    return {
        "hub": hub,
        "subentries": [
            {"id": s.subentry_id, "type": s.subentry_type, "title": s.title, "data": dict(s.data)}
            for s in entry.subentries.values()
        ],
        "covers": controller.snapshot() if controller is not None and started else {},
        "store": data.store.data.to_dict(),
    }
