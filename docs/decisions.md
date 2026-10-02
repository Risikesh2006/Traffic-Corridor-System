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
