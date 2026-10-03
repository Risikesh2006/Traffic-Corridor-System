# Corridor Control

Real-time ambulance signal-preemption system — a digital twin: real control
software and real users (operators, ambulance crews, hospital staff) working
on a simulated city. Only the physical world is simulated; all decision
logic, safety enforcement, and UIs are real.

Full requirements: [`docs/PRD.md`](docs/PRD.md). Architecture notes and ADRs:
[`docs/design.md`](docs/design.md), [`docs/decisions.md`](docs/decisions.md).
Guide for AI coding sessions: [`CLAUDE.md`](CLAUDE.md).

## Status

Phase 5 complete: route planning. The ambulance is dispatched to a hospital
(or the best available one), gets an edge-based A* route over a BPR cost model
that charges queues, signal delay, turns, stale data and uncontrollable
junctions, and is replanned under explicit stability rules (hysteresis, dwell,
commitment zone). Blocked routes switch at once to a precomputed, diverse backup.
Every decision is recorded with an explanation. Measured in the evaluation
scripts: a closed road is avoided one step after it is applied, but the cost
model does not yet rank routes reliably against trip outcomes (see
`docs/routing_report_example.txt` and `docs/design.md`).

Phase 4 complete: the perception layer — ingestion with deduplication and
clock-skew handling, a world state with confidence and historical priors, a
vehicle tracker with dead reckoning and ETAs, and a central orchestrator.

Phase 3 complete: the two-layer safety architecture — a central
`SafetySupervisor` (policy) and a local controller guard (exact timing),
connected only through a fail-closed `SafeSignalLink` gate with HMAC-signed
approvals, plus an independent `InvariantChecker` that never trusts either
layer's own verdict. Proven with property-based (10,000 examples), stateful,
full-simulation, and mutation testing — see `docs/safety_report_example.txt`
for a 20-seed evidence run (0 violations). There is still no routing or
corridor preemption (Phase 6 onwards); commands are still sent manually in
tests and scripts. See `docs/PRD.md`
§22 (Delivery Phases) for the full build plan.

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

### Running the safety evidence report
```bash
# from the repo root, with the backend venv active
python -m backend.scripts.safety_report --seeds 20 --minutes 10
```
Runs the real simulation with a chaotic commander (random PREEMPT/RENEW/
RELEASE through the safety gate) and a faulty network, across N seeds.
Prints command/rejection counts, invariant violations (must be 0), and
supervisor validation latency. Sample output: `docs/safety_report_example.txt`.

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
