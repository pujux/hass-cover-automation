"""Sun-hits-window geometry with a release margin (spec §2)."""

from __future__ import annotations

from .const import SUN_RELEASE_MARGIN
from .model import CoverConfig


def signed_azimuth_diff(sun_az: float, cover_az: float) -> float:
    """Difference in [-180, 180); negative = sun left of the facade normal."""
    return (sun_az - cover_az + 180.0) % 360.0 - 180.0


def inside_strict(cfg: CoverConfig, sun_az: float, elevation: float) -> bool:
    diff = signed_azimuth_diff(sun_az, cfg.azimuth)
    az_ok = -cfg.tolerance_left < diff < cfg.tolerance_right
    el_ok = cfg.elevation_min <= elevation <= cfg.elevation_max
    return az_ok and el_ok


def released(cfg: CoverConfig, sun_az: float, elevation: float, margin: float) -> bool:
    """True when the sun is outside the window by more than `margin` on any axis,
    or at/below the horizon (the lower bound never releases below the horizon)."""
    if elevation <= 0.0:
        return True
    diff = signed_azimuth_diff(sun_az, cfg.azimuth)
    if diff <= -cfg.tolerance_left - margin or diff >= cfg.tolerance_right + margin:
        return True
    return elevation < cfg.elevation_min - margin or elevation > cfg.elevation_max + margin


class SunHits:
    """Hysteresis around inside_strict(): on when strictly inside, off when released()."""

    def __init__(self, cfg: CoverConfig, margin: float = SUN_RELEASE_MARGIN) -> None:
        self.cfg = cfg
        self.margin = margin
        self.state: bool | None = None

    def seed(self, sun_az: float, elevation: float) -> bool:
        self.state = inside_strict(self.cfg, sun_az, elevation)
        return self.state

    def update(self, sun_az: float, elevation: float) -> bool:
        if self.state is None:
            return self.seed(sun_az, elevation)
        if self.state:
            if released(self.cfg, sun_az, elevation, self.margin):
                self.state = False
        elif inside_strict(self.cfg, sun_az, elevation):
            self.state = True
        return self.state
