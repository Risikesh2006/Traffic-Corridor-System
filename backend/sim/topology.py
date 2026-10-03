"""Geometric helper derived from the general junction model (PRD Phase 6
Stage 6.2, ADR-051).

Before the general model, this module derived approach/exit directions and
turn mappings from node coordinates. The general model already carries that
information directly on each `Junction` (`approaches`, `exits`,
`movements`), so `sim/traffic.py` and `sim/ambulance.py` read it from there
now instead. Only `is_border_intersection` (used by demand generation to
decide which segments feed from the city boundary) still earns its place as
a separate helper.
"""

from __future__ import annotations

from core.general_city import GeneralCity


def is_border_intersection(city: GeneralCity, node_id: str) -> bool:
    return len(city.intersection(node_id).approaches) < 4
