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
2. **HARDWARE BOUNDARY:** Control-system modules (state, tracking, routing,
   corridor, safety, controller, health, events) must NEVER import from
   `backend/sim/`. They talk to the physical world only through the
   interfaces in `backend/core/interfaces.py`.
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
  core/        domain models, clock, rng, config, interfaces, events base
  sim/         simulator (city, traffic, ambulance, sensors) — empty until Phase 2
  safety/      safety supervisor — empty until Phase 3
  controller/  intersection controller state machine — empty until Phase 3
  state/       world state, freshness, historical profiles — empty until Phase 4
  tracking/    vehicle tracker, ETA, dead reckoning — empty until Phase 4
  routing/     cost model, A*, Yen's — empty until Phase 5
  corridor/    preemption timing, leases, neighbour coordination — empty until Phase 6
  events/      event bus, event store, replay — empty until Phase 8
  health/      heartbeats, mode manager — empty until Phase 8
  chaos/       failure injection, invariant checker — empty until Phase 12
  api/         FastAPI app (REST, WebSocket, auth)
  scripts/     CLI scripts (generate_city, city_summary)
  tests/       pytest suite, including the import-boundary architecture test
frontend/      Next.js (App Router, TypeScript, Tailwind) — operator/crew/hospital UIs
config/        city.json (road network) and settings.yaml (tunable parameters)
docs/
  PRD.md         the full requirements document — do not modify
  design.md      architecture, domain model, failure handling, trade-offs
  decisions.md   ADR log
docker-compose.yml
```

## Current phase: Phase 1 complete
