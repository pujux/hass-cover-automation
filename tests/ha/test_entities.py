from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from custom_components.cover_automation import binary_sensor, const, sensor
from custom_components.cover_automation.engine.model import Status
from custom_components.cover_automation.views import CoverView, HubView
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity, EntityCategory

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


def fake_with_cover_views() -> FakeController:
    """A FakeController with one cover subentry ("sub1") already registered."""
    ctrl = FakeController()
    ctrl.cover_views["sub1"] = CoverView(name="Bedroom", cover_entity="cover.bedroom")
    ctrl.cover_names["sub1"] = "Bedroom"
    return ctrl


async def collect_views(
    hass: HomeAssistant, hub_entry: Any, module: Any, ctrl: FakeController
) -> tuple[list[Entity], list[str | None]]:
    """Run module.async_setup_entry against a fake runtime_data and collect what it adds."""
    hub_entry.runtime_data = SimpleNamespace(
        controller=ctrl, covers=dict.fromkeys(ctrl.cover_views)
    )
    entities: list[Entity] = []
    sub_ids: list[str | None] = []

    def add(
        new_entities: Iterable[Entity],
        update_before_add: bool = False,
        *,
        config_subentry_id: str | None = None,
    ) -> None:
        added = list(new_entities)
        entities.extend(added)
        sub_ids.extend([config_subentry_id] * len(added))

    await module.async_setup_entry(hass, hub_entry, add)
    return entities, sub_ids


async def test_sensor_platform(hass: HomeAssistant, hub_entry: Any) -> None:
    ctrl = fake_with_cover_views()
    at = datetime(2026, 7, 10, 19, 30, tzinfo=UTC)
    ctrl.hub_view = HubView(
        forecast_max_c=30.5,
        forecast_min_c=18.0,
        next_event_at=at,
        next_event_profile="Night",
        next_event_action="closed",
        next_event_covers=("Bedroom",),
    )
    ctrl.cover_views["sub1"] = CoverView(
        name="Bedroom",
        cover_entity="cover.bedroom",
        status=Status.CLOSED_SHADING,
        desired_state="closed",
        actual_state="closed",
        winning_layer="shading",
        reason="sun hits, hot day",
        sun_hits=True,
        sunny=True,
        hot_day=True,
        room_state="none",
        wind_state="inactive",
        owner="engine",
        last_engine_move=at,
    )
    entities, _sub_ids = await collect_views(hass, hub_entry, sensor, ctrl)
    by_key = {e.translation_key: e for e in entities}
    assert set(by_key) == {
        "forecast_max_today",
        "forecast_min_today",
        "next_scheduled_event",
        "status",
    }
    assert (
        by_key["forecast_max_today"].native_value == 30.5
        and by_key["forecast_max_today"].native_unit_of_measurement == "°C"
    )
    assert by_key["forecast_max_today"].device_class is SensorDeviceClass.TEMPERATURE
    assert by_key["next_scheduled_event"].native_value == at and by_key[
        "next_scheduled_event"
    ].extra_state_attributes == {"profile": "Night", "action": "closed", "covers": ["Bedroom"]}
    status = by_key["status"]
    assert status.entity_category is None and status.device_class is SensorDeviceClass.ENUM
    assert status.native_value == "closed_shading" and status.options == [s.value for s in Status]
    attrs = status.extra_state_attributes
    assert attrs["desired_state"] == "closed" and attrs["reason"] == "sun hits, hot day"
    assert attrs["last_engine_move"] == at.isoformat()
    assert {"desired_state", "actual_state", "reason"}.isdisjoint(
        type(status)._unrecorded_attributes
    )
    assert set(attrs) - {"desired_state", "actual_state", "reason"} <= set(
        type(status)._unrecorded_attributes
    )
    assert all(
        e.entity_category is EntityCategory.DIAGNOSTIC
        for e in entities
        if e.translation_key != "status"
    )


async def test_binary_sensor_platform(hass: HomeAssistant, hub_entry: Any) -> None:
    ctrl = fake_with_cover_views()
    since = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    ctrl.hub_view = HubView(
        sunny=True, hot_day=None, frost=False, any_wind_active=True, problem=True
    )
    ctrl.cover_views["sub1"] = CoverView(
        name="Bedroom",
        cover_entity="cover.bedroom",
        override_active=True,
        override_since=since,
        overridden_desired="closed",
        sun_hits=True,
        wind_active=True,
    )
    entities, _sub_ids = await collect_views(hass, hub_entry, binary_sensor, ctrl)
    by_key = {e.translation_key: e for e in entities}
    assert set(by_key) == {
        "hot_day",
        "sunny",
        "frost_active",
        "any_wind_protection_active",
        "problem",
        "manual_override",
        "sun_hits",
        "wind_protection_active",
    }
    assert by_key["sunny"].is_on is True
    assert by_key["hot_day"].is_on is None
    assert by_key["frost_active"].is_on is False
    assert by_key["problem"].is_on is True
    assert by_key["problem"].device_class is BinarySensorDeviceClass.PROBLEM
    mo = by_key["manual_override"]
    assert mo.entity_category is None and mo.is_on is True
    assert mo.extra_state_attributes == {"since": since.isoformat(), "overridden_desired": "closed"}
    assert by_key["sun_hits"].is_on is True and by_key["wind_protection_active"].is_on is True
    assert all(
        e.entity_category is EntityCategory.DIAGNOSTIC
        for e in entities
        if e.translation_key != "manual_override"
    )
