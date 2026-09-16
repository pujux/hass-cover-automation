"""Describe cover_automation_action events in the logbook (spec §4)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook.const import (
    LOGBOOK_ENTRY_CONTEXT_ID,
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.components.logbook.models import LazyEventPartialState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, callback

from . import const
from .controller import EVENT_ACTION

DescribeCallback = Callable[[LazyEventPartialState], dict[str, Any]]


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, DescribeCallback], None],
) -> None:
    @callback
    def describe(event: LazyEventPartialState) -> dict[str, Any]:
        data = event.data
        verb = "closed" if data.get("action") == "close" else "opened"
        message = f"{verb} for {data.get('layer')}: {data.get('reason')}"
        if data.get("simulated"):
            message += " (simulated)"
        return {
            LOGBOOK_ENTRY_NAME: data.get("cover_name"),
            LOGBOOK_ENTRY_MESSAGE: message,
            LOGBOOK_ENTRY_ENTITY_ID: data.get(ATTR_ENTITY_ID),
            LOGBOOK_ENTRY_CONTEXT_ID: event.context_id,
        }

    async_describe_event(const.DOMAIN, EVENT_ACTION, describe)
