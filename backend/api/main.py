"""FastAPI application entrypoint.

Phase 0 goal: a running backend with a health endpoint so
`docker compose up` can be verified end-to-end. Full routers (sim, trips,
incidents, corridor, chaos, ws, ...) land in later phases per PRD §15/§16.

Phase 2 adds a dev-only simulation snapshot endpoint (`DEV_SIM=1`) feeding
the developer map at `frontend/app/dev/map`.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from core.city_loader import load_city
from core.config import load_settings
from core.models import Priority
from sim.engine import Simulation
from sim.runner import RealtimeRunner

APP_VERSION = "0.1.0"

# Local dev: backend/ sits next to config/. Docker: CONFIG_DIR is set explicitly.
_DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", _DEFAULT_CONFIG_DIR))

DEV_ROUTE = ["A6", "B6", "B5", "C5", "C4", "D4", "D3", "E3", "E2", "F2", "F1"]

_dev_simulation: Simulation | None = None
_dev_runner: RealtimeRunner | None = None
_dev_task: asyncio.Task[None] | None = None


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global _dev_simulation, _dev_runner, _dev_task
    if os.environ.get("DEV_SIM") == "1":
        settings = load_settings(CONFIG_DIR / "settings.yaml")
        settings = settings.model_copy(update={"demand_peak_multiplier": 2.0})
        city = load_city(CONFIG_DIR / "city.json", settings)
        _dev_simulation = Simulation(city, settings, seed=settings.default_seed)
        _dev_simulation.add_ambulance("AMB-1", DEV_ROUTE, Priority.P1)
        _dev_runner = RealtimeRunner(_dev_simulation, speed_multiplier=1.0)
        _dev_task = asyncio.create_task(_dev_runner.run())

    yield

    if _dev_runner is not None:
        _dev_runner.stop()
    if _dev_task is not None:
        _dev_task.cancel()


app = FastAPI(title="Corridor Control API", version=APP_VERSION, lifespan=lifespan)

_frontend_origin = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_frontend_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": APP_VERSION}


@app.get("/api/v1/_debug/city-summary")
def city_summary() -> dict[str, object]:
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    city = load_city(CONFIG_DIR / "city.json", settings)
    return {
        "intersections": len(city.intersections),
        "roads": len(city.roads),
        "hospitals": [h.id for h in city.hospitals],
        "min_green_s": settings.min_green_s,
    }


@app.get("/api/v1/sim/snapshot")
def sim_snapshot() -> dict[str, object]:
    if _dev_simulation is None:
        raise HTTPException(
            status_code=503,
            detail="dev simulation not running — start the backend with DEV_SIM=1",
        )
    return _dev_simulation.snapshot()
