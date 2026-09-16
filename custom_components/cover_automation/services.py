"""Services: reset_override and evaluate_now with HA target selection (spec §4)."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.target import TargetSelection, async_extract_referenced_entity_ids

from . import const
from .entity import ControllerProtocol

SERVICE_RESET_OVERRIDE = "reset_override"
SERVICE_EVALUATE_NOW = "evaluate_now"


def controllers(hass: HomeAssistant) -> list[ControllerProtocol]:
    out: list[ControllerProtocol] = []
    for entry in hass.config_entries.async_loaded_entries(const.DOMAIN):
        data = getattr(entry, "runtime_data", None)
        controller = getattr(data, "controller", None)
        if controller is not None:
            out.append(controller)
    return out


def resolve_cover_ids(hass: HomeAssistant, call: ServiceCall) -> set[str] | None:
    selection = TargetSelection(call.data)
    if not selection.has_any_target:
        return None
    selected = async_extract_referenced_entity_ids(hass, selection)
    ent_reg, dev_reg = er.async_get(hass), dr.async_get(hass)
    cover_ids: set[str] = set()
    for entity_id in selected.referenced | selected.indirectly_referenced:
        entry = ent_reg.async_get(entity_id)
        if entry is not None and entry.platform == const.DOMAIN and entry.config_subentry_id:
            cover_ids.add(entry.config_subentry_id)
    for device_id in selected.referenced_devices:
        device = dev_reg.async_get(device_id)
        if device is not None and device.config_subentry_id:
            cover_ids.add(device.config_subentry_id)
    return cover_ids


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(const.DOMAIN, SERVICE_RESET_OVERRIDE):
        return

    async def reset_override(call: ServiceCall) -> None:
        ids = resolve_cover_ids(hass, call)
        for controller in controllers(hass):
            for cover_id in controller.cover_views if ids is None else ids:
                if cover_id in controller.cover_views:
                    await controller.async_reset_override(cover_id)

    async def evaluate_now(call: ServiceCall) -> None:
        ids = resolve_cover_ids(hass, call)
        for controller in controllers(hass):
            targets = None if ids is None else [c for c in ids if c in controller.cover_views]
            await controller.async_evaluate_now(targets)

    schema = vol.Schema(cv.TARGET_SERVICE_FIELDS)
    hass.services.async_register(
        const.DOMAIN, SERVICE_RESET_OVERRIDE, reset_override, schema=schema
    )
    hass.services.async_register(const.DOMAIN, SERVICE_EVALUATE_NOW, evaluate_now, schema=schema)
