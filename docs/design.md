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

### Safety architecture (defence in depth)

Two independent layers, each checking what it can know reliably (ADR-011):

| Layer | Where | Knows | Checks |
|---|---|---|---|
| Safety Supervisor | Central (`backend/safety/`) | Policy, city config, last REPORTED intersection state (may be stale) | Is this command allowed at all? Well-formed, valid target/phase, controllable, version order, hold policy, rate limit |
| Controller guard | Field side (`backend/controller/`) | The EXACT current signal state and timers | Can this execute safely RIGHT NOW? Approval, idempotency, version, FLASHING, phase validity, timing (defer, never reject), local max-hold |

A command must pass both to take effect:

```
 central code
      |
      v
 SafeSignalLink.send(command)        <- the ONLY path central code may use (ADR-013)
      |
      v
 SafetySupervisor.validate()  --[rejected]--> nothing sent, Decision returned
      | [approved + HMAC approval attached]
      v
 SignalLink (SimNetwork)             <- latency / drop / duplicate
      |
      v
 IntersectionController.handle_message()
      |
      v
 controller guard: approval -> idempotency -> version -> FLASHING -> dispatch
      |
      v
 IntersectionStateMachine.request_phase_change()   <- defers, never bypasses timing
```

If the supervisor is unhealthy, `SafeSignalLink` rejects everything with
`SUPERVISOR_UNAVAILABLE` before the network is ever touched — fail closed
(ADR-013). If the network drops or delays a message, the controller guard
is still correct on its own, because it never depended on the supervisor's
timing judgement in the first place (ADR-011).

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

**Why two safety layers instead of one.** A single central checker would
have to either trust a possibly-stale view for exact timing (unsafe) or
refuse to approve anything whose view is more than an instant old (unusably
fragile under any real network latency). Splitting policy (central) from
timing (local) means staleness only ever costs *time*, never *safety*
(ADR-011) — this is the mechanism behind the PRD's central design
principle, not just a restatement of it.

**Why HMAC approvals.** Without a cryptographic approval, anything that
can reach a controller's inbound channel — a bug, a compromised dependency,
a misconfigured test — could command a signal change directly. Signing the
command's own fields (not a separate token) means a tampered field is
detected by the signature itself failing, not by a separate "did this
field change" check (ADR-012).

**Why fail closed.** FR-SAF-04 isn't just a validation rule; it's a
property of the WHOLE pipeline: `SafeSignalLink` refuses to even attempt
delivery when its supervisor is unavailable, rather than relying on the
supervisor to reject things itself (which it can't do if it's down).
"Nothing sent" is a stronger guarantee than "everything sent gets
rejected," because it doesn't depend on the broken component still working
correctly enough to reject (ADR-013).

**Why property-based and stateful testing.** Unit tests prove each check
works in the one scenario it was written for. The property test (random
command sequences — valid/garbage phases, hold durations, version
ordering, forged approvals, forced FLASHING_RED) and the stateful test
(a small multi-intersection system driven through randomly ordered rules —
time, commands, network faults, supervisor crashes, pedestrian calls) are
what actually search the space of orderings and inputs a hand-written test
suite wouldn't think to try — and the 10,000-example thorough run of the
property test found a real edge case (see ADR-019) that no unit test had
covered. The mutation tests (ADR-019) then prove the other direction: that
disabling a real check gets caught, so the property/stateful tests aren't
passing vacuously.

## 4. Failure & Edge Cases

| Case | Handling | Proven by |
|---|---|---|
| Safety Supervisor crash or silently stops heartbeating | `SafeSignalLink` fails closed — every command rejected with `SUPERVISOR_UNAVAILABLE`, nothing sent; existing holds expire on their own `hold_until`, enforced locally | `test_gate.py::test_supervisor_crash_rejects_everything_and_sends_nothing`, `::test_supervisor_missed_heartbeat_also_fails_closed` |
| Duplicate command (network retry/duplication) | Controller's idempotency cache returns the ORIGINAL result tagged `DUPLICATE`, never re-executes (INV-09) | `test_controller_guard.py::test_duplicate_returns_original_result_without_reexecuting`, checker INV-09 in the property/stateful/mutation tests |
| Out-of-order command (network reordering) | `plan_version` older than applied → `IGNORED_STALE_VERSION` at the controller (INV-08), and separately tracked at the supervisor from its own perspective | `test_controller_guard.py::test_stale_version_ignored`, `test_supervisor.py::test_stale_version_rejected` |
| Forged or tampered command | HMAC verification fails → `REJECTED/NOT_APPROVED`, independent of how the command arrived | `test_controller_guard.py::test_forged_approval_rejected`, `test_security.py` tamper tests, mutation test disabling verification |
| Hold would starve a cross-street approach | Controller interrupts its own hold to serve the starved approach, then resumes (INV-06), with zero central involvement | `test_controller_guard.py::test_local_max_hold_serves_starved_approach` |
| Pedestrian call mid-green | Green is floored past `ped_walk_ms + ped_clearance_ms`, deferring even a pending preemption request (INV-05) | `test_pedestrians.py`, checker INV-05 |
| Conflict monitor forces FLASHING_RED mid-transition | Allowed to cut amber/all-red/min-green/ped-clearance short — this is the fail-safe override, not a timing violation (see ADR-019) | `test_safety_properties.py` (found this exact case at 10,000 examples) |

## 5. Trade-offs

_To be expanded. See `docs/decisions.md` for trade-offs already recorded,
in particular ADR-006 through ADR-008 (point-queue model, permissive
turns, head-of-line blocking) and ADR-015/ADR-017 (bounded idempotency
store; reject-don't-cap hold policy) — each trades either simulation
fidelity or caller convenience for the performance, transparency, and
safety this project's actual evaluation criteria need._
