"""Domain model entities (PRD §9), as typed, validated Pydantic v2 models.

Simplification (ADR-004): a `Movement` is identified by the approach
direction it arrives from (e.g. `N` = the through movement entering from the
north approach). Only through-movements are modelled for now — no turning
movements. This keeps the conflict matrix small and matches the PRD's
two-phase (NS / EW) intersection model; turning movements are future work.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from core.clock import SimTimeMs


class Direction(StrEnum):
    N = "N"
    S = "S"
    E = "E"
    W = "W"


# A Movement is just the approach direction it arrives from (see module docstring).
Movement = Direction


class ControllerStatus(StrEnum):
    ONLINE = "ONLINE"
    UNREACHABLE = "UNREACHABLE"
    FLASHING = "FLASHING"


class RoadStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class SignalState(StrEnum):
    GREEN = "GREEN"
    AMBER = "AMBER"
    ALL_RED = "ALL_RED"
    FLASHING_RED = "FLASHING_RED"


class Priority(StrEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class PositionSource(StrEnum):
    GPS = "GPS"
    DEAD_RECKONING = "DEAD_RECKONING"


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


class OperatingMode(StrEnum):
    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"
    SAFE = "SAFE"


class UserRole(StrEnum):
    OPERATOR = "OPERATOR"
    CREW = "CREW"
    HOSPITAL = "HOSPITAL"


class IncidentType(StrEnum):
    ACCIDENT = "ACCIDENT"
    CLOSURE = "CLOSURE"
    OTHER = "OTHER"


class CommandAckStatus(StrEnum):
    EXECUTED = "EXECUTED"
    DUPLICATE = "DUPLICATE"
    IGNORED_STALE_VERSION = "IGNORED_STALE_VERSION"
    REJECTED = "REJECTED"


class SensorMetric(StrEnum):
    DENSITY = "DENSITY"
    QUEUE_COUNT = "QUEUE_COUNT"


class EstimateSource(StrEnum):
    LIVE = "LIVE"
    BLENDED = "BLENDED"
    HISTORICAL = "HISTORICAL"


def build_idempotency_key(plan_version: int, intersection_id: str, seq: int) -> str:
    """Canonical idempotency key (PRD §9/§10.6 FR-COR-05): "v{plan_version}:{id}:{seq:04d}"."""
    return f"v{plan_version}:{intersection_id}:{seq:04d}"


class Approach(BaseModel):
    id: str
    intersection_id: str
    direction: Direction
    lanes: int = Field(ge=1)
    queue_length: int = Field(default=0, ge=0)


class Phase(BaseModel):
    id: str
    movements: list[Movement]
    min_green_ms: int = Field(gt=0)
    amber_ms: int = Field(gt=0)
    all_red_ms: int = Field(gt=0)
    ped_clearance_ms: int = Field(default=0, ge=0)


class ConflictMatrix(BaseModel):
    """Stores authored conflict pairs; `conflicts()` is symmetric regardless
    of storage order. `city_loader` separately checks that the *authored*
    data is explicitly symmetric and free of self-conflicts, as a data
    quality gate on config files (not a runtime requirement of this class).
    """

    movements: list[Movement]
    conflict_pairs: list[tuple[Movement, Movement]]

    def conflicts(self, a: Movement, b: Movement) -> bool:
        return (a, b) in self.conflict_pairs or (b, a) in self.conflict_pairs

    def is_compatible(self, movements: set[Movement]) -> bool:
        items = list(movements)
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if self.conflicts(items[i], items[j]):
                    return False
        return True


class SignalPlanEntry(BaseModel):
    """One phase's share of the default (non-preempted) signal cycle."""

    phase_id: str
    green_ms: int = Field(gt=0)


class Intersection(BaseModel):
    id: str
    x: float
    y: float
    approaches: list[Approach] = Field(default_factory=list)
    phases: list[Phase] = Field(default_factory=list)
    conflict_matrix: ConflictMatrix
    default_plan: list[SignalPlanEntry] = Field(default_factory=list)
    cycle_offset_ms: int = Field(default=0, ge=0)
    controller_status: ControllerStatus = ControllerStatus.ONLINE
    current_phase: str | None = None
    phase_started_at: SimTimeMs = 0


class RoadSegment(BaseModel):
    id: str
    from_node: str
    to_node: str
    length_m: float = Field(gt=0)
    lanes: int = Field(ge=1)
    free_flow_speed_mps: float = Field(gt=0)
    capacity_vph: float = Field(gt=0)
    status: RoadStatus = RoadStatus.OPEN
    density_estimate: float = 0.0
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class Hospital(BaseModel):
    id: str
    node_id: str
    name: str
    status: HospitalStatus = HospitalStatus.AVAILABLE


class City(BaseModel):
    """The static road network. Mutable live state (queues, density,
    controller status) lives on the entities themselves for now; a
    dedicated World State module arrives in a later phase.
    """

    intersections: list[Intersection]
    roads: list[RoadSegment]
    hospitals: list[Hospital]
    ambulance_entry_node: str

    def intersection(self, node_id: str) -> Intersection:
        for node in self.intersections:
            if node.id == node_id:
                return node
        raise KeyError(f"unknown intersection: {node_id}")

    def segment(self, segment_id: str) -> RoadSegment:
        for road in self.roads:
            if road.id == segment_id:
                return road
        raise KeyError(f"unknown road segment: {segment_id}")

    def outgoing(self, node_id: str) -> list[RoadSegment]:
        return [r for r in self.roads if r.from_node == node_id]

    def incoming(self, node_id: str) -> list[RoadSegment]:
        return [r for r in self.roads if r.to_node == node_id]

    def neighbors(self, node_id: str) -> list[str]:
        return [r.to_node for r in self.outgoing(node_id)]

    def distance_m(self, a: str, b: str) -> float:
        node_a, node_b = self.intersection(a), self.intersection(b)
        return float(((node_a.x - node_b.x) ** 2 + (node_a.y - node_b.y) ** 2) ** 0.5)


class SensorReading(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    road_id: str
    metric: SensorMetric = SensorMetric.DENSITY
    value: float
    timestamp: SimTimeMs
    sequence: int
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class GpsFix(BaseModel):
    model_config = ConfigDict(frozen=True)

    vehicle_id: str
    x: float
    y: float
    speed_mps: float
    heading_deg: float
    timestamp: SimTimeMs
    sequence: int


class PresenceReading(BaseModel):
    """A stop-line emergency-vehicle presence detector firing (PRD Phase 4
    D1) — a secondary confirmation source used when GPS is lost. Real-world
    equivalent: optical/acoustic emergency vehicle detectors."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    intersection_id: str
    approach_direction: Direction
    vehicle_id: str
    timestamp: SimTimeMs
    sequence: int


class ActiveHoldInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    phase_id: str
    hold_until: SimTimeMs


class StatusReport(BaseModel):
    """What an intersection controller reports up-link every
    heartbeat_interval_s (PRD Phase 4 A4) — subject to the same network
    latency/drops as any other message, so central code must treat it as
    possibly delayed or missing."""

    model_config = ConfigDict(frozen=True)

    intersection_id: str
    sequence: int
    sent_at: SimTimeMs
    controller_status: ControllerStatus
    signal_state: SignalState
    current_phase_id: str | None
    state_started_at: SimTimeMs
    red_since: dict[Direction, SimTimeMs]
    applied_plan_version: int
    active_hold: ActiveHoldInfo | None = None


class EmergencyVehicle(BaseModel):
    id: str
    priority: Priority
    x: float
    y: float
    speed_mps: float = 0.0
    heading_deg: float = 0.0
    destination_id: str
    last_seen_at: SimTimeMs
    position_source: PositionSource = PositionSource.GPS


class Route(BaseModel):
    id: str
    version: int = Field(ge=0)
    vehicle_id: str
    edges: list[str]
    cost_s: float = Field(ge=0)
    computed_at: SimTimeMs
    alternatives: list[list[str]] = Field(default_factory=list)


class CorridorPlanEntry(BaseModel):
    intersection_id: str
    phase_id: str
    eta: SimTimeMs
    request_at: SimTimeMs
    hold_until: SimTimeMs | None = None
    status: str = "PLANNED"


class CorridorPlan(BaseModel):
    route_version: int
    entries: list[CorridorPlanEntry] = Field(default_factory=list)


class PreemptionLease(BaseModel):
    lease_id: str
    intersection_id: str
    phase_id: str
    plan_version: int
    granted_at: SimTimeMs
    expires_at: SimTimeMs
    idempotency_key: str
    status: LeaseStatus = LeaseStatus.REQUESTED


class Command(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    type: CommandType
    intersection_id: str
    plan_version: int
    idempotency_key: str
    payload: dict[str, object] = Field(default_factory=dict)
    created_at: SimTimeMs
    approval: str | None = None


class CommandAck(BaseModel):
    model_config = ConfigDict(frozen=True)

    command_id: str
    intersection_id: str
    idempotency_key: str
    status: CommandAckStatus
    reason_code: str | None = None
    effective_at: SimTimeMs | None = None
    timestamp: SimTimeMs


class Incident(BaseModel):
    id: str
    type: IncidentType
    affected_edges: list[str]
    severity: str
    reported_at: SimTimeMs
    cleared_at: SimTimeMs | None = None


class User(BaseModel):
    id: str
    name: str
    role: UserRole
    assigned_vehicle_id: str | None = None
    hospital_id: str | None = None
