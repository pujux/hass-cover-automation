"""Command bookkeeping (spec §1.4 on-send effects, §5 command failures)."""

from __future__ import annotations

from datetime import datetime, timedelta

from . import override
from .const import BACKOFF_MAX_S, COMMAND_RETRY_S
from .model import CoverConfig, CoverPersisted, CoverRuntime, Layer, Owner, Pending, Target


def on_command_sent(
    p: CoverPersisted, rt: CoverRuntime, target: Target, layer: Layer, now: datetime
) -> None:
    p.engine_target = target
    p.owner = Owner.ENGINE
    rt.pending = Pending(
        target=target,
        layer=layer,
        sent_at=now,
        last_progress_at=now,
        prev_last_send_at=rt.last_send_at,
    )
    rt.last_send_at = now
    p.last_send_at = now
    rt.restoring_until = None
    rt.contrary_since = None
    rt.command_failed = False
    rt.unconfirmed = False
    if layer is Layer.SCHEDULE:
        override.clear(p)


def on_simulated(rt: CoverRuntime, target: Target, layer: Layer) -> None:
    rt.last_simulated = (layer, target)


def confirm(rt: CoverRuntime) -> None:
    rt.pending = None
    rt.contrary_since = None
    rt.consecutive_failures = 0
    rt.backoff_s = 0
    rt.backoff_until = None
    rt.command_failed = False
    rt.unconfirmed = False


def _next_backoff(rt: CoverRuntime, cfg: CoverConfig, now: datetime) -> datetime:
    rt.backoff_s = min(max(rt.backoff_s * 2, cfg.min_move_interval_s), BACKOFF_MAX_S)
    rt.backoff_until = now + timedelta(seconds=rt.backoff_s)
    return rt.backoff_until


def on_command_failed(
    p: CoverPersisted, rt: CoverRuntime, cfg: CoverConfig, now: datetime
) -> datetime:
    # Ownership stays with the engine; the retry decides.
    if rt.pending is not None:
        # A command that never reached the cover must not start the minimum interval, or the
        # 30 s retry (spec §5) would be deferred by gate 8 for a full interval.
        rt.last_send_at = rt.pending.prev_last_send_at
        p.last_send_at = rt.pending.prev_last_send_at
    rt.pending = None
    rt.contrary_since = None
    rt.command_failed = True
    rt.consecutive_failures += 1
    if rt.consecutive_failures == 1:
        rt.backoff_until = now + timedelta(seconds=COMMAND_RETRY_S)
        return rt.backoff_until
    return _next_backoff(rt, cfg, now)


def on_unconfirmed(
    p: CoverPersisted, rt: CoverRuntime, cfg: CoverConfig, now: datetime
) -> datetime:
    del p
    rt.pending = None
    rt.contrary_since = None
    rt.unconfirmed = True
    rt.consecutive_failures += 1
    return _next_backoff(rt, cfg, now)
