import random
from pathlib import Path

import pytest

from core.city_loader import load_city
from core.config import Settings, load_settings
from core.events import EventType
from core.models import GpsFix, PositionSource, Priority
from events.bus import EventBus
from tracking.map_match import MapMatcher
from tracking.tracker import VehicleTracker

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
ROUTE = ["A2", "B2", "C2", "D2"]  # eastbound straight line, 300m segments
VEH = "AMB-1"


def _settings(**overrides) -> Settings:
    base = load_settings(CONFIG_DIR / "settings.yaml")
    return base.model_copy(update=overrides)


def _xy(node: str) -> tuple[float, float]:
    return (ord(node[0]) - ord("A")) * 300.0, (int(node[1:]) - 1) * 300.0


class _World:
    """Minimal stand-ins for the queue/stale hooks the tracker needs."""

    def queue_wait(self, intersection_id: str, now: int) -> int:
        return 0

    def stale_fraction(self, segments: list[str], now: int) -> float:
        return 0.0


def _tracker(settings: Settings | None = None, bus: EventBus | None = None):
    settings = settings or _settings()
    city = load_city(CONFIG_DIR / "city.json", settings)
    matcher = MapMatcher(city, radius_m=settings.map_match_radius_m)
    world = _World()
    tracker = VehicleTracker(
        city, settings, matcher, world.queue_wait, world.stale_fraction, bus=bus
    )
    return tracker, city, settings


def _fix(x, y, t, seq, speed=10.0, heading=0.0) -> GpsFix:
    return GpsFix(
        vehicle_id=VEH, x=x, y=y, speed_mps=speed, heading_deg=heading, timestamp=t, sequence=seq
    )


def _drive(tracker, speed=10.0, noise=0.0, seconds=60, gps_on=lambda t: True, seed=4417):
    """Feeds 1 Hz fixes along ROUTE; returns the true (x, y, t) timeline."""
    rng = random.Random(seed)
    ax, ay = _xy(ROUTE[0])
    cx, cy = _xy(ROUTE[-1])
    total = ((cx - ax) ** 2 + (cy - ay) ** 2) ** 0.5
    truth = []
    seq = 0
    for second in range(1, seconds + 1):
        t = second * 1000
        s = min(speed * second, total)
        x = ax + (cx - ax) * (s / total) + rng.gauss(0, noise)
        y = ay + rng.gauss(0, noise)
        truth.append((t, ax + (cx - ax) * (s / total), ay))
        now = t
        if gps_on(t):
            seq += 1
            tracker.ingest_fix(_fix(x, y, t, seq, speed=speed))
        tracker.update(now)
    return truth


def _register(tracker):
    tracker.register(VEH, Priority.P1, ROUTE, now=0)


def test_tracks_position_along_route_with_clean_gps():
    tracker, city, settings = _tracker()
    _register(tracker)
    _drive(tracker, speed=10.0, noise=0.0, seconds=30)
    snap = tracker.snapshot(VEH, now=30_000)
    assert snap.s_m == pytest.approx(300.0, abs=15.0)  # 10 m/s * 30 s
    assert snap.position_source == PositionSource.GPS


def test_eta_on_clear_road_is_close_to_truth():
    tracker, city, settings = _tracker()
    _register(tracker)
    _drive(tracker, speed=10.0, noise=0.0, seconds=20)
    snap = tracker.snapshot(VEH, now=20_000)
    b2 = next(e for e in snap.etas if e.intersection_id == "B2")
    # B2 is 300 m ahead of the vehicle at t=20s? No: at 200 m. 100 m left at 10 m/s = 10 s.
    assert b2.eta_unimpeded_ms == pytest.approx(30_000, abs=3_000)


# -- E4 dead reckoning -----------------------------------------------------


def test_gps_loss_switches_to_dead_reckoning_and_emits_events():
    bus = EventBus()
    tracker, city, settings = _tracker(bus=bus)
    _register(tracker)
    # GPS works for 10s, then goes dark for 20s.
    _drive(tracker, speed=10.0, noise=0.0, seconds=30, gps_on=lambda t: t <= 10_000)
    snap = tracker.snapshot(VEH, now=30_000)
    assert snap.position_source == PositionSource.DEAD_RECKONING
    types = [e.type for e in bus.history()]
    assert EventType.GPS_LOST in types
    assert EventType.TRACKING_DEGRADED in types


def test_dead_reckoning_position_continues_smoothly():
    tracker, city, settings = _tracker()
    _register(tracker)
    # Feed 10s of clean GPS, then go dark; sample s mid-run, not after the fact.
    sample = {}
    for sec in range(1, 21):
        t = sec * 1000
        if t <= 10_000:
            tracker.ingest_fix(_fix(10.0 * sec, 300.0, t, seq=sec, speed=10.0))
        tracker.update(t)
        sample[sec] = tracker.snapshot(VEH, now=t).s_m
    # Dead reckoning starts only after gps_loss_threshold_s (3s, FR-TRK-04);
    # from then on the position advances steadily at the last filtered speed.
    assert sample[13] == pytest.approx(sample[10], abs=1.0)
    steps = [sample[i + 1] - sample[i] for i in range(14, 20)]
    assert all(step > 0 for step in steps)
    assert sample[20] - sample[10] == pytest.approx(100.0, rel=0.1)


def test_uncertainty_grows_during_gps_loss():
    tracker, city, settings = _tracker()
    _register(tracker)
    _drive(tracker, speed=10.0, noise=0.0, seconds=5, gps_on=lambda t: True)
    early = tracker.snapshot(VEH, now=6_000).uncertainty_m
    _drive(tracker, speed=10.0, noise=0.0, seconds=20, gps_on=lambda t: False)
    late = tracker.snapshot(VEH, now=25_000).uncertainty_m
    assert late > early


def test_reconciliation_on_gps_return_consistent_case_emits_restored():
    bus = EventBus()
    tracker, city, settings = _tracker(bus=bus)
    _register(tracker)
    # One continuous 20s drive: GPS 0-10s, dark 10-15s, back 15-20s.
    _drive(tracker, speed=10.0, noise=0.0, seconds=20, gps_on=lambda t: t <= 10_000 or t > 15_000)
    restored = [e for e in bus.history() if e.type == EventType.GPS_RESTORED]
    assert restored
    assert restored[-1].payload["reconciled_consistent"] is True


# -- E5 deviation ------------------------------------------------------------


def test_deviation_fires_on_sustained_off_route_fixes():
    bus = EventBus()
    tracker, city, settings = _tracker(bus=bus)
    _register(tracker)
    _drive(tracker, speed=10.0, noise=0.0, seconds=5)
    # Feed fixes far north of the route (no road there).
    for i in range(5):
        t = 6_000 + i * 1000
        tracker.ingest_fix(_fix(600.0, -2000.0, t, seq=100 + i))
        tracker.update(t)
    assert any(e.type == EventType.ROUTE_DEVIATION for e in bus.history())


def test_deviation_does_not_fire_on_single_noisy_fix():
    bus = EventBus()
    tracker, city, settings = _tracker(bus=bus)
    _register(tracker)
    _drive(tracker, speed=10.0, noise=0.0, seconds=5)
    tracker.ingest_fix(_fix(600.0, -2000.0, 6_000, seq=200))
    tracker.update(6_000)
    _drive(tracker, speed=10.0, noise=0.0, seconds=5, gps_on=lambda t: True)
    assert not any(e.type == EventType.ROUTE_DEVIATION for e in bus.history())


# -- E6 stop-line passage -----------------------------------------------------


def test_stop_line_passage_detected_with_margin():
    bus = EventBus()
    tracker, city, settings = _tracker(bus=bus)
    _register(tracker)
    _drive(tracker, speed=10.0, noise=0.0, seconds=40)  # s=400, past B2 (300)
    passed = [e for e in bus.history() if e.type == EventType.STOP_LINE_PASSED]
    assert any(e.payload["intersection_id"] == "B2" for e in passed)


def test_stop_line_not_passed_under_noise_before_reaching_it():
    bus = EventBus()
    tracker, city, settings = _tracker(bus=bus)
    _register(tracker)
    # Stay 10 m short of B2 (at 290 m) with 3 m of noise: must not fire.
    rng = random.Random(7)
    for second in range(1, 30):
        t = second * 1000
        s = min(290.0, 5.0 * second)
        tracker.ingest_fix(_fix(s + rng.gauss(0, 3.0), rng.gauss(0, 3.0), t, seq=second, speed=5.0))
        tracker.update(t)
    passed = [e for e in bus.history() if e.type == EventType.STOP_LINE_PASSED]
    assert not any(e.payload["intersection_id"] == "B2" for e in passed)


def test_presence_places_vehicle_at_stop_line_without_claiming_passage():
    from core.models import Direction, PresenceReading

    bus = EventBus()
    tracker, city, settings = _tracker(bus=bus)
    _register(tracker)
    _drive(tracker, speed=10.0, noise=0.0, seconds=10, gps_on=lambda t: True)
    tracker.ingest_presence(
        PresenceReading(
            source_id="det-B2",
            intersection_id="B2",
            approach_direction=Direction.W,
            vehicle_id=VEH,
            timestamp=12_000,
            sequence=1,
        )
    )
    tracker.update(12_000)
    passed = [e for e in bus.history() if e.type == EventType.STOP_LINE_PASSED]
    assert not any(e.payload["intersection_id"] == "B2" for e in passed)
    assert tracker.snapshot(VEH, now=12_000).s_m == pytest.approx(300.0, abs=0.5)


def test_dead_reckoning_holds_at_a_red_stop_line():
    tracker, city, settings = _tracker()  # default hook: every approach is red
    _register(tracker)
    # GPS works until t=20s (vehicle at 200 m), then goes dark. Dead reckoning
    # would carry the vehicle through the B2 stop line at 300 m; the red holds it.
    sample = {}
    for sec in range(1, 41):
        t = sec * 1000
        if sec <= 20:
            tracker.ingest_fix(_fix(10.0 * sec, 300.0, t, seq=sec, speed=10.0))
        tracker.update(t)
        sample[sec] = tracker.snapshot(VEH, now=t).s_m
    assert sample[40] <= 300.0 + settings.stop_line_margin_m
    assert sample[40] == pytest.approx(sample[35], abs=0.5)


def test_stopped_vehicle_at_stop_line_never_reported_passed_under_gps_noise():
    bus = EventBus()
    tracker, city, settings = _tracker(bus=bus)
    _register(tracker)
    rng = random.Random(2024)
    # Arrive at B2 (300 m), then sit still for 60 s with 5 m GPS noise.
    for sec in range(1, 91):
        t = sec * 1000
        s = min(300.0, 10.0 * sec)
        tracker.ingest_fix(
            _fix(s + rng.gauss(0, 5.0), 300.0 + rng.gauss(0, 5.0), t, seq=sec, speed=0.0)
        )
        tracker.update(t)
    passed = [e for e in bus.history() if e.type == EventType.STOP_LINE_PASSED]
    assert not any(e.payload["intersection_id"] == "B2" for e in passed)
