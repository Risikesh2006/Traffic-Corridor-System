"""Adapter from World State to the routing cost model (PRD FR-RT-02).

Routing depends only on the `RoutingContext` protocol; this module is the one
place it meets `state/`. Missing data is never treated as clear: an
intersection with no fresh report counts as uncontrollable, and a segment with
no live reading uses its pessimistic historical value.
"""

from __future__ import annotations

from core.clock import SimTimeMs
from core.config import Settings
from core.models import City, ControllerStatus, SensorMetric
from state.world_state import WorldState


class WorldRoutingContext:
    def __init__(self, city: City, settings: Settings, world: WorldState) -> None:
        self._city = city
        self._settings = settings
        self._world = world
        self._cycle_s: dict[str, float] = {}
        for node in city.intersections:
            cycle_ms = 0
            for entry in node.default_plan:
                phase = next(p for p in node.phases if p.id == entry.phase_id)
                cycle_ms += entry.green_ms + phase.amber_ms + phase.all_red_ms
            self._cycle_s[node.id] = cycle_ms / 1000.0

    def live(self, segment_id: str, metric: SensorMetric, now: SimTimeMs) -> tuple[float, float]:
        estimate = self._world.estimate(segment_id, metric, now)
        return estimate.value, estimate.confidence

    def prior(self, segment_id: str, metric: SensorMetric, sim_time: SimTimeMs) -> float:
        return self._world.prior_value(segment_id, metric, sim_time)

    def is_closed(self, segment_id: str) -> bool:
        return self._world.road_status(segment_id) == "CLOSED"

    def controller_status(self, node_id: str, now: SimTimeMs) -> ControllerStatus:
        return self._world.intersection_view(node_id, now).controller_status

    def normal_red_wait_s(self, node_id: str) -> float:
        return self._cycle_s[node_id] * self._settings.eta_expected_red_fraction
