"""Generates `data/historical_profile.json` from the simulator — a
deterministic, reproducible stand-in for real historical data (ADR, Phase 4
Part C1; see docs/decisions.md).

Each seed in `history_seeds` runs headless, with no faults, for one full
simulated day starting at 00:00:00, so every 15-minute-of-day bucket gets
coverage. Every `SAMPLE_INTERVAL_S` seconds it samples each segment's TRUE
queue length and density (not noisy sensor readings: the prior should
reflect ground truth). Seeds run in parallel worker processes, each
independent and deterministic; results are merged in seed order, so the
output file is identical regardless of scheduling.

Run from the repo root:
  python -m backend.scripts.build_history
"""

from __future__ import annotations

import json
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ on path

from core.city_loader import load_city  # noqa: E402
from core.config import Settings, load_settings  # noqa: E402
from core.models import City  # noqa: E402
from sim.engine import Simulation  # noqa: E402

SAMPLE_INTERVAL_S = 60
SIM_VERSION = "phase4"
DAY_MS = 24 * 3600 * 1000

# segment_id -> bucket_index -> [(density, queue), ...]
SampleTable = dict[str, list[list[tuple[float, float]]]]


def _bucket_index(sim_time_ms: int, bucket_minutes: int) -> int:
    total_minutes = sim_time_ms // 60_000
    return int((total_minutes // bucket_minutes) % ((24 * 60) // bucket_minutes))


def _run_seed(seed: int, settings: Settings, city: City, duration_ms: int = DAY_MS) -> SampleTable:
    bucket_minutes = settings.history_bucket_minutes
    n_buckets = (24 * 60) // bucket_minutes
    table: SampleTable = {r.id: [[] for _ in range(n_buckets)] for r in city.roads}

    sim = Simulation(city, settings, seed=seed, start_wall_label="00:00:00")
    ticks_per_sample = (SAMPLE_INTERVAL_S * 1000) // settings.sim_tick_ms
    total_ticks = duration_ms // settings.sim_tick_ms

    for i in range(total_ticks):
        now = sim.step()
        if (i + 1) % ticks_per_sample == 0:
            bucket = _bucket_index(now, bucket_minutes)
            for road in city.roads:
                seg = sim.traffic.segments[road.id]
                vehicles = len(seg.queue) + len(seg.in_transit)
                km_lane = (road.length_m / 1000.0) * road.lanes
                density = vehicles / km_lane if km_lane > 0 else 0.0
                table[road.id][bucket].append((density, float(len(seg.queue))))
    return table


def _iqr(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    ordered = sorted(values)
    half = len(ordered) // 2
    q1 = statistics.median(ordered[:half])
    q3 = statistics.median(ordered[(len(ordered) + 1) // 2 :])
    return q3 - q1


def _merge(
    tables: list[SampleTable], city: City, n_buckets: int
) -> dict[str, list[dict[str, float]]]:
    merged: dict[str, list[dict[str, float]]] = {}
    for road in sorted(city.roads, key=lambda r: r.id):
        rows = []
        for b in range(n_buckets):
            samples: list[tuple[float, float]] = []
            for table in tables:
                samples.extend(table[road.id][b])
            if not samples:
                rows.append({"density": 0.0, "queue": 0.0, "dispersion": 0.0})
                continue
            densities = [s[0] for s in samples]
            queues = [s[1] for s in samples]
            rows.append(
                {
                    "density": statistics.median(densities),
                    "queue": statistics.median(queues),
                    "dispersion": _iqr(densities),
                }
            )
        merged[road.id] = rows
    return merged


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    settings = load_settings(repo_root / "config" / "settings.yaml")
    city = load_city(repo_root / "config" / "city.json", settings)
    n_buckets = (24 * 60) // settings.history_bucket_minutes

    seeds = list(settings.history_seeds)
    with ProcessPoolExecutor() as pool:
        futures = [pool.submit(_run_seed, seed, settings, city) for seed in seeds]
        tables = [f.result() for f in futures]  # collected in seed order
    print(f"simulated {len(seeds)} seeds x 24h")

    output = {
        "metadata": {
            "seeds": seeds,
            "bucket_minutes": settings.history_bucket_minutes,
            "sim_version": SIM_VERSION,
            "sample_interval_s": SAMPLE_INTERVAL_S,
        },
        "bucket_minutes": settings.history_bucket_minutes,
        "segments": _merge(tables, city, n_buckets),
    }

    out_path = repo_root / "data" / "historical_profile.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out_path} ({len(output['segments'])} segments, {n_buckets} buckets each)")


if __name__ == "__main__":
    main()
