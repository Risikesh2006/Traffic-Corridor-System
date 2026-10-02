"""Runtime configuration (PRD §18) and city topology loading (FR-SIM-01)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from pydantic import BaseModel

from core.models import ConflictMatrix, Hospital, Intersection, RoadSegment


class Settings(BaseModel):
    amber_s: float = 4
    all_red_s: float = 2
    min_green_s: float = 10
    ped_clearance_s: float = 7
    max_hold_s: float = 90
    lease_duration_s: float = 30
    lookahead_horizon_s: float = 90
    headway_s: float = 2.0
    startup_loss_s: float = 2.0
    base_buffer_s: float = 3
    tau_density_s: float = 30
    tau_gps_s: float = 5
    density_ttl_s: float = 60
    gps_loss_threshold_s: float = 3
    pessimism_factor: float = 1.2
    uncontrollable_penalty_s: float = 45
    hysteresis_abs_s: float = 15
    hysteresis_rel: float = 0.10
    k_alternatives: int = 3
    corridor_tick_s: float = 1
    periodic_replan_s: float = 10
    heartbeat_interval_s: float = 1
    heartbeat_timeout_s: float = 3
    max_gps_speed_mps: float = 50

    @classmethod
    def load(cls, path: str | Path) -> Settings:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(**data)


class CityConfig(BaseModel):
    intersections: list[Intersection]
    roads: list[RoadSegment]
    hospitals: list[Hospital]

    @classmethod
    def load(cls, path: str | Path) -> CityConfig:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        intersections = [
            Intersection(
                id=node["id"],
                x=node["x"],
                y=node["y"],
                approaches=node.get("approaches", []),
                phases=node.get("phases", []),
                conflict_matrix=ConflictMatrix(**node["conflict_matrix"]),
            )
            for node in raw["intersections"]
        ]
        roads = [RoadSegment(**road) for road in raw["roads"]]
        hospitals = [Hospital(**h) for h in raw["hospitals"]]
        return cls(intersections=intersections, roads=roads, hospitals=hospitals)
