"""FastAPI application entrypoint.

Phase 0 goal: a running backend with a health endpoint so
`docker compose up` can be verified end-to-end. Full routers (sim, trips,
incidents, corridor, chaos, ws, ...) land in later phases per PRD §15/§16.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.city_loader import load_city
from core.config import load_settings

APP_VERSION = "0.1.0"

app = FastAPI(title="Corridor Control API", version=APP_VERSION)

_frontend_origin = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_frontend_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Local dev: backend/ sits next to config/. Docker: CONFIG_DIR is set explicitly.
_DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", _DEFAULT_CONFIG_DIR))


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
