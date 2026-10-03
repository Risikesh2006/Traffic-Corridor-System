"""The central system's belief about the network (PRD §10.3, FR-WS-01..06).

Every answer carries its own uncertainty. Missing data is never treated as
"empty road" (FR-WS-06): a segment never heard from falls back to its
historical prior, pessimistically inflated.

Density/queue estimate (per segment, metric, at time `now`):
  age        = now - source timestamp of the latest live reading
  confidence = exp(-age / tau)                         (FR-WS-02)
  estimate   = confidence*live + (1-confidence)*prior  (FR-WS-03)
  if age > density_ttl or no live value ever:
      estimate = prior * pessimism_factor, source=HISTORICAL, stale=True  (FR-WS-04)

All queries take `now` explicitly; nothing reads a hidden clock. Iteration
is deterministic (sorted keys).
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from core.clock import Clock, SimTimeMs
from core.config import Settings
from core.models import (
    City,
    ControllerStatus,
    Direction,
    EstimateSource,
    SensorMetric,
    SensorReading,
    SignalState,
    StatusReport,
)
from safety.supervisor import IntersectionView as SupervisorView
from state.history import HistoricalProfile


@dataclass(frozen=True)
class Estimate:
    value: float
    confidence: float
    source: EstimateSource
    age_ms: int | None
    stale: bool


@dataclass(frozen=True)
class LiveValue:
    value: float
    source_timestamp: SimTimeMs
    received_at: SimTimeMs
    sequence: int


@dataclass(frozen=True)
class IntersectionView:
    intersection_id: str
    controller_status: ControllerStatus
    signal_state: SignalState | None
    current_phase_id: str | None
    state_started_at: SimTimeMs | None
    red_since: dict[Direction, SimTimeMs]
    applied_plan_version: int
    report_age_ms: int | None


@dataclass
class _IntersectionRecord:
    report: StatusReport | None = None
    received_at: SimTimeMs | None = None


@dataclass
class WorldState:
    city: City
    settings: Settings
    clock: Clock
    profile: HistoricalProfile | None = None
    _live: dict[tuple[str, SensorMetric], LiveValue] = field(default_factory=dict)
    _intersections: dict[str, _IntersectionRecord] = field(default_factory=dict)
    _closed_roads: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._segment_ids = sorted(r.id for r in self.city.roads)
        self._approach_of: dict[str, tuple[str, Direction]] = {
            sid: (self.city.segment(sid).to_node, self.city.approach_direction(sid))
            for sid in self._segment_ids
        }
        self._intersection_ids = sorted(n.id for n in self.city.intersections)

    # -- ingestion of accepted inputs ---------------------------------

    def apply_sensor(self, reading: SensorReading, received_at: SimTimeMs) -> None:
        key = (reading.road_id, reading.metric)
        current = self._live.get(key)
        if current is not None and reading.sequence <= current.sequence:
            return  # never let an older reading overwrite a newer one
        self._live[key] = LiveValue(
            value=reading.value,
            source_timestamp=reading.timestamp,
            received_at=received_at,
            sequence=reading.sequence,
        )

    def apply_status(self, report: StatusReport, received_at: SimTimeMs) -> None:
        record = self._intersections.setdefault(report.intersection_id, _IntersectionRecord())
        if record.report is not None and report.sequence <= record.report.sequence:
            return
        record.report = report
        record.received_at = received_at

    # -- road status (FR-WS-05) ----------------------------------------

    def close_road(self, segment_ids: list[str], reason: str) -> None:
        for sid in segment_ids:
            self.city.segment(sid)  # raises KeyError for unknown segments
            self._closed_roads[sid] = reason

    def reopen_road(self, segment_ids: list[str]) -> None:
        for sid in segment_ids:
            self._closed_roads.pop(sid, None)

    def road_status(self, segment_id: str) -> str:
        return "CLOSED" if segment_id in self._closed_roads else "OPEN"

    # -- density / queue estimates (FR-WS-01..04, FR-WS-06) -------------

    def estimate(self, segment_id: str, metric: SensorMetric, now: SimTimeMs) -> Estimate:
        prior_value = self._prior(segment_id, metric, now)
        live = self._live.get((segment_id, metric))
        pessimistic_prior = prior_value * self.settings.pessimism_factor

        if live is None:
            return Estimate(pessimistic_prior, 0.0, EstimateSource.HISTORICAL, None, True)

        age_ms = max(0, now - live.source_timestamp)
        if age_ms > self.settings.density_ttl_ms:
            return Estimate(pessimistic_prior, 0.0, EstimateSource.HISTORICAL, age_ms, True)

        confidence = math.exp(-age_ms / self.settings.tau_density_ms)
        value = confidence * live.value + (1.0 - confidence) * prior_value
        source = EstimateSource.LIVE if confidence >= 0.99 else EstimateSource.BLENDED
        return Estimate(value, confidence, source, age_ms, False)

    def queue_estimate(
        self, intersection_id: str, approach: Direction, now: SimTimeMs
    ) -> Estimate | None:
        """Estimated queue at one approach's stop line (FR-COR input for
        clearance time). None if no segment feeds that approach."""
        for sid in self._segment_ids:
            node, direction = self._approach_of[sid]
            if node == intersection_id and direction == approach:
                return self.estimate(sid, SensorMetric.QUEUE_COUNT, now)
        return None

    def _prior(self, segment_id: str, metric: SensorMetric, now: SimTimeMs) -> float:
        if self.profile is None:
            return 0.0
        prior = self.profile.lookup(segment_id, now, self.clock)
        return prior.queue if metric == SensorMetric.QUEUE_COUNT else prior.density

    # -- intersection view (the supervisor's input, FR-WS / FH-06) -------

    def intersection_view(self, intersection_id: str, now: SimTimeMs) -> IntersectionView:
        record = self._intersections.get(intersection_id)
        if record is None or record.report is None or record.received_at is None:
            return IntersectionView(
                intersection_id=intersection_id,
                controller_status=ControllerStatus.UNREACHABLE,
                signal_state=None,
                current_phase_id=None,
                state_started_at=None,
                red_since={},
                applied_plan_version=0,
                report_age_ms=None,
            )

        report = record.report
        report_age = max(0, now - record.received_at)
        if report_age > self.settings.heartbeat_timeout_ms:
            status = ControllerStatus.UNREACHABLE
        else:
            status = report.controller_status
        return IntersectionView(
            intersection_id=intersection_id,
            controller_status=status,
            signal_state=report.signal_state,
            state_started_at=report.state_started_at,
            current_phase_id=report.current_phase_id,
            red_since=dict(report.red_since),
            applied_plan_version=report.applied_plan_version,
            report_age_ms=report_age,
        )

    def supervisor_view(self, intersection_id: str, now: SimTimeMs) -> SupervisorView:
        """The view the Safety Supervisor validates against (Phase 3's
        IntersectionView), derived from what World State believes now."""
        view = self.intersection_view(intersection_id, now)
        reported_at = now - (view.report_age_ms or 0)
        return SupervisorView(
            intersection_id=intersection_id,
            controller_status=view.controller_status,
            reported_at=reported_at,
            current_phase_id=view.current_phase_id,
            red_since=view.red_since,
        )

    # -- freshness summary (for UI and health monitor later) -------------

    def freshness_summary(self, now: SimTimeMs) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for sid in self._segment_ids:
            est = self.estimate(sid, SensorMetric.DENSITY, now)
            if est.source == EstimateSource.LIVE:
                counts["fresh"] += 1
            elif est.source == EstimateSource.BLENDED:
                counts["ageing"] += 1
            else:
                counts["stale"] += 1
        return dict(sorted(counts.items()))
