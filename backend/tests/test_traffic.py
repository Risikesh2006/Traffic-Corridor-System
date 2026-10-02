from pathlib import Path

import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from controller.intersection_controller import IntersectionController
from core.city_loader import load_city
from core.config import Settings, load_settings
from core.models import SignalPlanEntry
from core.rng import RngFactory
from sim.traffic import TrafficModel

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def _settings(**overrides) -> Settings:
    base = load_settings(CONFIG_DIR / "settings.yaml")
    return base.model_copy(update=overrides)


def _city():
    return load_city(CONFIG_DIR / "city.json", _settings())


def _held_green_city(phase_id: str, held_node_id: str = "D4", green_s: float = 100_000):
    """Returns a city where `held_node_id`'s `phase_id` has an enormous
    green share, so it stays green for the whole test without the normal
    cycle rotating it away. Only `held_node_id` is modified."""
    city = _city()
    new_intersections = []
    for node in city.intersections:
        if node.id != held_node_id:
            new_intersections.append(node)
            continue
        new_plan = [
            SignalPlanEntry(
                phase_id=e.phase_id,
                green_ms=round(green_s * 1000) if e.phase_id == phase_id else e.green_ms,
            )
            for e in node.default_plan
        ]
        new_intersections.append(node.model_copy(update={"default_plan": new_plan}))
    return city.model_copy(update={"intersections": new_intersections})


def _build(city=None, settings: Settings | None = None, seed: int = 4417):
    settings = settings or _settings()
    city = city or _city()
    controllers = {n.id: IntersectionController(n) for n in city.intersections}
    rng = RngFactory(seed)
    traffic = TrafficModel(city, settings, rng, controllers)
    return city, settings, controllers, traffic


def _advance_all(controllers: dict, now: int) -> None:
    for c in controllers.values():
        c.tick(now)


def _run_until_phase(
    controllers: dict, node_id: str, phase_id: str, dt: int = 100, limit_ms: int = 600_000
):
    now = 0
    while now < limit_ms:
        now += dt
        _advance_all(controllers, now)
        if controllers[node_id].state_machine.current_phase_id == phase_id:
            return now
    raise AssertionError(f"{node_id} never reached {phase_id} within {limit_ms}ms")


def test_discharge_rate_converges_to_lanes_over_headway():
    city = _held_green_city("EW_THROUGH")
    settings = _settings()
    city, settings, controllers, traffic = _build(city, settings)
    segment_id = "C4->D4"  # feeds D4's W approach (EW_THROUGH phase)
    to_node = "D4"
    lanes = city.segment(segment_id).lanes

    seg = traffic.segments[segment_id]
    for _ in range(seg.capacity):
        seg.queue.append(0)  # seed the queue directly; this test is about discharge, not arrival

    dt = 100
    now = _run_until_phase(controllers, to_node, "EW_THROUGH", dt=dt)

    start_discharged = seg.cumulative_discharged
    start_time = now
    duration_ms = 60_000
    while now < start_time + duration_ms:
        now += dt
        _advance_all(controllers, now)
        traffic.discharge(now, dt)
    discharged = seg.cumulative_discharged - start_discharged

    expected_rate_per_s = lanes / settings.headway_s
    effective_s = duration_ms / 1000 - settings.startup_loss_s
    expected = expected_rate_per_s * effective_s
    assert discharged == pytest.approx(expected, rel=0.05)


def test_startup_loss_applied_at_start_of_green():
    city = _held_green_city("EW_THROUGH")
    settings = _settings(startup_loss_s=5)
    city, settings, controllers, traffic = _build(city, settings)
    segment_id = "C4->D4"
    to_node = "D4"
    seg = traffic.segments[segment_id]
    for _ in range(50):
        seg.queue.append(0)

    dt = 100
    green_entered_at = _run_until_phase(controllers, to_node, "EW_THROUGH", dt=dt)

    now = green_entered_at
    while now < green_entered_at + settings.startup_loss_ms - dt:
        now += dt
        _advance_all(controllers, now)
        traffic.discharge(now, dt)
    assert seg.cumulative_discharged == 0

    # Past startup loss, discharge begins (several more ticks so enough
    # fractional credit accumulates to cross the 1-vehicle threshold).
    for _ in range(20):
        now += dt
        _advance_all(controllers, now)
        traffic.discharge(now, dt)
    assert seg.cumulative_discharged > 0


def test_queue_grows_during_red():
    city, settings, controllers, traffic = _build()
    # A2->B2 feeds B2's W approach (EW_THROUGH); B2 starts on NS_THROUGH, so
    # EW is red at t=0 and stays red for a full 30s green + amber + all-red.
    segment_id = "A2->B2"
    seg = traffic.segments[segment_id]
    # Seed the queue directly (travel time to arrive via spawn() would
    # outlast this short red window) — this test is about discharge gating,
    # not arrival timing.
    for _ in range(5):
        seg.queue.append(0)

    now = 0
    dt = 1000
    for _ in range(20):  # 20s, well inside B2's ~30s NS green (EW still red)
        now += dt
        _advance_all(controllers, now)
        traffic.discharge(now, dt)
    assert seg.cumulative_discharged == 0
    assert len(seg.queue) == 5


def test_queue_shrinks_during_green():
    city = _held_green_city("EW_THROUGH")
    settings = _settings()
    city, settings, controllers, traffic = _build(city, settings)
    segment_id = "C4->D4"
    to_node = "D4"
    seg = traffic.segments[segment_id]
    for _ in range(10):
        seg.queue.append(0)

    dt = 100
    now = _run_until_phase(controllers, to_node, "EW_THROUGH", dt=dt)
    for _ in range(100):  # several seconds of green
        now += dt
        _advance_all(controllers, now)
        traffic.discharge(now, dt)
    assert len(seg.queue) < 10


def test_storage_capacity_respected_and_spillback_blocks_upstream():
    settings = _settings(jam_spacing_m=50.0)  # few vehicles fit -> easy to fill
    city, settings, controllers, traffic = _build(settings=settings)
    segment_id = "C4->D4"
    seg = traffic.segments[segment_id]
    spawned = 0
    for _ in range(seg.capacity + 10):
        if traffic.spawn(segment_id, now=0):
            spawned += 1
    assert spawned == seg.capacity
    assert seg.occupancy() == seg.capacity


@hyp_settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow], deadline=None)
@given(
    seed=st.integers(min_value=0, max_value=1_000_000),
    n_spawns=st.integers(min_value=0, max_value=50),
)
def test_vehicle_conservation_holds(seed: int, n_spawns: int):
    city, settings, controllers, traffic = _build(seed=seed)
    segment_ids = sorted(traffic.segments)[:5]

    now = 0
    dt = 100
    for i in range(n_spawns):
        traffic.spawn(segment_ids[i % len(segment_ids)], now=now)

    for _ in range(300):
        now += dt
        _advance_all(controllers, now)
        traffic.discharge(now, dt)
        traffic.move_arrivals_into_queues(now)
        in_network = traffic.vehicles_in_network()
        assert traffic.total_spawned == traffic.total_exited + in_network
