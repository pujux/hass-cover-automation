from __future__ import annotations

from custom_components.cover_automation import const
from custom_components.cover_automation.engine.model import Owner
from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir

from tests.ha.conftest import (
    cover_subentry_data,
    profile_subentry_data,
    set_cover,
    set_sensor,
    set_sun,
    set_weather,
)


async def setup_hub(hass: HomeAssistant, hub_entry) -> None:
    set_weather(hass)
    set_sun(hass)
    set_sensor(hass, "sensor.wind", 5, unit="km/h", device_class="wind_speed")
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_creates_hub_and_cover_devices(hass: HomeAssistant, hub_entry) -> None:
    hass.config_entries.async_add_subentry(
        hub_entry, ConfigSubentry(**profile_subentry_data("Night"))
    )
    hass.config_entries.async_add_subentry(
        hub_entry, ConfigSubentry(**cover_subentry_data("cover.bedroom"))
    )
    set_cover(hass, "cover.bedroom")
    await setup_hub(hass, hub_entry)
    assert hub_entry.state is ConfigEntryState.LOADED

    registry = dr.async_get(hass)
    hub_device = registry.async_get_device_by_identifier(
        (const.DOMAIN, hub_entry.entry_id), hub_entry.entry_id
    )
    assert hub_device is not None and hub_device.config_subentry_id is None
    cover_sub = next(
        s for s in hub_entry.subentries.values() if s.subentry_type == const.SUBENTRY_COVER
    )
    cover_device = registry.async_get_device_by_identifier(
        (const.DOMAIN, cover_sub.subentry_id), hub_entry.entry_id
    )
    assert cover_device is not None
    assert cover_device.config_subentry_id == cover_sub.subentry_id
    assert cover_device.via_device_id == hub_device.id and cover_device.name == "Bedroom"

    data = hub_entry.runtime_data
    assert data.hub.weather_entity == "weather.home"
    assert set(data.covers) == {cover_sub.subentry_id}
    assert len(data.profiles) == 1
    assert data.hub_device_id == hub_device.id


async def test_setup_not_ready_without_weather_or_sun(hass: HomeAssistant, hub_entry) -> None:
    set_sun(hass)
    assert not await hass.config_entries.async_setup(hub_entry.entry_id)
    assert hub_entry.state is ConfigEntryState.SETUP_RETRY
    set_weather(hass)
    hass.states.async_remove("sun.sun")
    await hass.config_entries.async_reload(hub_entry.entry_id)
    assert hub_entry.state is ConfigEntryState.SETUP_RETRY


async def test_missing_optional_sensor_raises_repair_but_loads(
    hass: HomeAssistant, hub_entry
) -> None:
    set_weather(hass)
    set_sun(hass)  # no sensor.wind state
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    await hass.async_block_till_done()
    assert hub_entry.state is ConfigEntryState.LOADED
    issue = ir.async_get(hass).async_get_issue(
        const.DOMAIN, f"missing_entity_{hub_entry.entry_id}_sensor.wind"
    )
    assert issue is not None and issue.translation_key == "missing_entity"


async def test_store_is_loaded_and_saved_on_unload(
    hass: HomeAssistant, hub_entry, hass_storage
) -> None:
    hass.config_entries.async_add_subentry(
        hub_entry, ConfigSubentry(**cover_subentry_data("cover.bedroom"))
    )
    set_cover(hass, "cover.bedroom")
    await setup_hub(hass, hub_entry)
    data = hub_entry.runtime_data
    sub_id = next(iter(data.covers))
    data.store.data.covers[sub_id].owner = Owner.USER
    assert await hass.config_entries.async_unload(hub_entry.entry_id)
    await hass.async_block_till_done()
    assert hub_entry.state is ConfigEntryState.NOT_LOADED
    assert (
        hass_storage[const.storage_key(hub_entry.entry_id)]["data"]["covers"][sub_id]["owner"]
        == "user"
    )


async def test_subentry_change_reloads_entry(hass: HomeAssistant, hub_entry) -> None:
    await setup_hub(hass, hub_entry)
    assert hub_entry.runtime_data.covers == {}
    set_cover(hass, "cover.bedroom")
    hass.config_entries.async_add_subentry(
        hub_entry, ConfigSubentry(**cover_subentry_data("cover.bedroom"))
    )
    await hass.async_block_till_done()
    assert hub_entry.state is ConfigEntryState.LOADED
    assert len(hub_entry.runtime_data.covers) == 1


async def test_options_change_reloads_entry(hass: HomeAssistant, hub_entry) -> None:
    await setup_hub(hass, hub_entry)
    hass.config_entries.async_update_entry(
        hub_entry, options={**hub_entry.options, const.CONF_HOT_HIGH: 30.0}
    )
    await hass.async_block_till_done()
    assert hub_entry.runtime_data.hub.hot_high == 30.0


async def test_remove_entry_deletes_store(hass: HomeAssistant, hub_entry, hass_storage) -> None:
    await setup_hub(hass, hub_entry)
    key = const.storage_key(hub_entry.entry_id)
    await hub_entry.runtime_data.store.async_save()
    assert key in hass_storage
    await hass.config_entries.async_remove(hub_entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


async def test_stale_cover_device_can_be_removed(hass: HomeAssistant, hub_entry) -> None:
    hass.config_entries.async_add_subentry(
        hub_entry, ConfigSubentry(**cover_subentry_data("cover.bedroom"))
    )
    set_cover(hass, "cover.bedroom")
    await setup_hub(hass, hub_entry)
    from custom_components.cover_automation import async_remove_config_entry_device

    registry = dr.async_get(hass)
    hub_device = registry.async_get_device_by_identifier(
        (const.DOMAIN, hub_entry.entry_id), hub_entry.entry_id
    )
    cover_sub = next(iter(hub_entry.subentries.values()))
    cover_device = registry.async_get_device_by_identifier(
        (const.DOMAIN, cover_sub.subentry_id), hub_entry.entry_id
    )
    assert hub_device is not None and cover_device is not None
    assert not await async_remove_config_entry_device(hass, hub_entry, hub_device)
    assert not await async_remove_config_entry_device(hass, hub_entry, cover_device)
    stale = registry.async_get_or_create(
        config_entry_id=hub_entry.entry_id, identifiers={(const.DOMAIN, "gone")}
    )
    assert await async_remove_config_entry_device(hass, hub_entry, stale)
