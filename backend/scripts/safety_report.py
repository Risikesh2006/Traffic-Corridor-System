"""Runs the full-simulation chaotic-commander safety check across many
seeds and reports the evidence (PRD Phase 3 Part E).

Run from the repo root:
  python -m backend.scripts.safety_report --seeds 20 --minutes 10
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ on path

from chaos.commander import ChaoticCommander  # noqa: E402
from chaos.invariants import InvariantChecker  # noqa: E402
from core.general_city_loader import load_general_city  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.models import Priority  # noqa: E402
from safety.gate import SafeSignalLink  # noqa: E402
from safety.supervisor import IntersectionView, SafetySupervisor  # noqa: E402
from sim.engine import Simulation  # noqa: E402

SECRET = "safety-report-secret"
ROUTE = "A6,B6,B5,C5,C4,D4,D3,E3,E2,F2,F1".split(",")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--minutes", type=float, default=10.0)
    parser.add_argument("--start-seed", type=int, default=0)
    return parser.parse_args()


def run_one(
    seed: int, duration_ms: int, validate_durations_ms: list[float]
) -> tuple[InvariantChecker, ChaoticCommander]:
    settings = load_settings(Path(__file__).resolve().parents[2] / "config" / "settings.yaml")
    settings = settings.model_copy(
        update={
            "demand_peak_multiplier": 2.5,
            "hmac_secret": SECRET,
            "network_drop_probability": 0.1,
            "network_duplicate_probability": 0.1,
            "network_latency_jitter_ms": 50,
        }
    )
    city = load_general_city(
        Path(__file__).resolve().parents[2] / "config" / "cities" / "grid.city.json", settings
    )
    sim = Simulation(city, settings, seed=seed)
    sim.add_ambulance("AMB-1", ROUTE, Priority.P1)

    supervisor = SafetySupervisor(city, settings, sim.clock, secret=SECRET)
    _instrument_validate(supervisor, validate_durations_ms)

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
    commander = ChaoticCommander(city, list(sim.controllers), settings, sim.rng, gate)
    checker = InvariantChecker(city, settings, sim.controllers, hmac_secret=SECRET)

    class _Heartbeat:
        def on_tick(self, now: int) -> None:
            supervisor.heartbeat()

    class _Commander:
        def on_tick(self, now: int) -> None:
            commander.step(now)

    sim.add_observer(_Heartbeat())
    sim.add_observer(_Commander())
    sim.add_observer(checker)

    sim.run_for(duration_ms)
    return checker, commander


def _instrument_validate(supervisor: SafetySupervisor, out: list[float]) -> None:
    original = supervisor.validate

    def timed(command, view):  # type: ignore[no-untyped-def]
        start = time.perf_counter()
        result = original(command, view)
        out.append((time.perf_counter() - start) * 1000)
        return result

    supervisor.validate = timed  # type: ignore[method-assign]


def main() -> None:
    args = parse_args()
    duration_ms = round(args.minutes * 60 * 1000)

    total_sent = 0
    total_approved = 0
    total_executed = 0
    total_duplicates = 0
    total_stale_ignored = 0
    rejected_by_reason: Counter[str] = Counter()
    total_violations = 0
    validate_durations_ms: list[float] = []

    wall_start = time.perf_counter()
    for i in range(args.seeds):
        seed = args.start_seed + i
        checker, commander = run_one(seed, duration_ms, validate_durations_ms)
        total_sent += commander.sent
        total_approved += commander.approved
        total_executed += commander.executed
        total_duplicates += commander.duplicates
        total_stale_ignored += commander.stale_version_ignored
        rejected_by_reason.update(commander.rejected_by_reason)
        n_violations = len(checker.violations())
        total_violations += n_violations
        status = "OK" if n_violations == 0 else f"{n_violations} VIOLATIONS"
        print(
            f"seed={seed}: sent={commander.sent} approved={commander.approved} "
            f"executed={commander.executed} -> {status}"
        )
    wall_elapsed_s = time.perf_counter() - wall_start

    print()
    print("=== Commands ===")
    print(
        f"sent={total_sent} approved={total_approved} executed={total_executed} "
        f"duplicates={total_duplicates} stale_version_ignored={total_stale_ignored}"
    )
    print("rejected by reason:")
    for reason, count in sorted(rejected_by_reason.items()):
        print(f"  {reason}: {count}")
    print()

    print("=== Invariant violations ===")
    print(f"{total_violations} (must be 0)")
    print()

    validate_durations_ms.sort()
    n = len(validate_durations_ms)
    if n:
        p50 = validate_durations_ms[int(n * 0.50)]
        p95 = validate_durations_ms[int(n * 0.95)]
        print("=== Supervisor validation latency ===")
        print(f"p50={p50:.4f}ms p95={p95:.4f}ms over {n} calls")
        print()

    print("=== Performance ===")
    print(f"{args.seeds} seeds x {args.minutes} sim-minutes in {wall_elapsed_s:.2f}s wall time")

    if total_violations > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
