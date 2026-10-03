"""Headless simulation runner — this run's output is the Phase 2 baseline
(no routing/corridor/safety logic exists yet, so this is literally "what
happens without our system").

Run from the repo root:
  python -m backend.scripts.run_sim --seed 4417 --minutes 10 --peak \
      --route A6,B6,B5,C5,C4,D4,D3,E3,E2,F2,F1
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ on path

from core.general_city_loader import load_general_city  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.models import Priority  # noqa: E402
from sim.engine import Simulation  # noqa: E402

DEFAULT_ROUTE = "A6,B6,B5,C5,C4,D4,D3,E3,E2,F2,F1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=4417)
    parser.add_argument("--minutes", type=float, default=10.0)
    parser.add_argument("--peak", action="store_true", help="force peak-hour demand conditions")
    parser.add_argument(
        "--peak-multiplier",
        type=float,
        default=2.5,
        help="demand_peak_multiplier applied when --peak is set",
    )
    parser.add_argument("--route", type=str, default=DEFAULT_ROUTE)
    parser.add_argument("--vehicle-id", type=str, default="AMB-1")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    config_dir = repo_root / "config"

    settings = load_settings(config_dir / "settings.yaml")
    if args.peak:
        settings = settings.model_copy(update={"demand_peak_multiplier": args.peak_multiplier})
    city = load_general_city(config_dir / "cities" / "grid.city.json", settings)

    sim = Simulation(city, settings, seed=args.seed)
    route = args.route.split(",")
    sim.add_ambulance(args.vehicle_id, route, Priority.P1)

    wall_start = time.perf_counter()
    sim.run_for(round(args.minutes * 60 * 1000))
    wall_elapsed_s = time.perf_counter() - wall_start

    print_report(sim, args.vehicle_id, wall_elapsed_s)


def print_report(sim: Simulation, vehicle_id: str, wall_elapsed_s: float) -> None:
    metrics = sim.metrics

    spawned, exited, in_network = metrics.vehicle_conservation()
    conserved = spawned == exited + in_network
    print("=== Vehicle conservation ===")
    print(
        f"spawned={spawned} exited={exited} in_network={in_network} "
        f"conserved={'PASS' if conserved else 'FAIL'}"
    )
    print()

    print("=== Top 10 longest average queues ===")
    for segment_id, avg, mx in metrics.top_longest_queues(10):
        print(f"  {segment_id:16s} avg={avg:6.2f}  max={mx:3d}")
    print()

    print("=== Network-wide average delay ===")
    print(f"  {metrics.average_delay_ms() / 1000:.2f} s/vehicle")
    print()

    worst = metrics.worst_hold()
    print("=== Worst continuous red hold ===")
    if worst:
        node_id, direction, duration_ms = worst
        print(f"  {node_id} {direction}: {duration_ms / 1000:.1f} s")
    print()

    print(f"=== Ambulance {vehicle_id} ===")
    summary = metrics.ambulance_summary(vehicle_id)
    if summary["arrived"]:
        print(f"  travel_time: {summary['travel_time_ms'] / 1000:.1f} s")
    else:
        print("  did not arrive within the run duration")
    print(f"  stops: {summary['stops']}")
    print(f"  stopped_time: {summary['stopped_time_ms'] / 1000:.1f} s")
    print("  time per section:")
    for segment_id, ms in summary["section_times_ms"].items():
        print(f"    {segment_id:16s} {ms / 1000:.1f} s")
    print()

    print("=== Determinism ===")
    print(f"  final state_hash: {sim.state_hash()}")
    print()

    print("=== Performance ===")
    print(f"  wall-clock runtime: {wall_elapsed_s:.2f} s")


if __name__ == "__main__":
    main()
