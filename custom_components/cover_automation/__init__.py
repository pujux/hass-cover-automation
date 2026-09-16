"""Cover Automation: drives roller covers open or closed from sun, weather, room temperature,
wind, frost, door sensors and schedules. Setup order per spec §5.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from homeassistant.components.weather.const import WeatherEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir

from . import const
from .config_map import CoverBindings, HubConfig, cover_config, hub_config, profile
from .engine.model import CoverConfig, CoverPersisted
from .engine.schedule import Profile
from .store import CoverAutomationStore

_LOGGER = logging.getLogger(__name__)
SUN_ENTITY = "sun.sun"


@dataclass(slots=True)
class CoverAutomationData:
    hub: HubConfig
    covers: dict[str, tuple[CoverConfig, CoverBindings]]
    profiles: dict[str, Profile]
    store: CoverAutomationStore
    hub_device_id: str
    missing_entities: list[str] = field(default_factory=list)


type CoverAutomationConfigEntry = ConfigEntry[CoverAutomationData]


def _missing_entity_issue_id(entry: ConfigEntry, entity_id: str) -> str:
    return f"missing_entity_{entry.entry_id}_{entity_id}"


def _validate_required_entities(hass: HomeAssistant, hub: HubConfig) -> None:
    """Weather (with daily forecast) and sun.sun must exist, otherwise retry (spec §5)."""
    weather = hass.states.get(hub.weather_entity)
    if weather is None or weather.state in ("unavailable", "unknown"):
        raise ConfigEntryNotReady(f"weather entity {hub.weather_entity} is not available yet")
    features = int(weather.attributes.get("supported_features", 0) or 0)
    if not features & WeatherEntityFeature.FORECAST_DAILY:
        msg = f"weather entity {hub.weather_entity} reports no daily forecast yet"
        raise ConfigEntryNotReady(msg)
    if hass.states.get(SUN_ENTITY) is None:
        raise ConfigEntryNotReady("sun.sun is not available yet")


def _check_optional_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    hub: HubConfig,
    covers: dict[str, tuple[CoverConfig, CoverBindings]],
) -> list[str]:
    """Missing optional sensors do not block setup; they raise repair issues (spec §5)."""
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
    return missing


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


async def async_setup_entry(hass: HomeAssistant, entry: CoverAutomationConfigEntry) -> bool:
    hub = hub_config(entry)
    _validate_required_entities(hass, hub)

    profiles = {
        s.subentry_id: profile(s) for s in entry.get_subentries_of_type(const.SUBENTRY_PROFILE)
    }
    covers: dict[str, tuple[CoverConfig, CoverBindings]] = {}
    for subentry in entry.get_subentries_of_type(const.SUBENTRY_COVER):
        cfg, bind = cover_config(subentry, hub)
        if cfg.profile_id is not None and cfg.profile_id not in profiles:
            _LOGGER.warning(
                "Cover %s references missing profile %s; treating as none", cfg.name, cfg.profile_id
            )
            ir.async_create_issue(
                hass,
                const.DOMAIN,
                f"missing_profile_{subentry.subentry_id}",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="missing_profile",
                translation_placeholders={"cover": cfg.name},
            )
        else:
            ir.async_delete_issue(hass, const.DOMAIN, f"missing_profile_{subentry.subentry_id}")
        covers[subentry.subentry_id] = (cfg, bind)

    hub_device_id = ensure_devices(hass, entry)
    store = CoverAutomationStore(hass, entry.entry_id)
    await store.async_load()
    for subentry_id in covers:
        store.data.covers.setdefault(subentry_id, CoverPersisted())

    entry.runtime_data = CoverAutomationData(
        hub=hub,
        covers=covers,
        profiles=profiles,
        store=store,
        hub_device_id=hub_device_id,
        missing_entities=_check_optional_entities(hass, entry, hub, covers),
    )

    await hass.config_entries.async_forward_entry_setups(entry, const.PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Hub options and subentry changes arrive here; a reload rebuilds runtime_data (spec §3)."""
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: CoverAutomationConfigEntry) -> bool:
    await entry.runtime_data.store.async_save()
    return await hass.config_entries.async_unload_platforms(entry, const.PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await CoverAutomationStore(hass, entry.entry_id).async_remove()


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # downgrade from a newer major version is not supported
    return entry.version <= 1


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting devices whose subentry no longer exists (spec §5)."""
    if device.config_subentry_id is None:
        return (const.DOMAIN, entry.entry_id) not in device.identifiers
    return device.config_subentry_id not in entry.subentries
