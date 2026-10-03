# Phase 6 Progress — Real City (Chennai)

Spec: docs/specs/phase-6-real-city.md
Branch: phase-6-real-city

| Stage | Scope | Status |
|---|---|---|
| 6.1 | Expand: general model, conflicts, phasing, general grid, loader, adapter | DONE |
| 6.2 | Migrate + contract → Gate A | BLOCKED on a scoping finding (ADR-051); see below |
| 6.3 | OSM fetch + Chennai build + map viewer → ⛔ Checkpoint B | NOT STARTED |
| 6.4 | Indian realism, geometry tracking, UTM, calibration, history | NOT STARTED |
| 6.5 | Both-city tests, performance, reports, docs, merge | NOT STARTED |

## Already done (verified against the spec)
- `a1cbd18` docs(phase-6): spec saved, progress file, CLAUDE.md protocol, ADRs 046-047
- `4a4c2c7` feat(core): `core/junctions.py` general model (Approach, Exit, Movement,
  JunctionPhase, symmetric JunctionConflictMatrix, Junction.conflict_matrix) + 6 tests
- `3735563` style(tests): conflict test formatting
- Full fast suite: 331 passed (before the junction module; junction tests pass on their own)
- `dfdd1d0` feat(core): conflict detection from junction geometry — `core/conflicts.py`
  (chord crossing, merges, free near-side option, U-turn rule, bearing-based turn types)
  and `tests/test_conflicts.py` (16 tests incl. Hypothesis). Covers 6.1.2 partially:
  the spec's new `core/junctions.py` model is not yet created, and conflicts still
  use plain `MovementGeometry` records rather than the final `Movement` model.
- `c84a7f8` refactor(core): `TurnType` and `IntersectionKind` now live in `core/models.py`.

## Not yet done
- Stage 6.2 onward (see spec). Nothing left in Stage 6.1.
- Step 0 remainder: dependency install (osmnx, networkx, shapely, pyproj, geopandas) and an
  ADR recording their versions. Deferred until the importer stage.

- `core/phasing.py` (6.1.3): PAIRED for regular four-way, SPLIT otherwise; validity checks; cycle bounds; ADR-048; `tests/test_phasing.py` (18 tests incl. Hypothesis). Full fast suite: 355 passed.

- `core/general_city.py` + `core/general_city_loader.py` (6.1.4): GeneralCity/GeneralRoadSegment
  container, loader re-derives conflicts and phase validity from geometry rather than trusting
  stored data (ADR-049). `scripts/generate_general_grid.py` writes `config/cities/grid.city.json`
  (36 junctions, 120 segments, 2 hospitals) using the same ADR-003 grid layout as the old
  `config/city.json`, with compass bearings matching the existing Direction convention.
  12 loader tests pass. Full fast suite: 367 passed.

- `core/legacy_adapter.py` + `tests/test_legacy_adapter.py` (6.1.5): converts a GeneralCity
  (grid) into the old `core.models.City` shape. IMPORTANT FINDING (ADR-050): exact phase/
  conflict-matrix equivalence with the hand-authored grid is not achievable, and forcing it
  would hide a real safety gap — the old grid's direction-level conflict table never modelled
  merge conflicts between a far-side turn and the opposing approach's near-side turn into the
  same exit. The adapter is therefore conservative (one phase per direction, never paired;
  conflict matrix aggregated up from the general model's own per-movement conflicts) and the
  test checks structural equivalence plus "never loses a known old conflict" at regular 4-way
  junctions, with a named test documenting the new merge conflict found. 7 new tests. Full
  fast suite: 374 passed. Stage 6.1 is now fully DONE.

- Session 3: read controller/state_machine.py, controller/intersection_controller.py,
  safety/supervisor.py, chaos/invariants.py in full to plan the Stage 6.2 migration.
  Found the City-is-a-shared-value coupling problem (ADR-051) before writing any migration
  code — no production code was changed this session. Stopped to report the finding and get
  a decision, per the resumable protocol's "genuine blocker needing a decision" stop
  condition. Working tree is clean on top of commit 8aca349 (end of Stage 6.1).

## Session log
- Session 1: branch `phase-6-real-city` created; spec saved; progress file created; 6.1.1 model and conflict geometry committed. Stopped for session length.

## Open questions / decisions pending
- BLOCKING FINDING (ADR-051): Stage 6.2 cannot be done one module per commit as the spec
  describes. `core.models.City` is a single shared object built once in
  `runtime/composition.py` and passed by reference into controller, safety, invariants,
  sim/*, state, tracking and routing simultaneously (28 of 42 non-test backend files import
  `core.models` directly). Migrating the controller's input type breaks `sim/engine.py`'s
  construction call immediately, which breaks everything downstream of it. There is no seam
  for an independent, individually-green commit per module.
- Decision needed from the user: proceed with Stage 6.2 as ONE coordinated migration of the
  shared City type across all of controller/safety/chaos/sim/state/tracking/routing/central/
  api, verified together (including the thorough 10,000-example Hypothesis profile) before
  committing — this is large, high-risk, and will take real time to do carefully and is not
  something to rush. Alternative: descope Stage 6.2 differently (e.g., accept the old model
  stays authoritative for the grid/safety layer indefinitely, and the general model is used
  ONLY for Chennai import + a parallel, separately-verified safety story there) — this would
  need its own ADR and is a bigger product decision than a code change.
- Dependency install (osmnx, networkx, shapely, pyproj, geopandas) is still pending and not
  yet attempted; needed only once Stage 6.3 starts.

## Next step
WAITING ON USER DECISION (see Open questions above) before writing any Stage 6.2 migration
code. Once decided, if proceeding with the coordinated migration: start from
`runtime/composition.py` and work outward — change it to build a `GeneralCity` (via
`load_general_city` + `generate_general_grid`'s approach, or a new loader entry point) and
pass it to `Simulation`/`CentralSystem`; then update, file by file, every consumer listed in
ADR-051 to accept `Junction`/`GeneralRoadSegment`/movement-ids/approach-ids instead of
`Intersection`/`RoadSegment`/`Direction` — `controller/state_machine.py` and
`controller/intersection_controller.py` first (red_since keyed by approach id, green/amber
sets of movement ids, detect_conflict over JunctionConflictMatrix), then `safety/supervisor.py`
and `safety/gate.py`, then `chaos/invariants.py`, then `sim/traffic.py` and `sim/ambulance.py`
(queue per approach; a vehicle must pick its specific movement/exit BEFORE checking whether
that movement is currently served, not just whether its approach has some green phase — this
is a real behavioural fix, not a rename, since far-side can now be a separate phase from
through+near-side per ADR-050), `sim/network.py`, `sim/sensors.py`, `sim/vehicle_feed.py`,
`sim/demand.py`, `sim/topology.py`, `sim/guidance.py`, then `state/world_state.py`,
`tracking/*`, `routing/*`, `central/system.py`. Update every corresponding test file
alongside its module (test_controller.py, test_safety_properties.py, test_safety_stateful.py,
test_gate.py, test_supervisor.py, test_traffic.py, test_ambulance.py, test_network.py,
test_world_state.py, test_tracker.py, routing tests, test_central_integration.py,
test_boundary.py, etc.) — none of these may have their assertions weakened, only their
junction/city construction changed. Run the FULL suite (including
`HYPOTHESIS_PROFILE=thorough pytest tests/test_safety_properties.py tests/test_safety_stateful.py`)
before committing. Then CONTRACT (delete old model classes, city_loader.py's old path,
legacy_adapter.py; update test_boundary.py), then GATE A per the spec's checklist. If Gate A
fails, STOP and report — do not proceed to Stage 6.3 without explicit approval.
