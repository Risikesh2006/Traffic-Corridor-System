"""Routing report (PRD FR-RT, Phase 5 Part E2).

Runs the same seed twice with the same injected congestion: once with the
ambulance following the planner (dispatched to a hospital), and once on the
fixed Phase 2 baseline route. Prints the initial route and its cost
breakdown, the alternatives, every routing decision with its explanation,
switch counts, planner latency, and the trip outcome of both runs.

    python -m backend.scripts.routing_report --seed 4417 --minutes 10 \
        --jam C5->C4:120:300

Times for --jam and --close are absolute simulation seconds.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ on path

from core.models import Priority  # noqa: E402
from routing.cost import evaluate_path  # noqa: E402
from routing.planner import RouteDecision  # noqa: E402
from runtime.composition import Runtime, build_runtime  # noqa: E402

VEHICLE = "AMB-1"
BASELINE_ROUTE = ["A6", "B6", "B5", "C5", "C4", "D4", "D3", "E3", "E2", "F2", "F1"]
WARMUP_S = 15
SNAPSHOT_S = 100
ADOPTED_OUTCOMES = {"ADOPTED", "FAST_PATH"}


@dataclass
class Jam:
    segment: str
    start_s: int
    duration_s: int
    vph: float


@dataclass
class Closure:
    segment: str
    at_s: int


@dataclass
class RunResult:
    label: str
    arrived: bool
    travel_s: float | None
    stops: int
    stopped_s: float
    decisions: list[RouteDecision] = field(default_factory=list)
    initial_route: list[str] = field(default_factory=list)
    initial_breakdown: list[str] = field(default_factory=list)
    snapshot_alternatives: list[str] = field(default_factory=list)
    planner_ms: list[float] = field(default_factory=list)
    yen_expansions: int = 0
    steps: int = 0
    closure_applied_s: float | None = None
    initial_cost_s: float | None = None
    baseline_model_cost_s: float | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--seed", type=int, default=4417)
    parser.add_argument("--minutes", type=float, default=10.0)
    parser.add_argument("--peak", type=float, default=2.5)
    parser.add_argument("--hospital", default="H1", help="hospital id for the planned run")
    parser.add_argument("--jam", default="C5->C4:120:300", help="SEGMENT:START_S:DURATION_S")
    parser.add_argument("--jam-vph", type=float, default=3000.0)
    parser.add_argument("--close", default=None, help="SEGMENT:AT_S closes a road (fast-path test)")
    return parser.parse_args()


def parse_jam(text: str, vph: float) -> Jam:
    segment, start, duration = text.rsplit(":", 2)
    return Jam(segment, int(start), int(duration), vph)


def parse_closure(text: str) -> Closure:
    segment, at = text.rsplit(":", 1)
    return Closure(segment, int(at))


def _time_planner(runtime: Runtime, samples: list[float]) -> None:
    planner = runtime.central.planner
    original = planner.step

    def timed(now: int) -> None:
        start = time.perf_counter()
        original(now)
        samples.append((time.perf_counter() - start) * 1000.0)

    planner.step = timed  # type: ignore[method-assign]


def _describe(decision: RouteDecision) -> str:
    stamp = f"t={decision.at_ms / 1000:7.1f}s"
    versions = (
        f"v{decision.version_from}->v{decision.version_to}"
        if decision.version_to is not None
        else f"v{decision.version_from}"
    )
    triggers = ",".join(decision.triggers)
    rejected = f"  [rejected: {'; '.join(decision.rejected)}]" if decision.rejected else ""
    return (
        f"{stamp}  {decision.outcome:<16} {versions:<10} "
        f"[{triggers}] {decision.explanation}{rejected}"
    )


def run(
    label: str,
    args: argparse.Namespace,
    jam: Jam,
    closure: Closure | None,
    planned: bool,
) -> RunResult:
    runtime = build_runtime(seed=args.seed, peak_multiplier=args.peak)
    runtime.sim.traffic.inject_congestion(
        jam.segment, jam.vph, jam.start_s * 1000, (jam.start_s + jam.duration_s) * 1000
    )
    samples: list[float] = []
    _time_planner(runtime, samples)
    runtime.run_for(WARMUP_S * 1000)

    result = RunResult(label=label, arrived=False, travel_s=None, stops=0, stopped_s=0.0)
    if planned:
        route = runtime.dispatch(VEHICLE, Priority.P1, args.hospital)
        if route is None:
            raise SystemExit(f"no route to hospital {args.hospital}")
    else:
        route = list(BASELINE_ROUTE)
        runtime.add_ambulance(VEHICLE, route, Priority.P1)
    result.initial_route = list(route)
    start_ms = runtime.sim.clock.now()
    planner = runtime.central.planner

    if planned:
        plan = planner.active_route(VEHICLE)
        segments = [f"{plan.nodes[i]}->{plan.nodes[i + 1]}" for i in range(len(plan.nodes) - 1)]
        evaluation = evaluate_path(
            runtime.central.graph,
            runtime.central.routing_context,
            runtime.settings,
            segments,
            0.0,
            start_ms,
        )
        result.initial_breakdown = evaluation.explain()
        result.initial_cost_s = evaluation.total_s
        baseline_segments = [
            f"{a}->{b}" for a, b in zip(BASELINE_ROUTE, BASELINE_ROUTE[1:], strict=False)
        ]
        result.baseline_model_cost_s = evaluate_path(
            runtime.central.graph,
            runtime.central.routing_context,
            runtime.settings,
            baseline_segments,
            0.0,
            start_ms,
        ).total_s

    horizon_ms = int(args.minutes * 60 * 1000)
    closure_done = False
    snapshot_done = False
    while runtime.sim.clock.now() - start_ms < horizon_ms:
        elapsed_s = (runtime.sim.clock.now() - start_ms) / 1000.0
        absolute_s = runtime.sim.clock.now() / 1000.0
        if closure is not None and not closure_done and absolute_s >= closure.at_s:
            runtime.central.world.close_road([closure.segment], "scenario closure")
            result.closure_applied_s = absolute_s
            closure_done = True
        if planned and not snapshot_done and elapsed_s >= SNAPSHOT_S:
            result.snapshot_alternatives = [
                f"{alt.total_s:6.1f} s  overlap {alt.overlap:4.0%}  via "
                f"{' > '.join(s.split('->')[1] for s in alt.segments[:3])}"
                for alt in planner.alternatives(VEHICLE)
            ]
            snapshot_done = True
        runtime.run_for(1000)
        amb = runtime.sim.ambulances.ambulances[VEHICLE]
        if amb.arrival_time is not None:
            break

    amb = runtime.sim.ambulances.ambulances[VEHICLE]
    result.arrived = amb.arrival_time is not None
    if amb.arrival_time is not None:
        result.travel_s = (amb.arrival_time - amb.start_time) / 1000.0 if amb.start_time else None
    result.stops = amb.total_stops
    result.stopped_s = amb.total_stopped_time_ms / 1000.0
    result.planner_ms = samples
    result.steps = len(samples)
    if planned:
        result.decisions = planner.decisions(VEHICLE)
        result.yen_expansions = planner.yen_expansions(VEHICLE)
    return result


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(pct / 100.0 * (len(ordered) - 1)))
    return ordered[index]


def _print_planned(result: RunResult, args: argparse.Namespace) -> None:
    print("INITIAL ROUTE (planned)")
    print("  " + " > ".join(result.initial_route))
    print("  cost breakdown (dispatch time):")
    for line in result.initial_breakdown:
        print(f"    {line}")
    print(f"ALTERNATIVES at t={SNAPSHOT_S}s")
    for line in result.snapshot_alternatives or ["(none)"]:
        print(f"  {line}")
    if result.initial_cost_s is not None and result.baseline_model_cost_s is not None:
        print(
            f"  model cost at dispatch: planned route {result.initial_cost_s:.0f} s, "
            f"fixed baseline route {result.baseline_model_cost_s:.0f} s"
        )
    print("ROUTING DECISIONS")
    for decision in result.decisions:
        print(f"  {_describe(decision)}")
    switches = [d for d in result.decisions if d.outcome in ADOPTED_OUTCOMES]
    kept = [d for d in result.decisions if d.outcome == "KEPT"]
    print(f"SWITCHES: {len(switches)}   KEPT (rejected candidates): {len(kept)}")
    print()


def _print_outcome(results: list[RunResult]) -> None:
    print("TRIP OUTCOME")
    print(f"  {'run':<28}{'arrived':>8}{'travel s':>10}{'stops':>7}{'stopped s':>11}")
    for r in results:
        travel = f"{r.travel_s:8.1f}" if r.travel_s is not None else "       -"
        print(f"  {r.label:<28}{str(r.arrived):>8}{travel:>10}{r.stops:>7}{r.stopped_s:>11.1f}")
    print()


def _print_latency(planned: RunResult) -> None:
    ms = planned.planner_ms
    print("PLANNER LATENCY (wall clock per central step, planner only)")
    print(
        f"  steps {len(ms)}   p50 {_percentile(ms, 50):.2f} ms   "
        f"p95 {_percentile(ms, 95):.2f} ms   max {max(ms, default=0):.2f} ms"
    )
    expansions = [d.expansions for d in planned.decisions if d.expansions]
    if expansions:
        print(
            f"  A* expansions per plan: median {statistics.median(expansions):.0f}, "
            f"max {max(expansions)}"
        )
    if planned.steps:
        print(
            f"  Yen expansions (total over run): {planned.yen_expansions}, "
            f"per step {planned.yen_expansions / planned.steps:.1f}"
        )
    print()


def main() -> None:
    args = parse_args()
    jam = parse_jam(args.jam, args.jam_vph)
    closure = parse_closure(args.close) if args.close else None
    print(
        f"seed {args.seed}  minutes {args.minutes}  peak x{args.peak}  "
        f"jam {args.jam} @ {args.jam_vph:.0f} vph" + (f"  close {args.close}" if args.close else "")
    )
    print()
    planned = run("planner (dispatched)", args, jam, closure, planned=True)
    baseline = run("fixed baseline route", args, jam, closure, planned=False)
    _print_planned(planned, args)
    _print_latency(planned)
    _print_outcome([planned, baseline])


if __name__ == "__main__":
    main()
