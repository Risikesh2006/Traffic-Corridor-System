"""Tracking and world-state evaluation against ground truth (PRD Phase 4 Part F).

Run from the repo root:
  python -m backend.scripts.tracking_report --seed 4417 --minutes 10 \
      --gps-loss 60:20 --stale-sensors B:120:90

--gps-loss START:DURATION   GPS off for DURATION seconds starting at START seconds
--stale-sensors COL:START:DURATION   sensors on column COL frozen for DURATION s
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ on path

from core.models import Priority  # noqa: E402
from runtime.composition import build_runtime  # noqa: E402
from runtime.evaluation import Evaluation, Scenario, percentile  # noqa: E402

ROUTE = "A6,B6,B5,C5,C4,D4,D3,E3,E2,F2,F1".split(",")
VEHICLE = "AMB-1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=4417)
    parser.add_argument("--minutes", type=float, default=10.0)
    parser.add_argument("--gps-loss", type=str, default="")
    parser.add_argument("--stale-sensors", type=str, default="")
    return parser.parse_args()


def build_scenario(args: argparse.Namespace) -> Scenario:
    kwargs: dict[str, object] = {}
    if args.gps_loss:
        start, duration = args.gps_loss.split(":")
        kwargs.update(gps_loss_start_s=float(start), gps_loss_duration_s=float(duration))
    if args.stale_sensors:
        column, start, duration = args.stale_sensors.split(":")
        kwargs.update(
            stale_column=column, stale_start_s=float(start), stale_duration_s=float(duration)
        )
    return Scenario(**kwargs)  # type: ignore[arg-type]


def _fmt(values: list[float], unit: str) -> str:
    if not values:
        return "no samples"
    return (
        f"p50={percentile(values, 0.5):.2f}{unit} p95={percentile(values, 0.95):.2f}{unit} "
        f"(n={len(values)})"
    )


def main() -> None:
    args = parse_args()
    runtime = build_runtime(seed=args.seed, peak_multiplier=2.5)
    runtime.add_ambulance(VEHICLE, ROUTE, Priority.P1)
    evaluation = Evaluation(runtime, VEHICLE, ROUTE, build_scenario(args))
    report = evaluation.run(round(args.minutes * 60 * 1000))

    print("=== Position error (tracked vs true, metres) ===")
    print(f"  with GPS:          {_fmt(report.position_error_gps_m, 'm')}")
    print(f"  dead reckoning:    {_fmt(report.position_error_dr_m, 'm')}")
    print()

    print("=== ETA error vs true arrival (seconds) ===")
    for h in (30, 60):
        samples = [s for s in report.eta_samples if s.horizon_s == h]
        if not samples:
            print(f"  {h}s horizon: no samples")
            continue
        exp_err = [abs(s.error_expected_s) for s in samples]
        unimp_err = [abs(s.error_unimpeded_s) for s in samples]
        exp_bias = median(s.error_expected_s for s in samples)
        unimp_bias = median(s.error_unimpeded_s for s in samples)
        inside = sum(1 for s in samples if s.inside_window) / len(samples) * 100
        print(
            f"  {h}s horizon  expected:  abs {_fmt(exp_err, 's')}  median signed {exp_bias:+.1f}s"
        )
        print(
            f"  {h}s horizon  unimpeded: abs {_fmt(unimp_err, 's')}"
            f"  median signed {unimp_bias:+.1f}s"
        )
        print(f"  {h}s horizon  arrival inside [eta_low, eta_high]: {inside:.1f}%")
    print()

    print("=== Density estimate error (veh/km/lane) ===")
    print(f"  fresh segments:  {_fmt(report.density_error_fresh, '')}")
    print(f"  stale segments:  {_fmt(report.density_error_stale, '')}")
    print()

    print("=== Gateway counters (per source) ===")
    totals: dict[str, int] = {}
    for counters in report.gateway_stats.values():
        for reason, count in counters.items():
            totals[reason] = totals.get(reason, 0) + count
    for reason in sorted(totals):
        print(f"  {reason}: {totals[reason]}")
    print()

    print("=== Tracking events ===")
    print(f"  route deviation events: {report.deviation_events} (true route: 0 expected)")
    print(f"  stop-line passage timing error (s): {_fmt(report.stop_line_errors_s, 's')}")
    print()

    print("=== Latency ===")
    print(f"  ingestion per input (ms): {_fmt(report.ingest_ms, 'ms')}")
    print(f"  central step per tick (ms): {_fmt(report.central_step_ms, 'ms')}")


if __name__ == "__main__":
    main()
