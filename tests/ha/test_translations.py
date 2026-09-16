from __future__ import annotations

import json
from pathlib import Path

from custom_components.cover_automation import const
from custom_components.cover_automation.config_flow import (
    _HUB_OPTION_KEYS,
    cover_schema,
    hub_data_schema,
    profile_schema,
    thresholds_schema,
)

TRANSLATIONS = json.loads(
    Path("custom_components/cover_automation/translations/en.json").read_text()
)


def keys_of(schema) -> set[str]:
    return {str(k) for k in schema.schema}


def test_hub_flow_fields_are_translated(hass) -> None:
    for step, schema in (
        ("user", hub_data_schema()),
        ("reconfigure", hub_data_schema()),
        ("thresholds", thresholds_schema({}, "°C")),
    ):
        translated = set(TRANSLATIONS["config"]["step"][step]["data"])
        assert keys_of(schema) <= translated, f"{step}: {keys_of(schema) - translated}"
    assert set(_HUB_OPTION_KEYS) <= set(TRANSLATIONS["options"]["step"]["init"]["data"])


def test_flow_errors_and_aborts_are_translated() -> None:
    assert {"entity_not_found", "weather_no_daily"} <= set(TRANSLATIONS["config"]["error"])
    assert {"single_instance_allowed", "reconfigure_successful"} <= set(
        TRANSLATIONS["config"]["abort"]
    )
    cover = TRANSLATIONS["config_subentries"]["cover"]
    assert {
        "already_configured",
        "cover_unsupported",
        "floor_not_below_ceiling",
        "wind_lower_not_below_upper",
        "room_sensor_required",
        "confirm_window_too_short",
    } <= set(cover["error"])
    assert "invalid_rules" in TRANSLATIONS["config_subentries"]["profile"]["error"]
    for kind in ("cover", "profile"):
        block = TRANSLATIONS["config_subentries"][kind]
        assert block["entry_type"] and block["initiate_flow"]["user"]
        assert set(block["step"]) >= {"user", "reconfigure"}


def test_cover_subentry_fields_are_translated(hass, hub_entry) -> None:
    schema = cover_schema(hass, hub_entry, {}, "°C")
    for step in ("user", "reconfigure"):
        translated = set(TRANSLATIONS["config_subentries"]["cover"]["step"][step]["data"])
        assert keys_of(schema) <= translated, f"{step}: {keys_of(schema) - translated}"


def test_profile_subentry_fields_and_sections_are_translated() -> None:
    schema = profile_schema({})
    for step in ("user", "reconfigure"):
        block = TRANSLATIONS["config_subentries"]["profile"]["step"][step]
        top = {k for k in keys_of(schema) if not k.startswith("rule_")}
        assert top <= set(block["data"])
        for i in range(1, const.MAX_RULES + 1):
            sec = block["sections"][f"rule_{i}"]
            assert set(sec["data"]) == {
                const.CONF_RULE_ENABLED,
                const.CONF_RULE_ACTION,
                const.CONF_RULE_TIME_MODE,
                const.CONF_RULE_TIME,
                const.CONF_RULE_OFFSET,
                const.CONF_RULE_EARLIEST,
                const.CONF_RULE_LATEST,
            }


def test_selector_options_and_issues_are_translated() -> None:
    sel = TRANSLATIONS["selector"]
    assert set(sel["weather_condition"]["options"]) == set(const.WEATHER_CONDITIONS)
    assert set(sel["shading_rule"]["options"]) == {"forecast_with_room", "room_only", "either"}
    assert set(sel["time_mode"]["options"]) == {"fixed", "sunrise", "sunset"}
    assert set(sel["wind_action"]["options"]) == {"open", "hold"}
    assert set(sel["rule_action"]["options"]) == {"closed", "open"}
    assert {"missing_entity", "missing_profile"} <= set(TRANSLATIONS["issues"])


def test_translation_keys_are_sorted_recursively() -> None:
    def check(obj, path="root"):
        if isinstance(obj, dict):
            assert list(obj) == sorted(obj), f"unsorted keys at {path}"
            for k, v in obj.items():
                check(v, f"{path}.{k}")

    check(TRANSLATIONS)
