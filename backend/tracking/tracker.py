"""The vehicle tracker (PRD §10.4 FR-TRK-01..06, FH-02, FH-11).

Per vehicle it maintains a one-dimensional along-route state (`s`, `v`),
map-matches each accepted GPS fix, dead-reckons through GPS loss, detects
route deviation, and reports stop-line passage with hysteresis. ETAs come
from `tracking.eta` using the World State's queue and signal figures, which
the caller passes in as functions so the tracker never reads the network or
the simulator directly.

All time is an explicit `now` argument; iteration is over sorted IDs.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Protocol

from core.clock import SimTimeMs
from core.config import Settings
from core.events import EventType
from core.models import City, GpsFix, PositionSource, PresenceReading, Priority
from events.bus import EventBus
from tracking.eta import IntersectionEta, StopLineTarget, compute_etas
from tracking.filter import AlphaBetaFilter, EwmaSpeed
from tracking.map_match import MapMatcher

SPEED_WINDOW = 10


class QueueWaitFn(Protocol):
    def __call__(self, intersection_id: str, now: SimTimeMs) -> int: ...


class ApproachGreenFn(Protocol):
    def __call__(self, intersection_id: str, segment_id: str, now: SimTimeMs) -> bool: ...


class RedWaitFn(Protocol):
    def __call__(self, intersection_id: str, segment_id: str, now: SimTimeMs) -> int: ...


class StaleFractionFn(Protocol):
    def __call__(self, route_segments: list[str], now: SimTimeMs) -> float: ...


@dataclass
class VehicleTrack:
    vehicle_id: str
    priority: Priority
    route: list[str]
    route_segments: list[str]
    cumulative_m: list[float]
    filter: AlphaBetaFilter
    ewma: EwmaSpeed
    speeds: deque[float] = field(default_factory=lambda: deque(maxlen=SPEED_WINDOW))
    pending_fixes: list[GpsFix] = field(default_factory=list)
    pending_presence: list[PresenceReading] = field(default_factory=list)
    last_fix: GpsFix | None = None
    last_fix_at: SimTimeMs | None = None
    previous_segment: str | None = None
    matched_segment: str | None = None
    position_source: PositionSource = PositionSource.GPS
    uncertainty_m: float = 0.0
    gps_lost_emitted: bool = False
    off_route_count: int = 0
    off_route_since: SimTimeMs | None = None
    deviation_emitted: bool = False
    passed: set[str] = field(default_factory=set)
    stop_line_s: dict[str, float] = field(default_factory=dict)
    last_updated: SimTimeMs | None = None
    held_at_stop: str | None = None


@dataclass(frozen=True)
class TrackSnapshot:
    vehicle_id: str
    x: float
    y: float
    s_m: float
    v_mps: float
    matched_segment: str | None
    position_source: PositionSource
    uncertainty_m: float
    last_fix_age_ms: int | None
    etas: list[IntersectionEta]


class VehicleTracker:
    def __init__(
        self,
        city: City,
        settings: Settings,
        matcher: MapMatcher,
        queue_wait: QueueWaitFn,
        stale_fraction: StaleFractionFn,
        approach_green: ApproachGreenFn | None = None,
        red_wait: RedWaitFn | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self.city = city
        self.settings = settings
        self.matcher = matcher
        self._queue_wait = queue_wait
        self._stale_fraction = stale_fraction
        self._approach_green = approach_green or (lambda node, seg, now: False)
        self._red_wait = red_wait or (lambda node, seg, now: 0)
        self.bus = bus
        self._tracks: dict[str, VehicleTrack] = {}
        self._cycle_ms = self._compute_cycles()

    # -- registration and routes -----------------------------------------

    def register(
        self, vehicle_id: str, priority: Priority, route: list[str], now: SimTimeMs
    ) -> None:
        if vehicle_id in self._tracks:
            raise ValueError(f"vehicle {vehicle_id} already tracked")
        self._tracks[vehicle_id] = self._build_track(vehicle_id, priority, route)
        self._tracks[vehicle_id].last_updated = now

    def set_route(self, vehicle_id: str, route: list[str]) -> None:
        """Replace the route ahead. The caller guarantees the new route shares the
        prefix already travelled, so distance along it and every passed stop line
        stay valid; all other tracking state is kept."""
        track = self._tracks[vehicle_id]
        rebuilt = self._build_track(vehicle_id, track.priority, route)
        track.route = rebuilt.route
        track.route_segments = rebuilt.route_segments
        track.cumulative_m = rebuilt.cumulative_m

    # -- inputs (already accepted by the ingestion gateway) -----------------

    def ingest_fix(self, fix: GpsFix) -> None:
        if fix.vehicle_id in self._tracks:
            self._tracks[fix.vehicle_id].pending_fixes.append(fix)

    def ingest_presence(self, reading: PresenceReading) -> None:
        if reading.vehicle_id in self._tracks:
            self._tracks[reading.vehicle_id].pending_presence.append(reading)

    # -- the per-tick update --------------------------------------------

    def update(self, now: SimTimeMs) -> None:
        for vehicle_id in sorted(self._tracks):
            track = self._tracks[vehicle_id]
            for fix in sorted(track.pending_fixes, key=lambda f: (f.timestamp, f.sequence)):
                self._apply_fix(track, fix, now)
            track.pending_fixes.clear()
            self._apply_presence(track, now)
            track.pending_presence.clear()
            self._dead_reckon_if_needed(track, now)
            self._check_stop_lines(track, now)
            self._check_deviation(track, now)
            track.last_updated = now

    def _apply_fix(self, track: VehicleTrack, fix: GpsFix, now: SimTimeMs) -> None:
        was_lost = track.position_source == PositionSource.DEAD_RECKONING
        match = self.matcher.match(
            fix.x,
            fix.y,
            fix.heading_deg,
            route_segments=frozenset(track.route_segments),
            previous_segment=track.previous_segment,
        )
        on_route = match is not None and match.segment_id in track.route_segments

        if match is not None and on_route:
            index = track.route_segments.index(match.segment_id)
            s_measured = track.cumulative_m[index] + match.offset_m
            if not track.filter.state:
                track.filter.initialize(s_measured, fix.speed_mps, fix.timestamp)
            elif was_lost:
                predicted = track.filter.predict(fix.timestamp)
                consistent = abs(s_measured - predicted) <= track.uncertainty_m
                if consistent:
                    track.filter.update(s_measured, fix.timestamp)
                else:
                    track.filter.initialize(s_measured, fix.speed_mps, fix.timestamp)
                self._emit(
                    EventType.GPS_RESTORED,
                    now,
                    {"vehicle_id": track.vehicle_id, "reconciled_consistent": consistent},
                )
            else:
                track.filter.update(s_measured, fix.timestamp)
            track.matched_segment = match.segment_id
            track.previous_segment = match.segment_id
        elif was_lost and track.filter.state:
            # Back on GPS but off the route: keep dead-reckoned estimate, record restoration.
            self._emit(
                EventType.GPS_RESTORED,
                now,
                {"vehicle_id": track.vehicle_id, "reconciled_consistent": False},
            )

        track.position_source = PositionSource.GPS
        track.gps_lost_emitted = False
        track.last_fix = fix
        track.last_fix_at = fix.timestamp
        track.ewma.observe(fix.speed_mps)
        track.speeds.append(fix.speed_mps)

        far = match is not None and match.distance_m > self.settings.deviation_distance_m
        off_route = match is None or not on_route or far
        if off_route:
            track.off_route_count += 1
            if track.off_route_since is None:
                track.off_route_since = fix.timestamp
        else:
            track.off_route_count = 0
            track.off_route_since = None

    def _apply_presence(self, track: VehicleTrack, now: SimTimeMs) -> None:
        """A presence detector fires when the vehicle reaches a stop line. That
        places the vehicle AT the line, which is not the same as having passed
        it: passage is only ever reported by _check_stop_lines, after the
        vehicle has moved beyond the line by the margin. Confusing the two
        would release a preemption too early."""
        for reading in sorted(track.pending_presence, key=lambda r: r.sequence):
            node = reading.intersection_id
            if node in track.passed or node not in track.route[1:]:
                continue
            if track.filter.state is None:
                continue
            stop_s = self._stop_line_s(track, node)
            track.filter.initialize(stop_s, 0.0, reading.timestamp)
            track.uncertainty_m = self.settings.gps_noise_std_m
            track.held_at_stop = node

    def _dead_reckon_if_needed(self, track: VehicleTrack, now: SimTimeMs) -> None:
        if track.filter.state is None:
            return
        age = None if track.last_fix_at is None else now - track.last_fix_at
        if age is not None and age <= self.settings.gps_loss_threshold_ms:
            track.uncertainty_m = self._uncertainty_m(age)
            return
        if track.position_source != PositionSource.DEAD_RECKONING:
            track.position_source = PositionSource.DEAD_RECKONING
        self._advance_without_gps(track, now)
        track.uncertainty_m = self._uncertainty_m(age or 0)
        if not track.gps_lost_emitted:
            track.gps_lost_emitted = True
            self._emit(EventType.GPS_LOST, now, {"vehicle_id": track.vehicle_id})
            self._emit(EventType.TRACKING_DEGRADED, now, {"vehicle_id": track.vehicle_id})

    def _advance_without_gps(self, track: VehicleTrack, now: SimTimeMs) -> None:
        """Dead-reckons along the route, but a vehicle cannot drive through a
        red it has no evidence is green: when the next stop line is reached
        on a non-green approach, the estimate is held there at zero speed
        until the approach shows green (or GPS returns)."""
        state = track.filter.state
        if state is None:
            return
        if track.held_at_stop is not None:
            if self._approach_is_green(track, track.held_at_stop, now):
                track.held_at_stop = None
                track.filter.initialize(state.s_m, self._cruise_speed(track), now)
            else:
                track.filter.initialize(state.s_m, 0.0, now)
                return
        track.filter.coast(now)
        for node in track.route[1:]:
            if node in track.passed:
                continue
            stop_s = self._stop_line_s(track, node)
            advanced = track.filter.state
            if advanced is None:
                return
            if advanced.s_m >= stop_s and not self._approach_is_green(track, node, now):
                track.filter.initialize(stop_s, 0.0, now)
                track.held_at_stop = node
            break

    def _expected_red_wait(self, track: VehicleTrack, node: str, now: SimTimeMs) -> int:
        index = track.route.index(node)
        segment_id = f"{track.route[index - 1]}->{node}"
        return self._red_wait(node, segment_id, now)

    def _approach_is_green(self, track: VehicleTrack, node: str, now: SimTimeMs) -> bool:
        index = track.route.index(node)
        segment_id = f"{track.route[index - 1]}->{node}"
        return self._approach_green(node, segment_id, now)

    def _cruise_speed(self, track: VehicleTrack) -> float:
        if track.ewma.value is not None and track.ewma.value > 0:
            return track.ewma.value
        return self.settings.ambulance_speed_factor * 11.11

    def _check_stop_lines(self, track: VehicleTrack, now: SimTimeMs) -> None:
        if track.filter.state is None:
            return
        s = track.filter.state.s_m
        for node in track.route[1:]:
            if node in track.passed:
                continue
            stop_s = self._stop_line_s(track, node)
            if s >= stop_s + self.settings.stop_line_margin_m:
                track.passed.add(node)
                self._emit(
                    EventType.STOP_LINE_PASSED,
                    now,
                    {"vehicle_id": track.vehicle_id, "intersection_id": node, "source": "gps"},
                )
            else:
                break  # stop lines are in route order; the next one isn't reached yet

    def _check_deviation(self, track: VehicleTrack, now: SimTimeMs) -> None:
        if track.deviation_emitted:
            return
        by_count = track.off_route_count >= self.settings.deviation_fix_count
        by_duration = (
            track.off_route_since is not None
            and now - track.off_route_since >= self.settings.deviation_duration_ms
        )
        if by_count or by_duration:
            track.deviation_emitted = True
            self._emit(
                EventType.ROUTE_DEVIATION,
                now,
                {"vehicle_id": track.vehicle_id, "matched_segment": track.matched_segment},
            )

    # -- queries ----------------------------------------------------------

    def snapshot(self, vehicle_id: str, now: SimTimeMs) -> TrackSnapshot:
        track = self._tracks[vehicle_id]
        s = track.filter.state.s_m if track.filter.state else 0.0
        v = track.filter.state.v_mps if track.filter.state else 0.0
        x, y = self._position_at(track, s)
        age = None if track.last_fix_at is None else max(0, now - track.last_fix_at)
        return TrackSnapshot(
            vehicle_id=vehicle_id,
            x=x,
            y=y,
            s_m=s,
            v_mps=v,
            matched_segment=track.matched_segment,
            position_source=track.position_source,
            uncertainty_m=track.uncertainty_m,
            last_fix_age_ms=age,
            etas=self._etas(track, now),
        )

    def route_position(self, vehicle_id: str, now: SimTimeMs) -> tuple[int, float]:
        """Index of the route segment the vehicle is on, and its offset in metres
        along that segment (the planner's start state for replanning)."""
        track = self._tracks[vehicle_id]
        s_m = self.snapshot(vehicle_id, now).s_m
        last = len(track.route_segments) - 1
        index = 0
        for j in range(last + 1):
            if track.cumulative_m[j] <= s_m:
                index = j
        index = min(index, last)
        return index, max(0.0, s_m - track.cumulative_m[index])

    def route(self, vehicle_id: str) -> list[str]:
        return list(self._tracks[vehicle_id].route)

    def tracked_ids(self) -> list[str]:
        return sorted(self._tracks)

    # -- helpers ----------------------------------------------------------

    def _build_track(self, vehicle_id: str, priority: Priority, route: list[str]) -> VehicleTrack:
        if len(route) < 2:
            raise ValueError("route must have at least two nodes")
        route_segments = [f"{route[i]}->{route[i + 1]}" for i in range(len(route) - 1)]
        cumulative = [0.0]
        for sid in route_segments:
            cumulative.append(cumulative[-1] + self.city.segment(sid).length_m)
        return VehicleTrack(
            vehicle_id=vehicle_id,
            priority=priority,
            route=list(route),
            route_segments=route_segments,
            cumulative_m=cumulative,
            filter=AlphaBetaFilter(self.settings.alpha_beta_alpha, self.settings.alpha_beta_beta),
            ewma=EwmaSpeed(self.settings.ewma_speed_alpha),
        )

    def _stop_line_s(self, track: VehicleTrack, node: str) -> float:
        index = track.route.index(node)
        return track.cumulative_m[index]

    def _uncertainty_m(self, age_ms: int) -> float:
        growth = self.settings.dead_reckoning_growth_mps * (age_ms / 1000.0)
        return self.settings.gps_noise_std_m + growth

    def _position_at(self, track: VehicleTrack, s: float) -> tuple[float, float]:
        s = min(max(s, 0.0), track.cumulative_m[-1])
        for i, sid in enumerate(track.route_segments):
            if s <= track.cumulative_m[i + 1] or i == len(track.route_segments) - 1:
                road = self.city.segment(sid)
                a = self.city.intersection(road.from_node)
                b = self.city.intersection(road.to_node)
                frac = 0.0 if road.length_m == 0 else (s - track.cumulative_m[i]) / road.length_m
                frac = min(max(frac, 0.0), 1.0)
                return a.x + (b.x - a.x) * frac, a.y + (b.y - a.y) * frac
        return self.city.intersection(track.route[-1]).x, self.city.intersection(track.route[-1]).y

    def _etas(self, track: VehicleTrack, now: SimTimeMs) -> list[IntersectionEta]:
        if track.filter.state is None:
            return []
        s = track.filter.state.s_m
        targets = []
        for node in track.route[1:]:
            if node in track.passed:
                continue
            stop_s = self._stop_line_s(track, node)
            targets.append(
                StopLineTarget(
                    intersection_id=node,
                    route_index=track.route.index(node),
                    s_m=stop_s,
                    queue_wait_ms=self._queue_wait(node, now),
                    cycle_ms=self._cycle_ms[node],
                    expected_red_wait_ms=self._expected_red_wait(track, node, now),
                )
            )
        speeds = list(track.speeds)
        speed_std = _std(speeds)
        age = 0 if track.last_fix_at is None else max(0, now - track.last_fix_at)
        return compute_etas(
            self.city,
            self.settings,
            track.route,
            targets,
            s_m=s,
            speed_std_mps=speed_std,
            gps_age_ms=age,
            stale_fraction=self._stale_fraction(track.route_segments, now),
            now=now,
        )

    def _compute_cycles(self) -> dict[str, int]:
        cycles: dict[str, int] = {}
        for node in self.city.intersections:
            total = 0
            for entry in node.default_plan:
                phase = next(p for p in node.phases if p.id == entry.phase_id)
                total += entry.green_ms + phase.amber_ms + phase.all_red_ms
            cycles[node.id] = total
        return cycles

    def _emit(self, event_type: EventType, now: SimTimeMs, payload: dict[str, object]) -> None:
        if self.bus is not None:
            self.bus.publish(event_type, now, payload)


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))
