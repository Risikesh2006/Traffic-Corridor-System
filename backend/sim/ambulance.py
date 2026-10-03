"""Simulated ambulance (PRD FR-SIM-05). Route is supplied manually in this
phase (no routing/corridor logic yet) — a list of intersection IDs it will
visit in order.

Speed: `free_flow_speed_mps * ambulance_speed_factor` on the current
segment — there is no separate "segment speed cap" distinct from
free-flow speed in this model, so the PRD's `min(free_flow * 1.2,
segment_speed_cap)` collapses to just `free_flow * ambulance_speed_factor`.

Stop-line behaviour:
- GREEN and the queue ahead has fully cleared -> passes straight through.
- RED, or AMBER past the point a safe stop is still possible -> STOPS
  (counts a stop) and waits for GREEN.
- GREEN but a queue exists ahead -> records how many vehicles were ahead of
  it (reduced by `yield_factor`, the fraction assumed to pull aside) and
  waits until that many have discharged from the segment.

Migrated to the general junction model (PRD Phase 6 Stage 6.2, ADR-051):
the ambulance's required signal is no longer "the approach's compass
direction" but its specific MOVEMENT — the approach it arrives on, into the
exit its next route segment departs by. ADR-050 found that a far-side turn
can be in a different phase from the through/near-side movements of the
same approach, so checking only the approach's general state would be
wrong whenever the ambulance's own movement is a far-side turn held back
while the rest of the approach is served.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

from controller.intersection_controller import IntersectionController
from core.clock import SimTimeMs
from core.config import Settings
from core.general_city import GeneralCity
from core.junctions import Junction
from core.models import PresenceReading, Priority, SignalState
from sim.traffic import TrafficModel


class AmbulanceState(StrEnum):
    MOVING = "MOVING"
    STOPPED_AT_RED = "STOPPED_AT_RED"
    WAITING_FOR_QUEUE = "WAITING_FOR_QUEUE"
    ARRIVED = "ARRIVED"


@dataclass
class SimAmbulance:
    vehicle_id: str
    route: list[str]  # intersection IDs, route[0] is the start node
    priority: Priority = Priority.P1

    x: float = 0.0
    y: float = 0.0
    speed_mps: float = 0.0
    heading_deg: float = 0.0

    state: AmbulanceState = AmbulanceState.MOVING
    current_segment_id: str | None = None
    distance_on_segment_m: float = 0.0
    route_index: int = 0  # index of the node the ambulance is currently departing from

    total_stops: int = 0
    total_stopped_time_ms: int = 0
    stop_started_at: SimTimeMs | None = None
    _wait_target_discharged: int | None = field(default=None, repr=False)

    start_time: SimTimeMs | None = None
    arrival_time: SimTimeMs | None = None
    section_times_ms: dict[str, int] = field(default_factory=dict)
    _section_entered_at: SimTimeMs = 0


class AmbulanceSim:
    """Drives one or more `SimAmbulance`s against a `City` + `TrafficModel`."""

    def __init__(
        self,
        city: GeneralCity,
        settings: Settings,
        traffic: TrafficModel,
        controllers: dict[str, IntersectionController],
    ) -> None:
        self.city = city
        self.settings = settings
        self.traffic = traffic
        self._controllers = controllers
        self.ambulances: dict[str, SimAmbulance] = {}
        self._presence: list[PresenceReading] = []
        self._presence_seq = 0

    def add(
        self, vehicle_id: str, route: list[str], priority: Priority, now: SimTimeMs
    ) -> SimAmbulance:
        if len(route) < 2:
            raise ValueError("route must have at least 2 nodes")
        start_node = self.city.intersection(route[0])
        amb = SimAmbulance(
            vehicle_id=vehicle_id,
            route=route,
            priority=priority,
            x=start_node.x,
            y=start_node.y,
            start_time=now,
        )
        amb._section_entered_at = now
        self._enter_segment(amb, route[0], route[1], now)
        self.ambulances[vehicle_id] = amb
        return amb

    def adopt_route(self, vehicle_id: str, full_route: list[str]) -> bool:
        """Replace the route ahead of the ambulance with `full_route`.

        The ambulance is committed to the segment it is on (departing
        `route[route_index]`, heading for `route[route_index + 1]`), so the new
        route must keep both of those nodes. Any divergence before that point is
        already behind the vehicle and the route is refused (the caller reports
        CANNOT_FOLLOW); the route is then left unchanged."""
        amb = self.ambulances[vehicle_id]
        committed = amb.route_index + 2
        if amb.state == AmbulanceState.ARRIVED or len(full_route) < committed:
            return False
        if full_route[:committed] != amb.route[:committed]:
            return False
        amb.route = list(full_route)
        return True

    def step(self, now: SimTimeMs, dt_ms: int) -> None:
        for vehicle_id in sorted(self.ambulances):
            amb = self.ambulances[vehicle_id]
            if amb.state != AmbulanceState.ARRIVED:
                self._step_one(amb, now, dt_ms)

    # -- internals -----------------------------------------------------

    def _enter_segment(
        self, amb: SimAmbulance, from_node: str, to_node: str, now: SimTimeMs
    ) -> None:
        segment_id = f"{from_node}->{to_node}"
        amb.current_segment_id = segment_id
        amb.distance_on_segment_m = 0.0
        road = self.city.segment(segment_id)
        amb.speed_mps = road.free_flow_speed_mps * self.settings.ambulance_speed_factor
        amb.heading_deg = self._heading(from_node, to_node)
        amb.state = AmbulanceState.MOVING

    def _heading(self, from_node: str, to_node: str) -> float:
        a, b = self.city.intersection(from_node), self.city.intersection(to_node)
        return math.degrees(math.atan2(b.y - a.y, b.x - a.x)) % 360

    def _step_one(self, amb: SimAmbulance, now: SimTimeMs, dt_ms: int) -> None:
        if amb.state in (AmbulanceState.STOPPED_AT_RED, AmbulanceState.WAITING_FOR_QUEUE):
            if self._still_blocked(amb, now):
                return
            self._resume(amb, now)

        assert amb.current_segment_id is not None
        road = self.city.segment(amb.current_segment_id)
        amb.distance_on_segment_m += amb.speed_mps * dt_ms / 1000.0

        from_node = amb.route[amb.route_index]
        to_node = amb.route[amb.route_index + 1]
        frac = min(1.0, amb.distance_on_segment_m / road.length_m)
        a, b = self.city.intersection(from_node), self.city.intersection(to_node)
        amb.x = a.x + (b.x - a.x) * frac
        amb.y = a.y + (b.y - a.y) * frac

        if amb.distance_on_segment_m < road.length_m:
            return

        # Reached the stop line at `to_node`.
        self._handle_stop_line(amb, to_node, now)

    def _required_movement_id(self, amb: SimAmbulance, junction: Junction) -> str | None:
        """The movement (approach -> exit) the ambulance needs at the
        junction it is departing `amb.current_segment_id` into, from its
        current route. None if the ambulance has no further segment (its
        route ends at this junction)."""
        if amb.route_index >= len(amb.route) - 1:
            return None
        assert amb.current_segment_id is not None
        next_segment_id = f"{amb.route[amb.route_index]}->{amb.route[amb.route_index + 1]}"
        approach = next(a for a in junction.approaches if a.segment_id == amb.current_segment_id)
        exit_ = next(e for e in junction.exits if e.segment_id == next_segment_id)
        movement = next(
            (
                m
                for m in junction.movements
                if m.approach_id == approach.id and m.exit_id == exit_.id
            ),
            None,
        )
        return movement.id if movement is not None else None

    def _signal_for_required_movement(self, amb: SimAmbulance, to_node: str) -> SignalState:
        junction = self.city.intersection(to_node)
        movement_id = self._required_movement_id(amb, junction)
        if movement_id is None:
            return SignalState.ALL_RED  # no next segment: shouldn't be asked, treat as stop
        controller = self._controller_at(to_node)
        return controller.movement_view().get(movement_id, SignalState.ALL_RED)

    def _handle_stop_line(self, amb: SimAmbulance, to_node: str, now: SimTimeMs) -> None:
        amb.route_index += 1
        self._record_presence(amb, to_node, now)
        assert amb.current_segment_id is not None
        amb.section_times_ms[amb.current_segment_id] = now - amb._section_entered_at

        if amb.route_index == len(amb.route) - 1:
            amb.state = AmbulanceState.ARRIVED
            amb.arrival_time = now
            amb.speed_mps = 0.0
            return

        segment_id = amb.current_segment_id
        assert segment_id is not None
        signal = self._signal_for_required_movement(amb, to_node)
        queue_len = self.traffic.queue_length(segment_id)

        if signal == SignalState.GREEN and queue_len == 0:
            self._advance_to_next_segment(amb, to_node, now)
            return

        if signal != SignalState.GREEN:
            self._begin_stop(amb, AmbulanceState.STOPPED_AT_RED, now)
            return

        # GREEN but queue ahead: wait for (yield-adjusted) vehicles to clear.
        effective_wait = round(queue_len * (1 - self.settings.yield_factor))
        if effective_wait <= 0:
            self._advance_to_next_segment(amb, to_node, now)
            return
        seg = self.traffic.segments[segment_id]
        amb._wait_target_discharged = seg.cumulative_discharged + effective_wait
        self._begin_stop(amb, AmbulanceState.WAITING_FOR_QUEUE, now)

    def drain_presence_readings(self) -> list[PresenceReading]:
        readings, self._presence = self._presence, []
        return readings

    def _record_presence(self, amb: SimAmbulance, node_id: str, now: SimTimeMs) -> None:
        self._presence_seq += 1
        segment_id = f"{amb.route[amb.route_index - 1]}->{node_id}"
        junction = self.city.intersection(node_id)
        approach = next(a for a in junction.approaches if a.segment_id == segment_id)
        self._presence.append(
            PresenceReading(
                source_id=f"detector-{node_id}",
                intersection_id=node_id,
                approach_id=approach.id,
                vehicle_id=amb.vehicle_id,
                timestamp=now,
                sequence=self._presence_seq,
            )
        )

    def _begin_stop(self, amb: SimAmbulance, state: AmbulanceState, now: SimTimeMs) -> None:
        amb.state = state
        amb.speed_mps = 0.0
        amb.stop_started_at = now
        amb.total_stops += 1

    def _still_blocked(self, amb: SimAmbulance, now: SimTimeMs) -> bool:
        segment_id = amb.current_segment_id
        assert segment_id is not None
        to_node = amb.route[amb.route_index]
        signal = self._signal_for_required_movement(amb, to_node)

        if amb.state == AmbulanceState.STOPPED_AT_RED:
            if signal != SignalState.GREEN:
                return True
            queue_len = self.traffic.queue_length(segment_id)
            if queue_len == 0:
                return False
            effective_wait = round(queue_len * (1 - self.settings.yield_factor))
            if effective_wait <= 0:
                return False
            seg = self.traffic.segments[segment_id]
            amb._wait_target_discharged = seg.cumulative_discharged + effective_wait
            amb.state = AmbulanceState.WAITING_FOR_QUEUE
            return True

        # WAITING_FOR_QUEUE
        seg = self.traffic.segments[segment_id]
        assert amb._wait_target_discharged is not None
        return seg.cumulative_discharged < amb._wait_target_discharged

    def _resume(self, amb: SimAmbulance, now: SimTimeMs) -> None:
        if amb.stop_started_at is not None:
            amb.total_stopped_time_ms += now - amb.stop_started_at
        amb.stop_started_at = None
        amb._wait_target_discharged = None
        to_node = amb.route[amb.route_index]
        self._advance_to_next_segment(amb, to_node, now)

    def _advance_to_next_segment(self, amb: SimAmbulance, from_node: str, now: SimTimeMs) -> None:
        amb._section_entered_at = now
        if amb.route_index >= len(amb.route) - 1:
            amb.state = AmbulanceState.ARRIVED
            amb.arrival_time = now
            amb.speed_mps = 0.0
            return
        to_node = amb.route[amb.route_index + 1]
        self._enter_segment(amb, from_node, to_node, now)

    def _controller_at(self, node_id: str) -> IntersectionController:
        return self._controllers[node_id]
