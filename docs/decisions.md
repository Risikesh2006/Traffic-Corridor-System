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
