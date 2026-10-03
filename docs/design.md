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

### Routing (Phase 5)

Data flows one way: world state and the tracked position feed the cost model
(`routing/cost.py`, through the `RoutingContext` adapter in `routing/context.py`).
The cost model feeds A* (`routing/astar.py`), which feeds Yen's alternatives
(`routing/yen.py`) and the planner (`routing/planner.py`). The planner writes
decisions and events, and sends routes through `GuidanceLink`. The simulated
ambulance acknowledges each route (`sim/guidance.py`), and the tracker takes the
new route into effect only when it is acknowledged. Central code never imports
the simulator: routes reach the vehicle through the interface alone.

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

**Why A* over an edge-based graph, and not D* Lite or contraction hierarchies.**
The network has about 120 directed segments. Measured over the 10-minute report,
a whole planner step (including Yen's alternatives) took 88 ms at p95 and 115 ms
at most, against a full-replan budget of 300 ms (PRD §14.1). D* Lite pays off when costs change constantly and
the search must be repaired incrementally; here it is simpler to replan from
the vehicle's current position every time (FR-RT-05) than to maintain a repair
state. Contraction hierarchies need a static graph, and our costs change every
second with congestion. The search runs over directed segments (ADR-038) so turn
penalties and U-turn bans are exact.

**Why this cost model.** Each component is separate because the explanation has to
name it (ADR-033 to ADR-040). Travel time follows BPR with the density mapped
through a documented critical density, and the ambulance's congestion sensitivity
is below one because other vehicles make way. Queue clearance and the normal
signal delay are both charged at every stop line, because the first version
charged signal delay only at uncontrollable intersections and then predicted
239 s for a trip that took 423 s. Stale data never reduces a cost. A forecast
blends the current density with history by how far away the segment is (ADR-035),
because the traffic a segment has when the ambulance arrives is what matters.

**Why hysteresis, dwell and a commitment zone.** A route that changes every few
seconds gives the crew nothing to act on, and the ambulance may be part-way
through a junction when the advice changes. The gain must exceed the larger of
15 s and 10% of the remaining cost (the stricter reading of FR-RT-04, ADR-037),
the last switch must be at least 20 s old, and the vehicle must be more than 80 m
and 8 s from the next junction. A blocked route is the one exception, because
continuing on it is worse than any flapping.

**Why diverse alternatives.** The point of a backup is that it survives the failure
of the primary. Two candidates that share 90% of their road fail together, so
Yen's search keeps a candidate only if less than 70% of its length is shared with
the primary or with an existing alternative (ADR-042). The search is budgeted per
central step and resumes deterministically, so it never delays the primary route.

**What the evaluation shows, and what it does not.** In the closure scenario the
planner switched off the closed road one step after it was applied and adopted a
bypass. In the jam scenario the planner's route was not better in practice
(423 s against 337 s for the fixed baseline), although the model had ranked it
first. The likely causes are the signal delay, which the model approximates by a
fixed share of the cycle, and pedestrian extensions, which lengthen greens and
are absent from the projected schedule (the same gap that limits the 60 s ETA
window in Phase 4). The route ranking is therefore not yet validated against
outcomes, and calibrating it is the first item for the next iteration (ADR-036).

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
| Route blocked by a closed road ahead | Always replaced, ignoring dwell and commitment; a precomputed backup starting from the current segment is adopted in the same step (fast path). Measured: closure at 60 s, bypass adopted at 61 s | `test_routing_planner.py::test_blocked_route_switches_even_inside_dwell_and_commitment`, `::test_fast_path_switches_in_the_same_step_to_a_precomputed_backup`, integration `test_planner_switches_off_a_closed_road_without_flapping` |
| Ambulance already committed past the divergence point | The vehicle answers `CANNOT_FOLLOW` and keeps its route; the planner replans from the true position | `test_guidance.py::test_route_that_diverges_after_the_ambulance_passed_is_refused`, `test_routing_planner.py::test_cannot_follow_ack_keeps_the_route_and_requests_a_replan` |
| Route pushed twice or out of order | Older versions never overwrite newer ones; the vehicle acknowledges each outcome | `test_guidance.py::test_older_route_versions_never_overwrite_newer_ones` |
| Route change in flight | Nothing new is adopted until the previous route is acknowledged (blocked routes excepted) | `test_routing_planner.py::test_no_second_switch_while_the_previous_route_is_unacknowledged` |
| Stale data on a route segment | Confidence scales an uncertainty penalty up, never down, so stale roads are never preferred | `test_routing_cost.py::test_stale_data_never_makes_a_road_cheaper` |
| No live data for a segment | Historical prior, inflated by the pessimism factor, and marked stale | ADR-031, `test_world_state.py` |
| Uncontrollable downstream intersection | Penalty of `uncontrollable_penalty_s` on top of the normal red wait; a status change triggers a replan of every vehicle routed through it | `test_routing_cost.py::test_uncontrollable_downstream_node_adds_the_configured_penalty`, `test_routing_planner.py` trigger coverage |
| Assigned hospital unavailable | Goal falls back to any available hospital and triggers a replan (FR-RT-07) | Trigger path in `RoutePlanner._detect_triggers` |
| Hospital unreachable | Dispatch returns no route instead of an empty one | `test_routing_planner.py::test_dispatch_returns_none_when_the_hospital_cannot_be_reached` |
| Route revisits a passed intersection | Banned in the search and rejected in the planner, so stop-line passage cannot fire twice | `test_routing_planner.py` (route geometry), ADR-044 |
| Two near-equal routes (flapping risk) | Hysteresis of max(15 s, 10%), 20 s dwell, commitment zone; every rejection is recorded with its reason | `test_routing_planner.py::test_small_gain_is_rejected_with_the_threshold_reason`, `::test_minimum_dwell_blocks_a_switch`, `::test_commitment_zone_blocks_a_switch_near_the_junction` |
| Congestion on the planned route | Replans when the remaining cost drifts over 15%; switches only when the gain beats the stability rules. Measured with a 3000 vph jam on C4-D4 from 60 s, the best bypass was 7 s cheaper, below the 41 s threshold, so the planner kept its route. A jam on C5-C4, which the planned route does not use, needed no switch | Report runs (see `docs/routing_report_example.txt`); `test_routing_integration.py` |

## 5. Trade-offs

**Greedy replanning versus global optimisation (Phase 5).** The planner replans
each vehicle from its current position and does not optimise the whole corridor.
That is what makes it fast and explainable, and it is safe because each route is
checked against the stability rules. The cost is that one ambulance's best route
can take a red light that a corridor-wide plan would have spread across the
network. Phase 6 (corridor preemption) is where that trade-off has to be revisited.

**Stability versus optimality (Phase 5).** The hysteresis, dwell and commitment
rules mean the vehicle sometimes keeps a route that a fresh search would reject.
The report's 7 s bypass under the C4-D4 jam was ignored for exactly this reason.
This favours trust in the advice over a small expected saving, and the choice is
recorded per decision, so it can be tuned with evidence.

_The earlier trade-offs are still recorded in `docs/decisions.md`,
in particular ADR-006 through ADR-008 (point-queue model, permissive
turns, head-of-line blocking) and ADR-015/ADR-017 (bounded idempotency
store; reject-don't-cap hold policy) — each trades either simulation
fidelity or caller convenience for the performance, transparency, and
safety this project's actual evaluation criteria need._
