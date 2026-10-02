# Corridor Control

Real-time ambulance signal-preemption system — a digital twin: real control
software and real users (operators, ambulance crews, hospital staff) working
on a simulated city. Only the physical world is simulated; all decision
logic, safety enforcement, and UIs are real.

Full requirements: [`prd.md`](prd.md).

## Status

Phase 0 (project setup) complete. See `prd.md` §22 (Delivery Phases) for the
full build plan.

## Repository structure

```
backend/    Python 3.12 / FastAPI control system (see backend/ for module layout)
frontend/   React / Vite / TypeScript operator, crew and hospital UIs
config/     city.json (road network) and settings.yaml (tunable parameters)
docs/       design notes and diagrams
```

## Running locally

### Backend
```bash
cd backend
python -m venv .venv
.venv/Scripts/activate        # .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"
pytest                        # run the test suite
uvicorn api.main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

### Regenerating the default city
```bash
python scripts/generate_city.py
```
Produces `config/city.json`: a 6×6 grid with two diagonal arterials and one
hospital (`HOSP-F1` at node F1), per PRD FR-SIM-02.

### Docker
```bash
docker compose up
```
Backend on `:8000`, frontend on `:4173`.

## Design principle

The central system decides priority. Each intersection guarantees its own
safety. A crash, network loss or bad data may make the ambulance slower, but
can never make an intersection unsafe.
