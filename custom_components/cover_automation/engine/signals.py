"""Stateful signal primitives (spec §2). Completed in Task 4."""

from __future__ import annotations

from datetime import datetime


class ContinuousCondition:
    """True once `cond` has been True for `duration_s` accumulated seconds.

    None pauses accumulation (leave_alone), False resets it.
    """

    def __init__(self, duration_s: float) -> None:
        self.duration_s = float(duration_s)
        self._accum_s = 0.0
        self._last_true_at: datetime | None = None

    def reset(self) -> None:
        self._accum_s = 0.0
        self._last_true_at = None

    def update(self, cond: bool | None, now: datetime) -> bool:
        if cond is None:
            self._last_true_at = None
        elif cond:
            if self._last_true_at is not None:
                self._accum_s += (now - self._last_true_at).total_seconds()
            self._last_true_at = now
        else:
            self.reset()
        return self._accum_s >= self.duration_s

    def remaining_s(self) -> float | None:
        """Seconds until satisfied if the condition stays True; None if not running."""
        if self._last_true_at is None:
            return None
        return max(0.0, self.duration_s - self._accum_s)
