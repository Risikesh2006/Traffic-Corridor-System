from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from core.city_loader import load_city
from core.config import Settings, load_settings
from core.models import City
from tracking.eta import StopLineTarget, compute_etas

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
ROUTE = ["A2", "B2", "C2"]  # two 300m segments, 40 km/h free flow


def _setup() -> tuple[Settings, City]:
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    city = load_city(CONFIG_DIR / "city.json", settings)
    return settings, city


def _targets(queue_wait_ms: int = 0) -> list[StopLineTarget]:
    return [
        StopLineTarget("B2", 1, 300.0, queue_wait_ms=queue_wait_ms, cycle_ms=60_000),
        StopLineTarget("C2", 2, 600.0, queue_wait_ms=0, cycle_ms=60_000),
    ]


def test_unimpeded_eta_uses_cruise_speed_not_current_speed() -> None:
    settings, city = _setup()
    etas = compute_etas(city, settings, ROUTE, _targets(), 0.0, 0.0, 0, 0.0, now=0)
    b2 = next(e for e in etas if e.intersection_id == "B2")
    cruise = 11.11111111111111 * settings.ambulance_speed_factor
    assert b2.eta_unimpeded_ms == pytest.approx(round(300.0 / cruise * 1000), abs=5)


def test_expected_eta_adds_queue_and_red_wait() -> None:
    settings, city = _setup()
    targets = [
        StopLineTarget(
            "B2", 1, 300.0, queue_wait_ms=7000, cycle_ms=60_000, expected_red_wait_ms=30_000
        )
    ]
    e = compute_etas(city, settings, ROUTE, targets, 0.0, 0.0, 0, 0.0, now=0)[0]
    # Green arrives at 30 s, after the vehicle would reach the line at ~22.5 s,
    # so departure is the green time plus the 7 s queue discharge ahead.
    assert e.eta_expected_ms == max(e.eta_unimpeded_ms, 30_000) + 7000


def test_window_widens_with_gps_age() -> None:
    settings, city = _setup()
    fresh = compute_etas(city, settings, ROUTE, _targets(), 0.0, 1.0, 0, 0.0, now=0)[0]
    stale = compute_etas(city, settings, ROUTE, _targets(), 0.0, 1.0, 10_000, 0.0, now=0)[0]
    assert (stale.eta_high_ms - stale.eta_low_ms) > (fresh.eta_high_ms - fresh.eta_low_ms)


def test_window_widens_with_staleness() -> None:
    settings, city = _setup()
    clean = compute_etas(city, settings, ROUTE, _targets(), 0.0, 0.0, 0, 0.0, now=0)[0]
    stale = compute_etas(city, settings, ROUTE, _targets(), 0.0, 0.0, 0, 1.0, now=0)[0]
    assert (stale.eta_high_ms - stale.eta_low_ms) > (clean.eta_high_ms - clean.eta_low_ms)


def test_passed_stop_lines_are_skipped() -> None:
    settings, city = _setup()
    etas = compute_etas(city, settings, ROUTE, _targets(), 350.0, 0.0, 0, 0.0, now=0)
    assert [e.intersection_id for e in etas] == ["C2"]


@given(
    speed_std=st.floats(min_value=0.0, max_value=5.0),
    gps_age=st.integers(min_value=0, max_value=60_000),
    stale=st.floats(min_value=0.0, max_value=1.0),
    queue_wait=st.integers(min_value=0, max_value=120_000),
)
def test_window_always_contains_unimpeded_and_is_ordered(
    speed_std: float, gps_age: int, stale: float, queue_wait: int
) -> None:
    settings, city = _setup()
    targets = [StopLineTarget("B2", 1, 300.0, queue_wait_ms=queue_wait, cycle_ms=60_000)]
    e = compute_etas(city, settings, ROUTE, targets, 0.0, speed_std, gps_age, stale, now=0)[0]
    assert e.eta_low_ms <= e.eta_unimpeded_ms <= e.eta_high_ms
    assert e.eta_low_ms <= e.eta_high_ms
    assert e.eta_low_ms <= e.eta_expected_ms <= e.eta_high_ms
