"""Point-queue traffic model with travel delay and storage limits (ADR:
point-queue model). Background vehicles are NOT individual objects — a
queue is a deque of join-timestamps and an in-transit pipeline is a deque
of arrival-timestamps. This keeps a 36-intersection, 120-segment network
cheap enough to simulate many minutes in well under the performance budget
(PRD §21, Phase 2 perf test), while still giving exact per-vehicle delay
(discharge time minus queue-join time) via the timestamps.

Discharge rule: saturation flow of `lanes / headway_s` vehicles/second,
starting `startup_loss_s` after the movement becomes eligible, using
fractional "discharge credit" so the rate is exact at any tick size.
Amber eligibility: discharge continues through the first
`amber_discharge_fraction` of amber (default: first half) — a documented
simplification standing in for "vehicles already committed may finish
crossing" (ADR: amber discharge rule).

Turning (PRD Phase 6 Stage 6.2, ADR-051): each discharged vehicle draws a
TURN TYPE (through/near-side/far-side) from the "turning" RNG stream, then
picks the matching MOVEMENT from its approach in the general junction
model (`core/junctions.py`) — replacing the old compass TURN_MAP. The
movement must be its OWN currently permitted one: ADR-050 found that a
far-side turn can be in a different phase from the through/near-side
movements of the same approach, so a single approach-level "is green"
check is no longer sufficient — eligibility is re-checked at the specific
movement before it is allowed to discharge, and a movement that is not
currently served blocks the queue exactly like a full next segment (never
discharges an unpermitted movement). If the drawn turn type has no
matching movement at all from this approach (a 2-arm corner, for example,
offers only one turn type), the vehicle exits the network — the same
fallback the old model used at a grid border.

A vehicle that cannot advance because the next segment is full blocks the
whole queue until space frees up (ADR: head-of-line blocking).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from controller.intersection_controller import IntersectionController
from core.clock import SimTimeMs
from core.config import Settings
from core.general_city import GeneralCity
from core.models import SignalState, TurnType
from core.rng import RngFactory


@dataclass
class _Injection:
    segment_id: str
    rate_per_s: float
    start_ms: SimTimeMs
    end_ms: SimTimeMs
    credit: float = 0.0


class SegmentTrafficState:
    __slots__ = (
        "capacity",
        "lanes",
        "in_transit",
        "queue",
        "discharge_credit",
        "green_start_at",
        "cumulative_discharged",
    )

    def __init__(self, capacity: int, lanes: int) -> None:
        self.capacity = capacity
        self.lanes = lanes
        self.in_transit: deque[SimTimeMs] = deque()
        self.queue: deque[SimTimeMs] = deque()
        self.discharge_credit: float = 0.0
        self.green_start_at: SimTimeMs | None = None
        self.cumulative_discharged: int = 0

    def occupancy(self) -> int:
        return len(self.in_transit) + len(self.queue)


class TrafficModel:
    def __init__(
        self,
        city: GeneralCity,
        settings: Settings,
        rng: RngFactory,
        controllers: dict[str, IntersectionController],
    ) -> None:
        self.city = city
        self.settings = settings
        self._controllers = controllers
        self._turning_rng = rng.stream("turning")
        self._injections: list[_Injection] = []

        # segment_id -> (approach_id, approach's junction id) it feeds.
        self._approach_of: dict[str, tuple[str, str]] = {
            approach.segment_id: (approach.id, node.id)
            for node in city.intersections
            for approach in node.approaches
        }

        self.segments: dict[str, SegmentTrafficState] = {}
        for road in city.roads:
            capacity = max(1, math.floor(road.length_m * road.lanes / settings.jam_spacing_m))
            self.segments[road.id] = SegmentTrafficState(capacity=capacity, lanes=road.lanes)

        self.total_spawned = 0
        self.total_exited = 0
        self.total_queue_delay_ms = 0
        self.total_queue_delay_count = 0

    # -- demand entry point ------------------------------------------------

    def spawn(self, segment_id: str, now: SimTimeMs) -> bool:
        """Attempt to place one vehicle at the start of `segment_id`.
        Returns False (vehicle lost) if the segment has no storage space."""
        seg = self.segments[segment_id]
        if seg.occupancy() >= seg.capacity:
            return False
        road = self.city.segment(segment_id)
        travel_ms = round(road.length_m / road.free_flow_speed_mps * 1000)
        seg.in_transit.append(now + travel_ms)
        self.total_spawned += 1
        return True

    def inject_congestion(
        self, segment_id: str, extra_vph: float, start_ms: SimTimeMs, end_ms: SimTimeMs
    ) -> None:
        """Fault control: add `extra_vph` vehicles/hour onto a segment between
        start_ms and end_ms, on top of normal traffic (used to create a jam)."""
        self.segments[segment_id]  # raises KeyError for unknown segments
        self._injections.append(_Injection(segment_id, extra_vph / 3600.0, start_ms, end_ms))

    def apply_injections(self, now: SimTimeMs, dt_ms: int) -> None:
        for injection in self._injections:
            if not injection.start_ms <= now < injection.end_ms:
                continue
            injection.credit += injection.rate_per_s * dt_ms / 1000.0
            while injection.credit >= 1.0:
                injection.credit -= 1.0
                self.spawn(injection.segment_id, now)

    def queue_length(self, segment_id: str) -> int:
        return len(self.segments[segment_id].queue)

    # -- per-tick steps (called by the engine in fixed order) -------------

    def discharge(self, now: SimTimeMs, dt_ms: int) -> None:
        for segment_id in sorted(self.segments):
            self._discharge_segment(segment_id, now, dt_ms)

    def move_arrivals_into_queues(self, now: SimTimeMs) -> None:
        for segment_id in sorted(self.segments):
            seg = self.segments[segment_id]
            while seg.in_transit and seg.in_transit[0] <= now:
                seg.in_transit.popleft()
                seg.queue.append(now)

    # -- internals -----------------------------------------------------

    def _is_eligible(self, segment_id: str, now: SimTimeMs) -> bool:
        approach_id, node_id = self._approach_of[segment_id]
        controller = self._controllers[node_id]
        sm = controller.state_machine
        state = controller.approach_view().get(approach_id, SignalState.ALL_RED)
        if state == SignalState.GREEN:
            return True
        if state == SignalState.AMBER:
            amber_elapsed = now - sm.state_started_at
            amber_budget = sm.current_phase().amber_ms * self.settings.amber_discharge_fraction
            return amber_elapsed < amber_budget
        return False

    def _discharge_segment(self, segment_id: str, now: SimTimeMs, dt_ms: int) -> None:
        seg = self.segments[segment_id]
        if not seg.queue:
            seg.green_start_at = None
            seg.discharge_credit = 0.0
            return

        eligible = self._is_eligible(segment_id, now)
        if not eligible:
            seg.green_start_at = None
            seg.discharge_credit = 0.0
            return

        if seg.green_start_at is None:
            seg.green_start_at = now

        elapsed = now - seg.green_start_at
        if elapsed < self.settings.startup_loss_ms:
            return

        rate_per_ms = seg.lanes / (self.settings.headway_s * 1000)
        seg.discharge_credit += dt_ms * rate_per_ms
        # Bound runaway accumulation while permanently blocked.
        seg.discharge_credit = min(seg.discharge_credit, len(seg.queue) + 1)

        while seg.discharge_credit >= 1.0 and seg.queue:
            if not self._try_discharge_one(segment_id, seg, now):
                break
            seg.discharge_credit -= 1.0

    def _try_discharge_one(self, segment_id: str, seg: SegmentTrafficState, now: SimTimeMs) -> bool:
        approach_id, node_id = self._approach_of[segment_id]
        junction = self.city.intersection(node_id)
        turn_type = self._pick_turn_type()
        candidates = sorted(
            (
                m
                for m in junction.movements
                if m.approach_id == approach_id and m.turn_type == turn_type
            ),
            key=lambda m: m.id,
        )

        if not candidates:
            # No movement of this turn type exists from this approach (e.g.
            # a 2-arm corner only offers one turn type) — exits the network,
            # the same fallback the old grid-only model used at a border.
            join_time = seg.queue.popleft()
            self._record_discharge(seg, join_time, now)
            self.total_exited += 1
            return True

        movement = candidates[0]
        controller = self._controllers[node_id]
        state = controller.movement_view().get(movement.id, SignalState.ALL_RED)
        if state not in (SignalState.GREEN, SignalState.AMBER):
            return False  # this specific movement isn't currently permitted
        if state == SignalState.AMBER:
            sm = controller.state_machine
            amber_elapsed = now - sm.state_started_at
            amber_budget = sm.current_phase().amber_ms * self.settings.amber_discharge_fraction
            if amber_elapsed >= amber_budget:
                return False

        exit_ = next(e for e in junction.exits if e.id == movement.exit_id)
        next_segment_id = exit_.segment_id
        next_seg = self.segments[next_segment_id]
        if next_seg.occupancy() >= next_seg.capacity:
            return False  # head-of-line blocking

        join_time = seg.queue.popleft()
        self._record_discharge(seg, join_time, now)
        road = self.city.segment(next_segment_id)
        travel_ms = round(road.length_m / road.free_flow_speed_mps * 1000)
        next_seg.in_transit.append(now + travel_ms)
        return True

    def _record_discharge(
        self, seg: SegmentTrafficState, join_time: SimTimeMs, now: SimTimeMs
    ) -> None:
        seg.cumulative_discharged += 1
        self.total_queue_delay_ms += now - join_time
        self.total_queue_delay_count += 1

    def _pick_turn_type(self) -> TurnType:
        """Weighted by the existing through/left/right settings, mapped to
        THROUGH/NEAR_SIDE/FAR_SIDE (near-side = left under the default
        left-hand driving convention, ADR-039)."""
        r = self._turning_rng.random()
        s = self.settings
        if r < s.turn_through_prob:
            return TurnType.THROUGH
        if r < s.turn_through_prob + s.turn_left_prob:
            return TurnType.NEAR_SIDE
        return TurnType.FAR_SIDE

    # -- aggregate accounting ----------------------------------------------

    def vehicles_in_network(self) -> int:
        return sum(seg.occupancy() for seg in self.segments.values())

    def average_queue_delay_ms(self) -> float:
        if self.total_queue_delay_count == 0:
            return 0.0
        return self.total_queue_delay_ms / self.total_queue_delay_count
