"""The central system orchestrator (PRD Phase 4 Part A2).

Holds every central component — ingestion gateway, world state, vehicle
tracker, and the fail-closed safety gate — and advances them in one fixed
order per call to `step(now)`:

  1. drain the feeds (fixes, sensor readings, status reports, presence)
  2. ingest through the gateway (validation, dedup, plausibility)
  3. update world state with accepted sensor and status inputs
  4. update the tracker with accepted fixes and presence readings
  5. run the tracker's per-vehicle update (dead reckoning, deviation, stop lines)

Routing and corridor planning slot in after step 5 in later phases (marked
below). This module depends only on `core` interfaces, `events`, `ingestion`,
`state`, `tracking`, and `safety`; it never imports `sim` or `controller`.
"""

from __future__ import annotations

from core.clock import Clock, SimTimeMs
from core.config import Settings
from core.interfaces import PresenceFeed, SensorFeed, SignalLink, StatusFeed, VehicleFeed
from core.models import (
    City,
    ControllerStatus,
    Direction,
    EmergencyVehicle,
    Intersection,
    Priority,
    SensorMetric,
    SignalState,
)
from events.bus import EventBus
from ingestion.gateway import IngestionGateway
from safety.gate import SafeSignalLink
from safety.supervisor import SafetySupervisor
from state.history import HistoricalProfile
from state.world_state import WorldState
from tracking.map_match import MapMatcher
from tracking.tracker import VehicleTracker


class CentralSystem:
    def __init__(
        self,
        city: City,
        settings: Settings,
        clock: Clock,
        vehicle_feed: VehicleFeed,
        sensor_feed: SensorFeed,
        status_feed: StatusFeed,
        presence_feed: PresenceFeed,
        signal_link: SignalLink,
        profile: HistoricalProfile | None = None,
    ) -> None:
        self.city = city
        self.settings = settings
        self.clock = clock
        self.bus = EventBus()

        self._vehicle_feed = vehicle_feed
        self._sensor_feed = sensor_feed
        self._status_feed = status_feed
        self._presence_feed = presence_feed

        self.gateway = IngestionGateway(clock, settings, self.bus)
        self.world = WorldState(city, settings, clock, profile=profile)
        self.matcher = MapMatcher(city, radius_m=settings.map_match_radius_m)
        self.tracker = VehicleTracker(
            city,
            settings,
            self.matcher,
            queue_wait=self._queue_wait_ms,
            stale_fraction=self._stale_fraction,
            approach_green=self._approach_green,
            red_wait=self._red_wait_ms,
            bus=self.bus,
        )
        self.supervisor = SafetySupervisor(city, settings, clock)
        self.gate = SafeSignalLink(
            signal_link,
            self.supervisor,
            view_source=lambda node_id: self.world.supervisor_view(node_id, clock.now()),
            clock=clock,
        )
        self._tracked: dict[str, Priority] = {}

    def track_vehicle(self, vehicle_id: str, priority: Priority, route: list[str]) -> None:
        self.tracker.register(vehicle_id, priority, route, self.clock.now())
        self._tracked[vehicle_id] = priority

    def step(self, now: SimTimeMs) -> None:
        # 1-2. drain feeds and ingest through the gateway
        for fix in self._vehicle_feed.drain_fixes():
            accepted = self.gateway.ingest_gps_fix(fix)
            if accepted is not None:
                self.tracker.ingest_fix(accepted)

        for reading in self._sensor_feed.drain_readings():
            accepted_reading = self.gateway.ingest_sensor_reading(reading)
            if accepted_reading is not None:
                self.world.apply_sensor(accepted_reading, received_at=now)

        for report in self._status_feed.drain_status_reports():
            accepted_report = self.gateway.ingest_status_report(report)
            if accepted_report is not None:
                self.world.apply_status(accepted_report, received_at=now)
                self.supervisor.heartbeat()

        for presence in self._presence_feed.drain_presence_readings():
            self.tracker.ingest_presence(presence)

        # 3-5. tracker update (world state is already current for this tick)
        self.tracker.update(now)

        # Routing (Phase 5) and corridor planning (Phase 6) run here.

    def snapshot_position(self, vehicle_id: str) -> EmergencyVehicle | None:
        if vehicle_id not in self._tracked:
            return None
        now = self.clock.now()
        snap = self.tracker.snapshot(vehicle_id, now)
        age_ms = snap.last_fix_age_ms or 0
        return EmergencyVehicle(
            id=vehicle_id,
            priority=self._tracked[vehicle_id],
            x=snap.x,
            y=snap.y,
            speed_mps=snap.v_mps,
            heading_deg=0.0,
            destination_id=self.tracker.route(vehicle_id)[-1],
            last_seen_at=now - age_ms,
            position_source=snap.position_source,
        )

    # -- hooks the tracker uses ---------------------------------------------

    def _queue_wait_ms(self, intersection_id: str, now: SimTimeMs) -> int:
        """Expected discharge wait from the worst approach's queue estimate
        (conservative: the vehicle could face any approach's queue)."""
        headway_ms = round(self.settings.headway_s * 1000)
        startup_ms = self.settings.startup_loss_ms
        worst = 0.0
        for direction in Direction:
            estimate = self.world.queue_estimate(intersection_id, direction, now)
            if estimate is not None:
                worst = max(worst, estimate.value)
        return round(worst * headway_ms) + startup_ms if worst > 0 else 0

    def _red_wait_ms(self, intersection_id: str, segment_id: str, now: SimTimeMs) -> int:
        """Time until this approach next turns green, projected forward from the
        controller's last reported state through its configured default plan.
        Unknown state falls back to the expected-red-fraction prior: missing
        data is never treated as a clear road."""
        if self._approach_green(intersection_id, segment_id, now):
            return 0
        node = self.city.intersection(intersection_id)
        view = self.world.intersection_view(intersection_id, now)
        direction = self.city.approach_direction(segment_id)
        if view.controller_status != ControllerStatus.ONLINE or view.state_started_at is None:
            return round(self._cycle_ms(node) * self.settings.eta_expected_red_fraction)
        phases = [entry.phase_id for entry in node.default_plan]
        t = view.state_started_at
        state = view.signal_state
        phase_id = view.current_phase_id
        if phase_id is None or phase_id not in phases:
            return round(self._cycle_ms(node) * self.settings.eta_expected_red_fraction)
        index = phases.index(phase_id)
        for _ in range(4 * len(phases)):
            phase = next(p for p in node.phases if p.id == phases[index])
            green_ms = next(e.green_ms for e in node.default_plan if e.phase_id == phase.id)
            if state == SignalState.GREEN:
                t += green_ms
                state = SignalState.AMBER
                t_amber_end = t + phase.amber_ms
                t = t_amber_end
                state = SignalState.ALL_RED
            elif state == SignalState.AMBER:
                t += phase.amber_ms
                state = SignalState.ALL_RED
            elif state == SignalState.ALL_RED:
                index = (index + 1) % len(phases)
                next_phase = next(p for p in node.phases if p.id == phases[index])
                t += next_phase.all_red_ms
                state = SignalState.GREEN
                if direction in next_phase.movements:
                    return max(0, t - now)
            else:
                break
        return round(self._cycle_ms(node) * self.settings.eta_expected_red_fraction)

    def _cycle_ms(self, node: Intersection) -> int:
        total = 0
        for entry in node.default_plan:
            phase = next(p for p in node.phases if p.id == entry.phase_id)
            total += entry.green_ms + phase.amber_ms + phase.all_red_ms
        return total

    def _approach_green(self, intersection_id: str, segment_id: str, now: SimTimeMs) -> bool:
        """Unknown counts as red: a stop is the conservative assumption."""
        view = self.world.intersection_view(intersection_id, now)
        if view.controller_status != ControllerStatus.ONLINE:
            return False
        if view.signal_state != SignalState.GREEN or view.current_phase_id is None:
            return False
        node = self.city.intersection(intersection_id)
        phase = next((p for p in node.phases if p.id == view.current_phase_id), None)
        if phase is None:
            return False
        return self.city.approach_direction(segment_id) in phase.movements

    def _stale_fraction(self, route_segments: list[str], now: SimTimeMs) -> float:
        if not route_segments:
            return 0.0
        stale = sum(
            1 for sid in route_segments if self.world.estimate(sid, SensorMetric.DENSITY, now).stale
        )
        return stale / len(route_segments)
