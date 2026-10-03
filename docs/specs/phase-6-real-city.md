# PHASE 6 — REAL CITY (CHENNAI) AS THE PRIMARY MAP — RESUMABLE MASTER PROMPT

## HOW THIS PROMPT WORKS (read first)
- This prompt is RESUMABLE. The same prompt is pasted at the start of every session until Phase 6 is complete.
- FIRST RUN: if `docs/progress/phase-6.md` does NOT exist, do STEP 0 (bootstrap), then start Stage 6.1.
- LATER RUNS: if `docs/progress/phase-6.md` EXISTS, read it and resume from its "Next step". Do NOT redo work marked done — verify it briefly instead.
- Work through the stages in order, as far as this session allows. Stop ONLY at: (a) ⛔ Checkpoint B (human map confirmation), (b) a failed Gate A condition, (c) a genuine blocker that needs my decision, or (d) the session getting long.
- If the session is getting long: finish the current step, make sure ALL tests pass, commit, update the progress file with an exact "Next step", and end with: "Paste the Phase 6 prompt again to continue." Never stop mid-step. Never leave the test suite red.

## ROLE
You are a senior engineer generalising a safety-critical traffic control system from an idealised 6×6 grid to a REAL city road network imported from OpenStreetMap. Real data is messy: irregular junctions, one-way streets, curved roads, missing tags. Make the domain model general, import the real map reproducibly, generate SAFE signal phases automatically, and prove the whole system still works — and is still safe — on BOTH the real city and the grid, WITHOUT ever breaking the architecture along the way. Explain key decisions briefly.

## CONTEXT
Project: "Corridor Control" — Emergency Traffic Corridor. Requirements: `docs/PRD.md`.
At the start of EVERY session read: `CLAUDE.md`, `docs/progress/phase-6.md` (if it exists), `docs/decisions.md`, and the code relevant to the current stage.

Phases 0–5 are complete on the 6×6 grid: deterministic simulation (SimClock, seeded RNG streams, fixed tick order, state hash); intersection controllers with local guards (PREEMPT / RENEW / RELEASE, local max hold, hold expiry, FLASHING_RED, pedestrian clearance); Safety Supervisor + fail-closed SafeSignalLink gate + HMAC approvals; InvariantChecker; event bus; ingestion gateway; World State; vehicle tracker; routing (edge-based A*, cost model, time-aware costs, Yen's alternatives, hysteresis, guidance); CentralSystem; `runtime` composition root.

Some Phase 6 work may already exist (e.g. conflict geometry and shared enums). Find it, record it in the progress file, verify it against this spec, and complete it — do not rewrite working code.

PRODUCT DECISION: A real area of Chennai, India becomes the PRIMARY city. The 6×6 grid stays as the SECONDARY city for fast tests, debugging, and proof that the system is general. The SAME control code runs on both, selected by configuration only.

PHASE NUMBERING: This is the new Phase 6. Corridor Control becomes Phase 7, Dynamic Events Phase 8, and so on — 16 phases total (0–15).

## NON-NEGOTIABLE RULES
1. All rules in CLAUDE.md apply: determinism (no wall clock, no global random, deterministic iteration and tie-breaking), module boundaries, commands only via SafeSignalLink, no magic numbers (settings.yaml + typed Settings), ADRs in docs/decisions.md, Conventional Commits.
2. The FULL test suite must pass after EVERY commit. Never commit a broken state.
3. NEVER weaken a safety test. Safety tests may change HOW they build junctions, never WHAT they assert. If a safety test fails, the new code is wrong.
4. Map import is REPRODUCIBLE and OFFLINE at runtime: download once, cache in the repo, build deterministically from the cache. Only `backend/scripts/fetch_osm.py` may access the network.
5. No hard-coded Chennai IDs or coordinates in control code. City-specific data lives in city config files.
6. OpenStreetMap data is © OpenStreetMap contributors (ODbL). Store attribution with the data and show it wherever the map is displayed.
7. Refactor strategy is EXPAND → MIGRATE → CONTRACT: add the new model alongside the old, migrate one module at a time with the suite green after each, then delete the old model. Record this as an ADR.

## SESSION PROTOCOL (also add to CLAUDE.md under "Multi-session work")
- Start: read CLAUDE.md, the progress file, and this stage's section below.
- Work only on the current stage; follow the stage order.
- Small commits; full suite green after each.
- End of session: update the progress file (status, what was done, commits, test results, exact next step) and commit it.
- Never skip ⛔ Checkpoint B. Never skip Gate A conditions.

---

## STEP 0 — BOOTSTRAP (first run only)
1. Create and switch to branch `phase-6-real-city`; commit any uncommitted work first.
2. Save THIS ENTIRE PROMPT verbatim to `docs/specs/phase-6-real-city.md` (the reference spec for all sessions).
3. Create `docs/progress/phase-6.md`:
Phase 6 Progress — Real City (Chennai)

Spec: docs/specs/phase-6-real-city.md

Stage	Scope	Status
6.1	Expand: general model, conflicts, phasing, general grid, loader, adapter	NOT STARTED
6.2	Migrate + contract → Gate A	NOT STARTED
6.3	OSM fetch + Chennai build + map viewer → ⛔ Checkpoint B	NOT STARTED
6.4	Indian realism, geometry tracking, UTM, calibration, history	NOT STARTED
6.5	Both-city tests, performance, reports, docs, merge	NOT STARTED
Already done
Session log
Open questions / decisions pending
Next step
4. Fill "Already done" accurately from the current code.
5. Add the SESSION PROTOCOL to CLAUDE.md.
6. Add dependencies: backend `osmnx`, `networkx` (importer only), `shapely>=2`, `pyproj`, `geopandas`; frontend `maplibre-gl`. Record versions in an ADR.
7. Commit. Continue to Stage 6.1 in the same session.

---

## STAGE 6.1 — EXPAND (new model alongside the old; no behaviour change)

### 6.1.1 New model (`backend/core/junctions.py`, NEW module; old classes untouched for now)
- `Approach`: id, intersection_id, incoming segment id, `bearing_deg` (direction of travel arriving), lanes.
- `Exit`: id, intersection_id, outgoing segment id, `bearing_deg`.
- `Movement`: id, `approach_id`, `exit_id`, `turn_type` ∈ {THROUGH, NEAR_SIDE, FAR_SIDE, U_TURN}, from the angle between approach and exit bearings (configurable thresholds). NEAR_SIDE / FAR_SIDE instead of left/right so driving side is configuration. India drives on the LEFT: near-side = left turn, far-side = right turn.
- `Phase`: id, set of movement ids green together, timings.
- `ConflictMatrix` over MOVEMENTS: symmetric `conflicts(a, b)`, `is_compatible(set)`.
- `IntersectionKind`: SIGNALISED, UNSIGNALISED, ROUNDABOUT.
- `RoadSegment` additions: `geometry` (list of (x, y) metres), `geometry_latlng`, `road_class`, `oneway`, `name`.

### 6.1.2 Geometric conflict detection (`backend/core/conflicts.py`)
- Place approach entry points and exit points on a circle around the junction by bearing. A movement is a chord from approach point to exit point.
- CONFLICT if chords cross (endpoints interleave on the circle), OR two movements from different approaches use the same exit (merge).
- Exceptions configurable: "free near-side turn" (common in Indian cities) per junction class.
- U-turns conflict with all movements except those from the same approach (configurable; default disallowed at signalised junctions).
- ADR + diagram in design.md: "conflicts from circular chord-crossing test".

### 6.1.3 Automatic phase generation (`backend/core/phasing.py`)
- PAIRED: regular 4-way junctions whose opposing approaches align within a bearing tolerance → pair opposing approaches (through + near-side); far-side turns only if conflict-free, else their own phase.
- SPLIT: one approach per phase (all its movements together) — common at Indian junctions; still check merges.
- Choose paired if valid and regular, else split; per-junction override in city config.
- Every phase must pass `is_compatible`; every movement must be served by ≥ 1 phase; fail loudly otherwise.
- Green splits proportional to approach importance (road class, lanes) within min/max green; cycle length within configured bounds (e.g. 60–150 s).
- ADR: real Chennai signal timings are not public, so plans are generated from geometry and road class.

### 6.1.4 General-format grid + loader
- Grid generator emits a NEW general-format file `config/cities/grid.city.json` (approaches, exits, movements, phases from 6.1.3, straight-line geometry). Keep the old grid file working.
- City loader validates the general format: duplicate IDs, unknown references, conflict matrix symmetric and irreflexive, every phase conflict-free, every movement served, hospitals on known nodes, strongly connected graph, timings within settings.

### 6.1.5 Temporary adapter (`backend/core/legacy_adapter.py`)
- Converts a general-model grid city into the OLD model shape used by the current system.
- Test: adapter output for the grid equals the old grid exactly (intersections, segments, phases, conflicts).

### 6.1.6 Tests
- `test_conflicts.py`: 4-way, T, Y, 5-way, skewed 4-way, one-way approaches; free near-side config; U-turn rule; Hypothesis over random junction shapes → symmetric, irreflexive.
- `test_phasing.py`: phases conflict-free; every movement served; paired vs split selection; splits/cycle bounds; Hypothesis over random junction shapes → always valid.
- Loader validation tests; adapter equivalence test.

### 6.1.7 Stage done when
- New model, conflicts, phasing, general grid, loader and adapter complete and tested; ENTIRE existing suite still green; no changes to controller/safety/sim/tracking/routing.
- ADRs: general junction model; near-side/far-side naming; chord-crossing conflicts; paired vs split phasing; generated signal plans; expand-migrate-contract.
- Progress file updated. Continue to 6.2.

---

## STAGE 6.2 — MIGRATE + CONTRACT (ends at Gate A)

Migrate ONE module per commit, full suite green after each:
1. `controller/`: phases as movement sets; `red_since` per APPROACH; local self-check on movement conflicts.
2. `safety/`: supervisor and gate validate phases and holds on movements/approaches.
3. `chaos/invariants.py`: INV-01 on conflicting MOVEMENTS; INV-06 per approach.
4. Phase 3 property-based and stateful safety tests: run on the general model across the grid PLUS synthetic T, Y, 5-way and skewed junctions (paired and split). Change only how junctions are built — never the assertions.
5. `sim/`: queue per approach; exit choice by movement turning probabilities (by turn type and exit road class); UNSIGNALISED give-way capacity model (major road by class has priority; minor approaches discharge within configurable gap-acceptance capacity); ROUNDABOUT = unsignalised with entry capacity.
6. `state/` and `tracking/`: approach-based queue estimates; stop-line positions per approach.
7. `routing/`: turn types/penalties from `Movement.turn_type`; banned movements; one-way respected; give-way delay at unsignalised junctions; required movement at a junction = (arriving approach → leaving exit).
8. CONTRACT: default grid uses the general-format file; delete old model classes, old loader path, and `legacy_adapter.py`; boundary test forbids the old names.

### GATE A (automatic — verify ALL; record results in the progress file)
- [ ] Full test suite green.
- [ ] Phase 3 safety tests run on the general model across grid + T + Y + 5-way + skewed junctions, with the `thorough` Hypothesis profile (≥ 10,000 examples), zero violations.
- [ ] `git diff` of every safety test file shows NO changed, removed or loosened assertions (only junction construction changes). List each changed safety test file and what changed.
- [ ] `run_sim` grid baseline (same seed as the Phase 2 example) is essentially unchanged (report before/after travel time, stops, average delay; explain any difference).
- [ ] Old model and adapter fully removed; boundary test passes.
If ALL pass → record "Gate A PASSED" and continue to 6.3 in the same session.
If ANY fails → STOP and report exactly what failed and your proposed fix. Wait for my decision.

---

## STAGE 6.3 — OPENSTREETMAP FETCH + CHENNAI BUILD (ends at ⛔ Checkpoint B)

### 6.3.1 City configs
`config/cities/chennai_central.yaml`:
```yaml
name: chennai_central
display_name: "Chennai — Park Town / Egmore"
center:
  geocode: "Rajiv Gandhi Government General Hospital, Chennai, India"
  # or explicit: lat: <value>, lng: <value>
radius_m: 1500
network_type: drive
keep_road_classes: [trunk, primary, secondary, tertiary, unclassified, residential]
consolidate_tolerance_m: 15
signal_snap_m: 30
driving_side: left
utm_epsg: 32644
force_signalised: []
force_unsignalised: []
hospitals:
  mode: osm_query
  require_names: []
  max: 3
reference_trip:
  entry: AUTO_FARTHEST_MAJOR_BOUNDARY
  hospital: NEAREST_MAJOR_HOSPITAL_TO_CENTER
attribution: "© OpenStreetMap contributors"
```
`config/cities/grid.yaml` points to the grid generator. Setting `active_city` (keep `grid` as default until Checkpoint B is approved).

### 6.3.2 `backend/scripts/fetch_osm.py --city chennai_central` (run once)
- OSMnx: geocode center (or explicit lat/lng); download drivable network within radius, `highway=traffic_signals` nodes, `amenity=hospital` features, `junction=roundabout` ways.
- Save to `data/osm/chennai_central/`: GraphML, GeoJSON (signals, hospitals, roundabouts), `metadata.json` (query, timestamp, library versions, attribution).
- Respect Nominatim/Overpass policies (single request, cached, identifying user agent). Commit the cache.

### 6.3.3 `backend/scripts/build_city.py --city chennai_central` (deterministic)
1. Load cache; project to UTM 44N (EPSG:32644); keep lat/lng for display.
2. Filter road classes; keep the LARGEST STRONGLY CONNECTED component; report what was dropped.
3. Consolidate complex junctions (`consolidate_intersections`, tolerance from config); report before/after.
4. Segment attributes: length from geometry; lanes from tag else road-class default; speed from `maxspeed` else road-class default (realistic Chennai urban: trunk/primary 40 km/h, secondary 30, tertiary 25, residential 20 — configurable); capacity per lane by class; oneway; name; geometry.
5. Snap `traffic_signals` to consolidated junctions within `signal_snap_m` → SIGNALISED; roundabouts → ROUNDABOUT; others → UNSIGNALISED; apply overrides; report counts.
6. Approaches, exits, movements, conflicts, phases (Stage 6.1 code).
7. Hospitals → nearest network node; keep up to `max`.
8. Resolve reference trip (entry, hospital); store in city.json.
9. Stable deterministic IDs (from sorted OSM node IDs); human-readable labels from street names (e.g. "Poonamallee High Rd × EVR Periyar Salai").
10. Validate with the loader; write `config/cities/chennai_central.city.json`.
- Build report → `docs/city_build_report_chennai.txt` (nodes, segments, signalised/unsignalised/roundabouts, phases-per-junction histogram, dropped components, defaults used). Same input → byte-identical output.

### 6.3.4 Minimal map viewer
`/dev/map` with a city switcher. Chennai: OpenStreetMap raster tiles (MapLibre GL JS) + OUR road geometry as a GeoJSON overlay; signal markers styled by kind; hospitals; reference-trip entry and hospital. If tiles fail, still draw our roads on a plain background. Show "© OpenStreetMap contributors". A static view of the built city is enough at this point.

### 6.3.5 Tests
`test_osm_build.py`: deterministic (byte-identical); strongly connected; stable IDs; signals snapped; overrides applied; hospitals mapped; reference trip resolved; validation passes; uses ONLY the cache (block sockets in the test).

### 6.3.6 ADRs
OSM caching and reproducibility; junction consolidation; signal snapping and manual overrides; road-class defaults; UTM projection.

### ⛔ CHECKPOINT B — STOP AND WAIT FOR ME
Report:
1. Build report summary.
2. Selected hospitals (names) and reference trip (entry and hospital, with street names).
3. How to open the map viewer (exact commands and URL).
4. Anything suspicious (large dropped components, few signals on main roads, odd hospital choice).
5. Updated progress file.
Then STOP. I will reply either "approved, continue to Stage 6.4" or with changes (center, radius, force_signalised / force_unsignalised by street names, hospital names). For changes: update config, re-fetch only if center/radius changed, rebuild, report again, and wait again.

---

## STAGE 6.4 — INDIAN REALISM + GEOMETRY-AWARE TRACKING + CALIBRATION

1. Indian traffic realism: vehicle mix (cars, two-wheelers, autos, buses) with PCU factors; saturation headway applied per PCU; Chennai `yield_factor` slightly higher; demand by road class with the peak profile. All values configurable; cite the source used (e.g. Indian Roads Congress guidance) or state clearly they are assumptions. State honestly in design.md that real-time traffic counts are not public, so demand is simulated on a real layout.
2. Coordinates: UTM internally; lat/lng only at the edges via a per-city pyproj converter (grid keeps a synthetic anchor). Replace the old lat/lng helper.
3. Geometry tracking: map matching onto segment POLYLINES with a shapely `STRtree`; along-route distance on polylines; stop-line = end of approach geometry minus configurable setback; simulated ambulance moves along polylines and emits GPS convertible to lat/lng.
4. Run simulation + central system live on Chennai end to end (traffic, signals, ambulance on its planned route, tracking, routing).
5. Historical profiles per city → `data/historical_profile_<city>.json`.
6. Calibration (peak, 10 min, Chennai): queues mainly on major roads near signals; no city-wide gridlock; no empty city. Tune demand per road class; record values in an ADR.
7. Dev map: live simulation on Chennai (traffic colours, live signals, ambulance, route).
8. Tests: `test_geometry_tracking.py` (curved-road map matching, polyline distances, lat/lng ↔ UTM round trip within centimetres); `test_unsignalised.py` (give-way model, routing delay, uncontrollable); determinism on Chennai (same seed → same state hash).
9. Switch default `active_city` to `chennai_central`.
10. ADRs: PCU and India-specific assumptions; UTM coordinate policy; calibration values.
Stage done when everything above works, the full suite is green, and the progress file is updated. Continue to 6.5.

---

## STAGE 6.5 — BOTH-CITY TESTS, PERFORMANCE, REPORTS, DOCS, MERGE

1. Parameterise ALL existing safety, simulation, tracking and routing integration tests over `city ∈ {grid, chennai_central}`. Phase 3 property and stateful safety tests pass on both with zero violations.
2. Performance (`slow`) on Chennai: 10 simulated minutes within acceptable wall time (report; optimise if > 30 s); replan p95 < 300 ms; central step within budget.
3. Run `routing_report` and `tracking_report` on Chennai; save `docs/routing_report_chennai_example.txt` and `docs/tracking_report_chennai_example.txt`.
4. Finish the dev map: city switcher, live overlay, route and alternatives, signal kinds, hospitals, tile-failure fallback, attribution, labelled "Developer view".
5. Boundary test: only `scripts/fetch_osm.py` may use the network; importer code never imported by control modules; old model names cannot reappear.
6. Docs:
   - `docs/design.md`: Indian junction realities (irregular shapes, split phasing, mixed traffic, left-hand driving, incomplete signal data); city pipeline diagram (fetch → cache → build → validate → city.json → same control system); primary vs secondary city; conflict detection and phasing reasoning; expand-migrate-contract; limitations (generated signal timings, simulated demand, incomplete OSM tags).
   - `docs/decisions.md`: confirm every Phase 6 ADR exists.
   - `CLAUDE.md`: "Current phase: Phase 6 (Real City) complete", city config format, `fetch_osm` / `build_city` commands, switching cities, 16-phase numbering.
   - `docs/PRD.md`: add "Revision 1.2 — Chennai primary city" note at the top; update the Delivery Phases table to the 16-phase plan (do not rewrite other sections).
7. Merge `phase-6-real-city` into `main` once everything is green.

---

## PHASE 6 DONE WHEN (all true)
- Gate A passed and Checkpoint B approved.
- Cached OSM extract committed; deterministic, validated Chennai city with build report.
- All safety, simulation, tracking and routing tests pass on BOTH cities, zero invariant violations.
- Chennai reports saved; performance targets met or documented.
- Dev map shows real Chennai streets with live traffic, signals, ambulance and route; works without tiles.
- Default `active_city` = `chennai_central`; switching to `grid` needs no code changes.
- All ADRs, design.md, CLAUDE.md and PRD updates done; branch merged.
- Progress file shows every stage DONE.

## FINAL REPORT (when Phase 6 is complete)
Summary; test results on both cities (incl. Hypothesis example counts); Gate A results; Chennai build report highlights; calibration values; performance numbers; report highlights; deviations from this spec with reasons; and "Phase 6 complete — ready for Phase 7 (Corridor Control)".
