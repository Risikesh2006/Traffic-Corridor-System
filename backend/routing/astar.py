"""A* over the edge-based graph (PRD FR-RT-01, 11.3; Phase 5 Part C1).

Heuristic: straight-line distance from a segment's end node to the nearest
goal, divided by the fastest speed an ambulance can reach. Every segment cost
is at least length / speed cap, and the straight line never exceeds the road
distance, so the heuristic never overestimates (admissible) and satisfies
h(s) <= c(s, s') + h(s') (consistent). Proof sketch and tests are in
docs/decisions.md (ADR-034) and tests/test_heuristic.py.

Tie-breaking is deterministic: frontier entries order by (f, g, insertion
counter), and every neighbour list is pre-sorted, so the same input always
returns the same route regardless of dict or set iteration order.

Time-dependent costs: g is the accumulated cost in seconds and the entry time
of each segment is now + g. A state keeps its cheapest arrival; with
time-dependent costs this is a standard approximation (ADR-035) and the
returned path is always re-evaluated exactly by `evaluate_path`.
"""

from __future__ import annotations

import heapq
import itertools
import math
from dataclasses import dataclass

from core.clock import SimTimeMs
from core.config import Settings
from routing.cost import RoutingContext, step_cost
from routing.graph import RoutingGraph


@dataclass(frozen=True)
class SearchResult:
    found: bool
    segments: tuple[str, ...]
    goal: str | None
    total_s: float
    expanded: int


def straight_line_bound_s(
    graph: RoutingGraph, settings: Settings, node_id: str, goals: frozenset[str]
) -> float:
    if not goals:
        return math.inf
    x, y = graph.coords(node_id)
    best = math.inf
    for goal in sorted(goals):
        gx, gy = graph.coords(goal)
        best = min(best, math.hypot(gx - x, gy - y))
    return best / settings.ambulance_speed_cap_mps


def search(
    graph: RoutingGraph,
    ctx: RoutingContext,
    settings: Settings,
    starts: list[tuple[str, float]],
    goals: frozenset[str],
    now: SimTimeMs,
    banned_segments: frozenset[str] = frozenset(),
    banned_transitions: frozenset[tuple[str, str]] = frozenset(),
) -> SearchResult:
    """Cheapest path from any start state to any goal node.

    `starts` are (segment, g) pairs, where g is the cost already committed to
    before entering that segment's frontier (for the segment the vehicle is on,
    this is the cost of its remaining part, computed by the caller)."""
    counter = itertools.count()
    frontier: list[tuple[float, float, int, str]] = []
    best_g: dict[str, float] = {}
    parent: dict[str, str | None] = {}
    closed: set[str] = set()
    expanded = 0

    for segment_id, g0 in sorted(starts):
        if math.isinf(g0) or segment_id in banned_segments:
            continue
        if g0 < best_g.get(segment_id, math.inf):
            best_g[segment_id] = g0
            parent[segment_id] = None
            h = straight_line_bound_s(graph, settings, graph.info(segment_id).to_node, goals)
            heapq.heappush(frontier, (g0 + h, g0, next(counter), segment_id))

    while frontier:
        _f, g, _c, segment_id = heapq.heappop(frontier)
        if segment_id in closed or g > best_g[segment_id]:
            continue
        closed.add(segment_id)
        expanded += 1
        info = graph.info(segment_id)
        if info.to_node in goals:
            return SearchResult(
                found=True,
                segments=tuple(_reconstruct(parent, segment_id)),
                goal=info.to_node,
                total_s=g,
                expanded=expanded,
            )
        for transition in graph.successors[segment_id]:
            nxt = transition.to_segment
            if nxt in closed or nxt in banned_segments:
                continue
            if (segment_id, nxt) in banned_transitions:
                continue
            step = step_cost(graph, ctx, settings, segment_id, nxt, g, now)
            if step.closed:
                continue
            ng = g + step.total()
            if ng < best_g.get(nxt, math.inf):
                best_g[nxt] = ng
                parent[nxt] = segment_id
                h = straight_line_bound_s(graph, settings, graph.info(nxt).to_node, goals)
                heapq.heappush(frontier, (ng + h, ng, next(counter), nxt))

    return SearchResult(found=False, segments=(), goal=None, total_s=math.inf, expanded=expanded)


def _reconstruct(parent: dict[str, str | None], end: str) -> list[str]:
    path = [end]
    current = parent[end]
    while current is not None:
        path.append(current)
        current = parent[current]
    path.reverse()
    return path
