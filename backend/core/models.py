"""Domain model entities (PRD §9).

Pydantic models are used for schema validation at system boundaries (API, ingestion)
and for clean serialisation into the event store.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class RoadStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class ControllerStatus(StrEnum):
    ONLINE = "ONLINE"
    UNREACHABLE = "UNREACHABLE"
    FLASHING = "FLASHING"


class PositionSource(StrEnum):
    GPS = "GPS"
    DEAD_RECKONING = "DEAD_RECKONING"


class Priority(StrEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class LeaseStatus(StrEnum):
    REQUESTED = "REQUESTED"
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class CommandType(StrEnum):
    PREEMPT = "PREEMPT"
    RENEW = "RENEW"
    RELEASE = "RELEASE"
    GATE = "GATE"
    RECOVER = "RECOVER"


class HospitalStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class Role(StrEnum):
    OPERATOR = "OPERATOR"
    CREW = "CREW"
    HOSPITAL = "HOSPITAL"


class OperatingMode(StrEnum):
    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"
    SAFE = "SAFE"


class Phase(BaseModel):
    id: str
    movements: list[str]
    min_green_s: float
    amber_s: float
    all_red_s: float
    ped_clearance_s: float = 0.0


class ConflictMatrix(BaseModel):
    movements: list[str]
    conflicts: list[tuple[str, str]]

    def conflicts_with(self, a: str, b: str) -> bool:
        return (a, b) in self.conflicts or (b, a) in self.conflicts


class Approach(BaseModel):
    id: str
    intersection_id: str
    direction: str
    lanes: int = 1
    queue_length: int = 0


class Intersection(BaseModel):
    id: str
    x: float
    y: float
    approaches: list[Approach] = Field(default_factory=list)
    phases: list[Phase] = Field(default_factory=list)
    conflict_matrix: ConflictMatrix
    controller_status: ControllerStatus = ControllerStatus.ONLINE
    current_phase: str | None = None
    phase_started_at: float = 0.0


class RoadSegment(BaseModel):
    id: str
    from_node: str
    to_node: str
    length_m: float
    lanes: int = 1
    free_flow_speed_mps: float
    capacity_vph: float
    status: RoadStatus = RoadStatus.OPEN
    density_estimate: float = 0.0
    confidence: float = 0.0


class SensorReading(BaseModel):
    source_id: str
    road_id: str
    value: float
    timestamp: float
    sequence: int
    confidence: float = 1.0


class EmergencyVehicle(BaseModel):
    id: str
    priority: Priority
    x: float
    y: float
    speed_mps: float = 0.0
    heading_deg: float = 0.0
    destination_id: str
    last_seen_at: float
    position_source: PositionSource = PositionSource.GPS


class CorridorEntry(BaseModel):
    intersection_id: str
    phase_id: str
    eta: float
    request_at: float
    hold_until: float | None = None
    status: str = "PLANNED"


class Route(BaseModel):
    id: str
    version: int
    vehicle_id: str
    edges: list[str]
    cost_s: float
    computed_at: float
    alternatives: list[list[str]] = Field(default_factory=list)


class CorridorPlan(BaseModel):
    route_version: int
    entries: list[CorridorEntry] = Field(default_factory=list)


class PreemptionLease(BaseModel):
    lease_id: str
    intersection_id: str
    phase_id: str
    plan_version: int
    granted_at: float
    expires_at: float
    idempotency_key: str
    status: LeaseStatus = LeaseStatus.REQUESTED


class Command(BaseModel):
    id: str
    type: CommandType
    intersection_id: str
    plan_version: int
    idempotency_key: str
    payload: dict = Field(default_factory=dict)
    created_at: float


class Incident(BaseModel):
    id: str
    type: str
    affected_edges: list[str]
    severity: str
    reported_at: float
    cleared_at: float | None = None


class Hospital(BaseModel):
    id: str
    node_id: str
    name: str
    status: HospitalStatus = HospitalStatus.AVAILABLE


class Event(BaseModel):
    id: str
    type: str
    timestamp: float
    payload: dict
    causation_id: str | None = None
    correlation_id: str | None = None


class User(BaseModel):
    id: str
    name: str
    role: Role
    assigned_vehicle_id: str | None = None
    hospital_id: str | None = None
