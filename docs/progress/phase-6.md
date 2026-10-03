# Phase 6 Progress — Real City (Chennai)

Spec: docs/specs/phase-6-real-city.md
Branch: phase-6-real-city

| Stage | Scope | Status |
|---|---|---|
| 6.1 | Expand: general model, conflicts, phasing, general grid, loader, adapter | IN PROGRESS (model, conflicts, phasing, general grid + loader done) |
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
- 6.1.5 temporary adapter (`core/legacy_adapter.py`) + exact-equivalence test against the old grid
- Step 0 remainder: dependency install (osmnx, networkx, shapely, pyproj, geopandas) and an
  ADR recording their versions. Deferred until the importer stage.

- `core/phasing.py` (6.1.3): PAIRED for regular four-way, SPLIT otherwise; validity checks; cycle bounds; ADR-048; `tests/test_phasing.py` (18 tests incl. Hypothesis). Full fast suite: 355 passed.

- `core/general_city.py` + `core/general_city_loader.py` (6.1.4): GeneralCity/GeneralRoadSegment
  container, loader re-derives conflicts and phase validity from geometry rather than trusting
  stored data (ADR-049). `scripts/generate_general_grid.py` writes `config/cities/grid.city.json`
  (36 junctions, 120 segments, 2 hospitals) using the same ADR-003 grid layout as the old
  `config/city.json`, with compass bearings matching the existing Direction convention.
  12 loader tests pass. Full fast suite: 367 passed.

## Session log
- Session 1: branch `phase-6-real-city` created; spec saved; progress file created; 6.1.1 model and conflict geometry committed. Stopped for session length.

## Open questions / decisions pending
- None blocking. Dependency install (osmnx, networkx, shapely, pyproj, geopandas) is
  pending until the importer stage; it is not needed for 6.1.

## Next step
6.1.5: `backend/core/legacy_adapter.py` converts a `GeneralCity` built from `grid.city.json`
into the OLD `core.models.City` shape (intersections with compass Direction approaches,
authored ConflictMatrix, Phase with Direction movements) so Stage 6.2 can swap the data
source without changing consumer code yet. Write a test asserting the adapter's output for
the general grid equals `load_city(config/city.json, settings)` exactly (same intersections,
segments, phases, conflicts, hospitals, entry node) — this is the proof that the new model
is a faithful generalisation of the old one before any consumer migrates. Keep old model
classes untouched; this is the last "expand" step before Stage 6.2 migration begins.
