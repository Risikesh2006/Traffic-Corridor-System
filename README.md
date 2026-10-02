# Corridor Control

Real-time ambulance signal-preemption system — a digital twin: real control
software and real users (operators, ambulance crews, hospital staff) working
on a simulated city. Only the physical world is simulated; all decision
logic, safety enforcement, and UIs are real.

Full requirements: [`docs/PRD.md`](docs/PRD.md). Architecture notes and ADRs:
[`docs/design.md`](docs/design.md), [`docs/decisions.md`](docs/decisions.md).
Guide for AI coding sessions: [`CLAUDE.md`](CLAUDE.md).

## Status

Phase 2 complete: a deterministic, headless-runnable simulation — background
traffic, signal cycling, a simulated ambulance, sensors, GPS feed, and a
faulty network, all composed in `backend/sim/engine.py`. No routing,
preemption, or safety supervisor exists yet (Phases 3+); the ambulance just
follows a manually supplied route and obeys normal signals. See
`docs/baseline_example.txt` for a sample run and `docs/PRD.md` §22
(Delivery Phases) for the full build plan.

## Repository structure

```
backend/    Python 3.12 / FastAPI control system (see backend/ for module layout)
frontend/   Next.js (App Router, TypeScript, Tailwind) operator/crew/hospital UIs
config/     city.json (road network) and settings.yaml (tunable parameters)
docs/       PRD, design notes, ADRs
```

## Running locally

### Backend
```bash
cd backend
python -m venv .venv
.venv/Scripts/activate        # .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"
pytest                        # run the test suite
ruff check .                  # lint
mypy core                     # strict type-check (core/ only)
uvicorn api.main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
cp .env.example .env.local    # set NEXT_PUBLIC_API_URL if backend isn't on :8000
npm run dev                   # http://localhost:3000
```

### Regenerating the default city
```bash
cd backend
python scripts/generate_city.py
```
Produces `config/city.json`: a 6x6 grid (A1-F6) with arterials on row 4 and
column D, and hospitals H1 (F1) and H2 (F6). See ADR-003 in
`docs/decisions.md` for why grid arterials were used instead of the PRD's
diagonal arterials.

```bash
# from the repo root, with the backend venv active
python -m backend.scripts.city_summary
```
Validates and prints a summary of the current `config/city.json`.

### Running a headless simulation baseline
```bash
# from the repo root, with the backend venv active
python -m backend.scripts.run_sim --seed 4417 --minutes 10 --peak \
    --route A6,B6,B5,C5,C4,D4,D3,E3,E2,F2,F1
```
Prints vehicle conservation, queue stats, ambulance stop/travel-time
metrics, and a `state_hash` for determinism checks. Sample output:
`docs/baseline_example.txt`.

### Developer map (live debug view)
```bash
# backend, with a live simulation running in-process:
cd backend && DEV_SIM=1 uvicorn api.main:app --reload --port 8000
# frontend, separately:
cd frontend && npm run dev
```
Then open `http://localhost:3000/dev/map` — polls `/sim/snapshot` every
500ms and draws the grid, signal states, queues, and the ambulance. This is
a development debug view, not the final operator dashboard.

### Docker
```bash
docker compose up --build
```
Backend on `:8000`, frontend on `:3000`.

## Design principle

The central system decides priority. Each intersection guarantees its own
safety. A crash, network loss or bad data may make the ambulance slower, but
can never make an intersection unsafe.
