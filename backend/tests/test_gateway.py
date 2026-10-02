import time
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from core.clock import SimClock
from core.config import Settings, load_settings
from core.models import GpsFix, SensorReading
from events.bus import EventBus
from ingestion.gateway import IngestionGateway

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def _settings(**overrides) -> Settings:
    base = load_settings(CONFIG_DIR / "settings.yaml")
    return base.model_copy(update=overrides)


def _gateway(settings: Settings | None = None) -> tuple[IngestionGateway, SimClock, EventBus]:
    clock = SimClock()
    bus = EventBus()
    gw = IngestionGateway(clock, settings or _settings(), bus)
    return gw, clock, bus


def _fix(vehicle_id="AMB-1", x=0.0, y=0.0, seq=1, ts=0, speed=10.0) -> GpsFix:
    return GpsFix(
        vehicle_id=vehicle_id,
        x=x,
        y=y,
        speed_mps=speed,
        heading_deg=0.0,
        timestamp=ts,
        sequence=seq,
    )


def test_valid_fix_accepted():
    gw, clock, bus = _gateway()
    result = gw.ingest_gps_fix(_fix())
    assert result is not None
    assert gw.stats()["gps:AMB-1"]["accepted"] == 1


def test_duplicate_sequence_dropped():
    gw, clock, bus = _gateway()
    gw.ingest_gps_fix(_fix(seq=1, ts=0))
    result = gw.ingest_gps_fix(_fix(seq=1, ts=100))
    assert result is None
    assert gw.stats()["gps:AMB-1"]["duplicate"] == 1


def test_out_of_order_sequence_dropped():
    gw, clock, bus = _gateway()
    gw.ingest_gps_fix(_fix(seq=5, ts=0))
    result = gw.ingest_gps_fix(_fix(seq=3, ts=100))
    assert result is None
    assert gw.stats()["gps:AMB-1"]["out_of_order"] == 1


def test_implausible_speed_jump_rejected():
    settings = _settings(max_gps_speed_mps=50, max_gps_jump_m=100000)
    gw, clock, bus = _gateway(settings)
    gw.ingest_gps_fix(_fix(seq=1, ts=0, x=0, y=0))
    clock.advance(1000)
    # 1000m in 1s = 1000 m/s, way above 50 m/s limit.
    result = gw.ingest_gps_fix(_fix(seq=2, ts=1000, x=1000, y=0))
    assert result is None
    assert gw.stats()["gps:AMB-1"]["implausible"] == 1


def test_implausible_jump_rejected_even_with_zero_dt():
    settings = _settings(max_gps_jump_m=50)
    gw, clock, bus = _gateway(settings)
    gw.ingest_gps_fix(_fix(seq=1, ts=0, x=0, y=0))
    # Same timestamp, huge jump: speed calc would be infinite/undefined,
    # but the raw-distance check still catches it.
    result = gw.ingest_gps_fix(_fix(seq=2, ts=0, x=500, y=0))
    assert result is None
    assert gw.stats()["gps:AMB-1"]["implausible"] == 1


def test_future_timestamp_clamped_and_flagged():
    settings = _settings(clock_skew_tolerance_s=0.2)
    gw, clock, bus = _gateway(settings)
    far_future = gw.ingest_gps_fix(_fix(seq=1, ts=100_000))  # now=0, way in the future
    assert far_future is not None
    assert far_future.timestamp == 0  # clamped to received_at
    assert gw.stats()["gps:AMB-1"]["clock_skew"] == 1
    assert gw.stats()["gps:AMB-1"]["accepted"] == 1  # clamped, not rejected


def test_within_tolerance_not_flagged():
    settings = _settings(clock_skew_tolerance_s=1.0)
    gw, clock, bus = _gateway(settings)
    fix = gw.ingest_gps_fix(_fix(seq=1, ts=500))  # 500ms ahead, within 1s tolerance
    assert fix is not None
    assert fix.timestamp == 500
    assert "clock_skew" not in gw.stats().get("gps:AMB-1", {})


def test_accepted_fix_published_as_event():
    gw, clock, bus = _gateway()
    gw.ingest_gps_fix(_fix())
    history = bus.history()
    assert len(history) == 1
    assert history[0].type.value == "GPS_FIX"


def test_rejected_fix_not_published():
    gw, clock, bus = _gateway()
    gw.ingest_gps_fix(_fix(seq=5))
    gw.ingest_gps_fix(_fix(seq=3))  # out of order
    assert len(bus.history()) == 1  # only the first


def test_sensor_reading_accepted_and_published():
    gw, clock, bus = _gateway()
    reading = SensorReading(source_id="s1", road_id="A1->A2", value=5.0, timestamp=0, sequence=1)
    result = gw.ingest_sensor_reading(reading)
    assert result is not None
    assert bus.history()[0].type.value == "SENSOR_READING"


def test_sensor_duplicate_dropped():
    gw, clock, bus = _gateway()
    reading = SensorReading(source_id="s1", road_id="A1->A2", value=5.0, timestamp=0, sequence=1)
    gw.ingest_sensor_reading(reading)
    result = gw.ingest_sensor_reading(reading)
    assert result is None


def test_counters_accurate_across_mixed_stream():
    gw, clock, bus = _gateway()
    gw.ingest_gps_fix(_fix(seq=1, ts=0))
    gw.ingest_gps_fix(_fix(seq=2, ts=100))
    gw.ingest_gps_fix(_fix(seq=2, ts=200))  # duplicate
    gw.ingest_gps_fix(_fix(seq=1, ts=300))  # out of order
    stats = gw.stats()["gps:AMB-1"]
    assert stats["accepted"] == 2
    assert stats["duplicate"] == 1
    assert stats["out_of_order"] == 1


@given(
    sequences=st.lists(st.integers(min_value=0, max_value=20), min_size=1, max_size=50)
)
def test_accepted_set_matches_deduplicated_in_order_shuffle(sequences: list[int]) -> None:
    """Feed a shuffled/duplicated stream of sequence numbers; the accepted
    set must equal exactly the strictly-increasing subsequence a
    single-pass last-sequence tracker would produce."""
    gw, clock, bus = _gateway()
    accepted = []
    last = None
    for seq in sequences:
        result = gw.ingest_gps_fix(_fix(seq=seq, ts=clock.now()))
        if result is not None:
            accepted.append(seq)
        clock.advance(10)

    # Re-derive expected acceptance with the same rule, independently.
    expected = []
    last = None
    for seq in sequences:
        if last is None or seq > last:
            expected.append(seq)
            last = seq
    assert accepted == expected


@pytest.mark.slow
def test_ingestion_latency_well_under_1ms() -> None:
    gw, clock, bus = _gateway()
    n = 5000
    durations_ms = []
    for i in range(n):
        clock.advance(10)
        start = time.perf_counter()
        gw.ingest_gps_fix(_fix(vehicle_id=f"veh-{i % 50}", seq=i // 50, ts=clock.now()))
        durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    p95 = durations_ms[int(n * 0.95)]
    print(f"\ningestion latency: p95={p95:.4f}ms over {n} calls")
    assert p95 < 1.0, f"p95={p95:.4f}ms exceeds the 1ms budget"
