from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import yaml
from custom_components.cover_automation import binary_sensor, button, const, select, sensor, switch
from custom_components.cover_automation import services as services_mod
from custom_components.cover_automation.config_flow import (
    _HUB_OPTION_KEYS,
    cover_schema,
    hub_data_schema,
    profile_schema,
    thresholds_schema,
)
from custom_components.cover_automation.engine.model import (
    Mode,
    ReopeningMode,
    ShadingMode,
    ShadingRule,
    Status,
    WindAction,
)
from custom_components.cover_automation.engine.schedule import RuleAction, TimeMode
from custom_components.cover_automation.views import CoverView

from tests.ha.fakes import FakeController

TRANSLATIONS = json.loads(
    Path("custom_components/cover_automation/translations/en.json").read_text()
)
SERVICES_YAML = Path("custom_components/cover_automation/services.yaml")


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
        assert "reconfigure_successful" in block["abort"]


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
    assert set(sel["shading_rule"]["options"]) == {r.value for r in ShadingRule}
    assert set(sel["time_mode"]["options"]) == {m.value for m in TimeMode}
    assert set(sel["wind_action"]["options"]) == {a.value for a in WindAction}
    assert set(sel["rule_action"]["options"]) == {a.value for a in RuleAction}
    assert {"missing_entity", "missing_profile"} <= set(TRANSLATIONS["issues"])


def test_translation_keys_are_sorted_recursively() -> None:
    def check(obj, path="root"):
        if isinstance(obj, dict):
            assert list(obj) == sorted(obj), f"unsorted keys at {path}"
            for k, v in obj.items():
                check(v, f"{path}.{k}")

    check(TRANSLATIONS)


# --- Task 9: entity and service translations ---

PLATFORM_MODULES = {
    "binary_sensor": binary_sensor,
    "button": button,
    "select": select,
    "sensor": sensor,
    "switch": switch,
}


async def platform_keys(hass, hub_entry, module) -> set[str]:
    """Translation keys the platform's entities actually declare (never a hardcoded list)."""
    ctrl = FakeController()
    ctrl.cover_views["sub1"] = CoverView(name="Bedroom", cover_entity="cover.bedroom")
    ctrl.cover_names["sub1"] = "Bedroom"
    hub_entry.runtime_data = SimpleNamespace(controller=ctrl, covers={"sub1": (None, None)})
    added: list = []

    def add(entities, update_before_add=False, *, config_subentry_id=None):
        added.extend(entities)

    await module.async_setup_entry(hass, hub_entry, add)
    return {e.translation_key for e in added}


async def test_every_entity_translation_key_has_a_name(hass, hub_entry) -> None:
    entity_block = TRANSLATIONS["entity"]
    for platform, module in PLATFORM_MODULES.items():
        keys = await platform_keys(hass, hub_entry, module)
        assert keys, f"{platform}: no entities collected"
        translated = {k for k, v in entity_block.get(platform, {}).items() if v.get("name")}
        assert keys <= translated, f"{platform}: {keys - translated}"


def test_status_sensor_states_are_translated() -> None:
    states = TRANSLATIONS["entity"]["sensor"]["status"]["state"]
    assert set(states) == {s.value for s in Status}
    assert all(states.values())


def test_select_options_are_translated() -> None:
    selects = TRANSLATIONS["entity"]["select"]
    for key, enum in (
        ("shading_mode", ShadingMode),
        ("reopening_mode", ReopeningMode),
        ("mode", Mode),
    ):
        states = selects[key]["state"]
        assert set(states) == {m.value for m in enum}, key
        assert all(states.values()), key


def test_services_are_translated() -> None:
    services_block = TRANSLATIONS["services"]
    registered = set(yaml.safe_load(SERVICES_YAML.read_text()))
    assert registered == {services_mod.SERVICE_RESET_OVERRIDE, services_mod.SERVICE_EVALUATE_NOW}
    assert set(services_block) == registered
    for name in registered:
        assert services_block[name]["name"] and services_block[name]["description"]
