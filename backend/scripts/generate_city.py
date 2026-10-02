"""Generates the default 6x6 grid city config (PRD §10.1 FR-SIM-02; ADR-003).

Run: python -m backend.scripts.generate_city
Writes config/city.json.

Layout: columns A (west) .. F (east), rows 1 (north) .. 6 (south), IDs like
"C4". Block spacing 300 m. Arterials run along row 4 (A4-F4, east-west) and
column D (D1-D6, north-south) — upgraded grid corridors rather than the
PRD's diagonal arterials, so every intersection stays a standard 4-way
junction (see ADR-003 in docs/decisions.md).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ on path

from core.config import Settings, load_settings  # noqa: E402

COLS = ["A", "B", "C", "D", "E", "F"]
ROWS = [1, 2, 3, 4, 5, 6]
SPACING_M = 300.0
ARTERIAL_ROW = 4
ARTERIAL_COL = "D"

NORMAL_LANES, NORMAL_KMH, NORMAL_CAP = 2, 40.0, 1600.0
ARTERIAL_LANES, ARTERIAL_KMH, ARTERIAL_CAP = 3, 60.0, 2400.0

MOVEMENTS = ["N", "S", "E", "W"]
# Authored symmetrically and with no self-conflicts, per city_loader's checks.
CONFLICT_PAIRS = [
    ["N", "E"], ["E", "N"],
    ["N", "W"], ["W", "N"],
    ["S", "E"], ["E", "S"],
    ["S", "W"], ["W", "S"],
]

# Opposite-direction lookup: the approach direction a road from `d` feeds.
OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}


def col_index(col: str) -> int:
    return COLS.index(col)


def node_id(col: str, row: int) -> str:
    return f"{col}{row}"


def kmh_to_mps(kmh: float) -> float:
    return kmh / 3.6


def is_arterial_segment(a_col: str, a_row: int, b_col: str, b_row: int) -> bool:
    """A segment is arterial if both its endpoints lie on the row-4 corridor
    (horizontal) or the column-D corridor (vertical)."""
    if a_row == b_row == ARTERIAL_ROW:
        return True
    if a_col == b_col == ARTERIAL_COL:
        return True
    return False


def build_phases(settings: Settings) -> list[dict]:
    return [
        {
            "id": "NS_THROUGH",
            "movements": ["N", "S"],
            "min_green_ms": settings.min_green_ms,
            "amber_ms": settings.amber_ms,
            "all_red_ms": settings.all_red_ms,
            "ped_clearance_ms": settings.ped_clearance_ms,
        },
        {
            "id": "EW_THROUGH",
            "movements": ["E", "W"],
            "min_green_ms": settings.min_green_ms,
            "amber_ms": settings.amber_ms,
            "all_red_ms": settings.all_red_ms,
            "ped_clearance_ms": settings.ped_clearance_ms,
        },
    ]


def build_default_plan(col: str, row: int) -> list[dict]:
    """30s/30s baseline; favour the arterial direction 40/20 where exactly
    one arterial corridor passes through. At D4 (both corridors cross) we
    keep it even, since favouring either direction would just starve the
    other arterial — a reasonable default, not a PRD requirement."""
    on_row_arterial = row == ARTERIAL_ROW
    on_col_arterial = col == ARTERIAL_COL

    if on_row_arterial and not on_col_arterial:
        ns_green_s, ew_green_s = 20, 40
    elif on_col_arterial and not on_row_arterial:
        ns_green_s, ew_green_s = 40, 20
    else:
        ns_green_s, ew_green_s = 30, 30

    return [
        {"phase_id": "NS_THROUGH", "green_ms": ns_green_s * 1000},
        {"phase_id": "EW_THROUGH", "green_ms": ew_green_s * 1000},
    ]


def build_intersections(settings: Settings) -> list[dict]:
    phases = build_phases(settings)
    nodes = []
    for col in COLS:
        for row in ROWS:
            approaches = []
            # North approach exists if there is a node to the north (row - 1).
            if row > min(ROWS):
                approaches.append(
                    {"id": f"{col}{row}-N", "intersection_id": node_id(col, row), "direction": "N", "lanes": 2}
                )
            if row < max(ROWS):
                approaches.append(
                    {"id": f"{col}{row}-S", "intersection_id": node_id(col, row), "direction": "S", "lanes": 2}
                )
            if col_index(col) > 0:
                approaches.append(
                    {"id": f"{col}{row}-W", "intersection_id": node_id(col, row), "direction": "W", "lanes": 2}
                )
            if col_index(col) < len(COLS) - 1:
                approaches.append(
                    {"id": f"{col}{row}-E", "intersection_id": node_id(col, row), "direction": "E", "lanes": 2}
                )

            nodes.append(
                {
                    "id": node_id(col, row),
                    "x": col_index(col) * SPACING_M,
                    "y": (row - 1) * SPACING_M,
                    "approaches": approaches,
                    "phases": phases,
                    "conflict_matrix": {"movements": MOVEMENTS, "conflict_pairs": CONFLICT_PAIRS},
                    "default_plan": build_default_plan(col, row),
                    "cycle_offset_ms": 0,
                }
            )
    return nodes


def build_roads() -> list[dict]:
    roads: list[dict] = []

    def add_directed_pair(a_col: str, a_row: int, b_col: str, b_row: int) -> None:
        arterial = is_arterial_segment(a_col, a_row, b_col, b_row)
        lanes = ARTERIAL_LANES if arterial else NORMAL_LANES
        speed_mps = kmh_to_mps(ARTERIAL_KMH if arterial else NORMAL_KMH)
        capacity = ARTERIAL_CAP if arterial else NORMAL_CAP
        length = SPACING_M

        a, b = node_id(a_col, a_row), node_id(b_col, b_row)
        for src, dst in ((a, b), (b, a)):
            roads.append(
                {
                    "id": f"{src}->{dst}",
                    "from_node": src,
                    "to_node": dst,
                    "length_m": length,
                    "lanes": lanes,
                    "free_flow_speed_mps": speed_mps,
                    "capacity_vph": capacity,
                    "status": "OPEN",
                    "density_estimate": 0.0,
                    "confidence": 0.0,
                }
            )

    for col in COLS:
        for row in ROWS:
            if row < max(ROWS):
                add_directed_pair(col, row, col, row + 1)  # vertical: this node -> south neighbor
            if col_index(col) < len(COLS) - 1:
                next_col = COLS[col_index(col) + 1]
                add_directed_pair(col, row, next_col, row)  # horizontal: this node -> east neighbor

    return roads


def build_hospitals() -> list[dict]:
    return [
        {"id": "H1", "node_id": "F1", "name": "City General", "status": "AVAILABLE"},
        {"id": "H2", "node_id": "F6", "name": "Southside Medical", "status": "AVAILABLE"},
    ]


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    settings = load_settings(repo_root / "config" / "settings.yaml")

    city = {
        "intersections": build_intersections(settings),
        "roads": build_roads(),
        "hospitals": build_hospitals(),
        "ambulance_entry_node": "A6",
    }

    out_path = repo_root / "config" / "city.json"
    out_path.write_text(json.dumps(city, indent=2), encoding="utf-8")
    print(
        f"Wrote {out_path} with {len(city['intersections'])} intersections, "
        f"{len(city['roads'])} directed road segments, {len(city['hospitals'])} hospitals."
    )


if __name__ == "__main__":
    main()
