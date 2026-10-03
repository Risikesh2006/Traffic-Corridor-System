"""Scored map matching of GPS fixes onto road segments (PRD FR-TRK-02).

Candidates are segments whose geometry lies within `map_match_radius_m` of
the fix, found through a uniform grid index so matching cost does not grow
with network size. Each candidate is scored (lower is better):

  score = distance_m / radius
        + HEADING_WEIGHT * heading_penalty      (0 = agrees, 1 = opposite)
        - ROUTE_BONUS    if on the vehicle's route
        - CONTINUITY_BONUS if it is the previously matched segment or
                              one of its successors

Heading disambiguates opposite directions of the same physical corridor —
both carriageways are geometrically identical, so distance alone can't tell
them apart. ADR-022 records why this scoring rather than HMM map matching
(Newson & Krumm), which is the more robust alternative for dense urban
networks but needs transition probabilities we don't yet have.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from core.models import City

HEADING_WEIGHT = 0.5
ROUTE_BONUS = 0.3
CONTINUITY_BONUS = 0.2
GRID_CELL_M = 100.0


@dataclass(frozen=True)
class MatchResult:
    segment_id: str
    offset_m: float  # distance along the matched segment from its from_node
    distance_m: float
    score: float
    quality: float  # 0..1, higher is more certain


def _bearing_deg(dx: float, dy: float) -> float:
    return math.degrees(math.atan2(dy, dx)) % 360.0


def _heading_penalty(fix_heading: float, segment_bearing: float) -> float:
    diff = math.radians(fix_heading - segment_bearing)
    return (1.0 - math.cos(diff)) / 2.0


class MapMatcher:
    def __init__(self, city: City, radius_m: float) -> None:
        self.city = city
        self.radius_m = radius_m
        self._geometry: dict[str, tuple[float, float, float, float, float]] = {}
        self._grid: dict[tuple[int, int], list[str]] = defaultdict(list)

        for road in sorted(city.roads, key=lambda r: r.id):
            a = city.intersection(road.from_node)
            b = city.intersection(road.to_node)
            bearing = _bearing_deg(b.x - a.x, b.y - a.y)
            self._geometry[road.id] = (a.x, a.y, b.x, b.y, bearing)
            for cell in self._cells_for_segment(a.x, a.y, b.x, b.y):
                self._grid[cell].append(road.id)

    @staticmethod
    def _cell(x: float, y: float) -> tuple[int, int]:
        return int(math.floor(x / GRID_CELL_M)), int(math.floor(y / GRID_CELL_M))

    def _cells_for_segment(
        self, ax: float, ay: float, bx: float, by: float
    ) -> set[tuple[int, int]]:
        cells = set()
        steps = max(1, int(math.hypot(bx - ax, by - ay) / GRID_CELL_M) + 1)
        for i in range(steps + 1):
            t = i / steps
            cells.add(self._cell(ax + (bx - ax) * t, ay + (by - ay) * t))
        return cells

    def _candidates(self, x: float, y: float) -> list[str]:
        reach = int(math.ceil(self.radius_m / GRID_CELL_M))
        cx, cy = self._cell(x, y)
        found: set[str] = set()
        for dx in range(-reach, reach + 1):
            for dy in range(-reach, reach + 1):
                found.update(self._grid.get((cx + dx, cy + dy), ()))
        return sorted(found)

    def _distance_and_offset(self, segment_id: str, x: float, y: float) -> tuple[float, float]:
        ax, ay, bx, by, _ = self._geometry[segment_id]
        length = math.hypot(bx - ax, by - ay)
        if length == 0:
            return math.hypot(x - ax, y - ay), 0.0
        t = ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / (length * length)
        t = min(1.0, max(0.0, t))
        px, py = ax + (bx - ax) * t, ay + (by - ay) * t
        return math.hypot(x - px, y - py), t * length

    def match(
        self,
        x: float,
        y: float,
        heading_deg: float,
        route_segments: frozenset[str] = frozenset(),
        previous_segment: str | None = None,
    ) -> MatchResult | None:
        successors: set[str] = set()
        if previous_segment is not None:
            prev = self.city.segment(previous_segment)
            successors = {r.id for r in self.city.outgoing(prev.to_node)}

        best: MatchResult | None = None
        for segment_id in self._candidates(x, y):
            distance, offset = self._distance_and_offset(segment_id, x, y)
            if distance > self.radius_m:
                continue
            bearing = self._geometry[segment_id][4]
            score = distance / self.radius_m + HEADING_WEIGHT * _heading_penalty(
                heading_deg, bearing
            )
            if segment_id in route_segments:
                score -= ROUTE_BONUS
            if segment_id == previous_segment or segment_id in successors:
                score -= CONTINUITY_BONUS
            candidate = MatchResult(
                segment_id=segment_id,
                offset_m=offset,
                distance_m=distance,
                score=score,
                quality=max(0.0, 1.0 - distance / self.radius_m),
            )
            if best is None or (candidate.score, candidate.segment_id) < (
                best.score,
                best.segment_id,
            ):
                best = candidate
        return best
