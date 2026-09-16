"""Repair-issue helpers (spec §4 Repairs). All issues are non-fixable and auto-clearing."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from . import const

ISSUE_FROST_CONFLICT = "frost_conflict"
ISSUE_WIND_UNAVAILABLE = "wind_sensor_unavailable"
ISSUE_WIND_UNIT_CHANGED = "wind_unit_changed"
ISSUE_WEATHER_UNAVAILABLE = "weather_unavailable"
ISSUE_FORECAST_FAILED = "forecast_fetch_failed"
ISSUE_SUN_MISSING = "sun_missing"
ISSUE_FROST_SOURCE_UNAVAILABLE = "frost_source_unavailable"
ISSUE_DOOR_UNAVAILABLE = "door_sensor_unavailable"
ISSUE_ROOM_UNUSABLE = "room_sensor_unusable"
ISSUE_COVER_UNSUPPORTED = "cover_unsupported"
ISSUE_RULE_SKIPPED = "rule_skipped"
ISSUE_COMMAND_FAILURES = "command_failures"

_RUNTIME_KINDS = (
    ISSUE_FROST_CONFLICT,
    ISSUE_WIND_UNAVAILABLE,
    ISSUE_WIND_UNIT_CHANGED,
    ISSUE_WEATHER_UNAVAILABLE,
    ISSUE_FORECAST_FAILED,
    ISSUE_SUN_MISSING,
    ISSUE_FROST_SOURCE_UNAVAILABLE,
    ISSUE_DOOR_UNAVAILABLE,
    ISSUE_ROOM_UNUSABLE,
    ISSUE_COVER_UNSUPPORTED,
    ISSUE_RULE_SKIPPED,
    ISSUE_COMMAND_FAILURES,
)
SETUP_PREFIXES = (
    "missing_entity_",
    "missing_profile_",
    "broken_cover_config_",
    "broken_profile_config_",
)
ENTRY_ISSUE_PREFIXES: tuple[str, ...] = tuple(f"{k}_" for k in _RUNTIME_KINDS) + SETUP_PREFIXES


def hub_issue_id(entry_id: str, kind: str) -> str:
    return f"{kind}_{entry_id}"


def cover_issue_id(kind: str, subentry_id: str) -> str:
    return f"{kind}_{subentry_id}"


def set_issue(
    hass: HomeAssistant,
    issue_id: str,
    active: bool,
    *,
    translation_key: str,
    placeholders: dict[str, str] | None = None,
    severity: ir.IssueSeverity = ir.IssueSeverity.WARNING,
) -> bool:
    """Create the issue when active, delete it otherwise. Returns `active`."""
    if active:
        ir.async_create_issue(
            hass,
            const.DOMAIN,
            issue_id,
            is_fixable=False,
            severity=severity,
            translation_key=translation_key,
            translation_placeholders=placeholders,
        )
    else:
        ir.async_delete_issue(hass, const.DOMAIN, issue_id)
    return active


def entry_owned_issue_ids(
    entry: ConfigEntry,
    wanted_entities: Iterable[str],
    cover_ids: Iterable[str],
    profile_ids: Iterable[str],
) -> set[str]:
    """Every issue id this entry may legitimately hold right now (for the stale sweep)."""
    covers, profiles = list(cover_ids), list(profile_ids)
    ids = {f"missing_entity_{entry.entry_id}_{e}" for e in wanted_entities}
    ids.update(
        hub_issue_id(entry.entry_id, k)
        for k in (
            ISSUE_WIND_UNAVAILABLE,
            ISSUE_WEATHER_UNAVAILABLE,
            ISSUE_FORECAST_FAILED,
            ISSUE_SUN_MISSING,
            ISSUE_FROST_SOURCE_UNAVAILABLE,
        )
    )
    for cover_id in covers:
        ids.update(
            cover_issue_id(k, cover_id)
            for k in (
                "missing_profile",
                "broken_cover_config",
                ISSUE_FROST_CONFLICT,
                ISSUE_WIND_UNIT_CHANGED,
                ISSUE_DOOR_UNAVAILABLE,
                ISSUE_ROOM_UNUSABLE,
                ISSUE_COVER_UNSUPPORTED,
                ISSUE_COMMAND_FAILURES,
            )
        )
    for profile_id in profiles:
        ids.update(
            cover_issue_id(k, profile_id) for k in ("broken_profile_config", ISSUE_RULE_SKIPPED)
        )
    return ids
