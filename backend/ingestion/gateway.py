"""The ingestion gateway (PRD §10.2 FR-ING-01..05) — every input (GPS fix,
sensor reading, controller status report, command ack) passes through here
before anything else in the central system sees it.

For each input: validate (the Pydantic model already did schema validation
by construction — this layer adds the semantic checks), timestamp with
`received_at`, clamp future-skewed source clocks, deduplicate and reject
stale sequences, reject physically implausible GPS, and publish accepted
inputs as events.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from core.clock import Clock, SimTimeMs
from core.config import Settings
from core.events import EventType
from core.models import CommandAck, GpsFix, SensorReading, StatusReport
from events.bus import EventBus

_GPS = "gps"
_SENSOR = "sensor"
_STATUS = "status"
_ACK = "ack"


@dataclass(frozen=True)
class Rejection:
    source_id: str
    reason: str
    detail: str
    received_at: SimTimeMs


class IngestionGateway:
    def __init__(self, clock: Clock, settings: Settings, bus: EventBus | None = None) -> None:
        self.clock = clock
        self.settings = settings
        self.bus = bus

        # Bounded per-source state: one (kind, source_id) -> last accepted
        # sequence number. This is the dedup/out-of-order bound: O(number
        # of distinct sources), never O(number of messages) — a source
        # only ever occupies one entry regardless of how many messages it
        # has sent.
        self._last_sequence: dict[tuple[str, str], int] = {}
        self._last_gps_fix: dict[str, GpsFix] = {}

        self._stats: dict[str, Counter[str]] = {}
        self.rejections: list[Rejection] = []

    def stats(self) -> dict[str, dict[str, int]]:
        return {source_id: dict(counter) for source_id, counter in self._stats.items()}

    # -- GPS fixes --------------------------------------------------------

    def ingest_gps_fix(self, fix: GpsFix) -> GpsFix | None:
        source_id = f"{_GPS}:{fix.vehicle_id}"
        received_at = self.clock.now()

        clamped_timestamp, skewed = self._clamp_timestamp(fix.timestamp, received_at)
        if skewed:
            self._count(source_id, "clock_skew")
            fix = fix.model_copy(update={"timestamp": clamped_timestamp})

        ordering = self._check_ordering(source_id, fix.sequence)
        if ordering is not None:
            self._reject(source_id, ordering, f"sequence={fix.sequence}", received_at)
            return None

        last = self._last_gps_fix.get(fix.vehicle_id)
        if last is not None:
            dt_s = max(0.0, (fix.timestamp - last.timestamp) / 1000.0)
            distance_m = ((fix.x - last.x) ** 2 + (fix.y - last.y) ** 2) ** 0.5
            implausible_jump = distance_m > self.settings.max_gps_jump_m
            implausible_speed = dt_s > 0 and (distance_m / dt_s) > self.settings.max_gps_speed_mps
            if implausible_jump or implausible_speed:
                detail = f"jump={distance_m:.1f}m over dt={dt_s:.2f}s"
                self._reject(source_id, "implausible", detail, received_at)
                return None

        self._last_gps_fix[fix.vehicle_id] = fix
        self._count(source_id, "accepted")
        if self.bus is not None:
            payload = {"vehicle_id": fix.vehicle_id, "sequence": fix.sequence}
            self.bus.publish(EventType.GPS_FIX, received_at, payload)
        return fix

    # -- sensor readings --------------------------------------------------

    def ingest_sensor_reading(self, reading: SensorReading) -> SensorReading | None:
        source_id = f"{_SENSOR}:{reading.source_id}"
        received_at = self.clock.now()

        clamped_timestamp, skewed = self._clamp_timestamp(reading.timestamp, received_at)
        if skewed:
            self._count(source_id, "clock_skew")
            reading = reading.model_copy(update={"timestamp": clamped_timestamp})

        ordering = self._check_ordering(source_id, reading.sequence)
        if ordering is not None:
            self._reject(source_id, ordering, f"sequence={reading.sequence}", received_at)
            return None

        self._count(source_id, "accepted")
        if self.bus is not None:
            payload: dict[str, object] = {
                "road_id": reading.road_id,
                "metric": reading.metric.value,
                "sequence": reading.sequence,
            }
            self.bus.publish(EventType.SENSOR_READING, received_at, payload)
        return reading

    # -- controller status reports -----------------------------------------

    def ingest_status_report(self, report: StatusReport) -> StatusReport | None:
        source_id = f"{_STATUS}:{report.intersection_id}"
        received_at = self.clock.now()

        clamped_timestamp, skewed = self._clamp_timestamp(report.sent_at, received_at)
        if skewed:
            self._count(source_id, "clock_skew")
            report = report.model_copy(update={"sent_at": clamped_timestamp})

        ordering = self._check_ordering(source_id, report.sequence)
        if ordering is not None:
            self._reject(source_id, ordering, f"sequence={report.sequence}", received_at)
            return None

        self._count(source_id, "accepted")
        return report

    # -- command acks -----------------------------------------------------

    def ingest_command_ack(self, ack: CommandAck) -> CommandAck | None:
        # Acks aren't sequenced by the controller the same way GPS/sensors
        # are (they're replies, not a stream) — idempotency_key is already
        # the controller's own dedup key. The gateway just timestamps and
        # publishes; no ordering/dedup state needed here.
        source_id = f"{_ACK}:{ack.intersection_id}"
        received_at = self.clock.now()
        self._count(source_id, "accepted")
        if self.bus is not None:
            self.bus.publish(
                EventType.COMMAND_ACKED,
                received_at,
                {"intersection_id": ack.intersection_id, "idempotency_key": ack.idempotency_key},
            )
        return ack

    # -- shared helpers --------------------------------------------------

    def _clamp_timestamp(
        self, source_timestamp: SimTimeMs, received_at: SimTimeMs
    ) -> tuple[SimTimeMs, bool]:
        if source_timestamp > received_at + self.settings.clock_skew_tolerance_ms:
            return received_at, True
        return source_timestamp, False

    def _check_ordering(self, source_id: str, sequence: int) -> str | None:
        key = (source_id, "seq")
        last = self._last_sequence.get(key)
        if last is not None:
            if sequence == last:
                return "duplicate"
            if sequence < last:
                return "out_of_order"
        self._last_sequence[key] = sequence
        return None

    def _count(self, source_id: str, reason: str) -> None:
        self._stats.setdefault(source_id, Counter())[reason] += 1

    def _reject(self, source_id: str, reason: str, detail: str, received_at: SimTimeMs) -> None:
        self._count(source_id, reason)
        self.rejections.append(Rejection(source_id, reason, detail, received_at))
