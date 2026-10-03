# Phase 6 Progress — Real City (Chennai)

Spec: docs/specs/phase-6-real-city.md
Branch: phase-6-real-city

| Stage | Scope | Status |
|---|---|---|
| 6.1 | Expand: general model, conflicts, phasing, general grid, loader, adapter | IN PROGRESS (model + conflicts done) |
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
- 6.1.3 phasing, 6.1.4 general grid + loader, 6.1.5 adapter, phasing/loader tests
- Step 0 remainder: progress file (this file), CLAUDE.md "Multi-session work" protocol,
  dependency install + ADR with versions, bootstrap commit.

## Session log
- Session 1: branch `phase-6-real-city` created; spec saved; progress file created; 6.1.1 model and conflict geometry committed. Stopped for session length.

## Open questions / decisions pending
- None blocking. Dependency install (osmnx, networkx, shapely, pyproj, geopandas) is
  pending until the importer stage; it is not needed for 6.1.

## Next step
6.1.3 `backend/core/phasing.py`: build JunctionPhase sets for SIGNALISED junctions.
Paired phasing for regular 4-way junctions (opposing approaches aligned within a bearing
tolerance), split phasing otherwise; every phase must pass `is_compatible`; every movement
served; green splits by road class and lanes within min/max green. Tests in
`tests/test_phasing.py` including Hypothesis. Then 6.1.4 general grid file and loader,
6.1.5 adapter, and the ADR for generated signal plans. Keep old model classes untouched
until Stage 6.2.
