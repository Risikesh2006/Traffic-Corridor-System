"""FastAPI application entrypoint.

Phase 0 goal: a running backend with a health endpoint so `docker compose up`
can be verified. Full routers (sim, trips, incidents, corridor, chaos, ws, ...)
land in later phases per PRD §15/§16.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import CityConfig, Settings

app = FastAPI(title="Corridor Control API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Local dev: backend/ sits next to config/. Docker: CONFIG_DIR is set explicitly.
_DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", _DEFAULT_CONFIG_DIR))


@app.get("/api/v1/health")
def health() -> dict:
    return {"status": "ok", "mode": "NORMAL"}


@app.get("/api/v1/_debug/city-summary")
def city_summary() -> dict:
    city = CityConfig.load(CONFIG_DIR / "city.json")
    settings = Settings.load(CONFIG_DIR / "settings.yaml")
    return {
        "intersections": len(city.intersections),
        "roads": len(city.roads),
        "hospitals": [h.id for h in city.hospitals],
        "min_green_s": settings.min_green_s,
    }
