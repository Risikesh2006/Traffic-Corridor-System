import math
from pathlib import Path

from core.city_loader import load_city
from core.clock import SimClock
from core.config import Settings, load_settings
from core.models import (
    ControllerStatus,
    Direction,
    EstimateSource,
    SensorMetric,
    SensorReading,
    SignalState,
    StatusReport,
)
from state.world_state import WorldState

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
SEG = "A2->B2"  # feeds B2's W approach


def _settings(**overrides) -> Settings:
    base = load_settings(CONFIG_DIR / "settings.yaml")
    return base.model_copy(update=overrides)


def _world(settings: Settings | None = None) -> tuple[WorldState, SimClock]:
    settings = settings or _settings()
    city = load_city(CONFIG_DIR / "city.json", settings)
    clock = SimClock()
    return WorldState(city, settings, clock), clock


def _reading(value: float, ts: int, seq: int = 1, road: str = SEG, metric=SensorMetric.DENSITY):
    return SensorReading(
        source_id="s", road_id=road, metric=metric, value=value, timestamp=ts, sequence=seq
    )


def test_never_received_segment_is_pessimistic_historical_not_zero():
    world, clock = _world()
    est = world.estimate(SEG, SensorMetric.DENSITY, now=0)
    assert est.source == EstimateSource.HISTORICAL
    assert est.stale
    assert est.confidence == 0.0
    # With no profile loaded the prior is 0 — but the estimate must still be
    # flagged stale. The non-zero-prior case is covered by test_history.py.


def test_confidence_decays_exponentially():
    settings = _settings(tau_density_s=30)
    world, clock = _world(settings)
    world.apply_sensor(_reading(10.0, ts=0), received_at=0)
    tau = settings.tau_density_ms
    est = world.estimate(SEG, SensorMetric.DENSITY, now=tau)
    assert math.isclose(est.confidence, math.exp(-1), rel_tol=1e-9)


def test_blend_math_matches_formula():
    settings = _settings(tau_density_s=30)
    world, clock = _world(settings)
    world.apply_sensor(_reading(10.0, ts=0), received_at=0)
    now = settings.tau_density_ms  # c = e^-1, age = tau
    est = world.estimate(SEG, SensorMetric.DENSITY, now)
    c = math.exp(-1)
    prior = 0.0  # no profile loaded
    assert math.isclose(est.value, c * 10.0 + (1 - c) * prior, rel_tol=1e-9)
    assert est.source == EstimateSource.BLENDED


def test_fresh_live_value_reported_as_live():
    world, clock = _world()
    world.apply_sensor(_reading(7.0, ts=1000), received_at=1000)
    est = world.estimate(SEG, SensorMetric.DENSITY, now=1000)
    assert est.source == EstimateSource.LIVE
    assert est.value == 7.0
    assert not est.stale


def test_ttl_expiry_falls_back_to_historical_with_pessimism():
    settings = _settings(density_ttl_s=60, pessimism_factor=1.2)
    world, clock = _world(settings)
    world.apply_sensor(_reading(99.0, ts=0), received_at=0)
    est = world.estimate(SEG, SensorMetric.DENSITY, now=settings.density_ttl_ms + 1)
    assert est.source == EstimateSource.HISTORICAL
    assert est.stale
    assert est.value != 99.0  # live value ignored past TTL


def test_older_reading_never_overwrites_newer():
    world, clock = _world()
    world.apply_sensor(_reading(5.0, ts=2000, seq=2), received_at=2000)
    world.apply_sensor(_reading(1.0, ts=1000, seq=1), received_at=2100)
    est = world.estimate(SEG, SensorMetric.DENSITY, now=2000)
    assert est.value == 5.0


def test_queue_estimate_uses_queue_metric_for_correct_approach():
    world, clock = _world()
    world.apply_sensor(
        _reading(12.0, ts=0, road=SEG, metric=SensorMetric.QUEUE_COUNT), received_at=0
    )
    est = world.queue_estimate("B2", Direction.W, now=0)
    assert est is not None
    assert est.value == 12.0
    assert est.source == EstimateSource.LIVE


def test_queue_estimate_none_for_unknown_approach():
    world, clock = _world()
    # A1 is a corner: it has no W approach at all.
    assert world.queue_estimate("A1", Direction.W, now=0) is None


def _status(seq=1, status=ControllerStatus.ONLINE, sent_at=0) -> StatusReport:
    return StatusReport(
        intersection_id="B2",
        sequence=seq,
        sent_at=sent_at,
        controller_status=status,
        signal_state=SignalState.GREEN,
        current_phase_id="NS_THROUGH",
        state_started_at=0,
        red_since={Direction.E: 0},
        applied_plan_version=0,
    )


def test_intersection_unknown_until_first_report():
    world, clock = _world()
    view = world.intersection_view("B2", now=0)
    assert view.controller_status == ControllerStatus.UNREACHABLE
    assert view.report_age_ms is None


def test_intersection_becomes_unreachable_after_heartbeat_timeout():
    settings = _settings(heartbeat_timeout_s=3)
    world, clock = _world(settings)
    world.apply_status(_status(), received_at=0)
    assert world.intersection_view("B2", now=1000).controller_status == ControllerStatus.ONLINE
    late = settings.heartbeat_timeout_ms + 1
    assert world.intersection_view("B2", now=late).controller_status == ControllerStatus.UNREACHABLE


def test_flashing_report_surfaces_as_flashing():
    world, clock = _world()
    world.apply_status(_status(status=ControllerStatus.FLASHING), received_at=0)
    assert world.intersection_view("B2", now=0).controller_status == ControllerStatus.FLASHING


def test_older_status_report_ignored():
    world, clock = _world()
    world.apply_status(_status(seq=5, status=ControllerStatus.ONLINE), received_at=0)
    world.apply_status(_status(seq=3, status=ControllerStatus.FLASHING), received_at=10)
    assert world.intersection_view("B2", now=10).controller_status == ControllerStatus.ONLINE


def test_supervisor_view_populated_from_world_state():
    world, clock = _world()
    world.apply_status(_status(), received_at=500)
    sup = world.supervisor_view("B2", now=1500)
    assert sup.intersection_id == "B2"
    assert sup.controller_status == ControllerStatus.ONLINE
    assert sup.reported_at == 500  # the moment the report was received
    assert sup.red_since == {Direction.E: 0}


def test_close_and_reopen_road():
    world, clock = _world()
    world.close_road([SEG], reason="accident")
    assert world.road_status(SEG) == "CLOSED"
    world.reopen_road([SEG])
    assert world.road_status(SEG) == "OPEN"


def test_freshness_summary_counts_never_received_as_stale():
    world, clock = _world()
    summary = world.freshness_summary(now=0)
    assert summary.get("stale", 0) == len(world._segment_ids)
    world.apply_sensor(_reading(3.0, ts=0), received_at=0)
    summary = world.freshness_summary(now=0)
    assert summary["fresh"] == 1
