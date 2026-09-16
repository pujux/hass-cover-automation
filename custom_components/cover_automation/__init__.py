"""Cover Automation: drives roller covers open or closed from sun, weather, room temperature,
wind, frost, door sensors and schedules. Setup order per spec §5.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from functools import partial

from homeassistant.components.weather.const import WeatherEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType

from . import const, repairs
from .config_map import CoverBindings, HubConfig, cover_config, hub_config, profile
from .controller import CoverAutomationController
from .engine.model import CoverConfig, CoverPersisted
from .engine.schedule import Profile
from .services import async_setup_services
from .store import CoverAutomationStore

_LOGGER = logging.getLogger(__name__)
SUN_ENTITY = "sun.sun"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(const.DOMAIN)


@dataclass(slots=True)
class CoverAutomationData:
    hub: HubConfig
    covers: dict[str, tuple[CoverConfig, CoverBindings]]
    profiles: dict[str, Profile]
    store: CoverAutomationStore
    hub_device_id: str
    controller: CoverAutomationController
    missing_entities: list[str] = field(default_factory=list)


type CoverAutomationConfigEntry = ConfigEntry[CoverAutomationData]


def _missing_entity_issue_id(entry: ConfigEntry, entity_id: str) -> str:
    return f"missing_entity_{entry.entry_id}_{entity_id}"


def _missing_profile_issue_id(subentry_id: str) -> str:
    return repairs.cover_issue_id("missing_profile", subentry_id)


def _broken_cover_config_issue_id(subentry_id: str) -> str:
    return repairs.cover_issue_id("broken_cover_config", subentry_id)


def _broken_profile_config_issue_id(subentry_id: str) -> str:
    return repairs.cover_issue_id("broken_profile_config", subentry_id)


def _validate_required_entities(hass: HomeAssistant, hub: HubConfig) -> None:
    """Weather (with daily forecast) and sun.sun must exist, otherwise retry (spec §5)."""
    weather = hass.states.get(hub.weather_entity)
    if weather is None or weather.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        raise ConfigEntryNotReady(f"weather entity {hub.weather_entity} is not available yet")
    features = int(weather.attributes.get("supported_features", 0) or 0)
    if not features & WeatherEntityFeature.FORECAST_DAILY:
        msg = f"weather entity {hub.weather_entity} reports no daily forecast yet"
        raise ConfigEntryNotReady(msg)
    if hass.states.get(SUN_ENTITY) is None:
        raise ConfigEntryNotReady("sun.sun is not available yet")


def _optional_entity_ids(
    hub: HubConfig, covers: dict[str, tuple[CoverConfig, CoverBindings]]
) -> list[str]:
    """Entities that are nice to have; missing ones get a repair but never block setup (spec §5)."""
    wanted: list[str] = [
        e
        for e in (
            hub.wind_sensor,
            hub.outdoor_temperature_sensor,
            hub.sunny_override_entity,
            hub.hot_override_entity,
        )
        if e
    ]
    for _cfg, bind in covers.values():
        wanted.extend(e for e in (bind.cover_entity, bind.door_sensor, bind.room_sensor) if e)
    return wanted


def _entry_issue_ids(entry: ConfigEntry, wanted_entities: Iterable[str]) -> set[str]:
    """Issue ids this entry currently owns: still-configured entities and subentries."""
    return repairs.entry_owned_issue_ids(
        entry,
        wanted_entities,
        [s.subentry_id for s in entry.get_subentries_of_type(const.SUBENTRY_COVER)],
        [s.subentry_id for s in entry.get_subentries_of_type(const.SUBENTRY_PROFILE)],
    )


def _delete_stale_issues(
    hass: HomeAssistant, entry: ConfigEntry, owned_issue_ids: set[str]
) -> None:
    """Delete issues HA never clears on its own: ones whose reference has disappeared.

    `missing_entity_*`, `missing_profile_*`, `broken_cover_config_*`, `broken_profile_config_*`
    and every runtime issue kind in `repairs.ENTRY_ISSUE_PREFIXES` are only ever created or
    cleared for entities/subentries that are still configured (see
    `_async_check_optional_entities` and `async_setup_entry`). Once a reference disappears --
    the wind sensor is cleared, a cover subentry is deleted, or the whole entry is removed (call
    with `owned_issue_ids=set()`) -- nothing else deletes its issue, so it would otherwise linger
    in Repairs forever. Matching these by a bare prefix (not scoped to this entry's current
    subentries) is safe because the integration is `single_config_entry`: only one hub entry
    ever exists.
    """
    registry = ir.async_get(hass)
    entity_prefix = f"missing_entity_{entry.entry_id}_"
    stale = [
        issue_id
        for domain, issue_id in registry.issues
        if domain == const.DOMAIN
        and issue_id not in owned_issue_ids
        and (
            issue_id.startswith(entity_prefix) or issue_id.startswith(repairs.ENTRY_ISSUE_PREFIXES)
        )
    ]
    for issue_id in stale:
        ir.async_delete_issue(hass, const.DOMAIN, issue_id)


async def _async_check_optional_entities(
    hass: HomeAssistant,
    entry: CoverAutomationConfigEntry,
    hub: HubConfig,
    covers: dict[str, tuple[CoverConfig, CoverBindings]],
) -> None:
    """Create/clear missing-entity repairs once HA has started (spec §5).

    Deferred past startup so a slower-starting integration (MQTT, ESPHome, a template sensor)
    doesn't get flagged missing before its entity has had a chance to register its state.
    """
    wanted = _optional_entity_ids(hub, covers)
    missing = [e for e in wanted if hass.states.get(e) is None]
    for entity_id in wanted:
        issue_id = _missing_entity_issue_id(entry, entity_id)
        if entity_id in missing:
            ir.async_create_issue(
                hass,
                const.DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="missing_entity",
                translation_placeholders={"entity_id": entity_id},
            )
        else:
            ir.async_delete_issue(hass, const.DOMAIN, issue_id)
    if getattr(entry, "runtime_data", None) is None:
        # The entry was unloaded/removed while this deferred check was pending.
        return
    entry.runtime_data.missing_entities = missing


@callback
def ensure_devices(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Create the hub device and one device per cover subentry (2026.8 rules).

    Returns the hub device id.
    """
    registry = dr.async_get(hass)
    hub_device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=None,
        identifiers={(const.DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Cover Automation",
        model="Hub",
        entry_type=dr.DeviceEntryType.SERVICE,
    )
    for subentry in entry.get_subentries_of_type(const.SUBENTRY_COVER):
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=subentry.subentry_id,
            identifiers={(const.DOMAIN, subentry.subentry_id)},
            name=str(subentry.data.get(const.CONF_NAME) or subentry.title),
            manufacturer="Cover Automation",
            model="Cover",
            via_device_id=hub_device.id,
        )
    return hub_device.id


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the domain services once, independent of any config entry (spec §4)."""
    del config
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CoverAutomationConfigEntry) -> bool:
    try:
        hub = hub_config(entry)
    except KeyError as err:
        msg = f"Cover Automation hub is missing required configuration: {err}"
        raise ConfigEntryError(msg) from err
    _validate_required_entities(hass, hub)

    profiles: dict[str, Profile] = {}
    for subentry in entry.get_subentries_of_type(const.SUBENTRY_PROFILE):
        profile_issue_id = _broken_profile_config_issue_id(subentry.subentry_id)
        try:
            profiles[subentry.subentry_id] = profile(subentry)
        except (ValueError, KeyError, TypeError) as err:
            _LOGGER.warning(
                "Schedule profile %s is misconfigured and will be skipped: %s",
                subentry.title,
                err,
            )
            ir.async_create_issue(
                hass,
                const.DOMAIN,
                profile_issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="broken_profile_config",
                translation_placeholders={"profile": subentry.title, "error": str(err)},
            )
        else:
            ir.async_delete_issue(hass, const.DOMAIN, profile_issue_id)

    covers: dict[str, tuple[CoverConfig, CoverBindings]] = {}
    for subentry in entry.get_subentries_of_type(const.SUBENTRY_COVER):
        broken_issue_id = _broken_cover_config_issue_id(subentry.subentry_id)
        try:
            cfg, bind = cover_config(subentry, hub)
        except (ValueError, KeyError, TypeError) as err:
            _LOGGER.warning(
                "Cover %s is misconfigured and will be skipped: %s", subentry.title, err
            )
            ir.async_create_issue(
                hass,
                const.DOMAIN,
                broken_issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="broken_cover_config",
                translation_placeholders={"cover": subentry.title, "error": str(err)},
            )
            continue
        ir.async_delete_issue(hass, const.DOMAIN, broken_issue_id)

        issue_id = _missing_profile_issue_id(subentry.subentry_id)
        if cfg.profile_id is not None and cfg.profile_id not in profiles:
            _LOGGER.warning(
                "Cover %s references missing profile %s; treating as none", cfg.name, cfg.profile_id
            )
            cfg = replace(cfg, profile_id=None)
            bind = replace(bind, profile_id=None)
            ir.async_create_issue(
                hass,
                const.DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="missing_profile",
                translation_placeholders={"cover": cfg.name},
            )
        else:
            ir.async_delete_issue(hass, const.DOMAIN, issue_id)
        covers[subentry.subentry_id] = (cfg, bind)

    wanted_entities = _optional_entity_ids(hub, covers)
    _delete_stale_issues(hass, entry, _entry_issue_ids(entry, wanted_entities))

    hub_device_id = ensure_devices(hass, entry)
    store = CoverAutomationStore(hass, entry.entry_id)
    await store.async_load()
    for subentry_id in covers:
        store.data.covers.setdefault(subentry_id, CoverPersisted())
    # "Removed" means the subentry is gone, not that it currently fails to parse: a cover
    # that is temporarily broken (see the broken_cover_config repair above) must keep its
    # persisted state, so prune against every cover subentry id, not just the ones in `covers`.
    store.prune({s.subentry_id for s in entry.get_subentries_of_type(const.SUBENTRY_COVER)})

    # The controller is built before the platforms are forwarded: every platform reads
    # `runtime_data.controller` in its `async_setup_entry`. It only starts (subscriptions,
    # timers, first evaluation) once Home Assistant has finished starting, so the engines
    # never see half-restored states.
    controller = CoverAutomationController(
        hass,
        entry,
        hub=hub,
        covers=covers,
        profiles=profiles,
        store=store,
        hub_device_id=hub_device_id,
    )
    entry.runtime_data = CoverAutomationData(
        hub=hub,
        covers=covers,
        profiles=profiles,
        store=store,
        hub_device_id=hub_device_id,
        controller=controller,
    )

    await hass.config_entries.async_forward_entry_setups(entry, const.PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    entry.async_on_unload(
        async_at_started(
            hass, partial(_async_check_optional_entities, entry=entry, hub=hub, covers=covers)
        )
    )
    entry.async_on_unload(async_at_started(hass, controller.async_start_job))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Hub options and subentry changes arrive here; a reload rebuilds runtime_data (spec §3)."""
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: CoverAutomationConfigEntry) -> bool:
    # Stop first: cancels every subscription and timer, drains in-flight evaluations and
    # saves the Store, so nothing can write to it (or command a cover) afterwards.
    await entry.runtime_data.controller.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, const.PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await CoverAutomationStore(hass, entry.entry_id).async_remove()
    _delete_stale_issues(hass, entry, owned_issue_ids=set())


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # newer major versions cannot be downgraded
    if entry.version > 1:
        return False
    if entry.minor_version < 1:
        hass.config_entries.async_update_entry(entry, version=1, minor_version=1)
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting devices whose subentry no longer exists (spec §5)."""
    if device.config_subentry_id is None:
        return (const.DOMAIN, entry.entry_id) not in device.identifiers
    return device.config_subentry_id not in entry.subentries
