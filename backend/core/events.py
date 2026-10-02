"""The event envelope used by the event bus and event store (PRD §9, §10.9).

Event IDs are deterministic (counter-based), not uuid4, so that two runs with
the same seed and inputs produce byte-identical event logs (ADR-001/AC-02).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from core.clock import SimTimeMs


class EventType(StrEnum):
    SENSOR_READING = "SENSOR_READING"
    GPS_FIX = "GPS_FIX"
    INCIDENT_REPORTED = "INCIDENT_REPORTED"
    INCIDENT_CLEARED = "INCIDENT_CLEARED"
    ROUTE_UPDATED = "ROUTE_UPDATED"
    COMMAND_PROPOSED = "COMMAND_PROPOSED"
    COMMAND_APPROVED = "COMMAND_APPROVED"
    COMMAND_REJECTED = "COMMAND_REJECTED"
    COMMAND_ACKED = "COMMAND_ACKED"
    LEASE_GRANTED = "LEASE_GRANTED"
    LEASE_RENEWED = "LEASE_RENEWED"
    LEASE_RELEASED = "LEASE_RELEASED"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    SIGNAL_CHANGED = "SIGNAL_CHANGED"
    MODE_CHANGED = "MODE_CHANGED"
    HEARTBEAT = "HEARTBEAT"
    DECISION = "DECISION"


class Event(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    type: EventType
    timestamp: SimTimeMs
    payload: dict[str, object]
    causation_id: str | None = None
    correlation_id: str | None = None


class EventIdFactory:
    """Deterministic, monotonically increasing event IDs: `evt-000001`, ...

    A counter (rather than content hashing) keeps IDs short and ordering
    obvious in logs/UIs, while staying fully deterministic per run.
    """

    def __init__(self) -> None:
        self._counter = 0

    def next_id(self) -> str:
        self._counter += 1
        return f"evt-{self._counter:06d}"
