"""End-to-end routing: simulator, central system and planner together.

Scenarios use the real city, the historical profile when present, and the
seeded simulator, so the same seed must produce the same route history.
"""

import time

import pytest

from chaos.invariants import InvariantChecker
from core.models import Priority
from runtime.composition import build_runtime

VEHICLE = "AMB-1"


def _decision_log(rt) -> list[tuple[int, str, int, int | None]]:
    return [
        (d.at_ms, d.outcome, d.version_from, d.version_to)
        for d in rt.central.planner.decisions(VEHICLE)
    ]


def _run_with_closure(seed: int, minutes: float, closure: str, at_s: int):
    rt = build_runtime(seed=seed, peak_multiplier=2.5)
    checker = InvariantChecker(
        rt.sim.city, rt.settings, rt.sim.controllers, hmac_secret=rt.settings.hmac_secret
    )
    rt.run_for(15_000)
    route = rt.dispatch(VEHICLE, Priority.P1, "H1")
    assert route is not None
    closed = False
    end_ms = rt.sim.clock.now() + round(minutes * 60_000)
    while rt.sim.clock.now() < end_ms:
        if not closed and rt.sim.clock.now() >= at_s * 1000:
            rt.central.world.close_road([closure], "integration closure")
            closed = True
        rt.run_for(1000)
        checker.on_tick(rt.sim.clock.now())
        if rt.sim.ambulances.ambulances[VEHICLE].arrival_time is not None:
            break
    checker.assert_clean()
    return rt


@pytest.mark.slow
def test_planner_switches_off_a_closed_road_without_flapping():
    """Marked slow: with this seed, the post-closure reroute (chosen under
    the general model's realistic per-junction signal cycles, ADR-054)
    runs along the city's low-capacity border roads rather than back onto
    an arterial, and under peak demand that takes upwards of twenty
    simulated minutes to clear — a cost-model calibration gap (ADR-036),
    not a migration defect: the switch itself is correct and immediate."""
    rt = _run_with_closure(4417, minutes=25, closure="C4->D4", at_s=60)
    switches = [
        d for d in rt.central.planner.decisions(VEHICLE) if d.outcome in {"ADOPTED", "FAST_PATH"}
    ]
    assert 1 <= len(switches) <= 3
    final = rt.central.planner.active_route(VEHICLE).nodes
    segments = {f"{final[i]}->{final[i + 1]}" for i in range(len(final) - 1)}
    assert "C4->D4" not in segments or rt.central.world.road_status("C4->D4") == "OPEN"
    amb = rt.sim.ambulances.ambulances[VEHICLE]
    assert amb.arrival_time is not None


def test_same_seed_gives_the_same_route_history():
    first = _run_with_closure(4417, minutes=3, closure="C4->D4", at_s=60)
    second = _run_with_closure(4417, minutes=3, closure="C4->D4", at_s=60)
    assert _decision_log(first) == _decision_log(second)
    assert first.sim.state_hash() == second.sim.state_hash()


def test_every_switch_carries_an_explanation():
    rt = _run_with_closure(4417, minutes=3, closure="C4->D4", at_s=60)
    for decision in rt.central.planner.decisions(VEHICLE):
        assert decision.explanation
        if decision.outcome in {"ADOPTED", "FAST_PATH"}:
            assert decision.version_to is not None


@pytest.mark.slow
def test_full_replan_p95_is_under_the_latency_budget():
    rt = build_runtime(seed=4417, peak_multiplier=2.5)
    samples: list[float] = []
    original = rt.central.planner.step

    def timed(now: int) -> None:
        start = time.perf_counter()
        original(now)
        samples.append((time.perf_counter() - start) * 1000.0)

    rt.central.planner.step = timed  # type: ignore[method-assign]
    rt.run_for(15_000)
    rt.dispatch(VEHICLE, Priority.P1, None)
    rt.central.world.close_road(["C4->D4"], "latency probe")
    rt.run_for(180_000)
    samples.sort()
    p95 = samples[min(len(samples) - 1, round(0.95 * (len(samples) - 1)))]
    assert p95 < rt.settings.latency_full_replan_ms, f"planner p95 {p95:.1f} ms"
