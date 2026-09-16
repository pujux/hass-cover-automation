from __future__ import annotations

from datetime import UTC, datetime

from custom_components.cover_automation import const
from custom_components.cover_automation.engine.model import (
    CoverPersisted,
    Owner,
    ReopeningMode,
    ShadingMode,
    Target,
)
from custom_components.cover_automation.store import CoverAutomationStore, StoreData
from homeassistant.core import HomeAssistant


def test_store_data_defaults_and_roundtrip():
    data = StoreData()
    assert data.covers == {} and data.latch.date is None
    assert data.shading_mode is ShadingMode.AUTO and data.reopening_mode is ReopeningMode.PASSIVE
    assert data.simulation is False and data.verbose is False
    data.covers["sub1"] = CoverPersisted(
        owner=Owner.ENGINE,
        engine_target=Target.CLOSED,
        manual_move_at=datetime(2026, 7, 1, 12, tzinfo=UTC),
    )
    data.shading_mode = ShadingMode.FORCED_ALL
    raw = data.to_dict()
    again = StoreData.from_dict(raw)
    assert again.covers["sub1"] == data.covers["sub1"]
    assert again.shading_mode is ShadingMode.FORCED_ALL


def test_from_dict_is_tolerant_of_garbage(caplog):
    raw = {
        "covers": {"good": CoverPersisted().to_dict(), "bad": {"owner": "martian"}},
        "latch": {"date": "not-a-date"},
        "shading_mode": "nonsense",
        "reopening_mode": None,
        "simulation": "yes",
    }
    data = StoreData.from_dict(raw)
    assert "good" in data.covers and data.covers["bad"] == CoverPersisted()
    assert data.latch.date is None
    assert data.shading_mode is ShadingMode.AUTO and data.reopening_mode is ReopeningMode.PASSIVE
    assert data.simulation is True
    assert "bad" in caplog.text


def test_from_dict_tolerates_non_dict_covers(caplog):
    data = StoreData.from_dict({"covers": ["not", "a", "dict"]})
    assert data.covers == {}
    assert caplog.text


def test_from_dict_tolerates_non_mapping_raw(caplog):
    data = StoreData.from_dict("garbage")
    assert data == StoreData()
    assert caplog.text


async def test_store_load_save_remove(hass: HomeAssistant, hass_storage: dict) -> None:
    store = CoverAutomationStore(hass, "entry1")
    data = await store.async_load()
    assert data == StoreData()
    data.covers["sub1"] = CoverPersisted(owner=Owner.USER)
    await store.async_save()
    key = const.storage_key("entry1")
    assert hass_storage[key]["version"] == const.STORAGE_VERSION
    assert hass_storage[key]["data"]["covers"]["sub1"]["owner"] == "user"

    store2 = CoverAutomationStore(hass, "entry1")
    loaded = await store2.async_load()
    assert loaded.covers["sub1"].owner is Owner.USER

    await store2.async_remove()
    assert key not in hass_storage


async def test_schedule_save_is_delayed(hass: HomeAssistant, hass_storage: dict, freezer) -> None:
    store = CoverAutomationStore(hass, "entry2")
    await store.async_load()
    store.data.simulation = True
    store.schedule_save()
    await hass.async_block_till_done()
    key = const.storage_key("entry2")
    assert key not in hass_storage  # not yet written
    from datetime import timedelta

    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass_storage[key]["data"]["simulation"] is True
