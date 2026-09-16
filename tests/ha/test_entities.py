from __future__ import annotations

from types import SimpleNamespace

from custom_components.cover_automation import button, const, select, switch
from custom_components.cover_automation.engine.model import Mode, ReopeningMode, ShadingMode
from custom_components.cover_automation.views import CoverView, HubView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory

from tests.ha.fakes import FakeController


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


# --- Task 6: switch, select and button platforms ---


async def collect(hass, hub_entry, module, ctrl):
    """Run a platform's async_setup_entry against a fake controller; return (entities, per-subentry ids)."""
    hub_entry.runtime_data = SimpleNamespace(controller=ctrl, covers={"sub1": (None, None)})
    added: list = []
    subentry_ids: list = []

    def add(entities, update_before_add=False, *, config_subentry_id=None):
        added.extend(entities)
        subentry_ids.append(config_subentry_id)

    await module.async_setup_entry(hass, hub_entry, add)
    return added, subentry_ids


def fake_with_cover():
    ctrl = FakeController()
    ctrl.cover_views["sub1"] = CoverView(
        name="Bedroom", cover_entity="cover.bedroom", enabled=True, mode=Mode.AUTO
    )
    ctrl.cover_names["sub1"] = "Bedroom"
    return ctrl


async def test_switch_platform(hass, hub_entry):
    ctrl = fake_with_cover()
    ctrl.hub_view = HubView(simulation=True, verbose=False)
    entities, sub_ids = await collect(hass, hub_entry, switch, ctrl)
    by_key = {e.translation_key: e for e in entities}
    assert set(by_key) == {"simulation_mode", "verbose_logging", "enabled"} and "sub1" in sub_ids
    assert all(e.entity_category is EntityCategory.CONFIG for e in entities)
    assert (
        by_key["simulation_mode"].is_on is True
        and by_key["verbose_logging"].is_on is False
        and by_key["enabled"].is_on is True
    )
    await by_key["simulation_mode"].async_turn_off()
    await by_key["verbose_logging"].async_turn_on()
    await by_key["enabled"].async_turn_off()
    assert ctrl.calls == [
        ("set_simulation", False),
        ("set_verbose", True),
        ("set_enabled", "sub1", False),
    ]
    assert (
        by_key["enabled"].unique_id == "sub1_enabled"
        and by_key["simulation_mode"].unique_id == f"{hub_entry.entry_id}_simulation_mode"
    )


async def test_select_platform(hass, hub_entry):
    ctrl = fake_with_cover()
    ctrl.hub_view = HubView(
        shading_mode=ShadingMode.FORCED_ALL, reopening_mode=ReopeningMode.ACTIVE
    )
    entities, _ = await collect(hass, hub_entry, select, ctrl)
    by_key = {e.translation_key: e for e in entities}
    assert by_key["shading_mode"].current_option == "forced_all" and by_key[
        "shading_mode"
    ].options == ["off", "auto", "forced_sunlit", "forced_all"]
    assert (
        by_key["reopening_mode"].current_option == "active"
        and by_key["mode"].current_option == "auto"
    )
    await by_key["shading_mode"].async_select_option("auto")
    await by_key["reopening_mode"].async_select_option("off")
    await by_key["mode"].async_select_option("dark_only")
    assert ctrl.calls == [
        ("set_shading_mode", ShadingMode.AUTO),
        ("set_reopening_mode", ReopeningMode.OFF),
        ("set_mode", "sub1", Mode.DARK_ONLY),
    ]
    assert all(e.entity_category is EntityCategory.CONFIG for e in entities)


async def test_button_platform(hass, hub_entry):
    ctrl = fake_with_cover()
    entities, _ = await collect(hass, hub_entry, button, ctrl)
    by_key = {e.translation_key: e for e in entities}
    await by_key["evaluate_now"].async_press()
    await by_key["reset_override"].async_press()
    assert ctrl.calls == [("evaluate_now", None), ("reset_override", "sub1")]
    assert all(e.entity_category is EntityCategory.CONFIG for e in entities)
