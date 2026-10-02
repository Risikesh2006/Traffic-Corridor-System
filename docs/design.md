# Design

## 1. Problem & Domain Understanding

_To be expanded in later phases (simulation, routing, corridor control)._

## 2. Architecture

_System architecture overview to be expanded as the simulation, safety,
routing, and corridor layers are built (PRD §8)._

### Domain Model

Entities implemented in `backend/core/models.py` (PRD §9), as typed,
validated Pydantic v2 models:

- **`Approach`** — one leg of an intersection (direction, lanes, queue length)
- **`Phase`** — a set of non-conflicting through-movements with timing
  (min green, amber, all-red, pedestrian clearance)
- **`ConflictMatrix`** — symmetric pairwise conflicts between movements;
  `is_compatible()` checks a candidate set of simultaneous movements
- **`SignalPlanEntry`** / **`Intersection.default_plan`** — the normal
  (non-preempted) signal cycle: green share per phase, plus a per-intersection
  `cycle_offset_ms` for future coordination
- **`Intersection`** — approaches, phases, conflict matrix, default plan,
  controller status, current phase
- **`RoadSegment`** — one directed edge between two intersections (length,
  lanes, free-flow speed, capacity, live status/density/confidence)
- **`Hospital`** — destination node with availability status
- **`City`** — the static network: intersections, roads, hospitals, and the
  ambulance entry node, with graph helpers (`intersection`, `segment`,
  `outgoing`, `incoming`, `neighbors`, `distance_m`)
- **`SensorReading`**, **`GpsFix`** — frozen, immutable facts from the
  ingestion boundary
- **`EmergencyVehicle`** — live ambulance state (position, speed, priority,
  destination, position source)
- **`Route`**, **`CorridorPlanEntry`**, **`CorridorPlan`** — routing and
  corridor-preemption planning output
- **`PreemptionLease`** — a time-limited preemption grant on one intersection
- **`Command`**, **`CommandAck`** — frozen, idempotent instructions to
  intersection controllers and their acknowledgements
- **`Incident`** — a reported road closure/accident
- **`User`** — role-scoped account (operator / crew / hospital)
- **`Event`** (`backend/core/events.py`) — the frozen envelope written to the
  event store and broadcast over WebSocket

### State machines (PRD §9, text only for now — behaviour lands with the
controller, corridor, and health modules in later phases)

**Intersection controller**
```
GREEN(phase) --[min green met & change requested]--> AMBER
AMBER --[amber_s elapsed]--> ALL_RED
ALL_RED --[all_red_s elapsed]--> GREEN(next phase)
GREEN(preempted phase) --[lease active]--> hold
any --[lease expired/released]--> transition to normal plan via AMBER/ALL_RED
any --[conflict detected]--> FLASHING_RED (fail-safe)
```

**Preemption lease**
```
REQUESTED --[supervisor approves, controller acks]--> ACTIVE
ACTIVE --[renewed]--> ACTIVE (expires_at extended)
ACTIVE --[ambulance passed / route changed / operator release]--> RELEASED
ACTIVE --[expires_at reached without renewal]--> EXPIRED
REQUESTED --[supervisor rejects]--> REJECTED
```

**Operating mode**
```
NORMAL --[stale data / unreachable controller / GPS lost]--> DEGRADED
DEGRADED --[all conditions recovered]--> NORMAL
NORMAL/DEGRADED --[safety supervisor down / planner unrecoverable]--> SAFE
SAFE --[components healthy again]--> DEGRADED --> NORMAL
```

## 3. Technical Reasoning

_To be expanded. See `docs/decisions.md` for the ADRs backing the choices
made so far (ADR-001 through ADR-004)._

## 4. Failure & Edge Cases

_To be expanded as the safety supervisor, resilience, and chaos modules are
built (PRD §12, §13)._

## 5. Trade-offs

_To be expanded. See `docs/decisions.md` for trade-offs already recorded._
