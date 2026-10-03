"""The general-format city container (PRD Phase 6 Stage 6.1.4).

Sits beside `core.models.City` (the old N/S/E/W format) during the
expand-migrate-contract refactor (ADR-047). A `GeneralCity` is built from
`Junction`s (core.junctions) rather than intersections with compass
approaches, and its road segments carry geometry, road class, one-way and
name, so the same container can later hold a real OpenStreetMap city.

Field and method names deliberately match `core.models.City`
(`intersections`, `roads`, `.intersection()`, `.segment()`, `.outgoing()`,
`.incoming()`, `.neighbors()`, `.distance_m()`) even though the per-object
shape is different (`Junction` instead of `Intersection`). This is not
aliasing for its own sake: every consumer that only navigates the graph
(looks up a node or a road, follows an edge) needs no change at all when
switching from one city type to the other, and ADR-051 found that nearly
every backend module does exactly that. Only code that reaches into
direction-specific detail (`.approaches`, `.phases`, `.conflict_matrix`)
needs to change, because that detail is genuinely different.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from core.junctions import Junction
from core.models import Hospital


class GeneralRoadSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    from_node: str
    to_node: str
    length_m: float = Field(gt=0)
    lanes: int = Field(ge=1)
    free_flow_speed_mps: float = Field(gt=0)
    capacity_vph: float = Field(gt=0)
    geometry: tuple[tuple[float, float], ...] = ()
    geometry_latlng: tuple[tuple[float, float], ...] | None = None
    road_class: str = "unclassified"
    oneway: bool = False
    name: str | None = None


class GeneralCity(BaseModel):
    model_config = ConfigDict(frozen=True)

    intersections: tuple[Junction, ...]
    roads: tuple[GeneralRoadSegment, ...]
    hospitals: tuple[Hospital, ...]
    ambulance_entry_node: str

    def intersection(self, node_id: str) -> Junction:
        for node in self.intersections:
            if node.id == node_id:
                return node
        raise KeyError(f"unknown intersection: {node_id}")

    def segment(self, segment_id: str) -> GeneralRoadSegment:
        for road in self.roads:
            if road.id == segment_id:
                return road
        raise KeyError(f"unknown road segment: {segment_id}")

    def outgoing(self, node_id: str) -> list[GeneralRoadSegment]:
        return [r for r in self.roads if r.from_node == node_id]

    def incoming(self, node_id: str) -> list[GeneralRoadSegment]:
        return [r for r in self.roads if r.to_node == node_id]

    def neighbors(self, node_id: str) -> list[str]:
        return [r.to_node for r in self.outgoing(node_id)]

    def distance_m(self, a: str, b: str) -> float:
        node_a, node_b = self.intersection(a), self.intersection(b)
        return float(((node_a.x - node_b.x) ** 2 + (node_a.y - node_b.y) ** 2) ** 0.5)
