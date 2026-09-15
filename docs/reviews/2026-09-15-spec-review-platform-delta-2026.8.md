# Platform review delta for HA 2026.8

Verified against `homeassistant==2026.8.3` (newest 2026.8.x on PyPI), downloaded and unpacked to `…/scratchpad/ha8/homeassistant`, diffed against the 2026.4.1 tree at `…/scratchpad/ha/homeassistant`. All line numbers below are from the **2026.8.3** tree.

## 1. Summary

Moving the floor to 2026.8 does not invalidate the review: all seven MAJOR findings and thirteen of the fifteen MINOR ones stand unchanged, and no finding becomes obsolete. The one substantive area that moved is the **device registry**, which was rewritten in this window: a device now belongs to exactly one config entry and at most one subentry (`DeviceEntry.config_entry_id: str`, `config_subentry_id: str | None`), `via_device` is deprecated in favour of `via_device_id` with removal in **2027.8**, and an unresolvable `via_device_id` now **raises `DeviceInfoError`** instead of logging — which turns F6 from a silent link-drop into a hard entity-add failure, so the fix becomes mandatory rather than advisable. F1 is reinforced rather than relaxed: both reload guards are still in place verbatim, and 2026.8 adds a **new** `report_usage` on the *hub* path (`ConfigFlow.async_update_reload_and_abort` with an update listener) that **breaks in 2026.12** — inside the target version's own service life. Two small wins arrived: `ConfigEntry.get_subentries_of_type()` cleans up the profile dropdown, and `async_get_device_id_by_identifier()` is the sanctioned way to build `via_device_id`. Conditional field visibility still does not exist in any form, so F19's fixed-slot form design is still the only option; integration triggers/conditions are now clearly mature (46/41 core integrations) but remain outside the chosen feature scope.

## 2. Status of F1–F22

| Id | Status | Evidence (2026.8.3) |
|----|--------|---------------------|
| F1 — subentry reconfigure vs update listener | **Still valid, reinforced** | `config_entries.py:3858` still raises `ValueError("Cannot update and reload entry with update listeners")`; `config_entries.py:3944-3948` now raises `"Config entry update listeners should not be used with OptionsFlowWithReload"`. See new F25. |
| F2 — unit handling | Still valid | `components/weather/__init__.py:707-708` unchanged: `to_temp_unit = self._temperature_unit`. |
| F3 — cover OPEN/CLOSE features | Still valid | `components/cover/__init__.py:103,107` still register with `[CoverEntityFeature.OPEN]` / `[CoverEntityFeature.CLOSE]`; `helpers/service.py:744-753` still raises `ServiceNotSupported`. |
| F4 — `reset_override` with no target | **Still valid, fix updated** | `helpers/service.py:693` `ENTITY_MATCH_ALL` unchanged. The helper to use for the plain-service fix moved — see new F28. |
| F5 — `EVENT_HOMEASSISTANT_STARTED` on reload | Still valid | `helpers/start.py:75` `async_at_started` unchanged. |
| F6 — hub device before cover devices | **Changed (worse / now enforced)** | `via_device_id` that is not a registered id now raises `DeviceInfoError` (`helpers/device_registry.py:1865-1871`), which `helpers/entity_platform.py:960-967` catches by aborting the entity add. See new F23. |
| F7 — RestoreEntity ordering/ownership | Still valid | `helpers/restore_state.py:30,33` (`STATE_DUMP_INTERVAL` 15 min, `STATE_EXPIRATION` 7 days); `helpers/entity_platform.py:507` still `await asyncio.gather(*pending)`. |
| F8 — `unavailable` as enum option | Still valid | `helpers/entity.py:1063` `_stringify_state` unchanged; `components/sensor/__init__.py:711` still raises for values outside `options`. |
| F9 — status-sensor attribute recorder bloat | Still valid | `helpers/entity.py:551-557` `_unrecorded_attributes` unchanged. |
| F10 — logbook prerequisites | Still valid | `components/logbook/__init__.py:145,159` `_process_logbook_platform` → `platform.async_describe_events` unchanged. |
| F11 — repairs placeholders / ignored issues | Still valid | `helpers/issue_registry.py:23,339,415` unchanged. |
| F12 — entity categories, unique ids | Still valid | `const.py` `class EntityCategory` docstring unchanged ("Not be included in indirect service calls to devices or areas"). |
| F13 — entity rename breaks the pointer | Still valid | `helpers/entity_registry.py:169,2006` `old_entity_id` unchanged. |
| F14 — forecast "today" / daily capability | Still valid | `components/weather/__init__.py:214-222` (`SupportsResponse.ONLY`, `required_features`) and `helpers/service.py:744-750` any-of test unchanged. |
| F15 — simulation mode self-triggering | Still valid | Spec-internal; no platform change. |
| F16 — `desired_at_manual_move` / frost-vs-door contradictions | Still valid | Spec-internal. |
| F17 — sun cadence & hysteresis framing | Still valid | `components/sun/entity.py:80-87` `_PHASE_UPDATES` byte-identical; `:93` `_unrecorded_attributes` unchanged. |
| F18 — timer helpers / DST | Still valid | `helpers/event.py:1552` `async_call_later`, `:1853` `async_track_time_change`; `const.py:273` `EVENT_CORE_CONFIG_UPDATE`. |
| F19 — profile dropdown + 4-rule form | **Still valid, one part easier** | No conditional visibility anywhere (see new F26); `data_entry_flow.py` `class section` still only supports `collapsed`. The dropdown gets a helper — new F27. |
| F20 — missing lifecycle/manifest pieces | **Still valid, one part refined** | `loader.py:966` `single_config_entry`, `helpers/storage.py:238,620` Store versioning unchanged. Device cleanup on subentry removal changed — new F29. |
| F21 — translations location / key shapes | Still valid | `helpers/translation.py:101` still loads only `file_path / "translations" / <lang>.json`. |
| F22 — engine purity, pinning, CI | **Changed (version pins)** | Python `>=3.14.2` (unchanged from 2026.4.1); the matching test lib is now `pytest-homeassistant-custom-component==0.13.357`. Action refs still valid. |

## 3. New findings that only exist with 2026.8

### F23 — `via_device` is deprecated and `via_device_id` now fails hard
- **Severity:** MAJOR
- **Spec section:** §3 "Cover subentry … creates its own device linked `via_device` to the hub device"
- **Problem.** The spec names `via_device` explicitly. In 2026.8 that keyword is deprecated with a stated removal in **HA Core 2027.8**, and resolving it is ambiguous by design because identifiers are no longer globally unique. The replacement, `via_device_id`, takes a device *id*, which means the hub device must already be registered — and if it is not, the call now raises instead of logging, and `entity_platform` responds by dropping the entity entirely.
- **Evidence.** `helpers/device_registry.py:138` — `via_device: tuple[str, str]  # Deprecated, use via_device_id instead`; `:1768-1770` — *"via_device is deprecated and will be removed in HA Core 2027.8, use via_device_id instead"*; `:1795-1798` — passing both raises `HomeAssistantError`; `:1863-1871` — an unresolvable `via_device_id` raises `DeviceInfoError(... "is not a registered device id")`; `helpers/entity_platform.py:960-967` — `except dr.DeviceInfoError` → `self.logger.error("… Not adding entity with invalid device info: …")` + `entity.add_to_platform_abort()`. The legacy `via_device` tuple path (`:1966-1993`) still only logs when unresolvable.
- **Fix.** Replace `via_device` with `via_device_id` in §3, and pre-create devices in `async_setup_entry` (pattern in §4 below). Build the id with `dr.async_get_device_id_by_identifier(hass, (DOMAIN, entry.entry_id), config_entry_id=entry.entry_id)` (`helpers/device_registry.py:3153`).

### F24 — One device = one config entry and at most one subentry
- **Severity:** MAJOR
- **Spec section:** §3 (hub device + one device per cover subentry); §4 (which entities go on which device)
- **Problem.** 2026.8 collapsed the many-to-many device↔entry model. A cover device now belongs to exactly one subentry, and *re-registering* an existing device under a different subentry silently moves it today and will raise in 2027.8. Concretely: if any hub-level entity (added with `config_subentry_id=None`) ever declares `DeviceInfo` whose identifiers match a cover device, that cover device is yanked out of its subentry — and subentry deletion would then no longer clean it up.
- **Evidence.** `helpers/device_registry.py:399-402` — `config_entry_id: str = attr.ib()` / `config_subentry_id: str | None = attr.ib(default=None)`; `:454-491` — `config_entries`, `config_entries_subentries` and `primary_config_entry` are now documented "Deprecated compatibility shim"s; `:1937-1955` — `report_usage("assigns an existing device to a different config subentry … this silently moves the device. A device belongs to one subentry …", breaks_in_ha_version="2027.8.0")`; `:1558-1568` — `async_get_device_by_identifier(identifier, config_entry_id)` with the note *"Identifiers are unique within a config entry"*.
- **Fix.** State in §3 that identifiers are `(DOMAIN, entry.entry_id)` for the hub and `(DOMAIN, subentry_id)` for each cover — disjoint by construction; that hub entities are added with `config_subentry_id=None` and per-cover entities always with their own `subentry_id`; and that nothing may ever attach a hub entity to a cover device. Read a device's subentry as `device.config_subentry_id` (never `config_entries_subentries`, which is a deprecated shim).

### F25 — Hub reconfigure + update listener breaks in 2026.12, not 2027.x
- **Severity:** MAJOR
- **Spec section:** §5 "Config changes"; §3 (hub reconfigure/options)
- **Problem.** F1 covered the *subentry* flow. 2026.8 extends the same rule to the **hub** flow, and on a much shorter clock: `ConfigFlow.async_update_reload_and_abort` on an entry that has update listeners now reports usage with removal in **2026.12.0** — four releases after the proposed minimum. A spec that ships with both an update listener and a reloading hub reconfigure step would start logging immediately and break within the target version's own service life.
- **Evidence.** `config_entries.py:3570-3576` — `if entry.update_listeners: report_usage("has an update listener and should use it for scheduling a reload", core_behavior=ReportBehavior.LOG, breaks_in_ha_version="2026.12.0", …)` inside `async_update_reload_and_abort`; the same report at `config_entries.py:3160-3166` on the `_abort_if_unique_id_configured` data-update path.
- **Fix.** Make §5 explicit and uniform: **one** update listener owns all reloading (`hass.config_entries.async_schedule_reload`); the hub config flow's reconfigure step ends with `async_update_and_abort`, the hub options flow is plain `OptionsFlow`, and every subentry flow ends with `async_update_and_abort`. That single rule now satisfies three separate guards (2026.12, plus the two hard `ValueError`s).

### F26 — No conditional field visibility exists; `show_advanced_options` is going away
- **Severity:** MINOR
- **Spec section:** §3 (wind fields hidden when the hub has no wind sensor; rule fields that depend on `time_mode`)
- **Problem.** Worth recording as settled: there is still no backend mechanism for showing or hiding a field based on another field's value, so per-field conditionality within a step remains impossible. The one adjacent mechanism, advanced-field marking, is now deprecated.
- **Evidence.** `helpers/selector.py` — no `visible`, `depends_on` or equivalent key anywhere; `grep -rn "depends_on" helpers/ data_entry_flow.py` returns nothing. `data_entry_flow.py` — `show_advanced_options` is wrapped in `@deprecated_function("a user friendly way to present additional options in the UI, for example a section", breaks_in_ha_version="2027.6")`, now hard-returns `True`, and the advanced-field filter was deleted from `add_suggested_values_to_schema`. `class section` still exposes only `collapsed`. New selectors in this window are `AutomationBehaviorSelector`, `EntityWithDeviceFilterSelector` and `SerialPortSelector`; there is **no** `DeviceClassSelector`/`StateClassSelector` (those are 2026.9) and **no** repeater/list selector.
- **Fix.** Keep F19's design (four collapsed `section`s, all fields optional, cross-field validation in the step handler). Hub-dependent conditionality — omitting the wind fields when the hub has no wind sensor — is still fine because the schema is built per flow run. Do not plan on marking anything `advanced`.

### F27 — `ConfigEntry.get_subentries_of_type()` simplifies the profile dropdown
- **Severity:** MINOR
- **Spec section:** §3 `schedule_profile`; §6 platform setup loops
- **Problem/opportunity.** F19's dropdown and every platform's "iterate the cover subentries" loop previously needed a hand-rolled filter over `entry.subentries.values()`.
- **Evidence.** `config_entries.py:643-649` — `def get_subentries_of_type(self, subentry_type: str) -> list[ConfigSubentry]`.
- **Fix.** Use `self._get_entry().get_subentries_of_type("profile")` to build the `SelectSelector` options (value = `subentry.subentry_id`, label = `subentry.title`) and `entry.get_subentries_of_type("cover")` in each platform's `async_setup_entry`. The empty-list case from F19 still has to be handled.

### F28 — The service target-extraction helpers you would use for F4 are deprecated
- **Severity:** MINOR
- **Spec section:** §4 "Services"
- **Problem.** F4's fix is a plain (non-entity) service that resolves its own targets. The obvious module-level helpers for that are deprecated with the `hass` argument removed in **2026.10** — before the target version is a year old.
- **Evidence.** `helpers/service.py:351,364,403,418,1011` — `@deprecated_hass_argument(breaks_in_ha_version="2026.10")` on `extract_entity_ids`, `async_extract_entities`, `async_extract_entity_ids`, `async_extract_config_entry_ids`, `verify_domain_control`. The current API is `helpers/target.py:158` `async_extract_referenced_entity_ids(...)` with `helpers/target.py:107` `TargetSelectorData` and `:117` `SelectedEntities`.
- **Fix.** Implement `cover_automation.reset_override` with `hass.services.async_register` and resolve targets via `homeassistant.helpers.target.async_extract_referenced_entity_ids(hass, TargetSelectorData(call.data))`, treating "nothing referenced" as "all covers".

### F29 — Subentry deletion now removes its devices outright
- **Severity:** MINOR
- **Spec section:** §5 (cleanup); refines F20
- **Problem/clarification.** In 2026.4 removing a subentry only detached it from devices. In 2026.8 it deletes them, which is what the spec wants and makes `async_remove_config_entry_device` genuinely optional.
- **Evidence.** `helpers/device_registry.py:3069-3080` — `async_clear_config_subentry(config_entry_id, config_subentry_id, domain=None)` iterates the entry's devices and calls `self.async_remove_device(device.id)` for each one whose `config_subentry_id` matches; `config_entries.py` now calls it as `dev_reg.async_clear_config_subentry(entry.entry_id, subentry_id, entry.domain)`.
- **Fix.** Say in §5 that deleting a cover subentry removes its device and entities automatically; keep `async_remove_config_entry_device` only as a manual escape hatch for stale devices, returning `True` when the device's subentry no longer exists.

### F30 — Integration triggers/conditions are mature in 2026.8, but out of scope
- **Severity:** MINOR (informational)
- **Spec section:** §4 (observability surface)
- **Problem/assessment.** The coordinator asked whether the "do not use yet" caveat still applies. There is **no** in-code experimental or do-not-use marker in 2026.8, and adoption is broad and growing.
- **Evidence.** `helpers/trigger.py:240` `class Trigger(abc.ABC)`, `:1353-1359` `class TriggerProtocol` whose docstring reads *"New implementations should only implement async_get_triggers"*; `helpers/condition.py:400` `class Condition`, `:1176` `class ConditionProtocol`. Core integrations shipping `triggers.yaml` went **42 → 46** and `conditions.yaml` **36 → 41** between 2026.4.1 and 2026.8.3 (`components/cover/triggers.yaml` among them).
- **Fix.** None for this spec — triggers and conditions are not in `docs/feature-selection.md`, and the current observability surface (status sensor + logbook event + services) already covers the chosen features. Record it as a deliberate non-goal so it is not re-litigated; nothing in the design blocks adding a `trigger.py` later.

### F31 — Attribute constants were renamed to `*EntityStateAttribute` enums (no action needed)
- **Severity:** MINOR (informational)
- **Spec section:** §1.1 (reading `current_position`); §2 (reading the weather entity's `temperature`)
- **Evidence.** `components/cover/const.py` — new `class CoverEntityStateAttribute(StrEnum)` with `IS_CLOSED`/`CURRENT_POSITION`/`CURRENT_TILT_POSITION`; `components/weather/__init__.py` now writes `WeatherEntityStateAttribute.TEMPERATURE_UNIT` etc. The legacy names are unchanged and still exported (`components/cover/const.py:7` `ATTR_CURRENT_POSITION = "current_position"`, re-exported at `components/cover/__init__.py:34,64`; `components/weather/const.py:42` `ATTR_WEATHER_TEMPERATURE = "temperature"`), with no deprecation wrapper.
- **Fix.** Attribute *string values* are unchanged, so reading `state.attributes["current_position"]` is safe. Prefer the new enums in new code. Note also that `is_closed` is present in cover state attributes in both versions — usable as a cross-check in §1.1's classifier, though `state` plus `current_position` remains sufficient.

### F32 — Device identifiers are now scoped per config entry
- **Severity:** MINOR
- **Spec section:** §3; §4 diagnostics
- **Problem.** Global identifier uniqueness is gone, and the global lookup is now an ambiguity-resolving best-effort with documented fallbacks.
- **Evidence.** `helpers/device_registry.py:1518-1554` — `async_get_device` docstring: *"Identifiers and connections are unique per config entry … one owned by the calling integration is preferred, falling back to the first match."* `:1558` `async_get_device_by_identifier(identifier, config_entry_id)` and `:1580` `async_get_device_by_connection(...)` are the unambiguous forms; `:3153` `async_get_device_id_by_identifier(hass, identifier, *, config_entry_id)` raises `ValueError` if absent.
- **Fix.** Always use the `config_entry_id`-scoped lookups in the controller and in `diagnostics.py`; never `async_get_device(identifiers=...)`.

## 4. Device-registry pattern for 2026.8

Create both device tiers explicitly in `async_setup_entry`, hub first, **before** `async_forward_entry_setups` — this satisfies F6, F23 and F24 at once.

```python
from homeassistant.helpers import device_registry as dr

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    dev_reg = dr.async_get(hass)

    # 1. Hub device: no subentry. config_subentry_id=None is explicit and valid.
    hub_device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=None,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Cover Automation",
        manufacturer="Cover Automation",
        entry_type=dr.DeviceEntryType.SERVICE,
    )

    # 2. One device per cover subentry, linked to the hub by device *id*.
    #    Equivalent to: dr.async_get_device_id_by_identifier(
    #        hass, (DOMAIN, entry.entry_id), config_entry_id=entry.entry_id)
    for subentry in entry.get_subentries_of_type("cover"):
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=subentry.subentry_id,
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            via_device_id=hub_device.id,          # NOT via_device=(DOMAIN, ...)
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))   # F1 / F25
    entry.async_on_unload(async_at_started(hass, _async_first_evaluation))  # F5
    return True
```

Entities then attach to the pre-created devices without re-declaring identity, which keeps them clear of the F24 "silently moves the device" path:

```python
# sensor.py — per-cover platform setup
for subentry in entry.get_subentries_of_type("cover"):
    async_add_entities(
        [CoverStatusSensor(controller, subentry)],
        config_subentry_id=subentry.subentry_id,   # must match the device's subentry
    )

# In the entity:
self._attr_has_entity_name = True
self._attr_unique_id = f"{subentry.subentry_id}_status"
self._attr_device_info = dr.DeviceInfo(identifiers={(DOMAIN, subentry.subentry_id)})
# No via_device / via_device_id here — the link is already on the device.

# Hub entities: async_add_entities([...]) with no config_subentry_id (defaults to None),
# device_info identifiers={(DOMAIN, entry.entry_id)}.
```

Reading a device's subentry, and the scoped lookup:

```python
device = dr.async_get(hass).async_get_device_by_identifier(
    (DOMAIN, subentry_id), entry.entry_id
)
subentry_id = device.config_subentry_id     # str | None — NOT config_entries_subentries
entry_id    = device.config_entry_id        # str        — NOT config_entries / primary_config_entry
```

Declaring `DeviceInfo(identifiers=...)` from an entity **still works** and still creates the device implicitly (`helpers/entity_platform.py:951-970` forwards `device_info` straight into `async_get_or_create` with the platform's `config_subentry_id`). Pre-creating is required only because `via_device_id` must resolve — and because pre-creation makes each device's subentry ownership explicit, which is what 2026.8 now enforces.

## 5. Verification log (2026.8.3, `file:line`)

**Device registry (rewritten)**
- `helpers/device_registry.py:138-139` — `via_device: tuple[str, str]  # Deprecated, use via_device_id instead` / `via_device_id: str` in `DeviceInfo`.
- `:399-402` — `DeviceEntry.config_entry_id: str`, `config_subentry_id: str | None`.
- `:454-491` — `config_entries`, `config_entries_subentries`, `primary_config_entry` documented as deprecated compatibility shims.
- `:623` — `DeletedDeviceEntry.config_subentry_id`.
- `:1518-1554` — `async_get_device` ambiguity resolution ("unique per config entry").
- `:1558-1568` / `:1580` — `async_get_device_by_identifier` / `async_get_device_by_connection`, both `config_entry_id`-scoped.
- `:1740-1771` — `async_get_or_create` signature; `config_subentry_id: str | UndefinedType | None = UNDEFINED`; via_device deprecation comment naming **2027.8**.
- `:1795-1806` — both-via forms rejected; unknown `config_subentry_id` raises.
- `:1863-1871` — `_resolve_via_device_id` failure → `DeviceInfoError`.
- `:1937-1955` — subentry re-assignment `report_usage`, `breaks_in_ha_version="2027.8.0"`.
- `:1966-1993` — deprecated `via_device` tuple resolution (still log-only).
- `:2064,2131-2190` — `async_update_device(new_config_subentry_id=...)` move semantics.
- `:3069-3090` — `async_clear_config_subentry(..., domain=None)` now calls `async_remove_device`.
- `:3153-3171` — `async_get_device_id_by_identifier(hass, identifier, *, config_entry_id)`.
- `async_get_device_and_config_entry_for_domain` **does not exist** in 2026.8.3 (`grep -rn` across the tree returns nothing); `helpers/device.py` exposes only the six `async_entity_id_to_*` / `async_device_info_to_link_*` / `async_remove_stale_devices_links_*` helpers.

**Config entries / subentries**
- `config_entries.py:643-649` — new `ConfigEntry.get_subentries_of_type`.
- `:3054` `async_get_supported_subentry_types`; `:3655` `ConfigSubentryFlowManager`; `:3732` `ConfigSubentryFlow`; `:3798` `async_update_and_abort`; `:3827` `async_update_reload_and_abort`; `:3873` `_get_entry`; `:3885` `_get_reconfigure_subentry`.
- `:3858` — `raise ValueError("Cannot update and reload entry with update listeners")` (unchanged).
- `:3944-3948` — `raise ValueError("Config entry update listeners should not be used with OptionsFlowWithReload")`; `:4045-4054` `OptionsFlowWithReload` / `automatic_reload`.
- `:3160-3166` and `:3570-3576` — **new** `report_usage("has an update listener and should use it for scheduling a reload", breaks_in_ha_version="2026.12.0")`.
- `:2662` `_async_save_and_notify`; `:2676/2686/2703` `async_add_subentry` / `async_remove_subentry` / `async_update_subentry` — update-listener notification path unchanged.
- `loader.py:966` — `single_config_entry` unchanged.

**Data entry flow / selectors**
- `data_entry_flow.py` — `class section` unchanged (`collapsed` only); `show_advanced_options` now `@deprecated_function(..., breaks_in_ha_version="2027.6")` returning `True`, advanced-field filtering removed from `add_suggested_values_to_schema`.
- `helpers/selector.py` — new: `AutomationBehaviorSelector`, `EntityWithDeviceFilterSelector`, `SerialPortSelector`. Absent: `DeviceClassSelector`, `StateClassSelector`, any repeater/list selector, any `visible`/`depends_on` key.

**Triggers / conditions**
- `helpers/trigger.py:240` `Trigger(abc.ABC)`; `:1353-1359` `TriggerProtocol.async_get_triggers` ("New implementations should only implement async_get_triggers").
- `helpers/condition.py:400` `Condition`; `:1176` `ConditionProtocol`.
- Adoption: `components/*/triggers.yaml` 42 → 46, `components/*/conditions.yaml` 36 → 41 (2026.4.1 → 2026.8.3). No experimental marker in code.

**Unchanged facts re-confirmed**
- `components/weather/__init__.py:214-222` service registration (`required_features`, `SupportsResponse.ONLY`); `:707-708` forecast unit conversion to the display unit.
- `components/sun/entity.py:80-87` `_PHASE_UPDATES`; `:93` `_unrecorded_attributes`.
- `helpers/restore_state.py:30,33`; `helpers/start.py:56,75`; `helpers/storage.py:238,620`; `helpers/translation.py:101`.
- `helpers/issue_registry.py:23,339,415`; `components/logbook/__init__.py:145,159`.
- `components/sensor/__init__.py:705,711` enum validation; `helpers/entity.py:551-557,1063`; `const.py` `EntityCategory` docstring; `const.py:273` `EVENT_CORE_CONFIG_UPDATE`.
- `components/cover/__init__.py:103,107` feature-gated services; `helpers/service.py:744-753` any-of `required_features` + `ServiceNotSupported`; `:693` `ENTITY_MATCH_ALL`; `:805` entity-service response keyed by entity_id.
- `helpers/entity_platform.py:507` `await asyncio.gather(*pending)`; `:951-970` device_info → `async_get_or_create` with `config_subentry_id`.
- `helpers/event.py:1552,1853`; `helpers/entity_registry.py:169,2006` (`old_entity_id`).

**Deprecations landing before 2027.8 that touch this design**
- `helpers/service.py:351,364,403,418,1011` — `@deprecated_hass_argument(breaks_in_ha_version="2026.10")`.
- `config_entries.py:3160-3166`, `:3570-3576` — update-listener + flow reload, **2026.12.0**.
- `data_entry_flow.py` — `show_advanced_options`, **2027.6**.
- `helpers/device_registry.py:1768-1770`, `:1937-1955` — `via_device` and cross-subentry device moves, **2027.8**.
- `helpers/entity_registry.py:1287-1293` — `async_generate_entity_id` → `async_get_available_entity_id`, **2027.2**.
- `helpers/entity_platform.py:896,913` — invalid / wrong-domain `entity_id`, **2027.2 / 2027.5** (avoided by never setting `entity_id` manually).

**Tooling**
- `homeassistant-2026.8.3.dist-info/METADATA` — `Requires-Python: >=3.14.2` (identical to 2026.4.1).
- PyPI: `pytest-homeassistant-custom-component==0.13.357` pins `homeassistant==2026.8.3` (`0.13.354` → `2026.8.0`, `0.13.355` → `2026.8.1`, `0.13.356` → `2026.8.2`); it requires Python `>=3.14` and brings `pytest-asyncio==1.4.0`, `pytest-freezer==0.4.9`, `pytest-socket`, `pytest-timeout`, `pytest-unordered`. Use `pytest-freezer` rather than raw `freezegun` for the controller-level time tests.
- CI actions still valid: `api.github.com/repos/home-assistant/actions` → 200 with `hassfest/action.yml` present; `api.github.com/repos/hacs/action` → 200. Keep both SHA-pinned as the reference repo does.