"""Ground-truth evaluation harness (PRD Phase 4 Part F).

Central code must never see ground truth. This module is allowed to: it sits
in the composition root, reads the simulator's true state each sample, and
compares it with what the central system believes. Used by the tracking
report and by tests; nothing in the control path depends on it.

Scenario faults are applied here by calling the simulator's own fault hooks
(GPS on/off, sensor freeze), at the scheduled simulated times.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from core.events import EventType
from core.models import PositionSource, SensorMetric
from runtime.composition import Runtime


@dataclass(frozen=True)
class Scenario:
    gps_loss_start_s: float | None = None
    gps_loss_duration_s: float = 0.0
    stale_column: str | None = None
    stale_start_s: float = 0.0
    stale_duration_s: float = 0.0


@dataclass(frozen=True)
class _Prediction:
    t_s: float
    node: str
    expected_s: float
    unimpeded_s: float
    low_s: float
    high_s: float


@dataclass
class EtaSample:
    horizon_s: int
    error_expected_s: float
    error_unimpeded_s: float
    inside_window: bool


@dataclass
class Report:
    position_error_gps_m: list[float] = field(default_factory=list)
    position_error_dr_m: list[float] = field(default_factory=list)
    eta_samples: list[EtaSample] = field(default_factory=list)
    density_error_fresh: list[float] = field(default_factory=list)
    density_error_stale: list[float] = field(default_factory=list)
    stop_line_errors_s: list[float] = field(default_factory=list)
    deviation_events: int = 0
    gateway_stats: dict[str, dict[str, int]] = field(default_factory=dict)
    ingest_ms: list[float] = field(default_factory=list)
    central_step_ms: list[float] = field(default_factory=list)


def _p(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))
    return ordered[index]


def percentile(values: list[float], q: float) -> float:
    return _p(values, q)


class Evaluation:
    def __init__(
        self, runtime: Runtime, vehicle_id: str, route: list[str], scenario: Scenario
    ) -> None:
        self.rt = runtime
        self.vehicle_id = vehicle_id
        self.route = route
        self.scenario = scenario
        self.report = Report()
        self._route_index_seen = 0
        self._predictions: list[_Prediction] = []
        self._true_arrival_s: dict[str, float] = {}
        self._true_departure_s: dict[str, float] = {}
        self._segment_seen: str | None = None
        self._gps_on = True
        self._stale_applied = False
        self._stale_cleared = False
        self._instrument_central()
        self._stale_segments = (
            [r.id for r in runtime.sim.city.roads if r.to_node[0] == scenario.stale_column]
            if scenario.stale_column
            else []
        )

    def _instrument_central(self) -> None:
        central = self.rt.central
        original_step = central.step

        def timed_step(now: int) -> None:
            start = time.perf_counter()
            original_step(now)
            self.report.central_step_ms.append((time.perf_counter() - start) * 1000)

        central.step = timed_step  # type: ignore[method-assign]
        gateway = central.gateway
        for name in ("ingest_gps_fix", "ingest_sensor_reading", "ingest_status_report"):
            original = getattr(gateway, name)

            def timed(item, _original=original):  # type: ignore[no-untyped-def]
                start = time.perf_counter()
                result = _original(item)
                self.report.ingest_ms.append((time.perf_counter() - start) * 1000)
                return result

            setattr(gateway, name, timed)

    # -- scenario faults --------------------------------------------------

    def _apply_faults(self, now_s: float) -> None:
        sc = self.scenario
        if sc.gps_loss_start_s is not None:
            should_be_on = not (
                sc.gps_loss_start_s <= now_s < sc.gps_loss_start_s + sc.gps_loss_duration_s
            )
            if should_be_on != self._gps_on:
                (
                    self.rt.sim.vehicle_feed.gps_on
                    if should_be_on
                    else self.rt.sim.vehicle_feed.gps_off
                )(self.vehicle_id)
                self._gps_on = should_be_on
        if self._stale_segments:
            if not self._stale_applied and now_s >= sc.stale_start_s:
                self.rt.sim.sensors.freeze(self._stale_segments)
                self._stale_applied = True
            if (
                self._stale_applied
                and not self._stale_cleared
                and now_s >= sc.stale_start_s + sc.stale_duration_s
            ):
                self.rt.sim.sensors.unfreeze(self._stale_segments)
                self._stale_cleared = True

    # -- sampling ------------------------------------------------------

    def run(self, duration_ms: int, sample_every_ms: int = 1000) -> Report:
        rt = self.rt
        ticks = duration_ms // rt.settings.sim_tick_ms
        sample_ticks = max(1, sample_every_ms // rt.settings.sim_tick_ms)
        for i in range(ticks):
            self._apply_faults(rt.sim.clock.now() / 1000.0)
            rt.step()
            self._track_true_arrivals()
            if i % sample_ticks == 0 and rt.sim.clock.now() > 0:
                self._sample()
        self._finish()
        return self.report

    def _track_true_arrivals(self) -> None:
        """Arrival is when the ambulance reaches a stop line (unimpeded ETA
        target). Departure is when it moves on from that line, which is when
        it actually clears the intersection (expected ETA target)."""
        amb = self.rt.sim.ambulances.ambulances.get(self.vehicle_id)
        if amb is None:
            return
        now_s = self.rt.sim.clock.now() / 1000.0
        while self._route_index_seen < amb.route_index:
            self._route_index_seen += 1
            self._true_arrival_s[self.route[self._route_index_seen]] = now_s
        if amb.current_segment_id != self._segment_seen and amb.current_segment_id is not None:
            if self._segment_seen is not None:
                node = self.route[amb.route_index]
                if node is not None:
                    self._true_departure_s[node] = now_s
            self._segment_seen = amb.current_segment_id

    def _sample(self) -> None:
        rt = self.rt
        now = rt.sim.clock.now()
        now_s = now / 1000.0
        amb = rt.sim.ambulances.ambulances.get(self.vehicle_id)
        if amb is None or amb.arrival_time is not None:
            return

        snap = rt.central.tracker.snapshot(self.vehicle_id, now)
        error = math.hypot(snap.x - amb.x, snap.y - amb.y)
        if snap.position_source == PositionSource.GPS:
            self.report.position_error_gps_m.append(error)
        else:
            self.report.position_error_dr_m.append(error)

        for eta in snap.etas:
            self._predictions.append(
                _Prediction(
                    t_s=now_s,
                    node=eta.intersection_id,
                    expected_s=eta.eta_expected_ms / 1000.0,
                    unimpeded_s=eta.eta_unimpeded_ms / 1000.0,
                    low_s=eta.eta_low_ms / 1000.0,
                    high_s=eta.eta_high_ms / 1000.0,
                )
            )

        self._sample_density(now)

    def _sample_density(self, now: int) -> None:
        sim = self.rt.sim
        for road in sim.city.roads:
            seg = sim.traffic.segments[road.id]
            km_lane = (road.length_m / 1000.0) * road.lanes
            truth = (len(seg.queue) + len(seg.in_transit)) / km_lane if km_lane > 0 else 0.0
            est = self.rt.central.world.estimate(road.id, SensorMetric.DENSITY, now)
            error = abs(est.value - truth)
            if est.stale:
                self.report.density_error_stale.append(error)
            else:
                self.report.density_error_fresh.append(error)

    def _finish(self) -> None:
        rt = self.rt
        for pred in self._predictions:
            arrival = self._true_arrival_s.get(pred.node)
            departure = self._true_departure_s.get(pred.node)
            if arrival is None or departure is None or arrival <= pred.t_s:
                continue
            horizon = arrival - pred.t_s
            for h in (30, 60):
                if abs(horizon - h) <= 1.0:
                    self.report.eta_samples.append(
                        EtaSample(
                            horizon_s=h,
                            error_expected_s=pred.expected_s - departure,
                            error_unimpeded_s=pred.unimpeded_s - arrival,
                            inside_window=pred.low_s <= departure <= pred.high_s,
                        )
                    )
        self.report.gateway_stats = rt.central.gateway.stats()
        self.report.deviation_events = sum(
            1 for e in rt.central.bus.history() if e.type == EventType.ROUTE_DEVIATION
        )
        for event in rt.central.bus.history():
            if event.type != EventType.STOP_LINE_PASSED:
                continue
            node = event.payload.get("intersection_id")
            true_s = self._true_departure_s.get(str(node))
            if true_s is not None:
                self.report.stop_line_errors_s.append(event.timestamp / 1000.0 - true_s)
