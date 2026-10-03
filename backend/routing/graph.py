"""Edge-based routing graph (PRD Phase 5 Part B1).

Search runs over DIRECTED SEGMENTS, not intersections, so a turn can carry its
own penalty and a U-turn can be banned. A state is the segment the vehicle is
on; a transition is "continue from this segment's stop line onto the next
segment" and is labelled with its turn type.

Turn classification uses node coordinates in screen orientation (y grows
south, matching `sim.topology` and the dev map): for travel vectors u then v,
the cross product u.x*v.y - u.y*v.x is negative for a left turn and positive
for a right turn.

Near-side and far-side are the two turn classes that carry different costs.
Near-side is the turn toward the kerb of the driving side (left for left-hand
traffic, right for right-hand traffic); far-side is the other one. Penalties
are assigned by these classes so the cost model never hard-codes a side.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from core.config import Settings
from core.models import City


class Turn(StrEnum):
    THROUGH = "THROUGH"
    NEAR_SIDE = "NEAR_SIDE"
    FAR_SIDE = "FAR_SIDE"
    U_TURN = "U_TURN"


@dataclass(frozen=True)
class Transition:
    to_segment: str
    turn: Turn


@dataclass(frozen=True)
class SegmentInfo:
    segment_id: str
    from_node: str
    to_node: str
    length_m: float
    lanes: int
    free_flow_mps: float
    dx: float
    dy: float


_COLLINEAR_EPS = 1e-9


def classify_turn(incoming: tuple[float, float], outgoing: tuple[float, float], side: str) -> Turn:
    ux, uy = incoming
    vx, vy = outgoing
    cross = ux * vy - uy * vx
    dot = ux * vx + uy * vy
    if abs(cross) <= _COLLINEAR_EPS and dot > 0:
        return Turn.THROUGH
    if abs(cross) <= _COLLINEAR_EPS and dot < 0:
        return Turn.U_TURN
    is_left = cross < 0
    if side == "left":
        return Turn.NEAR_SIDE if is_left else Turn.FAR_SIDE
    return Turn.NEAR_SIDE if not is_left else Turn.FAR_SIDE


class RoutingGraph:
    def __init__(self, city: City, settings: Settings) -> None:
        self.settings = settings
        self._coords = {n.id: (n.x, n.y) for n in city.intersections}
        self.segments: dict[str, SegmentInfo] = {}
        for road in sorted(city.roads, key=lambda r: r.id):
            ax, ay = self._coords[road.from_node]
            bx, by = self._coords[road.to_node]
            self.segments[road.id] = SegmentInfo(
                segment_id=road.id,
                from_node=road.from_node,
                to_node=road.to_node,
                length_m=road.length_m,
                lanes=road.lanes,
                free_flow_mps=road.free_flow_speed_mps,
                dx=bx - ax,
                dy=by - ay,
            )
        self.successors: dict[str, list[Transition]] = {}
        self.outgoing: dict[str, list[str]] = {n.id: [] for n in city.intersections}
        for sid in sorted(self.segments):
            self.outgoing[self.segments[sid].from_node].append(sid)
        for sid, info in self.segments.items():
            transitions: list[Transition] = []
            for next_id in self.outgoing[info.to_node]:
                nxt = self.segments[next_id]
                turn = classify_turn((info.dx, info.dy), (nxt.dx, nxt.dy), settings.driving_side)
                if turn == Turn.U_TURN and not settings.allow_u_turns:
                    continue
                transitions.append(Transition(next_id, turn))
            self.successors[sid] = transitions

    def info(self, segment_id: str) -> SegmentInfo:
        return self.segments[segment_id]

    def turn_between(self, from_segment: str, to_segment: str) -> Turn:
        for transition in self.successors[from_segment]:
            if transition.to_segment == to_segment:
                return transition.turn
        raise KeyError(f"no transition {from_segment} -> {to_segment}")

    def coords(self, node_id: str) -> tuple[float, float]:
        return self._coords[node_id]
