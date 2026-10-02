"""Prints a validated summary of the current city config.

Run from the repo root: python -m backend.scripts.city_summary
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ on path

from core.city_loader import CityValidationError, load_city  # noqa: E402
from core.config import load_settings  # noqa: E402


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    config_dir = repo_root / "config"
    settings = load_settings(config_dir / "settings.yaml")

    try:
        city = load_city(config_dir / "city.json", settings)
    except CityValidationError as exc:
        print(f"Validation: FAILED — {exc}")
        raise SystemExit(1) from None

    arterial_segments = [r for r in city.roads if r.lanes > 2]
    total_length_m = sum(r.length_m for r in city.roads)

    print(f"Intersections: {len(city.intersections)}")
    print(f"Directed road segments: {len(city.roads)}")
    print(f"Arterial segments: {len(arterial_segments)}")
    print(f"Hospitals: {len(city.hospitals)}")
    print(f"Total road length: {total_length_m:,.0f} m")
    print()

    by_approach_count: dict[int, list[str]] = {}
    phase_counts = {len(n.phases) for n in city.intersections}
    for node in city.intersections:
        by_approach_count.setdefault(len(node.approaches), []).append(node.id)

    print(f"Phase count: all intersections have {sorted(phase_counts)} phases")
    print("Approach count summary (border vs interior intersections):")
    for count in sorted(by_approach_count):
        ids = by_approach_count[count]
        print(f"  {count} approaches: {len(ids)} intersections (e.g. {ids[:3]})")
    print()
    print("Validation: PASSED")


if __name__ == "__main__":
    main()
