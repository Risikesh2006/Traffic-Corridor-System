# Corridor Control — guide for Claude Code sessions

## Project summary

Corridor Control is a real-time ambulance signal-preemption system — an
Emergency Traffic Corridor built as a digital twin: real control software
and real users (operators, ambulance crews, hospital staff) working on a
simulated city. Only the physical world (roads, vehicles, signal hardware,
sensors) is simulated; all decision logic, safety enforcement, and UIs are
real. Full requirements, with IDs referenced throughout the codebase
(`FR-*`, `INV-*`, `FH-*`, `AC-*`), live in `docs/PRD.md` — read it before
making non-trivial changes.

## Non-negotiable engineering rules

1. **DETERMINISM:** No code outside `backend/core/clock.py` may call
   `time.time()`, `datetime.now()`, or similar. All time comes from the
   `Clock` interface. No code may use the global `random` module. All
   randomness comes from seeded RNG streams created by `backend/core/rng.py`.
2. **HARDWARE BOUNDARY:** `backend/controller/` is field-side code: it may
   depend on `backend/core/` only, never on `backend/sim/`. Central control
   modules (state, tracking, routing, corridor, safety, health, events) may
   import neither `backend/sim/` nor `backend/controller/` — they reach a
   controller only through the `SignalLink` interface in
   `backend/core/interfaces.py` (today implemented by `SimNetwork`). Only
   `backend/sim/` is allowed to import both `core/` and `controller/` (it
   hosts and drives the field devices). See ADR-005.
3. **TIME UNITS:** Represent simulation time as integer milliseconds
   (`SimTimeMs = int`) to avoid floating-point drift. Durations in config are
   seconds; convert at load time.
4. **TYPES:** Full type hints everywhere. Pydantic v2 models for domain
   entities and config. Use `Enum`s for all status fields.
5. **NO MAGIC NUMBERS:** Every tunable value lives in `config/settings.yaml`
   and is loaded into a typed `Settings` model.
6. **TESTS:** Every module gets pytest tests. Run them and make them pass
   before declaring a task done.
7. **SMALL COMMITS:** Commit after each logical step with Conventional
   Commit messages (e.g. `feat(core): add SimClock`, `test(city): validate
   conflict matrices`).
8. **DECISIONS LOG:** Record any design decision or deviation from the PRD
   in `docs/decisions.md` as a short ADR entry (Context / Decision /
   Consequences).

Enforcement: `backend/tests/test_boundary.py` is an automated architecture
test that scans imports and fails the build if rules 1 or 2 are violated.

## Running the test suite at different depths

```bash
pytest                      # everything, including slow-marked tests
pytest -m "not slow"        # skip slow-marked tests (e.g. the 15s sim budget check)
```
Hypothesis's `thorough` profile (>=10,000 examples) is introduced in Phase 3
for the safety-layer property/stateful tests — see that phase's test files
for how to select it.

## Commands

Run from `backend/` unless noted.

```bash
# Backend — setup
python -m venv .venv && .venv/Scripts/activate   # .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"

# Backend — run
uvicorn api.main:app --reload --port 8000

# Backend — tests / lint / types
pytest
ruff check .
mypy core                      # strict, core/ only (see pyproject.toml)

# Regenerate the default city (from backend/)
python scripts/generate_city.py
# or from the repo root:
python -m backend.scripts.city_summary

# Headless simulation baseline (from the repo root)
python -m backend.scripts.run_sim --seed 4417 --minutes 10 --peak \
    --route A6,B6,B5,C5,C4,D4,D3,E3,E2,F2,F1
# DEV_SIM=1 also starts a live simulation in the backend process itself,
# polled by the frontend's developer map at /dev/map:
DEV_SIM=1 uvicorn api.main:app --reload --port 8000

# Frontend (from frontend/)
npm install
npm run dev          # http://localhost:3000
npm run build
npm run lint

# Full stack
docker compose up --build        # backend :8000, frontend :3000
```

## Repository structure

```
backend/
  core/        domain models, clock, rng, config, interfaces, events base,
               observer protocol
  controller/  intersection controller: state machine, message handling
               (field-side code — never imports sim/)
  sim/         simulator: traffic (point-queue), demand, ambulance, sensors,
               vehicle (GPS) feed, SimNetwork, engine (tick loop), runner
               (realtime pacing), metrics
  safety/      safety supervisor — empty until Phase 3
  state/       world state, freshness, historical profiles — empty until Phase 4
  tracking/    vehicle tracker, ETA, dead reckoning — empty until Phase 4
  routing/     cost model, A*, Yen's — empty until Phase 5
  corridor/    preemption timing, leases, neighbour coordination — empty until Phase 6
  events/      event bus, event store, replay — empty until Phase 8
  health/      heartbeats, mode manager — empty until Phase 8
  chaos/       invariant checker lands in Phase 3 (reused by chaos testing);
               failure injection itself is a later phase
  api/         FastAPI app (REST, WebSocket, auth); DEV_SIM=1 runs a live
               Simulation for the developer map
  scripts/     CLI scripts (generate_city, city_summary, run_sim)
  tests/       pytest suite, including the import-boundary architecture test
frontend/      Next.js (App Router, TypeScript, Tailwind) — operator/crew/
               hospital UIs, plus /dev/map (developer-only live map debug view)
config/        city.json (road network) and settings.yaml (tunable parameters)
docs/
  PRD.md         the full requirements document — do not modify
  design.md      architecture, domain model, failure handling, trade-offs
  decisions.md   ADR log
docker-compose.yml
```

## Current phase: Phase 2 complete
