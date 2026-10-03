# Phase 6 Progress — Real City (Chennai)

Spec: docs/specs/phase-6-real-city.md
Branch: phase-6-real-city

| Stage | Scope | Status |
|---|---|---|
| 6.1 | Expand: general model, conflicts, phasing, general grid, loader, adapter | DONE |
| 6.2 | Migrate + contract → Gate A | NOT STARTED |
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

## Session log
- Session 1: branch `phase-6-real-city` created; spec saved; progress file created; 6.1.1 model and conflict geometry committed. Stopped for session length.

## Open questions / decisions pending
- None blocking. Dependency install (osmnx, networkx, shapely, pyproj, geopandas) is
  pending until the importer stage; it is not needed for 6.1.

## Next step
Stage 6.1 is complete. Next: Stage 6.2 — MIGRATE + CONTRACT. Migrate ONE module per commit,
full suite green after each, per the spec's order: (1) controller/ — phases as movement sets,
red_since per approach, local self-check on movement conflicts; (2) safety/; (3)
chaos/invariants.py (INV-01 on movements, INV-06 per approach); (4) extend Phase 3 property
and stateful safety tests onto the general model across grid + synthetic T/Y/5-way/skewed
junctions with the thorough Hypothesis profile; (5) sim/ (queue per approach, give-way model
for UNSIGNALISED, roundabout as unsignalised with entry capacity); (6) state/ and tracking/
(approach-based queues, stop lines); (7) routing/ (turn types/penalties from Movement.turn_type,
one-way, give-way delay); (8) CONTRACT — delete core.models' old junction classes, city_loader.py's
old path, and legacy_adapter.py; update test_boundary.py. Then GATE A (spec has the exact
checklist) — if it fails, STOP and report, do not proceed to Stage 6.3 (OSM import) without
explicit approval. Given ADR-050's finding, the migration should carry the merge-conflict
fix forward (movement-level phases replace direction-level ones in the real model, not just
in this temporary adapter), which actually makes Stage 6.2 more correct than before, not a
regression.
