from __future__ import annotations

from typing import ClassVar

from custom_components.cover_automation import const
from custom_components.cover_automation.controller import EVENT_ACTION
from custom_components.cover_automation.logbook import async_describe_events
from homeassistant.core import HomeAssistant


async def test_describe_action_event(hass: HomeAssistant):
    described: dict = {}

    def register(domain, event_name, describe):
        described[(domain, event_name)] = describe

    async_describe_events(hass, register)
    describe = described[(const.DOMAIN, EVENT_ACTION)]

    class FakeEvent:
        data: ClassVar = {
            "entity_id": "cover.bedroom",
            "cover_name": "Bedroom",
            "action": "close",
            "reason": "sun hits, hot day",
            "layer": "shading",
            "simulated": True,
        }
        context_id = "ctx"

    entry = describe(FakeEvent())
    assert entry["name"] == "Bedroom" and entry["entity_id"] == "cover.bedroom"
    assert entry["message"] == "closed for shading: sun hits, hot day (simulated)"
