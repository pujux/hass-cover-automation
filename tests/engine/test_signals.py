from __future__ import annotations

from datetime import date, timedelta

from custom_components.cover_automation.engine.signals import (
    ContinuousCondition,
    DailyLatch,
    Debounce,
    FrostSignal,
    Graceful,
    RoomTemperature,
    WindProtection,
)

from tests.engine.conftest import at

T0 = at("2026-07-01", "12:00")


def plus(seconds: int):
    return T0 + timedelta(seconds=seconds)


def test_continuous_condition_pauses_on_none_and_resets_on_false():
    c = ContinuousCondition(60)
    assert c.update(True, T0) is False
    assert c.update(True, plus(30)) is False
    assert c.update(None, plus(40)) is False  # pause
    assert c.update(True, plus(100)) is False  # resumes: accum still 30
    assert c.update(True, plus(130)) is True  # 30 + 30 = 60
    assert c.update(False, plus(140)) is False
    assert c.remaining_s() is None
    c.update(True, plus(150))
    assert c.remaining_s() == 60.0


def test_graceful_holds_last_value_then_none():
    g: Graceful[str] = Graceful(grace_s=60)
    assert g.update("sunny", T0) == "sunny"
    assert g.update(None, plus(30)) == "sunny"  # grace starts here
    assert g.update(None, plus(89)) == "sunny"
    assert g.update(None, plus(90)) is None
    assert g.last_known == "sunny"
    assert g.update("cloudy", plus(70)) == "cloudy"


def test_graceful_without_history_is_none():
    g: Graceful[float] = Graceful(grace_s=60)
    assert g.update(None, T0) is None


def test_debounce_on_and_off_delays():
    d = Debounce(on_delay_s=600, off_delay_s=1200)
    assert d.seed(False, T0) is False
    assert d.update(True, plus(10)) is False
    assert d.update(True, plus(609)) is False
    assert d.update(True, plus(610)) is True
    assert d.next_change_at() is None
    assert d.update(False, plus(700)) is True
    assert d.next_change_at() == plus(700 + 1200)
    assert d.update(True, plus(1000)) is True  # flicker back: timer restarts
    assert d.update(False, plus(1100)) is True
    assert d.update(False, plus(2299)) is True
    assert d.update(False, plus(2300)) is False


def test_debounce_unknown_passes_through_and_reseeds():
    d = Debounce(600, 1200)
    d.seed(True, T0)
    assert d.update(None, plus(10)) is None
    assert d.update(True, plus(20)) is True  # first value after unknown seeds as settled


def test_daily_latch_flag_latches_until_rollover():
    latch = DailyLatch()
    day = date(2026, 7, 1)
    assert latch.update(day, None, None, 24.0, 13.0) is None  # no fetch yet
    assert latch.update(day, 26.0, 14.0, 24.0, 13.0) is True
    assert latch.update(day, 22.0, 12.0, 24.0, 13.0) is True  # inputs latched, flag latched
    assert latch.max == 26.0 and latch.min == 12.0
    assert latch.update(date(2026, 7, 2), None, None, 24.0, 13.0) is None  # new day: unknown
    assert latch.update(date(2026, 7, 2), 20.0, 14.0, 24.0, 13.0) is False
    assert latch.update(date(2026, 7, 2), 25.0, 15.0, 24.0, 13.0) is True  # false may become true
    assert latch.min == 14.0  # min only falls within a day


def test_daily_latch_low_threshold_optional_and_roundtrip():
    latch = DailyLatch()
    day = date(2026, 7, 1)
    assert latch.update(day, 26.0, 5.0, 24.0, None) is True  # low disabled
    d = latch.to_dict()
    assert DailyLatch.from_dict(d).to_dict() == d
    assert d["date"] == "2026-07-01"


def test_room_temperature_hysteresis_and_dwell():
    r = RoomTemperature(floor=21.0, ceiling=25.0, band=0.5, dwell_s=600)
    assert r.seed(23.0, T0) == (False, False, False)
    assert r.update(25.2, plus(10)) == (False, False, False)  # hot raw, dwell running
    assert r.update(25.2, plus(609)) == (False, False, False)
    assert r.update(25.2, plus(610)) == (False, True, False)
    assert r.update(24.7, plus(700)) == (False, True, False)  # inside band: stays hot
    assert r.update(24.4, plus(800)) == (False, True, False)  # below band, dwell running
    assert r.update(24.4, plus(1400)) == (False, False, False)
    assert r.update(None, plus(1500)) == (False, False, True)  # degraded: neither


def test_room_temperature_seed_resolves_band_toward_nearer_state():
    r = RoomTemperature(21.0, 25.0, 0.5, 600)
    assert r.seed(20.0, T0) == (True, False, False)
    assert r.seed(26.0, T0) == (False, True, False)
    assert r.seed(None, T0) == (False, False, True)


def test_wind_protection_hysteresis_and_hold():
    w = WindProtection(upper=60.0, lower=50.0, hold_s=900)
    assert w.update(55.0, T0) is False
    assert w.update(61.0, plus(10)) is True
    assert w.update(55.0, plus(20)) is True  # between thresholds: stays active
    assert w.update(49.0, plus(30)) is True  # below lower: hold starts
    assert w.next_check_at() == plus(30 + 900)
    assert w.update(52.0, plus(400)) is True  # back between: hold resets
    assert w.update(49.0, plus(500)) is True
    assert w.update(49.0, plus(1399)) is True
    assert w.update(49.0, plus(1400)) is False


def test_wind_protection_hold_restarts_after_unavailable_gap():
    w = WindProtection(60.0, 50.0, 900)
    assert w.update(65.0, T0) is True
    assert w.update(45.0, plus(1)) is True  # hold starts
    assert w.update(None, plus(5)) is True  # gap: state frozen, hold clock dropped
    assert w.next_check_at() is None
    assert w.update(45.0, plus(901)) is True  # would have released with a stale clock
    assert w.next_check_at() == plus(901 + 900)
    assert w.update(45.0, plus(1800)) is True
    assert w.update(45.0, plus(1801)) is False


def test_wind_protection_freezes_when_unavailable():
    w = WindProtection(60.0, 50.0, 900, active=True)
    assert w.update(None, T0) is True
    assert w.unavailable is True
    assert w.update(40.0, plus(10)) is True  # hold must still elapse
    assert w.unavailable is False


def test_frost_signal_hysteresis_grace_and_unknown():
    f = FrostSignal(threshold=0.0, release_k=1.0, grace_s=1800)
    assert f.update(3.0, T0) is False
    assert f.update(0.0, plus(10)) is True
    assert f.update(0.5, plus(20)) is True  # within release band
    assert f.update(1.1, plus(30)) is False
    assert f.update(-2.0, plus(40)) is True
    assert f.update(None, plus(50)) is True  # grace starts: last value held
    assert f.update(None, plus(50 + 1799)) is True
    assert f.update(None, plus(50 + 1800)) is None  # unknown after grace
    assert f.near_freezing_last_known is True
    f2 = FrostSignal(0.0, 1.0, 1800)
    f2.update(10.0, T0)
    f2.update(None, plus(1))
    assert f2.update(None, plus(1 + 1800)) is None
    assert f2.active is None and f2.near_freezing_last_known is False
