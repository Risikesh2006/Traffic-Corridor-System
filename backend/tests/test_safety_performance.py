"""Performance micro-benchmark (PRD Phase 3 Part B4, §14.1): supervisor
validation must stay well under 5ms p95.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from core.general_city_loader import load_general_city
from core.clock import SimClock
from core.config import load_settings
from core.models import Command, CommandType, ControllerStatus
from safety.supervisor import IntersectionView, SafetySupervisor

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
GRID_PATH = CONFIG_DIR / "cities" / "grid.city.json"


def _view(intersection_id: str, reported_at: int, phase_id: str) -> IntersectionView:
    return IntersectionView(
        intersection_id=intersection_id,
        controller_status=ControllerStatus.ONLINE,
        reported_at=reported_at,
        current_phase_id=phase_id,
        red_since={},
    )


@pytest.mark.slow
def test_validate_p95_under_5ms() -> None:
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    city = load_general_city(GRID_PATH, settings)
    clock = SimClock()
    supervisor = SafetySupervisor(city, settings, clock, secret="perf-test-secret")
    node_ids = [n.id for n in city.intersections]
    first_phase_id = {n.id: n.phases[0].id for n in city.intersections}

    n = 5000
    durations_ms: list[float] = []
    for i in range(n):
        node_id = node_ids[i % len(node_ids)]
        cmd = Command(
            id=f"cmd-{i}",
            type=CommandType.PREEMPT,
            intersection_id=node_id,
            plan_version=i,
            idempotency_key=f"v{i}:{node_id}:{i % 10000:04d}",
            payload={"phase_id": first_phase_id[node_id], "hold_until": i * 10 + 20_000},
            created_at=i * 10,
        )
        clock.advance(10)
        start = time.perf_counter()
        supervisor.validate(cmd, _view(node_id, reported_at=clock.now(), phase_id=first_phase_id[node_id]))
        durations_ms.append((time.perf_counter() - start) * 1000)

    durations_ms.sort()
    p50 = durations_ms[int(n * 0.50)]
    p95 = durations_ms[int(n * 0.95)]
    print(f"\nsupervisor.validate() latency: p50={p50:.4f}ms p95={p95:.4f}ms over {n} calls")
    assert p95 < 5.0, f"p95={p95:.4f}ms exceeds the 5ms budget"
