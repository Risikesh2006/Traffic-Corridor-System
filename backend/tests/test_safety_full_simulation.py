"""Full-simulation safety test (PRD Phase 3 Part D4): the real Phase 2
simulation (peak traffic, an ambulance on its route) runs for several
simulated minutes while a ChaoticCommander fires random PREEMPT/RENEW/
RELEASE commands through the SafeSignalLink gate at random intersections,
and the network randomly drops/duplicates/jitters messages. The
InvariantChecker must report zero violations throughout.

This pytest version uses a smaller seed/duration sweep (marked `slow`) to
stay CI-reasonable; `backend/scripts/safety_report.py` runs the full
20-seed x 10-minute sweep the PRD specifies and is the authoritative
verification artifact (see docs/safety_report_example.txt).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaos.commander import ChaoticCommander
from chaos.invariants import InvariantChecker
from core.city_loader import load_city
from core.config import load_settings
from core.models import Priority
from safety.gate import SafeSignalLink
from safety.supervisor import IntersectionView, SafetySupervisor
from sim.engine import Simulation

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
SECRET = "full-sim-test-secret"
ROUTE = "A6,B6,B5,C5,C4,D4,D3,E3,E2,F2,F1".split(",")


def _run_one(seed: int, duration_ms: int) -> InvariantChecker:
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    settings = settings.model_copy(
        update={
            "demand_peak_multiplier": 2.5,
            "hmac_secret": SECRET,
            "network_drop_probability": 0.1,
            "network_duplicate_probability": 0.1,
            "network_latency_jitter_ms": 50,
        }
    )
    city = load_city(CONFIG_DIR / "city.json", settings)
    sim = Simulation(city, settings, seed=seed)
    sim.add_ambulance("AMB-1", ROUTE, Priority.P1)

    supervisor = SafetySupervisor(city, settings, sim.clock, secret=SECRET)

    def view_source(intersection_id: str) -> IntersectionView:
        controller = sim.controllers[intersection_id]
        sm = controller.state_machine
        return IntersectionView(
            intersection_id=intersection_id,
            controller_status=sim.network.controller_status(intersection_id),
            reported_at=sim.clock.now(),
            current_phase_id=sm.current_phase_id,
            red_since=dict(sm.red_since),
        )

    gate = SafeSignalLink(sim.network, supervisor, view_source, sim.clock)
    commander = ChaoticCommander(list(sim.controllers), settings, sim.rng, gate)
    checker = InvariantChecker(city, settings, sim.controllers, hmac_secret=SECRET)

    class _SupervisorHeartbeat:
        def on_tick(self, now: int) -> None:
            supervisor.heartbeat()

    class _Commander:
        def on_tick(self, now: int) -> None:
            commander.step(now)

    sim.add_observer(_SupervisorHeartbeat())
    sim.add_observer(_Commander())
    sim.add_observer(checker)

    sim.run_for(duration_ms)
    return checker


@pytest.mark.slow
@pytest.mark.parametrize("seed", range(5))
def test_full_simulation_zero_violations(seed: int) -> None:
    checker = _run_one(seed, duration_ms=2 * 60 * 1000)
    checker.assert_clean()
