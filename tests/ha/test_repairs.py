from __future__ import annotations

from custom_components.cover_automation import const, repairs
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir


async def test_set_issue_creates_and_clears(hass: HomeAssistant) -> None:
    issue_id = repairs.cover_issue_id(repairs.ISSUE_FROST_CONFLICT, "sub1")
    assert issue_id == "frost_conflict_sub1"
    assert repairs.set_issue(
        hass, issue_id, True, translation_key="frost_conflict", placeholders={"cover": "Bedroom"}
    )
    issue = ir.async_get(hass).async_get_issue(const.DOMAIN, issue_id)
    assert (
        issue is not None
        and issue.translation_placeholders == {"cover": "Bedroom"}
        and not issue.is_fixable
    )
    assert repairs.set_issue(
        hass, issue_id, True, translation_key="frost_conflict", placeholders={"cover": "Bedroom"}
    )  # idempotent
    assert not repairs.set_issue(hass, issue_id, False, translation_key="frost_conflict")
    assert ir.async_get(hass).async_get_issue(const.DOMAIN, issue_id) is None


def test_prefixes_cover_every_kind() -> None:
    kinds = [v for k, v in vars(repairs).items() if k.startswith("ISSUE_")]
    assert kinds and all(
        any(f"{kind}_" == p for p in repairs.ENTRY_ISSUE_PREFIXES) for kind in kinds
    )
    assert {
        "missing_entity_",
        "missing_profile_",
        "broken_cover_config_",
        "broken_profile_config_",
    } <= set(repairs.ENTRY_ISSUE_PREFIXES)


async def test_stale_sweep_removes_runtime_issues_of_deleted_covers(
    hass: HomeAssistant, hub_entry
) -> None:
    from custom_components.cover_automation import _delete_stale_issues

    repairs.set_issue(
        hass,
        repairs.cover_issue_id(repairs.ISSUE_COMMAND_FAILURES, "gone"),
        True,
        translation_key="command_failures",
        placeholders={"cover": "x"},
    )
    keep = repairs.hub_issue_id(hub_entry.entry_id, repairs.ISSUE_WEATHER_UNAVAILABLE)
    repairs.set_issue(hass, keep, True, translation_key="weather_unavailable")
    _delete_stale_issues(hass, hub_entry, owned_issue_ids={keep})
    reg = ir.async_get(hass)
    assert reg.async_get_issue(const.DOMAIN, "command_failures_gone") is None
    assert reg.async_get_issue(const.DOMAIN, keep) is not None
