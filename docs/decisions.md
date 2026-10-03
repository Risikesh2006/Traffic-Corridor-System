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

---

## ADR-011: Two-layer safety split — central policy vs. local timing

**Context.** PRD §10.8 asks for defence in depth: a central Safety
Supervisor and a local controller guard. The question is what each layer
should actually be responsible for, given the supervisor's view of an
intersection (`IntersectionView`) can be arbitrarily stale (network delay,
drops) while the controller's own view of itself is always exactly current.

**Decision.** The supervisor owns *policy* — is this command well-formed,
targeting a real phase, for a controllable intersection, with a monotonic
version, a hold within lease/max-hold policy, under the rate limit? It
never tries to enforce *exact* timing (amber/all-red/min-green/ped
clearance), because by the time its decision reaches the controller, its
view could already be wrong. The controller guard re-validates everything
the supervisor could have gotten wrong from staleness — approval,
idempotency, version, FLASHING, phase validity — and is the ONLY layer that
enforces exact timing, because it's the only layer that can't be stale
about its own state. A command must pass both layers to take effect.

**Consequences.** Neither layer trusts the other. A compromised or buggy
central system can get every command rejected at the controller, but can
never make the controller itself apply an unsafe command, because the
controller never relies on the supervisor's timing judgement — only its
"is this allowed at all" judgement, re-checked against ground truth.

---

## ADR-012: HMAC approval tokens over a shared vocabulary in `core/`

**Context.** INV-10 requires that no command executes without supervisor
approval — otherwise anything with access to the controller's inbound
message channel (the network) could directly command a signal change. The
`ReasonCode` enum (rejection/deferral/duplicate explanations) needs to be
shared between the supervisor and the controller guard so a human reading
either layer's logs sees the same vocabulary.

**Decision.** `core/security.py` provides `sign()`/`verify()`: HMAC-SHA256
over the command's own canonical fields (type, intersection, phase_id,
hold_until, plan_version, idempotency_key, issued_at), compared with
`hmac.compare_digest` (constant-time, avoiding a timing side-channel).
`core/reasons.py` holds `ReasonCode`. Both live in `core/`, NOT in
`safety/` as the brief originally suggested — putting them in `safety/`
would force the field-side `controller` package to import a central
package to verify a signature or quote a reason code, inverting the
hardware boundary (ADR-005) the whole project is built around. The
secret itself comes from `Settings.hmac_secret`, with a documented
dev-only default and a `CORRIDOR_HMAC_SECRET` env var override.

**Consequences.** `controller/` still depends only on `core/`. A forged or
tampered command (wrong secret, or any field changed after signing) fails
`verify()` and is rejected with `NOT_APPROVED`, regardless of how it
arrived — this is the project's only defence against a compromised network
path being used to directly command a signal.

---

## ADR-013: Fail-closed gate — nothing sent when the supervisor is down

**Context.** FR-SAF-04 and FH-05 require that if the Safety Supervisor is
unavailable, no new commands execute, and the system stays safe (just
slower). The question is how "unavailable" is detected and what happens to
in-flight holds.

**Decision.** `SafeSignalLink` (the only path central code may use to send
commands — enforced by `test_boundary.py`, since `safety` is forbidden
from importing `sim`/`controller`, so no central code can get a raw
`SignalLink` to bypass it with) checks `supervisor.is_healthy()` before
doing anything else. A supervisor is healthy only if it hasn't been
explicitly `crash()`-ed AND has `heartbeat()`-ed within
`heartbeat_timeout_ms` — a silently-dead process (no explicit crash, just
stopped heartbeating) is caught the same way a deliberately crashed one is.
When unhealthy, every command is rejected with `SUPERVISOR_UNAVAILABLE`
and the underlying `SignalLink.send()` is never called. Existing holds are
NOT actively released — they simply stop being renewed, so they expire on
their own `hold_until` (enforced locally by the controller regardless of
the supervisor), and local max-hold enforcement (ADR-014) keeps running
independent of the supervisor the whole time.

**Consequences.** A supervisor outage degrades the system to "no new
preemptions" rather than "ambulance loses its current green" — the
in-progress hold rides out on controller-local logic alone. This is
exactly the degraded-but-safe behaviour the design principle promises:
central failure makes things slower, never unsafe.

---

## ADR-014: Local max-hold enforcement interrupts, then resumes, the hold

**Context.** PRD §10.8 A2 step 7 requires the controller to enforce
max-hold itself, even if the central system asks it not to — a stale or
compromised central system must never be able to starve a cross-street
approach indefinitely.

**Decision.** `IntersectionController._enforce_max_hold()` runs every
tick, independent of any command. If holding has left another approach's
`red_since` older than `max_hold_ms`, the controller interrupts its own
hold — `request_phase_change()` to the starved phase (overwriting the
pending target) — serves it for (at least) its min green, then
automatically re-requests the original held phase. The `ActiveHold` record
itself is untouched during the interruption (it's "paused," not
cancelled); it only clears on explicit `RELEASE` or `hold_until` expiry.

**Consequences.** This is enforced with zero central involvement — it
can't be, and doesn't need to be: `request_phase_change()` is the same
safe, timing-respecting primitive every other phase change in the system
uses, so the interruption and resumption both go through the normal
GREEN→AMBER→ALL-RED→GREEN sequence like anything else. The stateful and
full-simulation safety tests exercise this directly (holds with artificially
short `max_hold_ms`), and the InvariantChecker's INV-06 check is what
would catch a regression here.

---

## ADR-015: Idempotency store bound — last N keys, not a time window

**Context.** INV-09 requires every idempotency key to execute at most
once, but a controller can't remember every key forever (unbounded
memory over a long-running process).

**Decision.** `idempotency_store_size` (default 1000) bounds the
controller's idempotency cache as a simple FIFO: the oldest key is evicted
once the store is full. A time-window bound (e.g. "last N minutes") was
considered and rejected: it would require the controller to read the
clock proactively (nothing else in the state machine does — time only
ever arrives via `now` parameters), and a command count bound is also a
tighter, more predictable memory bound than a time bound would be under
bursty traffic. 1000 is generously larger than any plausible number of
in-flight commands for one intersection within the time a duplicate could
realistically arrive (network retries happen within seconds, not after
1000 other commands).

**Consequences.** A key evicted from the store and then resent would be
treated as new — a theoretical reproduction window, but one that would
require 1000 distinct commands to the same intersection between the
original and the retry, far outside any realistic duplicate-delivery
scenario (network retries/duplicates happen within the latency/jitter
window, milliseconds to low seconds).

---

## ADR-016: Stale-view policy — flag, never block

**Context.** PRD §10.8 B1 check 7 asks what the supervisor should do with
an old view: reject, or approve-with-a-flag?

**Decision.** An old view never blocks approval — `Decision.view_stale`
is set, but the command still passes if every other check passes. This
follows directly from ADR-011: the supervisor's view staleness is
precisely the thing the controller guard exists to be immune to. Rejecting
on staleness alone would make the whole system fail closed on nothing more
than ordinary network latency, which contradicts "a crash, network loss or
bad data may make the ambulance slower, but can never make an intersection
unsafe" — slow data should cost time, not availability.

**Consequences.** `view_stale=true` is available for the "why" log and
the future health monitor to surface ("approved on a 4s-old view") without
being a rejection reason. Exact safety is still guaranteed because the
controller guard doesn't care what the supervisor's view said — it checks
its own current state.

---

## ADR-017: Hold policy — reject, don't cap, an over-long or over-projected hold

**Context.** PRD §10.8 B1 check 5 explicitly asks to choose reject or cap
for two related checks: a single command's hold exceeding `lease_duration_s`,
and the *projected* red time of a conflicting approach exceeding
`max_hold_s`.

**Decision.** Both REJECT (`HOLD_TOO_LONG`, `MAX_HOLD_EXCEEDED`) rather
than silently truncating the requested hold. A cap means the caller
believes they got what they asked for and finds out only later (or never)
that they didn't — for a safety-adjacent system, "tell the caller exactly
what happened" beats "do something slightly different than requested
without saying so." A command wanting a longer hold than policy allows
should renew instead (that's what `RENEW` is for) or ask for less.

**Consequences.** Callers (eventually the corridor planner, Phase 6) must
handle `HOLD_TOO_LONG`/`MAX_HOLD_EXCEEDED` as real rejections and retry
with a shorter hold or a renewal strategy, rather than assuming their
request was honoured in spirit. The projection itself —
`(hold_until - reported red_since) + view_age` — adds the view's own
staleness as a conservative margin, so a borderline-stale view errs toward
rejecting rather than under-estimating the real projected hold.

---

## ADR-018: Minimal pedestrian model — a floor on green duration, not a full phase

**Context.** PRD §10.8 A4 asks for "a minimal pedestrian model" so
clearance checks are honest, without building a full WALK/flashing-don't-
walk signal head simulation.

**Decision.** A pedestrian call for a phase (generated by the physical
world, `sim/pedestrians.py`, never inside the pure/RNG-free state machine)
either activates immediately (if that phase is already green) or waits as
"pending" until that phase's next green. Once active,
`ped_service_end_at = call_time + ped_walk_ms + phase.ped_clearance_ms`
becomes an unconditional floor on how early GREEN may end — overriding
even a pending phase-change request, so a preemption can defer a
pedestrian clearance's *start* but never cut a clearance already running
short. There is no separate WALK/flashing-don't-walk signal state modelled;
the floor is purely a timing constraint on the existing GREEN state.

**Consequences.** This is enough to make INV-05 ("pedestrian clearance
never cut short") a real, testable guarantee without modelling a second
signal head per approach. What it doesn't capture: a real ped head's
WALK/flashing-don't-walk DISPLAY, or pedestrian clearance extending past
the vehicle green into amber/all-red (real signals sometimes time
clearance to finish during the vehicle amber) — here clearance is entirely
contained within the green window, a deliberate simplification.

---

## ADR-019: Invariant checker independence, and its FLASHING_RED exemptions

**Context.** The checker's job is to catch bugs in the controller/
supervisor, so it must never simply trust what they report — but it also
shouldn't flag *correct* fail-safe behaviour as a violation. The 10,000-
example Hypothesis property test (Part D2) found exactly this: entering
`FLASHING_RED` legitimately cuts short whatever amber/all-red/min-green/
pedestrian-clearance window was in progress, since a conflict monitor
forcing flashing red is the system's ultimate override and must not wait
for anything.

**Decision.** `InvariantChecker` re-derives every invariant from
first-hand observation — `signal_view()` for INV-01, consecutive
`state_machine.transitions` pairs for INV-02/03/04, `command_log` replayed
through the checker's OWN copy of `verify()` for INV-09/10 — rather than
reading any controller- or supervisor-computed verdict. INV-02/03/04/05
explicitly skip the check when the transition being evaluated enters
`FLASHING_RED`; INV-06 (max hold) is skipped entirely while a controller is
already in `FLASHING_RED`, since "every direction is red indefinitely" is
a different failure mode than a hold starving an approach.

**Consequences.** The mutation tests (Part D5) prove this independence
concretely: disabling the controller's own approval check or idempotency
store, or corrupting its timing data, still gets caught, because the
checker never asked the controller whether it did the right thing — it
measured.

---

## ADR-020: In-process event bus with no persistence yet

**Context.** Central components announce observations (GPS fixes, passages,
deviations) without knowing who listens. Phase 8 adds an event store that must
see everything without changing publishers.

**Decision.** `events/bus.py` is a synchronous publish/subscribe bus. Delivery is
FIFO in subscription order, IDs are deterministic, and a derived event inherits
its cause's correlation ID. There is no persistence or replay yet; the store
will be another subscriber.

**Consequences.** Publishers do not change when the store arrives. A slow
subscriber slows the publisher, which is acceptable for the single-process
deployment (PRD §8.1).

---

## ADR-021: The composition root is the only place sim and central code meet

**Context.** ADR-005 forbids central code from importing the simulator, yet
something must connect them.

**Decision.** `runtime/composition.py` is the only module that imports both. It
builds the simulation and the central system and passes the simulator's feeds
into central through the `core/interfaces.py` protocols. It owns the cadence:
the simulator steps every tick, central steps on the corridor cadence. Only
`api/` and `scripts/` may import `runtime/`; `test_boundary.py` enforces this.

**Consequences.** Replacing the simulator with real feeds means writing new
adapters in one place, and central code can be tested with fakes that implement
the protocols.

---

## ADR-022: Controllers report state up-link over the same lossy network

**Context.** Central code needs each intersection's signal state, current phase,
red-since time, applied plan version and active hold to validate commands and
predict arrivals. A report can be delayed, dropped or duplicated.

**Decision.** Each controller sends a `StatusReport` on the heartbeat interval
over an up-link in `SimNetwork`, subject to the same latency, drop and duplicate
faults as the down-link. Central code treats a missing or stale report as
unknown, never as "all clear". Unknown signal state counts as red for stopping
decisions.

**Consequences.** Staleness is visible and handled. The up-link reuses the
existing fault model, so the chaos tests cover it.

---

## ADR-023: Ingestion ordering, deduplication and clock-skew policy

**Context.** Every input must be validated and deduplicated before anything
downstream sees it, with bounded memory.

**Decision.** Per (kind, source), the gateway keeps the last accepted sequence:
equal is a duplicate, lower is out of order. Memory is bounded by the number of
sources, not messages. A source clock more than `clock_skew_tolerance_s` ahead
of receipt is clamped to the receive time and flagged, not rejected, because
device clocks drift. GPS fixes are rejected when the implied speed exceeds
`max_gps_speed_mps`, or when the raw jump exceeds `max_gps_jump_m` (this also
catches equal timestamps, where a speed ratio is undefined). Sensor sequences
are keyed per metric, because density and queue count are separate streams.

**Consequences.** Ingestion costs about 0.02 ms p95 against a 50 ms budget. The
per-metric key was a real bug: before the fix, 2,616 valid queue readings were
rejected as duplicates of density readings.

---

## ADR-024: Historical priors generated from the simulator

**Context.** No real history exists. A segment with no live data needs a prior,
and a prior of zero would mean "empty road", which is the unsafe answer.

**Decision.** `scripts/build_history.py` runs one simulated day for each of 5
seeds, samples each segment every 60 s, and stores the median and interquartile
range per 15-minute bucket (120 segments × 96 buckets). Seeds run in parallel
and are merged in seed order, so the output is reproducible. The result is
committed as `data/historical_profile.json`. Lookups interpolate between buckets
and wrap at midnight.

**Consequences.** The prior matches the simulator's demand model, which is the
only stand-in available. It is only as good as that model: the evaluation runs
at peak demand (×2.5) but the prior was built at normal demand, so stale-segment
density is badly underestimated (p50 5 veh/km/lane against a true value that is
much higher). This is reported, not hidden, and is the next thing to fix by
building the profile at peak.

---

## ADR-025: Alpha-beta filter on along-route state

**Context.** The tracker needs smooth position and speed from noisy, intermittent
GPS on a route that is effectively one-dimensional.

**Decision.** An alpha-beta filter (α = 0.6, β = 0.2) over distance along the
route and speed. An EWMA of observed speed (α = 0.3) feeds the uncertainty
estimate only. A Kalman filter would model two-dimensional covariance that the
along-route state does not need. Two gains are easy to explain and to test.

**Consequences.** Simple and explainable. A lateral or non-Gaussian model would
justify moving to a Kalman filter later.

---

## ADR-026: Scored map matching with a grid index

**Context.** A fix must be assigned to a segment. Opposite carriageways are
geometrically identical, so distance alone cannot separate them.

**Decision.** Candidates come from a 100 m grid index within the match radius.
Each is scored: distance over radius, plus 0.5 × heading penalty, minus 0.3 if on
the route, minus 0.2 for continuity with the previous match. The lowest score
wins.

**Consequences.** Cheap and tested (heading separates carriageways; route and
continuity bonuses hold the match through noise). An HMM with transition
probabilities is the robust alternative for denser networks.

---

## ADR-027: Two ETA definitions and the window formula

**Context.** Corridor planning needs the earliest possible arrival, and
hospitals need the realistic one. Using the current speed for the first is wrong:
a stopped ambulance would predict an arrival that never comes.

**Decision.**
- **Unimpeded**: arrival at the stop line at cruise speed (free-flow × ambulance
  speed factor), ignoring signals and queues. It is a lower bound on arrival.
- **Expected**: departure through the stop line, equal to
  `max(unimpeded arrival, next green + projected red wait) + queue wait`. The
  red wait is projected from the controller's reported phase through its
  default plan timings; unknown state falls back to `eta_expected_red_fraction`
  of the cycle.
- **Window** (half-width): `eta_base_window_s` + `eta_k_gps` × GPS age +
  `eta_k_speed` × speed std × travel time + `eta_k_stale` × stale fraction ×
  expected + `eta_k_wait` × red wait. It always contains the unimpeded ETA.

Two bugs shaped this. The first version measured the red wait from now and then
added it to a future arrival, double-counting the gap. Evaluation first scored
expected ETA against arrival at the line, when it should score departure.

**Consequences.** Measured on the 10-minute run: the expected ETA at 30 s has
median signed error +2.4 s. At 60 s the window contains the departure only 62.5%
of the time, with median bias −21 s. The likely cause is pedestrian service
extensions, which lengthen greens but are absent from the projected plan. This
is open; `eta_k_wait` partly compensates and is not fully tuned.

---

## ADR-028: Dead reckoning holds at a red stop line and reconciles on GPS return

**Context.** With GPS lost the tracker must still estimate position, and a
vehicle coasting through a red light would be wrong exactly where it matters.

**Decision.** After `gps_loss_threshold_s` (3 s) without a fix, the estimate
advances at the last filtered speed. If it reaches a stop line whose approach is
not green (unknown counts as red), it holds there at zero speed. On GPS return, a
fix consistent with the coasted estimate is blended in; an inconsistent but
plausible fix resets the filter.

**Consequences.** Median dead-reckoning error is 0.00 m on the 10-minute run. The
p95 of 77 m comes from a light turning green during the outage, which the
tracker cannot observe until GPS returns. This is the stated limit.

---

## ADR-029: Deviation requires persistence

**Context.** FR-TRK-05 requires a deviation event, but one noisy fix must not
trigger a replan.

**Decision.** A deviation is raised after `deviation_fix_count` (3) consecutive
off-route fixes (farther than `deviation_distance_m` from the route, or
unmatched), or after `deviation_duration_s` (5 s) off route. It is raised once
per route.

**Consequences.** A real detour is reported within a few fixes, which is what
Phase 7 replanning needs. The clean evaluation run raised no false deviations.

---

## ADR-030: Stop-line passage margin of 15 m; presence means "at the line"

**Context.** Phase 6 releases a preemption lease on passage. Releasing it while
the ambulance is still at the line hands the intersection back too early. GPS
noise is σ = 5 m.

**Decision.** Passage is reported only when the estimate is at least
`stop_line_margin_m` (15 m, three σ) beyond the line. A presence detector firing
means the vehicle has reached the line: it snaps the estimate to the line and
holds. It never counts as passage.

**Consequences.** A stopped vehicle under 5 m noise never triggers a false
passage (regression test). Passage is reported about 2 s after true departure
(p50 +2.0 s, p95 +2.1 s): the safe direction. Two earlier versions failed. A
5 m margin let noise trigger passage, and presence-as-passage released leases
while the vehicle was still stopped.

---

## ADR-031: Unknown is never treated as clear

**Context.** Signal state, queues, densities and priors can all be missing.
Defaulting any of them to clear makes the system optimistic exactly when it
should be cautious.

**Decision.** Unknown signal state is red for stopping. A segment with no live
data uses its historical prior, inflated by the pessimism factor, and is marked
stale. An intersection with no fresh report is treated as unknown, not as
normal. A zero prior is only ever the no-profile test condition.

**Consequences.** The system errs toward delay and assumed congestion. This costs
time, never safety, consistent with ADR-016.
---

## ADR-032: Route delivery as a guidance link, and CANNOT_FOLLOW for committed divergence

**Context.** Central code must push routes to the vehicle (FR-RT). The vehicle is already moving, so a new route can only be followed up to the point where it still agrees with the old one.

**Decision.** `core/interfaces.py` gains `GuidanceLink` (`send_route`, `drain_acks`). The simulated implementation (`sim/guidance.py`) delivers after `guidance_latency_s` (30 ms), waits `reaction_delay_s` (2 s) before adopting, and ignores any version not newer than the one last adopted. An ambulance on segment `route[k] -> route[k+1]` is committed to `route[k+1]`, so a new route must keep both nodes. Otherwise it answers `CANNOT_FOLLOW` and keeps its route; the planner then replans from the vehicle's true position.

**Consequences.** The vehicle never switches to a route it cannot physically follow. Every outcome is acknowledged, so the planner knows what is in effect, and the tracker's route changes only on `ADOPTED`.

---

## ADR-033: Density to BPR volume/capacity mapping

**Context.** BPR (PRD 11.1) needs a volume/capacity ratio. The world state gives density in vehicles per km per lane, not flow.

**Decision.** `x = min(density / critical_density, route_x_max)`, with `jam_density = 1000 / jam_spacing_m` per lane (7.5 m spacing, about 133 veh/km/lane), `critical_density = jam_density * route_critical_density_fraction` (0.5, about 67 veh/km/lane) and `route_x_max = 1.5`. BPR then uses the PRD's alpha 0.15 and beta 4, and the increment is scaled by `congestion_sensitivity` (0.5), because an ambulance is slowed less than other traffic. Free time is `length / min(free_flow * ambulance_speed_factor, ambulance_speed_cap_mps)`, with the cap at 20 m/s.

**Consequences.** The fraction 0.5 is a modelling choice, not a measured value. Capping `x` bounds the BPR slowdown at about 38%. Queues carry most of the congestion cost (ADR-036): a queue of 50 vehicles adds about 52 s, while the BPR term adds about 1 s at a density of 75 veh/km/lane.

---

## ADR-034: Admissible, consistent heuristic

**Context.** A* returns optimal routes only with an admissible heuristic, and it never reopens closed states only with a consistent one.

**Decision.** `h(s) = min over goals of straight_line(to_node(s), goal) / ambulance_speed_cap`.

**Proof sketch.** Every segment costs at least `length / cap`, because travel time is at least the free time at the speed cap and every penalty is non-negative. A straight line between two nodes is never longer than a road path between them, so `h` never exceeds the true remaining cost: it is admissible. For a transition `s -> s'`, the cost of `s'` is at least `dist(to(s), to(s')) / cap`, and by the triangle inequality `dist(to(s), g) <= dist(to(s), to(s')) + dist(to(s'), g)`. So `h(s) <= c(s, s') + h(s')`: it is consistent.

**Consequences.** Tests check admissibility against Dijkstra over every 7th segment and every 5th goal, and consistency on every transition in the graph.

---

## ADR-035: Time-aware costs, and the approximation they introduce

**Context.** A segment 20 minutes away will be entered in a different traffic state. The brief asks for forecast blending without the full machinery of time-dependent shortest paths.

**Decision.** A segment entered `dt` seconds from now uses `w * current + (1 - w) * historical(entry)` with `w = exp(-dt / forecast_tau_s)` and `forecast_tau_s = 120`. The entry time is `now + g`, where `g` is the cost accumulated so far. A* keeps the cheapest arrival per segment. With time-dependent costs strict optimality is no longer guaranteed, so every returned path is re-evaluated by `evaluate_path`, and that exact value is what a decision reports.

**Consequences.** Time-dependent costs can break strict optimality. In the tests the forecast is frozen (`forecast_tau_s = 1e9`), which makes the Dijkstra comparison exact. In operation the error is small because blending matters only beyond a few minutes.

---

## ADR-036: Normal signal delay in every stop-line cost, plus an uncontrollable penalty

**Context.** The first version costed a red light only at an intersection the planner could not command. The 10-minute report showed the planned route predicted at 239 s and actually taking 423 s, with 244 s stopped against 128 s on the baseline. Every stop line is a red light, and the model ignored it.

**Decision.** Every segment carries `signal_s`, the expected red wait at its downstream stop line under normal operation: `eta_expected_red_fraction * cycle`. An uncontrollable downstream intersection (no fresh report, unreachable, or flashing) adds `uncontrollable_penalty_s` (45 s) on top of that.

**Consequences.** Routes now reflect signal delay, but the model still overestimates trip time: in the first report it predicted 611 s for the planned route against 423 s observed. The red fraction is a fixed share of the cycle, and the real schedule is only partly known. What matters more is the ranking of routes, so the report prints the model's cost for the fixed baseline route next to the planned route. In the jam-on-B4-to-C4 scenario the model put the planned route at 611 s and the baseline at 677 s, yet the baseline arrived sooner. This is a known calibration gap, not solved in this phase.

---

## ADR-037: Stability rules: hysteresis as max(abs, rel), dwell, commitment, one route in flight

**Context.** PRD FR-RT-04 says "better by more than 15 s or 10%", while PRD 11.5 writes `max(15 s, 0.10 x old cost)`. The two readings differ: "or" switches more easily. Flapping between near-equal routes is the main risk to the crew's trust in the guidance.

**Decision.** Use the stricter `max` reading, as the brief specified. Adopt a candidate only if its gain exceeds `max(hysteresis_abs_s, hysteresis_rel * current remaining cost)`, at least `min_route_dwell_s` (20 s) has passed since the last switch, the vehicle is outside the commitment zone (within `commitment_distance_m`, 80 m, or less than `commitment_time_s`, 8 s, from the junction), and no other route is awaiting acknowledgement. A blocked route overrides all of these. Each rejected candidate is recorded with its reason.

**Consequences.** This is a deliberate deviation from the wording of FR-RT-04 (it is stricter). The cost is that a real but small improvement can be ignored. The commitment zone means the vehicle does not change its mind at the junction it is about to enter, and the one-route-in-flight rule stops two overlapping routes being sent at once.

---

## ADR-038: Edge-based search, with U-turns banned by default

**Context.** Turn penalties and U-turn bans need turn-level state, which intersection-level search cannot express.

**Decision.** Search runs over directed segments. A transition from segment `a` to `b` carries its turn type, and transitions are precomputed once at startup. U-turns are excluded unless `allow_u_turns` is set.

**Consequences.** The state space is about 120 segments. Turn penalties and U-turn bans are exact, and the search stays small enough to run every second.

---

## ADR-039: Turn penalties by near-side and far-side, with a configurable driving side

**Context.** The brief asks for turn costs by turn type, and notes that the side depends on the driving convention: India drives on the left, so a left turn is near-side there, and right-hand traffic reverses that. The city model has no driving-side field.

**Decision.** Turns are classified as THROUGH, NEAR_SIDE, FAR_SIDE or U_TURN. The class comes from the sign of the cross product of the incoming and outgoing travel vectors in screen orientation (y grows south, as in `sim/topology.py` and the dev map): a negative cross product is a left turn. `driving_side` (default `left`) decides which class is near-side. Penalties: through 0 s, near-side 3 s, far-side 5 s.

**Consequences.** The default follows the brief's left-hand assumption, which the user should confirm. The orientation assumption is covered by `test_left_and_right_are_classified_in_screen_orientation`. Changing the driving side needs no code change.

---

## ADR-040: Uncertainty penalty, which can only make a road less attractive

**Context.** Stale or low-confidence data must not make a road look better than it is.

**Decision.** `uncertainty = (1 - w * confidence) * uncertainty_weight * travel`, with `uncertainty_weight = 0.2`. The penalty is non-negative, so a stale road is never cheaper.

**Consequences.** `test_stale_data_never_makes_a_road_cheaper` enforces this direction.

---

## ADR-041: Deterministic tie-breaking everywhere in routing

**Context.** The brief requires the same input to give the same route every time.

**Decision.** Frontier entries are ordered by (f, g, insertion counter). Starts and neighbours are sorted. Yen's candidate heap is ordered by (cost, counter). No dict or set iteration order influences a result.

**Consequences.** `test_search_is_deterministic` and the seeded integration tests confirm identical decision histories for identical seeds.

---

## ADR-042: Yen's diversity filter and a resumable compute budget

**Context.** A backup route is only useful if it does not fail together with the primary when a shared road closes. Yen's search can also be expensive.

**Decision.** An alternative is kept only if less than `route_alternative_overlap_max` (0.7) of its length is shared with the primary or with any kept alternative. Work is budgeted in A* expansions (`route_alternative_budget_expansions`, 400 per central step). Spur searches are not split, so one step can overrun the budget by one search. The queue of pending spurs persists, so the result is identical whether the budget is spent in one call or many.

**Consequences.** Alternatives appear within a few steps of each route change. The overrun is bounded by one spur search, which is small on this network. The report measured about 85 to 95 Yen expansions per central step.

---

## ADR-043: Replan triggers, coalescing, and the fast-path switch

**Context.** The brief lists triggers from several sources, and asks for an immediate switch to a backup when the current route is blocked.

**Decision.** Triggers: periodic (10 s), ETA drift over 15%, a closed road ahead, a controllability change at an intersection on the route, an unavailable assigned hospital, ROUTE_DEVIATION from the event bus, and CANNOT_FOLLOW. Triggers accumulate per vehicle and cause one plan per step. A blocked route first tries the precomputed alternatives: one that starts from the current segment and avoids closed roads is adopted in the same step (FAST_PATH), and a full replan then runs, subject to ADR-037. Alternatives are rebased when the vehicle enters the next segment and dropped when they no longer start from it.

**Consequences.** The fast path adds no extra step. In the closure scenario the planner detected the closure one central step after it was applied (61 s, closure at 60 s) and adopted a bypass in that same step. The fast path with a valid precomputed backup is exercised by a unit test rather than by the report scenario, because in that scenario the alternatives all shared the approach to the closed segment.

---

## ADR-044: Routes never revisit an intersection, and set_route keeps tracking state

**Context.** The tracker looks up stop lines by node (`route.index(node)`), which is ambiguous if a route loops. Replacing the tracker's route must not forget that stop lines were already passed, or passage events would fire again.

**Decision.** The planner bans every segment that enters an intersection already passed or already on the route, and rejects any candidate that would still revisit a node. `tracker.set_route` replaces only the route fields (route, segments, cumulative distances) and keeps the filter, the passed set and all stop-line state, which is valid because the new route shares the travelled prefix.

**Consequences.** Passage events cannot fire twice for the same stop line after a reroute. The search space excludes looping routes, which are never useful on this network.

---

## ADR-045: Tick order gains route delivery and injected congestion

**Context.** The simulator's fixed tick order (ADR-009) must include the new guidance link and the congestion fault.

**Decision.** Step 2b delivers due route pushes (`SimGuidance.step`). Step 6b applies `inject_congestion` faults after demand. Both sit where their inputs are known, and the order is fixed so determinism holds.

**Consequences.** `state_hash` changes for runs that use the new steps, which is expected. The seeded determinism tests still pass.

---

## ADR-046: Conflicts from circular chord-crossing geometry

**Context.** Signal phasing must never allow two conflicting movements to be green together (INV-01). Hand-authored conflict tables do not scale to real junctions with irregular shapes, skewed arms and one-way approaches, and they are error-prone.

**Decision.** Each approach and exit is placed on a circle around the junction at its compass bearing. A movement is the chord from its approach point to its exit point. Two movements conflict when their chords cross (endpoints interleave around the circle), or when different approaches feed the same exit (merge). Movements from the same approach never conflict. U-turns conflict with all other approaches by default. A configurable "free near-side turn" removes crossing conflicts for near-side movements but keeps merge conflicts (common in Indian cities under left-hand driving). Turn types come from the signed heading change between arrival and departure. The test uses only the circular order of endpoints, so it does not depend on the map's coordinate orientation.

**Consequences.** Conflicts are derived from geometry for any junction shape and are unit-tested on 4-way, T, Y, 5-way and skewed junctions, with Hypothesis properties. Free near-side turns are a deliberate relaxation: they must be configured per junction class and are documented as an assumption.

---

## ADR-047: Expand, migrate, contract for the junction model

**Context.** The junction model is used by controllers, the safety supervisor, the invariant checker, the simulator, state, tracking and routing. Replacing it in one step would break the safety layer for a long stretch with no way to check progress, and would weaken the safety guarantees the Phase 3 tests protect.

**Decision.** Add the general model beside the existing one (expand). Migrate one module per commit, keeping the full suite green after each (migrate). Delete the old model, its loader path and the temporary adapter once nothing depends on them (contract). A boundary test forbids the old names from reappearing. Safety tests may change how junctions are constructed, never what they assert.

**Consequences.** Each commit is small and reversible. Progress is recorded in `docs/progress/phase-6.md`, so work can resume across sessions. The temporary adapter is a cost: it must be verified against the old grid exactly, and then removed.


---

## ADR-048: Signal plans generated from geometry and lane counts

**Context.** Chennai signal timings are not publicly available, and OpenStreetMap signal data is incomplete. Every signalised junction still needs a conflict-free phase plan that serves every movement.

**Decision.** `core/phasing.py` generates plans from junction geometry. PAIRED phasing is used only at regular four-way junctions, where opposing arms align within `regular_bearing_tolerance_deg` (15 degrees). Otherwise SPLIT phasing is used, one phase per approach. A paired plan is accepted only if it passes the same checks as split: every phase compatible under the conflict matrix, and every non-U-turn movement served. U-turns are excluded at signalised junctions, matching the default in ADR-046. Greens are shared by lane count, with a minimum of `min_green_s` and a maximum of `phase_green_max_s`, and the cycle is `cycle_target_s` clamped to `[cycle_min_s, cycle_max_s]`. Any violation raises `PhasingError`, so an invalid plan is never silently used.

**Consequences.** Plans are reproducible and valid by construction, but they are not calibrated to any real signal. Road-class weighting is not yet used, because the junction model carries lanes only. That weighting is deferred to the importer stage, where road class is available. Any Chennai result must say the timings are generated, not observed.


---

## ADR-049: General-format city loader re-derives conflicts rather than trusting stored data

**Context.** The old loader validates authored conflict pairs for symmetry, because a human wrote them. The general model derives conflicts from geometry (ADR-046) and generates phases from that derivation (ADR-048), so there is nothing authored to audit for consistency — but a hand-edited or imported city file could still carry a stale or tampered junction.

**Decision.** `core/general_city_loader.py` re-derives each junction's conflict matrix from its own geometry via `Junction.conflict_matrix()` and checks it is symmetric and irreflexive, checks every phase against that freshly derived matrix (never against anything stored), and checks every non-U-turn movement is served by some phase. It also checks every approach, exit, movement and phase reference stays within its own junction and resolves to a real segment, and that the whole graph is strongly connected, mirroring the old loader's checks but over junctions and segments instead of intersections and compass approaches.

**Consequences.** A city file cannot smuggle in an unsafe phase by editing its phases without also matching the junction's true geometry, because the loader never looks at anything the file claims about conflicts — only what the geometry implies. The generated grid (`config/cities/grid.city.json`) passes this loader, which doubles as an automatic correctness check on `core/phasing.py` and `core/conflicts.py` beyond the unit tests, now proven against a full 36-junction, 120-segment city.


---

## ADR-050: The general model finds merge conflicts the old grid's hand-authored matrix never modelled

**Context.** Stage 6.1.5 needed a temporary adapter from the general junction model back to the old `core.models.City` shape, so Stage 6.2 can migrate one consumer at a time. The plan was to prove the adapter's output is identical to the hand-authored `config/city.json`. Building it surfaced a real finding: it is not possible to make it identical, and forcing it would mean hiding a genuine safety gap in the old model.

**The finding.** The old grid's conflict table is a single global list (`CONFLICT_PAIRS` in `generate_city.py`) applied to every intersection, defined over compass directions, not individual turns. It says, for example, that N and S never conflict, so an `NS_THROUGH` phase lets every movement from both approaches run together — including a far-side turn from N and a near-side turn from S that both end on the same exit road. The general model finds this: it is a MERGE conflict (two different approaches feeding the same exit), which is real and distinct from the crossing conflicts the old table was built to catch. Checked directly: at a regular 4-way junction, `N-to-W` (N's far-side turn) and `S-to-W` (S's near-side turn) share the W exit and do conflict, even though neither crosses the other's path and no crossing-only table would ever catch it.

A second, smaller finding: the old table is universal and direction-only, so it lists conflicts for arms that do not exist at a given junction (e.g. a 2-arm corner still carries entries for N and W, which have no approach there). Those entries were never backed by a real movement and are not conflicts to preserve.

**Decision.** The adapter (`core/legacy_adapter.py`) does not try to reproduce the old phases or matrix bit-for-bit. It builds the old `ConflictMatrix` by aggregating the general model's own per-movement conflicts up to direction pairs (a direction pair conflicts if any one of their movements conflicts), which is strictly safe and a superset of the old table's real (geometrically backed) conflicts. For phases, it gives every direction its own single-direction old-style phase, never pairing opposing directions — pairing is unsafe in general, as the finding above shows, and a single-direction phase is always conflict-free because movements from the same approach never conflict (ADR-046). The equivalence test (`tests/test_legacy_adapter.py`) checks structure (nodes, segments, hospitals, entry node, approach directions) exactly, checks every known old conflict is preserved at regular 4-way junctions (where the old table's assumptions actually hold), and has a named test documenting the new merge conflict the old table missed.

**Consequences.** The adapter produces more phases per junction than the hand-authored file and never less-safe behaviour. The old grid's current safety story (Phases 0–5) is not invalidated: a soft lane-merge is a much lower-severity event than the right-angle collision the crossing checks exist to prevent, and the existing invariants and tests were written against the old table's scope. This is disclosed, not hidden, and should inform Stage 6.2: once the controller and safety layer migrate to movement-level phases, this gap closes because phases will never again be defined as "every movement from a whole compass direction."


---

## ADR-051: Stage 6.2 cannot be migrated one module per commit; `City` is a single shared value, not an interface

**Context.** The Phase 6 spec's Stage 6.2 lists a module-by-module migration order (controller, safety, invariants, sim, state/tracking, routing, then contract) with the full suite green after each commit. Attempting to start with just the controller found this infeasible as written.

**The finding.** `core.models.City` (and its `Intersection`, `RoadSegment`, `Direction`, `Phase`, `ConflictMatrix`) is not consumed behind a per-module interface — it is one object, built once in `runtime/composition.py`, passed by reference into the simulator, the controller, the safety supervisor, the invariant checker, World State, the vehicle tracker, and the router simultaneously. 28 of the 42 non-test backend files import from `core.models` directly. Changing `IntersectionController`'s constructor to accept a `Junction` instead of an `Intersection` does not compile against `sim/engine.py`'s existing call site, because `sim/engine.py` builds its controllers from the SAME `City` object it also hands to `TrafficModel`, `DemandModel`, `AmbulanceSim` and `SimNetwork` — all of which are typed against `Direction`/`RoadSegment`/`Intersection`. There is no seam at which the controller alone can move to the general model while its caller stays on the old one; the caller is the composition root, and the composition root is the one place that builds the whole `City`/`GeneralCity` for everything downstream.

This also explains why `core/legacy_adapter.py` (Stage 6.1.5) only had to go ONE direction (general → old): going the other way (old → general) is not meaningful, because the general model carries real information — approach bearings, exits, movement-level turn types, per-movement conflicts — that the old model never had and cannot supply.

**Decision.** Stage 6.2 will be executed as one coordinated migration of the shared `City` type used by `runtime/composition.py` and everything it wires together (`controller`, `safety`, `chaos`, `sim/*`, `state`, `tracking`, `routing`, `central`, `api`), verified together with the full test suite — including the Phase 3 property and stateful safety tests at the `thorough` Hypothesis profile — before any of it is committed. This is a deliberate, disclosed deviation from "one module per commit" (ADR-047's expand-migrate-contract principle is kept; only the commit granularity of the migrate step changes, because the modules are not actually independent). Progress within this effort is tracked file-by-file in the progress file so a future session can resume mid-migration without redoing verified work.

**Consequences.** This is the highest-risk, largest remaining piece of Phase 6. It will take real wall-clock time to execute and verify correctly, including re-running the 10,000-example Hypothesis profiles, which are themselves slow. It must not be rushed: a mistake here would be a silent regression in the safety layer the rest of this project exists to protect. Stage 6.3 (the OpenStreetMap importer, which needs `osmnx`, `shapely`, `pyproj`, `geopandas` — none of which are installed yet) and Checkpoint B (which structurally requires the user to confirm the chosen map area before any further work) remain gated behind this migration succeeding and Gate A passing.
