"""The general-format city container (PRD Phase 6 Stage 6.1.4).

Sits beside `core.models.City` (the old N/S/E/W format) during the
expand-migrate-contract refactor (ADR-047). A `GeneralCity` is built from
`Junction`s (core.junctions) rather than intersections with compass
approaches, and its road segments carry geometry, road class, one-way and
name, so the same container can later hold a real OpenStreetMap city.
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

    junctions: tuple[Junction, ...]
    segments: tuple[GeneralRoadSegment, ...]
    hospitals: tuple[Hospital, ...]
    ambulance_entry_node: str

    def junction(self, junction_id: str) -> Junction:
        for node in self.junctions:
            if node.id == junction_id:
                return node
        raise KeyError(f"unknown junction: {junction_id}")

    def segment(self, segment_id: str) -> GeneralRoadSegment:
        for road in self.segments:
            if road.id == segment_id:
                return road
        raise KeyError(f"unknown road segment: {segment_id}")

    def outgoing(self, junction_id: str) -> list[GeneralRoadSegment]:
        return [r for r in self.segments if r.from_node == junction_id]

    def incoming(self, junction_id: str) -> list[GeneralRoadSegment]:
        return [r for r in self.segments if r.to_node == junction_id]
