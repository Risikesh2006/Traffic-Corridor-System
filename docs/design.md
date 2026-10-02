# Design

## 1. Problem & Domain Understanding

### Phases, clearance intervals and queue discharge (PRD §7)

An intersection runs a cycle of **phases** — sets of movements that may
safely be green together (e.g. `NS_THROUGH`: the north and south approaches
going straight through). Movements in different phases may conflict (e.g.
a north–south through movement conflicts with east–west), so only one phase
may be green/amber at a time; the `ConflictMatrix` encodes which movement
pairs can never be green simultaneously (INV-01).

A phase can't simply switch straight to another phase's green — real
signals (and this simulation) always transition **GREEN → AMBER →
ALL-RED → GREEN(next phase)**. Amber warns drivers already committed to
the intersection; all-red gives the intersection time to fully clear before
the conflicting movement gets a green. Every phase also has a **minimum
green** — once started, it must run for at least that long, so a request to
change phases early can only ever be *deferred* to the earliest safe point,
never granted immediately (`IntersectionStateMachine.request_phase_change`).

When a movement turns red, vehicles queued on it don't vanish — they
**discharge** at a roughly constant saturation rate once their movement
goes green, after a brief **startup loss** (the first second or two of
green where vehicles are still accelerating from a stop). This is why
"just turn the signal green" isn't enough to clear a path for an emergency
vehicle: if a 15-vehicle queue is ahead of it, that queue still takes the
same ~30+ seconds to clear even with an instant green — the corridor
controller (a later phase) has to *time* the green so the queue is already
gone by the time the ambulance arrives, not react to its arrival.

## 2. Architecture

_System architecture overview to be expanded as the simulation, safety,
routing, and corridor layers are built (PRD §8)._

### Simulation (digital twin)

What's **simulated** (the physical world, `backend/sim/`): background
traffic (queue-level, not per-vehicle — PRD §10.1), the ambulance's physical
movement, road-sensor readings, GPS fixes, and the network between central
code and field controllers (latency, jitter, drops, duplicates). What's
**real** (`backend/controller/` and every later central module): the
intersection controller's signal logic, and everything that will sit above
it (safety, routing, corridor control). `backend/core/interfaces.py` is the
hardware abstraction boundary between them — control code depends only on
`VehicleFeed`/`SensorFeed`/`SignalLink`, never on `sim/` directly (see
ADR-005, enforced by `test_boundary.py`).

`Simulation.step()` (`sim/engine.py`) advances exactly one tick in a fixed,
deterministic order (ADR-009):

1. advance the clock
2. deliver any network messages due this tick
3. tick every intersection controller's state machine
4. discharge queues on green (traffic → next segment)
5. move in-transit vehicles into queues on arrival
6. spawn new background-traffic demand
7. move/stop/pass ambulances
8. emit sensor readings and GPS fixes due this tick
9. record metrics and notify observers (e.g. a future invariant checker)

Every run is fully reproducible from its `seed`: `Simulation.state_hash()`
is verified identical both within a process and across a fresh subprocess
for the same seed (`test_engine.py`).

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
made so far (ADR-001 through ADR-010)._

## 4. Failure & Edge Cases

_To be expanded as the safety supervisor, resilience, and chaos modules are
built (PRD §12, §13). One Phase 2 case worth noting now: the intersection
controller's local conflict self-check (`IntersectionStateMachine.
_check_invariant`) runs every tick and enters `FLASHING_RED` — a fail-safe,
terminal state for now — if it ever detects two conflicting movements would
be green/amber simultaneously. This is deliberately redundant with the
Safety Supervisor arriving in Phase 3: the controller never trusts that
nothing upstream could be wrong._

## 5. Trade-offs

_To be expanded. See `docs/decisions.md` for trade-offs already recorded,
in particular ADR-006 through ADR-008 (point-queue model, permissive
turns, head-of-line blocking) — each trades simulation fidelity for the
performance and simplicity this project's actual evaluation criteria
(safety, determinism, corridor timing) need._
