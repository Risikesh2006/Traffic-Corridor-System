from pathlib import Path

import pytest

from controller.intersection_controller import IntersectionController
from core.city_loader import load_city
from core.config import Settings, load_settings
from core.models import Priority
from core.rng import RngFactory
from sim.ambulance import AmbulanceSim
from sim.traffic import TrafficModel
from sim.vehicle_feed import SimVehicleFeed, latlng_to_xy, xy_to_latlng

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def _settings(**overrides) -> Settings:
    base = load_settings(CONFIG_DIR / "settings.yaml")
    return base.model_copy(update=overrides)


def _build(settings: Settings | None = None, seed: int = 4417):
    settings = settings or _settings()
    city = load_city(CONFIG_DIR / "city.json", settings)
    controllers = {n.id: IntersectionController(n) for n in city.intersections}
    rng = RngFactory(seed)
    traffic = TrafficModel(city, settings, rng, controllers)
    amb_sim = AmbulanceSim(city, settings, traffic, controllers)
    feed = SimVehicleFeed(settings, rng, amb_sim)
    return city, amb_sim, feed


def test_emits_at_1hz_by_default():
    settings = _settings(gps_interval_s=1)
    city, amb_sim, feed = _build(settings)
    amb_sim.add("AMB-1", ["A6", "A5"], Priority.P1, now=0)
    feed.register("AMB-1", now=0)

    now = 0
    for _ in range(9):  # < 1s
        now += 100
        feed.step(now)
    assert feed.drain_fixes() == []

    now += 100  # reaches 1000ms
    feed.step(now)
    fixes = feed.drain_fixes()
    assert len(fixes) == 1
    assert fixes[0].vehicle_id == "AMB-1"
    assert fixes[0].timestamp == 1000


def test_gps_off_stops_fixes():
    settings = _settings(gps_interval_s=1)
    city, amb_sim, feed = _build(settings)
    amb_sim.add("AMB-1", ["A6", "A5"], Priority.P1, now=0)
    feed.gps_off("AMB-1")
    feed.register("AMB-1", now=0)

    now = 0
    for _ in range(30):
        now += 1000
        feed.step(now)
    assert feed.drain_fixes() == []

    feed.gps_on("AMB-1")
    now += 1000
    feed.step(now)
    assert len(feed.drain_fixes()) == 1


def test_jump_injection_shifts_next_fix():
    settings = _settings(gps_interval_s=1, gps_noise_std_m=0.0)
    city, amb_sim, feed = _build(settings)
    amb = amb_sim.add("AMB-1", ["A6", "A5"], Priority.P1, now=0)
    feed.register("AMB-1", now=0)
    feed.inject_jump("AMB-1", metres=500.0)

    now = 1000
    feed.step(now)
    fix = feed.drain_fixes()[0]
    assert fix.x == pytest.approx(amb.x + 500.0, abs=1.0)

    # One-shot: the following fix is not jumped.
    now += 1000
    feed.step(now)
    fix2 = feed.drain_fixes()[0]
    assert fix2.x != pytest.approx(fix.x, abs=1.0)


def test_coordinate_conversion_round_trips():
    origin_lat, origin_lng = 12.9716, 77.5946
    x, y = 1234.5, -987.6
    lat, lng = xy_to_latlng(x, y, origin_lat, origin_lng)
    x2, y2 = latlng_to_xy(lat, lng, origin_lat, origin_lng)
    assert x2 == pytest.approx(x, abs=1e-6)
    assert y2 == pytest.approx(y, abs=1e-6)


def test_latest_fix_returns_most_recent():
    settings = _settings(gps_interval_s=1)
    city, amb_sim, feed = _build(settings)
    amb_sim.add("AMB-1", ["A6", "A5"], Priority.P1, now=0)
    feed.register("AMB-1", now=0)
    assert feed.latest_fix("AMB-1") is None

    now = 1000
    feed.step(now)
    feed.drain_fixes()
    fix = feed.latest_fix("AMB-1")
    assert fix is not None
    assert fix.timestamp == 1000
