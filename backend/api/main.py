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
from core.models import Priority, SensorMetric
from runtime.composition import Runtime, build_runtime
from sim.runner import RealtimeRunner

APP_VERSION = "0.1.0"

# Local dev: backend/ sits next to config/. Docker: CONFIG_DIR is set explicitly.
_DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", _DEFAULT_CONFIG_DIR))


_dev_runtime: Runtime | None = None
_dev_runner: RealtimeRunner | None = None
_dev_task: asyncio.Task[None] | None = None


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global _dev_runtime, _dev_runner, _dev_task
    if os.environ.get("DEV_SIM") == "1":
        _dev_runtime = build_runtime(peak_multiplier=2.0)
        _dev_runtime.dispatch("AMB-1", Priority.P1, None)
        _dev_runner = RealtimeRunner(_dev_runtime, speed_multiplier=1.0)
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
    if _dev_runtime is None:
        raise HTTPException(
            status_code=503,
            detail="dev simulation not running — start the backend with DEV_SIM=1",
        )
    now = _dev_runtime.sim.clock.now()
    snapshot = _dev_runtime.sim.snapshot()
    snapshot["tracked"] = _tracked_view(now)
    snapshot["freshness"] = _dev_runtime.central.world.freshness_summary(now)
    return snapshot


def _tracked_view(now: int) -> dict[str, object]:
    central = _dev_runtime.central  # type: ignore[union-attr]
    out: dict[str, object] = {}
    for vehicle_id in central.tracker.tracked_ids():
        snap = central.tracker.snapshot(vehicle_id, now)
        out[vehicle_id] = {
            "x": snap.x,
            "y": snap.y,
            "s_m": snap.s_m,
            "uncertainty_m": snap.uncertainty_m,
            "position_source": snap.position_source.value,
            "last_fix_age_ms": snap.last_fix_age_ms,
            "etas": [
                {
                    "intersection_id": e.intersection_id,
                    "eta_expected_ms": e.eta_expected_ms,
                    "eta_low_ms": e.eta_low_ms,
                    "eta_high_ms": e.eta_high_ms,
                }
                for e in snap.etas[:5]
            ],
        }
    stale = {
        road.id: central.world.estimate(road.id, SensorMetric.DENSITY, now).stale
        for road in _dev_runtime.sim.city.roads  # type: ignore[union-attr]
    }
    out["stale_segments"] = sorted(sid for sid, is_stale in stale.items() if is_stale)
    return out
