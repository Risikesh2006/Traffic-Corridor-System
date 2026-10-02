# Architecture Decision Records

Short ADRs for design decisions and deviations from `docs/PRD.md`. Format:
Context / Decision / Consequences.

---

## ADR-001: Simulation time as integer milliseconds

**Context.** The PRD's domain model and algorithms (§8.7, §11) are expressed
in seconds, and floating-point seconds is the obvious literal reading. But
every downstream computation — phase timers, lease expiry, idempotency
ordering, event ordering, replay — depends on exact equality and ordering of
timestamps. Float accumulation (`0.1 + 0.1 + 0.1 != 0.3`) risks subtle,
non-deterministic drift across long runs, which would silently break
reproducibility (AC-02) and safety invariant checks (§12).

**Decision.** All simulation time is `SimTimeMs = int`, milliseconds since
sim epoch. Config durations are authored in seconds (human-friendly) in
`settings.yaml` and converted to integer milliseconds once, at load time, via
`Settings.*_ms` properties. `SimClock.advance()` only accepts integer
milliseconds.

**Consequences.** Every domain model field that represents a point in time
or duration is a plain `int`, not `float`. Arithmetic on time is exact and
hashable, which makes event IDs, idempotency keys, and replay diffing
trivially reliable. The cost is that any computation genuinely needing
sub-millisecond precision isn't representable — not a concern at this
domain's timescales (amber ≥ 3s, ticks ≥ 100ms).

---

## ADR-002: Named, SHA-256-derived seeded RNG streams

**Context.** PRD §8.7 requires that identical seed + inputs reproduce an
identical run. Multiple independent subsystems (traffic demand, sensor
noise, GPS jitter, chaos injection) all need randomness. A single shared
`random.Random(seed)` instance would make every stream's sequence depend on
the exact order in which modules draw from it — adding or reordering a
consumer would silently change *every other* consumer's sequence, making
runs impossible to reason about and regressions impossible to isolate.

**Decision.** `RngFactory(seed)` hands out one `random.Random` per named
stream, seeded by `int(SHA256(f"{seed}:{name}")[:8 bytes])`. SHA-256 is used
instead of Python's built-in `hash()`, which is randomized per-process
(`PYTHONHASHSEED`) specifically to prevent this kind of reliance — using it
here would break reproducibility across separate runs/processes rather than
guarantee it.

**Consequences.** Any module can request `rng.stream("my_module")` at any
point without affecting other streams' sequences. Adding a new consumer is
always safe. The only coupling is the stream *name*, which becomes a stable,
documented contract (e.g., `"traffic_demand"`, `"sensor_noise"`,
`"chaos"`) — renaming a stream silently gives it a new sequence, which is
intentional and acceptable (it's equivalent to changing the seed for that
subsystem only).

---

## ADR-003: Grid-corridor arterials instead of diagonal arterials

**Context.** PRD §10.1 FR-SIM-02 and the component diagram in §8 mention
"2-3 diagonal arterials" in the default city. A diagonal road crossing a
6x6 grid, however, would intersect orthogonal grid roads at non-standard
angles and at points that aren't existing grid nodes — forcing either new
node types, non-4-way intersections, or off-grid intersection geometry, none
of which the domain model (`Approach.direction: N/S/E/W`, two-phase
NS/EW signals) is built to represent. The PRD itself doesn't specify how a
diagonal should interact with the grid's conflict matrices or phases.

**Decision.** Model arterials as upgraded *grid* corridors instead: row 4
(A4-F4, east-west) and column D (D1-D6, north-south), each 3 lanes / 60
km/h / 2400 veh/h·dir vs. the normal grid's 2 lanes / 40 km/h / 1600
veh/h·dir. Every intersection remains a standard 4-way junction with the
same two-phase (NS_THROUGH / EW_THROUGH) signal structure.

**Consequences.** The routing cost model (§11.1-11.2) still applies
unmodified — arterials are just edges with better free-flow speed and
capacity, exactly the kind of edge-weight differentiation A* already
handles. D4, where both arterials cross, is deliberately left at an even
30s/30s default split rather than favoring either direction (see
`scripts/generate_city.py:build_default_plan`), since favoring one arterial
there would just starve the other. This is a readable, lower-risk
substitute for the PRD's diagonal arterials; if true diagonal roads are
wanted later, they'd need new intersection geometry and a conflict-matrix
model that isn't N/S/E/W-only.

---

## ADR-004: Movements are through-only, identified by approach direction

**Context.** PRD §9's `Approach` entity has a `direction`, and §7's "Phases"
describes "sets of non-conflicting movements." Real intersections also have
turning movements (left/right), each with its own conflict semantics (e.g.,
a northbound left turn conflicts with southbound through traffic). Modelling
turns fully would roughly double the movement count per intersection and
significantly complicate the conflict matrix, phase design, and the city
generator, for no benefit to the corridor-control and safety logic this
project is actually evaluated on.

**Decision.** A `Movement` is just the `Direction` (N/S/E/W) of the approach
it arrives from, representing that approach's through movement. No turning
movements are modelled. Two phases per intersection: `NS_THROUGH` (N, S)
and `EW_THROUGH` (E, W), conflicting pairwise (N-E, N-W, S-E, S-W).

**Consequences.** The conflict matrix, phase model, and city generator stay
small and auditable, and match the PRD's own two-phase intersection
description. The simplification means the simulator won't model
turn-specific conflicts (e.g., a protected left-turn phase) — acceptable for
a through-corridor system where the ambulance's priority movements are
through movements. Documented here so it's not mistaken for an oversight.

---

## ADR-005: `controller` is field-side code; `sim` is the only module that may import it

**Context.** PRD §8.4 draws a hardware abstraction boundary so control logic
never depends on the simulator. Phase 2 adds the intersection controller
(`backend/controller/`) and the physical-world simulator (`backend/sim/`).
Both are new, and it would be easy to let a future module import whichever
one is convenient, quietly erasing the boundary the whole safety case rests
on: central code is supposed to reach a controller only through `SignalLink`.

**Decision.** `controller/` is treated as field-side software — what would
run inside the roadside signal cabinet. It may depend on `core/` only, never
on `sim/`. `sim/` is the physical world and legitimately hosts and drives
field devices, so it may import both `core/` and `controller/`. Central
control modules (`state`, `tracking`, `routing`, `corridor`, `safety`,
`health`, `events`) may import neither `sim/` nor `controller/` — they reach
a controller only via the `SignalLink` interface, which `SimNetwork`
implements today and a real NTCIP driver would implement later.
`backend/tests/test_boundary.py` enforces both rules by scanning imports.

**Consequences.** A `TrafficModel` or `AmbulanceSim` can call
`controller.signal_view()` directly (appropriate — in the real world a
vehicle's driver also just looks at the physical signal head). But nothing
in `corridor/` or `safety/` can ever reach around the network and poke a
controller directly, even by accident, because the import itself is a test
failure. This is what makes "a crash, network loss or bad data may make the
ambulance slower, but can never make an intersection unsafe" (PRD's central
design principle) structurally true rather than merely intended.

---

## ADR-006: Point-queue traffic model with explicit storage limits

**Context.** PRD §10.1/§11 call for queue-level (not vehicle-level
microscopic) traffic simulation, but still need realistic spillback: a full
downstream road should visibly block discharge from the road behind it.

**Decision.** Each directed `RoadSegment` has an in-transit pipeline (free-
flow travel time only) and a queue at its downstream stop line, both
represented as deques of timestamps rather than per-vehicle objects (exact
per-vehicle delay is recoverable as `discharge_time - queue_join_time`
without the overhead of an object per vehicle). Segment capacity =
`floor(length_m * lanes / jam_spacing_m)`; a segment at capacity refuses new
entries (`TrafficModel.spawn()` returns `False`).

**Consequences.** Background traffic for a 36-intersection, 120-segment
network stays cheap enough to simulate many minutes well inside the
performance budget (10 sim-minutes at peak in ~8s wall time, budget 15s).
The cost is that background vehicles are anonymous — there's no "vehicle
7142's full journey" to inspect, only aggregate queue/delay statistics. The
ambulance is deliberately the exception (see ADR-008): it's tracked as a
real object because there are only ever a handful of them and their
individual journey is the entire point of the system.

---

## ADR-007: Permissive turns, a fixed rotation convention, and amber discharge rule

**Context.** PRD §10.1 FR-SIM-04 needs a discharge model that disperses
traffic across the grid, not just straight through each intersection,
without building a full turning-movement/protected-phase model (ADR-004
already restricts movements to through-only at the signal level).

**Decision.** Each discharged background vehicle draws through/left/right
from `turn_through_prob`/`turn_left_prob`/`turn_right_prob` (default
70/15/15) and exits via a fixed 90-degree-rotation convention
(`sim/topology.py::TURN_MAP`) relative to its arrival direction — this is an
arbitrary but self-consistent choice, not a model of any real driving side,
since the queue-level model doesn't represent lanes or conflicting turning
paths anyway. Turns are *permissive*: a turning vehicle discharges on its
approach's own green with no protected left-turn phase or opposing-traffic
gap check. Separately, discharge is allowed to continue through the first
`amber_discharge_fraction` (default 50%) of amber, standing in for
"vehicles already committed to crossing finish their movement."

**Consequences.** Traffic actually disperses across the grid instead of
flowing in straight lines, which is what the arterial-corridor layout
(ADR-003) needs to be meaningfully stressed. The permissive-turn
simplification means the simulator won't produce the extra delay a real
protected-left phase or gap-acceptance model would — acceptable since no
turning-specific signal timing exists in this project's scope (ADR-004).

---

## ADR-008: Head-of-line blocking, and the ambulance as a tracked object

**Context.** With segment capacity limits (ADR-006), a vehicle ready to
discharge may find its target segment full. Something has to happen to the
vehicle behind it in the same lane.

**Decision.** If the head-of-queue vehicle can't advance (target segment at
capacity), the whole queue stops discharging that tick — no vehicle behind
it can "overtake" into a different direction, even if that direction has
room. Separately (not really about blocking, but related to how the
ambulance interacts with queues): the ambulance is NOT folded into the
anonymous queue model. It's a real `SimAmbulance` object with exact
position, speed and state, and at a stop line it reads the segment's queue
length and `TrafficModel`'s `cumulative_discharged` counter to know how many
vehicles are still ahead of it, rather than being enqueued itself.

**Consequences.** Head-of-line blocking is the standard, slightly
pessimistic simplification every queue-level traffic model makes (real
multi-lane roads let some vehicles route around a blocked lane) — acceptable
here since lanes aren't modelled individually. Keeping the ambulance as its
own object is what makes "stops", "stopped time", and "time per route
section" (metrics the whole project is evaluated on) exact rather than
statistically inferred.

---

## ADR-009: Fixed per-tick order and deterministic iteration everywhere

**Context.** PRD §8.7 requires bit-for-bit reproducibility from a seed. A
single `Simulation.step()` touches the network, every controller, traffic
discharge, arrivals, demand, ambulances, sensors and GPS — if the order in
which these run, or the order in which a dict/set of segments or
intersections is iterated, isn't pinned down, two runs with the same seed
could still diverge (e.g., Python's dict iteration order is
insertion-order-stable but a different *construction* order between two
runs — or a future refactor — could silently change behaviour).

**Decision.** `step()` always runs in exactly this order: (1) advance
clock, (2) deliver due network messages, (3) tick every controller, (4)
discharge queues on green, (5) move in-transit vehicles into queues, (6)
spawn demand, (7) move/stop/pass ambulances, (8) emit sensor/GPS readings
due this tick, (9) record metrics and notify observers. Every place code
iterates intersections or segments uses `sorted()` on their IDs, never bare
dict/set iteration.

**Consequences.** `test_engine.py` proves this holds not just within one
process but across a fresh subprocess (rules out any
`PYTHONHASHSEED`-flavoured accident). The cost is some repetitive
`for x in sorted(...)` boilerplate instead of plain iteration — a small,
deliberate price for a safety-critical system's core reproducibility
guarantee.

---

## ADR-010: Boundary demand sources, controller recovery, and request aftermath (Phase 2 judgment calls)

**Context.** Three points were genuinely ambiguous in the Phase 2 brief and
had to be resolved to keep building, rather than blocking on each:

1. PRD's "vehicles enter from the city boundary (every approach on a border
   intersection that faces outward)" doesn't map onto any real `Approach`
   object — approaches only exist where a real neighbouring node exists
   (Phase 1's model), so there's no "missing" approach to hang demand off.
2. What should `FLASHING_RED` do with no Safety Supervisor yet built
   (Phase 3)?
3. Once a `request_phase_change()`-requested phase has been served, what
   does the controller do next?

**Decision.**
1. Demand enters via every **outgoing** segment whose `from_node` is a
   border intersection (`sim/topology.py::is_border_intersection`), at the
   boundary rate; every segment (interior or not) additionally gets a small
   flat "parking" source. Traffic genuinely originates at/flows in from the
   grid's edges without inventing a virtual approach the domain model has
   no room for.
2. `enter_flashing()` is terminal for Phase 2 — no auto-recovery. This is
   the conservative default ("last line of defence" should not quietly
   un-defend itself), and it matches the PRD's intent that recovery from an
   unsafe state is an explicit, later-phase action (health monitor /
   operator), never automatic.
3. Normal round-robin cycling resumes from the served phase forward. No
   "return to where the cycle was interrupted" bookkeeping — simplest
   correct behaviour, and the actual release/recovery semantics that matter
   (compensation green, etc.) belong to corridor control (Phase 6), which
   will drive this via the same `request_phase_change()` primitive.

**Consequences.** All three are revisitable without changing any public
API: (1) demand source selection is isolated in `sim/demand.py::build_sources`;
(2) a health/recovery phase can add an explicit `recover()` method to the
state machine without touching `enter_flashing()`'s contract; (3) corridor
control can layer "resume previous plan" logic on top of
`request_phase_change()` without the state machine needing to know about
leases at all.
