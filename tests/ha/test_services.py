from __future__ import annotations

from types import SimpleNamespace

from custom_components.cover_automation import const, services
from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from tests.ha.fakes import FakeController


def fake_entry(hass, hub_entry, ctrl):
    hub_entry.runtime_data = SimpleNamespace(
        controller=ctrl, covers={"sub1": (None, None), "sub2": (None, None)}
    )
    hub_entry.mock_state(hass, ConfigEntryState.LOADED)


def _add_cover_subentries(hass: HomeAssistant, hub_entry, *subentry_ids: str) -> None:
    """Devices can only reference a subentry id the device registry knows about."""
    for subentry_id in subentry_ids:
        hass.config_entries.async_add_subentry(
            hub_entry,
            ConfigSubentry(
                data={},
                subentry_type=const.SUBENTRY_COVER,
                title=subentry_id,
                unique_id=None,
                subentry_id=subentry_id,
            ),
        )


async def test_setup_services_is_idempotent(hass: HomeAssistant):
    services.async_setup_services(hass)
    services.async_setup_services(hass)
    assert hass.services.has_service(const.DOMAIN, "reset_override") and hass.services.has_service(
        const.DOMAIN, "evaluate_now"
    )


async def test_resolve_targets_by_device_and_entity(hass: HomeAssistant, hub_entry):
    _add_cover_subentries(hass, hub_entry, "sub1", "sub2")
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=hub_entry.entry_id,
        config_subentry_id="sub2",
        identifiers={(const.DOMAIN, "sub2")},
    )
    ent = er.async_get(hass).async_get_or_create(
        "sensor", const.DOMAIN, "sub1_status", config_entry=hub_entry, config_subentry_id="sub1"
    )
    call = ServiceCall(
        hass, const.DOMAIN, "evaluate_now", {"device_id": [dev.id], "entity_id": [ent.entity_id]}
    )
    assert services.resolve_cover_ids(hass, call) == {"sub1", "sub2"}
    assert (
        services.resolve_cover_ids(hass, ServiceCall(hass, const.DOMAIN, "evaluate_now", {}))
        is None
    )


async def test_services_dispatch_to_controller(hass: HomeAssistant, hub_entry):
    from custom_components.cover_automation.views import CoverView

    ctrl = FakeController()
    ctrl.cover_views = {
        "sub1": CoverView(name="A", cover_entity="cover.a"),
        "sub2": CoverView(name="B", cover_entity="cover.b"),
    }
    fake_entry(hass, hub_entry, ctrl)
    _add_cover_subentries(hass, hub_entry, "sub1")
    services.async_setup_services(hass)
    await hass.services.async_call(const.DOMAIN, "evaluate_now", {}, blocking=True)
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=hub_entry.entry_id,
        config_subentry_id="sub1",
        identifiers={(const.DOMAIN, "sub1")},
    )
    await hass.services.async_call(
        const.DOMAIN, "reset_override", {"device_id": dev.id}, blocking=True
    )
    assert ctrl.calls == [("evaluate_now", None), ("reset_override", "sub1")]
