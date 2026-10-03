"""Edge cost model for routing (PRD 11.1, 11.2; Phase 5 Part B2-B3).

The cost of a segment is the time, in seconds, to traverse it and clear its
downstream stop line, decomposed into components so every route can be
explained:

  travel       BPR function of the density-derived volume/capacity ratio
  queue        clearance of the queue at the downstream stop line
  signal       expected red wait at the downstream stop line under normal
               operation (expected_red_fraction x cycle, ADR-036)
  turn         penalty for the turn that enters the segment
  uncertainty  penalty for stale or low-confidence data (never a bonus)
  uncontrollable  penalty when the downstream controller cannot be commanded

Density maps to the BPR ratio as x = min(density / critical_density, x_max),
with critical_density = jam_density * route_critical_density_fraction and
jam_density = 1000 / jam_spacing_m vehicles per km per lane. The fraction is
a documented modelling choice (ADR-033): half of jam density is where a
queue starts to visibly slow traffic.

Time-aware blending (look-ahead): a segment entered `dt` seconds from now is
costed with w * current + (1 - w) * historical(entry_time), where
w = exp(-dt / forecast_tau).

`step_cost` is the only place a segment's cost is computed. A* and path
evaluation both call it with the same accumulated cost, so the search and the
explanation can never disagree.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from core.clock import SimTimeMs
from core.config import Settings
from core.models import ControllerStatus, SensorMetric
from routing.graph import RoutingGraph, Turn


class RoutingContext(Protocol):
    def live(self, segment_id: str, metric: SensorMetric, now: SimTimeMs) -> tuple[float, float]:
        """Current (value, confidence) for a segment metric."""
        ...

    def prior(self, segment_id: str, metric: SensorMetric, sim_time: SimTimeMs) -> float:
        """Historical value for the time of day at `sim_time`."""
        ...

    def is_closed(self, segment_id: str) -> bool: ...

    def controller_status(self, node_id: str, now: SimTimeMs) -> ControllerStatus: ...

    def normal_red_wait_s(self, node_id: str) -> float:
        """Expected red wait at this node under normal operation."""
        ...


@dataclass(frozen=True)
class CostBreakdown:
    segment_id: str
    turn: Turn | None
    entry_ms: SimTimeMs
    travel_s: float
    queue_s: float
    turn_s: float
    uncertainty_s: float
    uncontrollable_s: float
    signal_s: float = 0.0
    closed: bool = False

    def total(self) -> float:
        if self.closed:
            return math.inf
        return (
            self.travel_s
            + self.queue_s
            + self.signal_s
            + self.turn_s
            + self.uncertainty_s
            + self.uncontrollable_s
        )

    def summary(self) -> str:
        if self.closed:
            return f"{self.segment_id}: closed"
        parts = [
            f"travel {self.travel_s:.1f}s",
            f"queue {self.queue_s:.1f}s",
            f"signal {self.signal_s:.1f}s",
            f"turn {self.turn_s:.1f}s",
            f"uncertainty {self.uncertainty_s:.1f}s",
            f"uncontrollable {self.uncontrollable_s:.1f}s",
        ]
        return f"{self.segment_id}: {self.total():.1f}s (" + ", ".join(parts) + ")"


def entry_ms_for(now: SimTimeMs, g_seconds: float) -> SimTimeMs:
    return now + round(g_seconds * 1000)


def step_cost(
    graph: RoutingGraph,
    ctx: RoutingContext,
    settings: Settings,
    prev_segment: str | None,
    segment_id: str,
    g_seconds: float,
    now: SimTimeMs,
    fraction: float = 1.0,
    is_current: bool = False,
) -> CostBreakdown:
    """Cost of traversing `segment_id` after accumulated cost `g_seconds`.

    `is_current` marks the segment the vehicle is already on: it is never
    treated as closed (the vehicle is on it regardless), and `fraction` is its
    remaining share. Other segments use fraction 1.0."""
    info = graph.info(segment_id)
    entry = entry_ms_for(now, g_seconds)

    if not is_current and ctx.is_closed(segment_id):
        return CostBreakdown(segment_id, None, entry, 0.0, 0.0, 0.0, 0.0, 0.0, closed=True)

    dt_s = max(0.0, (entry - now) / 1000.0)
    w = math.exp(-dt_s / (settings.forecast_tau_ms / 1000.0))

    density_now, confidence_now = ctx.live(segment_id, SensorMetric.DENSITY, now)
    density_prior = ctx.prior(segment_id, SensorMetric.DENSITY, entry)
    density = w * density_now + (1.0 - w) * density_prior
    jam_density = 1000.0 / settings.jam_spacing_m
    critical_density = jam_density * settings.route_critical_density_fraction
    x = min(density / critical_density, settings.route_x_max) if critical_density > 0 else 0.0
    free_amb_s = info.length_m / min(
        info.free_flow_mps * settings.ambulance_speed_factor, settings.ambulance_speed_cap_mps
    )
    travel = free_amb_s * (
        1.0 + settings.bpr_alpha * (x**settings.bpr_beta) * settings.congestion_sensitivity
    )

    queue_now, _ = ctx.live(segment_id, SensorMetric.QUEUE_COUNT, now)
    queue_prior = ctx.prior(segment_id, SensorMetric.QUEUE_COUNT, entry)
    queue_veh = w * queue_now + (1.0 - w) * queue_prior
    queue = (
        queue_veh / info.lanes * settings.headway_s + settings.startup_loss_s
        if queue_veh > 0
        else 0.0
    )

    turn: Turn | None = None
    turn_s = 0.0
    if prev_segment is not None:
        turn = graph.turn_between(prev_segment, segment_id)
        turn_s = _turn_penalty(turn, settings)

    confidence = w * confidence_now
    uncertainty = (1.0 - confidence) * settings.uncertainty_weight * travel

    uncontrollable = 0.0
    if ctx.controller_status(info.to_node, now) != ControllerStatus.ONLINE:
        uncontrollable = settings.uncontrollable_penalty_s

    signal = ctx.normal_red_wait_s(info.to_node)

    scale = fraction
    return CostBreakdown(
        segment_id=segment_id,
        turn=turn,
        entry_ms=entry,
        travel_s=travel * scale,
        queue_s=queue * scale,
        turn_s=turn_s,
        uncertainty_s=uncertainty * scale,
        uncontrollable_s=uncontrollable * scale,
        signal_s=signal * scale,
    )


def _turn_penalty(turn: Turn, settings: Settings) -> float:
    if turn == Turn.THROUGH:
        return settings.turn_penalty_through_s
    if turn == Turn.NEAR_SIDE:
        return settings.turn_penalty_near_side_s
    if turn == Turn.FAR_SIDE:
        return settings.turn_penalty_far_side_s
    raise ValueError("U-turns are not costed; the graph excludes them by default")


@dataclass(frozen=True)
class PathEvaluation:
    segments: tuple[str, ...]
    total_s: float
    breakdowns: tuple[CostBreakdown, ...]

    def explain(self) -> list[str]:
        return [bd.summary() for bd in self.breakdowns]


def evaluate_path(
    graph: RoutingGraph,
    ctx: RoutingContext,
    settings: Settings,
    segments: list[str] | tuple[str, ...],
    start_offset_m: float,
    now: SimTimeMs,
) -> PathEvaluation:
    """Cost of a complete path under the current model, at time `now`.

    The first segment is charged only for the part still ahead of the vehicle."""
    if not segments:
        return PathEvaluation((), 0.0, ())
    first = graph.info(segments[0])
    remaining = max(0.0, first.length_m - start_offset_m) / first.length_m
    breakdowns: list[CostBreakdown] = []
    g = 0.0
    prev: str | None = None
    for index, segment_id in enumerate(segments):
        is_current = index == 0
        fraction = remaining if is_current else 1.0
        bd = step_cost(
            graph, ctx, settings, prev, segment_id, g, now, fraction, is_current=is_current
        )
        breakdowns.append(bd)
        g += bd.total()
        if math.isinf(g):
            break
        prev = segment_id
    return PathEvaluation(tuple(segments), g, tuple(breakdowns))
