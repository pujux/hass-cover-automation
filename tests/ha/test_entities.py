from __future__ import annotations

from custom_components.cover_automation import const
from homeassistant.core import HomeAssistant


async def test_hub_entity_base_identity(hass: HomeAssistant, hub_entry) -> None:
    from custom_components.cover_automation.entity import CoverEntityBase, HubEntity
    from custom_components.cover_automation.views import CoverView

    from tests.ha.fakes import FakeController

    ctrl = FakeController()
    e = HubEntity(hub_entry, ctrl, "sunny")
    assert e.unique_id == f"{hub_entry.entry_id}_sunny" and e.translation_key == "sunny"
    assert e.has_entity_name
    assert (const.DOMAIN, hub_entry.entry_id) in e.device_info["identifiers"]
    ctrl.cover_views["sub1"] = CoverView(name="Bedroom", cover_entity="cover.bedroom")
    c = CoverEntityBase(hub_entry, ctrl, "sub1", "status")
    assert c.unique_id == "sub1_status" and (const.DOMAIN, "sub1") in c.device_info["identifiers"]
    assert c.view.name == "Bedroom"
