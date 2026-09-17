from __future__ import annotations

from custom_components.cover_automation.diagnostics import async_get_config_entry_diagnostics
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant

from tests.ha.conftest import cover_subentry_data, set_cover, set_sensor, set_sun, set_weather


async def test_diagnostics_without_controller(hass: HomeAssistant, hub_entry):
    hass.config_entries.async_add_subentry(
        hub_entry, ConfigSubentry(**cover_subentry_data("cover.bedroom"))
    )
    set_weather(hass)
    set_sun(hass)
    set_sensor(hass, "sensor.wind", 3, unit="km/h")
    set_cover(hass, "cover.bedroom")
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    await hass.async_block_till_done()
    diag = await async_get_config_entry_diagnostics(hass, hub_entry)
    assert diag["hub"]["weather_entity"] == "weather.home" and diag["hub"]["sunny_conditions"] == [
        "partlycloudy",
        "sunny",
    ]
    assert (
        diag["subentries"][0]["type"] == "cover"
        and diag["subentries"][0]["data"]["cover_entity"] == "cover.bedroom"
    )
    assert set(diag["store"]) >= {"covers", "latch", "shading_mode"}
    assert "covers" in diag
    assert diag["missing_entities"] == hub_entry.runtime_data.missing_entities == []
