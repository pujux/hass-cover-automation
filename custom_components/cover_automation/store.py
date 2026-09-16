"""Persisted engine state for one hub entry (spec §5)."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from . import const
from .engine.model import CoverPersisted, ReopeningMode, ShadingMode
from .engine.signals import DailyLatch

_LOGGER = logging.getLogger(__name__)
SAVE_DELAY_S = 1


def _enum_or_default[E: Enum](enum: type[E], raw: Any, default: E) -> E:
    try:
        return enum(raw)
    except (ValueError, TypeError):
        return default


@dataclass(slots=True)
class StoreData:
    covers: dict[str, CoverPersisted] = field(default_factory=dict)
    latch: DailyLatch = field(default_factory=DailyLatch)
    shading_mode: ShadingMode = ShadingMode.AUTO
    reopening_mode: ReopeningMode = ReopeningMode.PASSIVE
    simulation: bool = False
    verbose: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "covers": {cover_id: p.to_dict() for cover_id, p in self.covers.items()},
            "latch": self.latch.to_dict(),
            "shading_mode": self.shading_mode.value,
            "reopening_mode": self.reopening_mode.value,
            "simulation": self.simulation,
            "verbose": self.verbose,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> StoreData:
        if raw is None:
            raw = {}
        elif not isinstance(raw, dict):
            _LOGGER.warning(
                "Discarding persisted store data: expected a mapping, got %s",
                type(raw).__name__,
            )
            raw = {}
        covers_raw = raw.get("covers")
        if not isinstance(covers_raw, dict):
            if covers_raw is not None:
                _LOGGER.warning(
                    "Discarding persisted covers: expected a mapping, got %s",
                    type(covers_raw).__name__,
                )
            covers_raw = {}
        covers: dict[str, CoverPersisted] = {}
        for cover_id, record in covers_raw.items():
            try:
                covers[str(cover_id)] = CoverPersisted.from_dict(dict(record))
            except (ValueError, TypeError) as err:
                _LOGGER.warning("Discarding persisted state for cover %s: %s", cover_id, err)
                covers[str(cover_id)] = CoverPersisted()
        try:
            latch = DailyLatch.from_dict(dict(raw.get("latch") or {}))
        except (ValueError, TypeError) as err:
            _LOGGER.warning("Discarding persisted forecast latch: %s", err)
            latch = DailyLatch()
        return cls(
            covers=covers,
            latch=latch,
            shading_mode=_enum_or_default(ShadingMode, raw.get("shading_mode"), ShadingMode.AUTO),
            reopening_mode=_enum_or_default(
                ReopeningMode, raw.get("reopening_mode"), ReopeningMode.PASSIVE
            ),
            simulation=bool(raw.get("simulation", False)),
            verbose=bool(raw.get("verbose", False)),
        )


class _CoverAutomationHAStore(Store[dict[str, Any]]):
    """`Store` subclass with an explicit migration policy.

    Minor-version bumps never change the on-disk shape (new fields are simply optional in
    `StoreData.from_dict`), so they pass the raw data through unchanged. A major-version bump
    would mean an actual schema change; since none is implemented yet, it fails loudly instead
    of silently discarding data.
    """

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
    ) -> dict[str, Any]:
        if old_major_version != const.STORAGE_VERSION:
            msg = (
                f"cannot migrate {const.DOMAIN} store from major version {old_major_version} "
                f"to {const.STORAGE_VERSION}"
            )
            raise NotImplementedError(msg)
        return old_data


class CoverAutomationStore:
    """One HA Store per hub entry; the engine owns the state, entities are views (decision 17)."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = _CoverAutomationHAStore(
            hass,
            const.STORAGE_VERSION,
            const.storage_key(entry_id),
            minor_version=const.STORAGE_MINOR_VERSION,
        )
        self._data: StoreData | None = None

    @property
    def data(self) -> StoreData:
        if self._data is None:
            msg = "store not loaded"
            raise RuntimeError(msg)
        return self._data

    async def async_load(self) -> StoreData:
        raw = await self._store.async_load()
        self._data = StoreData.from_dict(raw)
        return self._data

    def prune(self, keep: Iterable[str]) -> None:
        """Drop persisted cover records whose subentry no longer exists."""
        keep_set = set(keep)
        for cover_id in [c for c in self.data.covers if c not in keep_set]:
            del self.data.covers[cover_id]

    def _to_save(self) -> dict[str, Any]:
        return self.data.to_dict()

    def schedule_save(self) -> None:
        self._store.async_delay_save(self._to_save, SAVE_DELAY_S)

    async def async_save(self) -> None:
        await self._store.async_save(self._to_save())

    async def async_remove(self) -> None:
        await self._store.async_remove()
