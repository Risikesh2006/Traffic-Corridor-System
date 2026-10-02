"""Generates the default 6x6 grid city config (PRD FR-SIM-02, §19.3).

Run: python scripts/generate_city.py
Writes config/city.json.
"""

from __future__ import annotations

import json
from pathlib import Path

COLS = ["A", "B", "C", "D", "E", "F"]
ROWS = [1, 2, 3, 4, 5, 6]
SPACING_M = 250.0

# Standard two-phase signal: N-S through/left vs E-W through/left.
MOVEMENTS = ["N", "S", "E", "W"]
CONFLICTS = [
    ("N", "E"), ("N", "W"), ("S", "E"), ("S", "W"),
    ("E", "N"), ("E", "S"), ("W", "N"), ("W", "S"),
]
PHASES = [
    {
        "id": "NS",
        "movements": ["N", "S"],
        "min_green_s": 10,
        "amber_s": 4,
        "all_red_s": 2,
        "ped_clearance_s": 7,
    },
    {
        "id": "EW",
        "movements": ["E", "W"],
        "min_green_s": 10,
        "amber_s": 4,
        "all_red_s": 2,
        "ped_clearance_s": 7,
    },
]


def node_id(col: str, row: int) -> str:
    return f"{col}{row}"


def build_intersections() -> list[dict]:
    nodes = []
    for col_idx, col in enumerate(COLS):
        for row in ROWS:
            approaches = []
            if row > 1:
                approaches.append({"id": f"{col}{row}-N", "intersection_id": node_id(col, row), "direction": "N", "lanes": 2})
            if row < max(ROWS):
                approaches.append({"id": f"{col}{row}-S", "intersection_id": node_id(col, row), "direction": "S", "lanes": 2})
            if col_idx < len(COLS) - 1:
                approaches.append({"id": f"{col}{row}-E", "intersection_id": node_id(col, row), "direction": "E", "lanes": 2})
            if col_idx > 0:
                approaches.append({"id": f"{col}{row}-W", "intersection_id": node_id(col, row), "direction": "W", "lanes": 2})
            nodes.append(
                {
                    "id": node_id(col, row),
                    "x": col_idx * SPACING_M,
                    "y": (row - 1) * SPACING_M,
                    "approaches": approaches,
                    "phases": PHASES,
                    "conflict_matrix": {"movements": MOVEMENTS, "conflicts": CONFLICTS},
                }
            )
    return nodes


def build_roads() -> list[dict]:
    roads = []
    free_flow = 13.4  # ~48 km/h
    capacity = 1800.0  # veh/h per direction baseline

    def add_road(a: str, b: str, length_m: float, lanes: int = 2, ff: float = free_flow, cap: float = capacity):
        roads.append(
            {
                "id": f"{a}-{b}",
                "from_node": a,
                "to_node": b,
                "length_m": length_m,
                "lanes": lanes,
                "free_flow_speed_mps": ff,
                "capacity_vph": cap,
                "status": "OPEN",
                "density_estimate": 0.0,
                "confidence": 0.0,
            }
        )

    # Grid edges, bidirectional.
    for col_idx, col in enumerate(COLS):
        for row in ROWS:
            if row < max(ROWS):
                a, b = node_id(col, row), node_id(col, row + 1)
                add_road(a, b, SPACING_M)
                add_road(b, a, SPACING_M)
            if col_idx < len(COLS) - 1:
                a, b = node_id(col, row), node_id(COLS[col_idx + 1], row)
                add_road(a, b, SPACING_M)
                add_road(b, a, SPACING_M)

    # Two diagonal arterials (faster, higher capacity), connecting corners through the middle.
    diag_1 = [("A1", "C3"), ("C3", "E5"), ("E5", "F6")]
    diag_2 = [("A6", "C4"), ("C4", "E2"), ("E2", "F1")]
    for a, b in diag_1 + diag_2:
        length = ((COLS.index(a[0]) - COLS.index(b[0])) ** 2 + (int(a[1:]) - int(b[1:])) ** 2) ** 0.5 * SPACING_M
        add_road(a, b, length, lanes=3, ff=16.7, cap=2400.0)
        add_road(b, a, length, lanes=3, ff=16.7, cap=2400.0)

    return roads


def build_hospitals() -> list[dict]:
    return [{"id": "HOSP-F1", "node_id": "F1", "name": "City General Hospital", "status": "AVAILABLE"}]


def main() -> None:
    city = {
        "intersections": build_intersections(),
        "roads": build_roads(),
        "hospitals": build_hospitals(),
    }
    out_path = Path(__file__).resolve().parent.parent / "config" / "city.json"
    out_path.write_text(json.dumps(city, indent=2), encoding="utf-8")
    print(f"Wrote {out_path} with {len(city['intersections'])} intersections, "
          f"{len(city['roads'])} roads, {len(city['hospitals'])} hospitals.")


if __name__ == "__main__":
    main()
