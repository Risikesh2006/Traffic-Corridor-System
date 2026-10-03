"""Generates the general-format 6x6 grid city (PRD Phase 6 Stage 6.1.4).

Run: python -m backend.scripts.generate_general_grid
Writes config/cities/grid.city.json.

This is the SAME physical layout as `generate_city.py` (ADR-003's grid
corridors), described in the general junction model (core.junctions) instead
of compass directions, so the migration (Stage 6.2) can switch the running
system onto it without changing the city. `generate_city.py` and
`config/city.json` are untouched (ADR-047: expand before migrate).

Approach and exit bearings use the same compass convention as the old
Direction enum (N=0, E=90, S=180, W=270), so turn classification here agrees
with every geometry test written against that convention.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ on path

from core.config import Settings, load_settings  # noqa: E402
from core.conflicts import turn_type_for  # noqa: E402
from core.general_city import GeneralCity, GeneralRoadSegment  # noqa: E402
from core.general_city_loader import load_general_city  # noqa: E402
from core.junctions import Approach, Exit, Junction, Movement  # noqa: E402
from core.models import Hospital, IntersectionKind  # noqa: E402
from core.phasing import generate_phases  # noqa: E402

COLS = ["A", "B", "C", "D", "E", "F"]
ROWS = [1, 2, 3, 4, 5, 6]
SPACING_M = 300.0
ARTERIAL_ROW = 4
ARTERIAL_COL = "D"

NORMAL_LANES, NORMAL_KMH, NORMAL_CAP = 2, 40.0, 1600.0
ARTERIAL_LANES, ARTERIAL_KMH, ARTERIAL_CAP = 3, 60.0, 2400.0

BEARING = {"N": 0.0, "E": 90.0, "S": 180.0, "W": 270.0}


def col_index(col: str) -> int:
    return COLS.index(col)


def node_id(col: str, row: int) -> str:
    return f"{col}{row}"


def kmh_to_mps(kmh: float) -> float:
    return kmh / 3.6


def is_arterial_segment(a_col: str, a_row: int, b_col: str, b_row: int) -> bool:
    if a_row == b_row == ARTERIAL_ROW:
        return True
    if a_col == b_col == ARTERIAL_COL:
        return True
    return False


def _neighbor(col: str, row: int, direction: str) -> tuple[str, int] | None:
    if direction == "N" and row > min(ROWS):
        return col, row - 1
    if direction == "S" and row < max(ROWS):
        return col, row + 1
    if direction == "W" and col_index(col) > 0:
        return COLS[col_index(col) - 1], row
    if direction == "E" and col_index(col) < len(COLS) - 1:
        return COLS[col_index(col) + 1], row
    return None


def build_segments() -> list[GeneralRoadSegment]:
    segments: list[GeneralRoadSegment] = []
    seen: set[str] = set()
    for col in COLS:
        for row in ROWS:
            this_id = node_id(col, row)
            for direction in ("N", "S", "E", "W"):
                neighbor = _neighbor(col, row, direction)
                if neighbor is None:
                    continue
                other_id = node_id(*neighbor)
                arterial = is_arterial_segment(col, row, *neighbor)
                lanes = ARTERIAL_LANES if arterial else NORMAL_LANES
                speed_mps = kmh_to_mps(ARTERIAL_KMH if arterial else NORMAL_KMH)
                capacity = ARTERIAL_CAP if arterial else NORMAL_CAP
                road_class = "primary" if arterial else "residential"
                for src, dst in ((this_id, other_id), (other_id, this_id)):
                    sid = f"{src}->{dst}"
                    if sid in seen:
                        continue
                    seen.add(sid)
                    a = _coords(src)
                    b = _coords(dst)
                    segments.append(
                        GeneralRoadSegment(
                            id=sid,
                            from_node=src,
                            to_node=dst,
                            length_m=SPACING_M,
                            lanes=lanes,
                            free_flow_speed_mps=speed_mps,
                            capacity_vph=capacity,
                            geometry=(a, b),
                            road_class=road_class,
                            oneway=False,
                            name=None,
                        )
                    )
    return segments


def _coords(nid: str) -> tuple[float, float]:
    col, row = nid[0], int(nid[1:])
    return col_index(col) * SPACING_M, (row - 1) * SPACING_M


def build_junction(col: str, row: int, settings: Settings) -> Junction:
    this_id = node_id(col, row)
    x, y = _coords(this_id)
    arms: dict[str, tuple[str, str]] = {}
    lanes_by_direction: dict[str, int] = {}
    for direction in ("N", "S", "E", "W"):
        neighbor = _neighbor(col, row, direction)
        if neighbor is None:
            continue
        other_id = node_id(*neighbor)
        arms[direction] = (f"{other_id}->{this_id}", f"{this_id}->{other_id}")
        arterial = is_arterial_segment(col, row, *neighbor)
        lanes_by_direction[direction] = ARTERIAL_LANES if arterial else NORMAL_LANES

    approaches = tuple(
        Approach(
            id=f"{this_id}-{d}-in",
            intersection_id=this_id,
            segment_id=in_seg,
            bearing_deg=BEARING[d],
            lanes=lanes_by_direction[d],
        )
        for d, (in_seg, _out_seg) in sorted(arms.items())
    )
    exits = tuple(
        Exit(
            id=f"{this_id}-{d}-out",
            intersection_id=this_id,
            segment_id=out_seg,
            bearing_deg=BEARING[d],
        )
        for d, (_in_seg, out_seg) in sorted(arms.items())
    )
    movements = []
    for d_in in sorted(arms):
        for d_out in sorted(arms):
            if d_in == d_out:
                continue
            turn = turn_type_for(
                BEARING[d_in],
                BEARING[d_out],
                settings.driving_side,
                settings.through_tolerance_deg,
                settings.uturn_threshold_deg,
            )
            movements.append(
                Movement(
                    id=f"{this_id}-{d_in}-to-{d_out}",
                    intersection_id=this_id,
                    approach_id=f"{this_id}-{d_in}-in",
                    exit_id=f"{this_id}-{d_out}-out",
                    turn_type=turn,
                )
            )

    junction = Junction(
        id=this_id,
        kind=IntersectionKind.SIGNALISED,
        x=x,
        y=y,
        approaches=approaches,
        exits=exits,
        movements=tuple(movements),
    )
    phases = generate_phases(junction, settings)
    return junction.model_copy(update={"phases": phases})


def build_hospitals() -> list[Hospital]:
    return [
        Hospital(id="H1", node_id="F1", name="City General"),
        Hospital(id="H2", node_id="F6", name="Southside Medical"),
    ]


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    settings = load_settings(repo_root / "config" / "settings.yaml")

    junctions = [build_junction(col, row, settings) for col in COLS for row in ROWS]
    segments = build_segments()
    hospitals = build_hospitals()

    city = GeneralCity(
        intersections=tuple(junctions),
        roads=tuple(segments),
        hospitals=tuple(hospitals),
        ambulance_entry_node="A6",
    )

    out_dir = repo_root / "config" / "cities"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "grid.city.json"
    out_path.write_text(city.model_dump_json(indent=2), encoding="utf-8")

    # Fail loudly here, not only in a test, if the generated file is invalid.
    load_general_city(out_path, settings)

    print(
        f"Wrote {out_path} with {len(city.intersections)} junctions, "
        f"{len(city.roads)} directed road segments, {len(city.hospitals)} hospitals."
    )


if __name__ == "__main__":
    main()
