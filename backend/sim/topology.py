"""Geometric helpers derived from `City` coordinates (PRD §9 ADR-004
convention: an approach's direction is the compass side it arrives from).

Pure and data-driven — works for any grid-aligned city, not hardcoded to the
default 6x6 layout, since it reads node (x, y) rather than parsing IDs.
"""

from __future__ import annotations

from core.models import City, Direction, RoadSegment


def approach_direction(city: City, road: RoadSegment) -> Direction:
    """The approach direction `road` feeds at its `to_node` — e.g. a segment
    arriving from the west (from_node.x < to_node.x) feeds the W approach."""
    origin = city.intersection(road.from_node)
    dest = city.intersection(road.to_node)
    dx, dy = origin.x - dest.x, origin.y - dest.y
    if dx != 0:
        return Direction.W if dx < 0 else Direction.E
    if dy != 0:
        return Direction.N if dy < 0 else Direction.S
    raise ValueError(f"road {road.id} has zero length between identical coordinates")


def compute_approach_directions(city: City) -> dict[str, Direction]:
    """segment_id -> the approach direction it feeds at its to_node."""
    return {road.id: approach_direction(city, road) for road in city.roads}


def compute_exits(city: City) -> dict[str, dict[Direction, str]]:
    """node_id -> {compass direction of travel leaving the node: segment_id}."""
    exits: dict[str, dict[Direction, str]] = {n.id: {} for n in city.intersections}
    for road in city.roads:
        # The segment leaves `from_node` heading toward `to_node`; that's the
        # opposite compass direction from the one it arrives at `to_node`.
        arrival_direction = approach_direction(city, road)
        departure_direction = _opposite(arrival_direction)
        exits[road.from_node][departure_direction] = road.id
    return exits


def is_border_intersection(city: City, node_id: str) -> bool:
    return len(city.intersection(node_id).approaches) < 4


def _opposite(direction: Direction) -> Direction:
    return {
        Direction.N: Direction.S,
        Direction.S: Direction.N,
        Direction.E: Direction.W,
        Direction.W: Direction.E,
    }[direction]


# Turn convention: a 90-degree rotation from the through direction. This is
# an arbitrary but self-consistent choice (it doesn't model a real driving
# side) — see ADR on permissive turns in docs/decisions.md.
TURN_MAP: dict[Direction, dict[str, Direction]] = {
    Direction.N: {"through": Direction.S, "left": Direction.E, "right": Direction.W},
    Direction.S: {"through": Direction.N, "left": Direction.W, "right": Direction.E},
    Direction.E: {"through": Direction.W, "left": Direction.N, "right": Direction.S},
    Direction.W: {"through": Direction.E, "left": Direction.S, "right": Direction.N},
}
