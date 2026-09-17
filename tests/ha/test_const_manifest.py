from __future__ import annotations

import json
from pathlib import Path

from custom_components.cover_automation import const
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

MANIFEST = Path("custom_components/cover_automation/manifest.json")


async def test_integration_is_discoverable(hass: HomeAssistant) -> None:
    integration = await async_get_integration(hass, const.DOMAIN)
    assert integration.domain == "cover_automation"
    assert integration.config_flow is True
    assert integration.single_config_entry is True
    assert set(integration.dependencies) == {"persistent_notification", "weather"}
    assert set(integration.after_dependencies) == {"logbook", "sun"}
    assert integration.iot_class == "calculated"
    assert integration.integration_type == "hub"


def test_manifest_keys() -> None:
    data = json.loads(MANIFEST.read_text())
    assert list(data)[:2] == ["domain", "name"], "hassfest: domain and name first"
    rest = list(data)[2:]
    assert rest == sorted(rest), "hassfest: remaining manifest keys alphabetical"
    assert data["domain"] == const.DOMAIN
    assert data["version"] == "0.5.0"
    repo = "https://github.com/pujux/hass-cover-automation"
    assert data["codeowners"] == ["@pujux"]
    assert data["documentation"] == repo
    assert data["issue_tracker"] == f"{repo}/issues"


def test_platforms_cover_every_entity_module() -> None:
    from homeassistant.const import Platform

    expected = {
        Platform.BINARY_SENSOR,
        Platform.BUTTON,
        Platform.SELECT,
        Platform.SENSOR,
        Platform.SWITCH,
    }
    assert set(const.PLATFORMS) == expected
    assert len(const.PLATFORMS) == len(expected)


def test_defaults_match_spec() -> None:
    assert const.DEFAULT_FROST_THRESHOLD == 0.0
    assert const.DEFAULT_SUNNY_CONDITIONS == ("sunny", "partlycloudy")
    assert isinstance(const.DEFAULT_SUNNY_CONDITIONS, tuple)
    assert (const.DEFAULT_SUNNY_ON_DELAY_MIN, const.DEFAULT_SUNNY_OFF_DELAY_MIN) == (10, 20)
    assert const.DEFAULT_WEATHER_GRACE_MIN == 30
    assert (const.DEFAULT_HOT_HIGH, const.DEFAULT_HOT_LOW) == (24.0, 13.0)
    assert const.DEFAULT_SUN_RELEASE_MARGIN == 2.0
    assert const.DEFAULT_TOLERANCE == 5.0
    assert const.DEFAULT_OVERRIDE_DWELL_MIN == 30
    assert (const.DEFAULT_TOLERANCE_LEFT, const.DEFAULT_TOLERANCE_RIGHT) == (60.0, 60.0)
    assert (const.DEFAULT_COMFORT_FLOOR, const.DEFAULT_COMFORT_CEILING) == (21.0, 25.0)
    assert const.DEFAULT_WIND_HOLD_MIN == 15
    assert const.DEFAULT_MIN_MOVE_INTERVAL_MIN == 10
    assert const.DEFAULT_CONFIRM_WINDOW_S == 120
    assert const.MAX_RULES == 4
    assert const.storage_key("abc") == "cover_automation.abc"
