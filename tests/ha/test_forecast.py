from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

import pytest
from custom_components.cover_automation.forecast import TodayForecast, async_fetch_today
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse

from tests.ha.conftest import WEATHER, set_weather

TZ = ZoneInfo("Europe/Vienna")


def register_forecast(hass: HomeAssistant, entries, *, raise_error: bool = False) -> None:
    async def handler(call: ServiceCall):
        if raise_error:
            raise RuntimeError("boom")
        return {call.data["entity_id"]: {"forecast": entries}}

    hass.services.async_register(
        "weather", "get_forecasts", handler, supports_response=SupportsResponse.ONLY
    )


async def test_fetch_today_picks_local_day_and_converts_to_celsius(hass: HomeAssistant) -> None:
    set_weather(hass, unit="°F")
    register_forecast(
        hass,
        [
            {
                "datetime": "2026-07-09T22:00:00+00:00",
                "temperature": 60.0,
                "templow": 50.0,
            },  # 10 July 00:00 Vienna
            {
                "datetime": "2026-07-10T22:00:00+00:00",
                "temperature": 86.0,
                "templow": 59.0,
            },  # 11 July
        ],
    )
    today = await async_fetch_today(hass, WEATHER, date(2026, 7, 10), TZ)
    assert (
        today is not None
        and today.max_c == pytest.approx(15.5556, abs=1e-3)
        and today.min_c == pytest.approx(10.0)
    )


async def test_fetch_today_handles_missing_low_and_no_match(hass: HomeAssistant) -> None:
    set_weather(hass)
    register_forecast(hass, [{"datetime": "2026-07-10T10:00:00+02:00", "temperature": 30.0}])
    assert await async_fetch_today(hass, WEATHER, date(2026, 7, 10), TZ) == TodayForecast(
        30.0, None
    )
    assert await async_fetch_today(hass, WEATHER, date(2026, 7, 12), TZ) is None


async def test_fetch_today_returns_none_on_error_or_empty(hass: HomeAssistant) -> None:
    set_weather(hass)
    register_forecast(hass, [], raise_error=True)
    assert await async_fetch_today(hass, WEATHER, date(2026, 7, 10), TZ) is None
    register_forecast(hass, [])
    assert await async_fetch_today(hass, WEATHER, date(2026, 7, 10), TZ) is None
