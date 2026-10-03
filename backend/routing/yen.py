"""Alternative routes with Yen's algorithm and a diversity filter (FR-RT-03,
PRD 11.4; Phase 5 Part C2).

Yen's algorithm produces loopless paths in non-decreasing cost. Each new
candidate is found by a spur search from one segment of an already-known
path, with the transitions that earlier paths used from that spur point and
the root segments before it removed. Candidates are ranked by exact
re-evaluation (`evaluate_path`), so ordering matches the explanation shown to
the operator.

Diversity: a candidate is kept as an alternative only if it shares less than
`route_alternative_overlap_max` of its length with the primary route and with
every alternative already kept. A backup that shares most of the road with
the primary fails together with it when that road closes, so it is useless.

Compute budget: the work is spread across central steps. `advance(budget)`
runs spur searches until the expansions used reach the budget, then returns.
It resumes from the same queue position next call, so the result is the same
whether it is computed in one call or many. A spur search is never split, so
one step can overrun the budget by at most one search.
"""

from __future__ import annotations

import heapq
import itertools
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field

from core.config import Settings
from routing.astar import search
from routing.cost import RoutingContext, evaluate_path
from routing.graph import RoutingGraph

MAX_CANDIDATE_PATHS = 40


@dataclass(frozen=True)
class Alternative:
    segments: tuple[str, ...]
    total_s: float
    overlap: float


@dataclass
class KShortestPaths:
    graph: RoutingGraph
    ctx: RoutingContext
    settings: Settings
    primary: tuple[str, ...]
    start_offset_m: float
    goals: frozenset[str]
    now: int
    k: int
    base_banned: frozenset[str] = frozenset()
    _paths: list[tuple[str, ...]] = field(default_factory=list)
    _candidates: list[tuple[float, int, tuple[str, ...]]] = field(default_factory=list)
    _seen: set[tuple[str, ...]] = field(default_factory=set)
    _spurs: deque[tuple[int, int]] = field(default_factory=deque)
    _counter: Iterator[int] = field(default_factory=itertools.count)
    alternatives: list[Alternative] = field(default_factory=list)
    expanded_total: int = 0
    done: bool = False

    def __post_init__(self) -> None:
        self._seen.add(self.primary)
        self._paths.append(self.primary)
        self._enqueue_spurs(0)
        self._primary_cost = evaluate_path(
            self.graph, self.ctx, self.settings, self.primary, self.start_offset_m, self.now
        ).total_s

    def advance(self, budget_expansions: int) -> None:
        used = 0
        while not self.done and used < budget_expansions:
            if self._spurs:
                path_index, spur_index = self._spurs.popleft()
                used += self._spur(path_index, spur_index)
                continue
            if len(self.alternatives) >= self.k or len(self._paths) >= MAX_CANDIDATE_PATHS:
                self.done = True
                break
            if not self._candidates:
                self.done = True
                break
            _cost, _c, path = heapq.heappop(self._candidates)
            if path in self._seen:
                continue
            self._seen.add(path)
            self._paths.append(path)
            self._enqueue_spurs(len(self._paths) - 1)
            self._consider_alternative(path)

    def _enqueue_spurs(self, path_index: int) -> None:
        path = self._paths[path_index]
        for spur_index in range(len(path) - 1):
            self._spurs.append((path_index, spur_index))

    def _spur(self, path_index: int, spur_index: int) -> int:
        path = self._paths[path_index]
        root = path[: spur_index + 1]
        spur_segment = root[-1]
        banned_transitions = frozenset(
            (spur_segment, other[spur_index + 1])
            for other in self._paths
            if other[: spur_index + 1] == root and len(other) > spur_index + 1
        )
        banned_segments = self.base_banned | frozenset(root[:-1])
        root_cost = evaluate_path(
            self.graph, self.ctx, self.settings, root, self.start_offset_m, self.now
        ).total_s
        result = search(
            self.graph,
            self.ctx,
            self.settings,
            starts=[(spur_segment, root_cost)],
            goals=self.goals,
            now=self.now,
            banned_segments=banned_segments,
            banned_transitions=banned_transitions,
        )
        self.expanded_total += result.expanded
        if result.found:
            candidate = root[:-1] + result.segments
            if candidate not in self._seen:
                total = evaluate_path(
                    self.graph, self.ctx, self.settings, candidate, self.start_offset_m, self.now
                ).total_s
                heapq.heappush(self._candidates, (total, next(self._counter), candidate))
        return result.expanded

    def _consider_alternative(self, path: tuple[str, ...]) -> None:
        overlap = self._max_overlap(path)
        if overlap >= self.settings.route_alternative_overlap_max:
            return
        total = evaluate_path(
            self.graph, self.ctx, self.settings, path, self.start_offset_m, self.now
        ).total_s
        self.alternatives.append(Alternative(path, total, overlap))

    def _max_overlap(self, path: tuple[str, ...]) -> float:
        length = sum(self.graph.info(s).length_m for s in path)
        if length <= 0:
            return 1.0
        best = 0.0
        reference = [self.primary] + [alt.segments for alt in self.alternatives]
        for other in reference:
            shared = set(path) & set(other)
            shared_len = sum(self.graph.info(s).length_m for s in shared)
            best = max(best, shared_len / length)
        return best

    def primary_cost(self) -> float:
        return self._primary_cost

    def is_complete(self) -> bool:
        return self.done
