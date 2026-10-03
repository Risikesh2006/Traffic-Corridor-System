from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from core.clock import SimTimeMs
from core.config import Settings, load_settings
from core.conflicts import ConflictRules, turn_type_for
from core.junctions import Approach, Exit, Junction, Movement
from core.models import IntersectionKind, TurnType
from core.phasing import PhasingError, cycle_ms, generate_phases, select_plan

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def _settings() -> Settings:
    return load_settings(CONFIG_DIR / "settings.yaml")


def _junction(bearings: dict[str, float], lanes: int = 2) -> Junction:
    approaches = tuple(
        Approach(
            id=f"{k}-in", intersection_id="J", segment_id=f"{k}->J", bearing_deg=b, lanes=lanes
        )
        for k, b in sorted(bearings.items())
    )
    exits = tuple(
        Exit(id=f"{k}-out", intersection_id="J", segment_id=f"J->{k}", bearing_deg=b)
        for k, b in sorted(bearings.items())
    )
    movements = []
    for k, b in sorted(bearings.items()):
        for e, eb in sorted(bearings.items()):
            if e == k:
                continue
            turn = turn_type_for(b, eb, "left", 30, 150)
            movements.append(
                Movement(
                    id=f"{k}-to-{e}",
                    intersection_id="J",
                    approach_id=f"{k}-in",
                    exit_id=f"{e}-out",
                    turn_type=turn,
                )
            )
    return Junction(
        id="J",
        kind=IntersectionKind.SIGNALISED,
        x=0.0,
        y=0.0,
        approaches=approaches,
        exits=exits,
        movements=tuple(movements),
    )


FOUR_WAY = {"N": 0.0, "E": 90.0, "S": 180.0, "W": 270.0}
T_JUNCTION = {"N": 0.0, "E": 90.0, "S": 180.0}
FIVE_WAY = {"N": 0.0, "NE": 72.0, "SE": 144.0, "SW": 216.0, "NW": 288.0}
SKEWED_FOUR_WAY = {"N": 10.0, "E": 95.0, "S": 185.0, "W": 255.0}


def _assert_valid(junction: Junction, phases: tuple[object, ...]) -> None:
    matrix = junction.conflict_matrix(ConflictRules())
    served: set[str] = set()
    for phase in phases:
        assert matrix.is_compatible(phase.movement_ids), phase.id
        served.update(phase.movement_ids)
    expected = {m.id for m in junction.movements if m.turn_type != TurnType.U_TURN}
    assert served == expected


def test_regular_four_way_uses_paired_phasing():
    strategy, groups = select_plan(_junction(FOUR_WAY), _settings())
    assert strategy == "PAIRED"
    assert len(groups) >= 2


def test_t_junction_uses_split_phasing():
    strategy, _groups = select_plan(_junction(T_JUNCTION), _settings())
    assert strategy == "SPLIT"


def test_five_way_uses_split_phasing():
    strategy, _groups = select_plan(_junction(FIVE_WAY), _settings())
    assert strategy == "SPLIT"


def test_skewed_four_way_outside_tolerance_uses_split_phasing():
    settings = _settings().model_copy(update={"regular_bearing_tolerance_deg": 5.0})
    strategy, _groups = select_plan(_junction(SKEWED_FOUR_WAY), settings)
    assert strategy == "SPLIT"


@pytest.mark.parametrize("bearings", [FOUR_WAY, T_JUNCTION, FIVE_WAY, SKEWED_FOUR_WAY])
def test_every_generated_phase_is_compatible_and_every_movement_is_served(bearings):
    junction = _junction(bearings)
    phases = generate_phases(junction, _settings())
    _assert_valid(junction, phases)


@pytest.mark.parametrize("bearings", [FOUR_WAY, T_JUNCTION, FIVE_WAY])
def test_splits_and_cycle_are_within_configured_bounds(bearings):
    settings = _settings()
    phases = generate_phases(_junction(bearings), settings)
    for phase in phases:
        assert phase.min_green_ms == settings.min_green_ms
        assert settings.min_green_ms <= phase.configured_green_ms <= settings.phase_green_max_ms
    total = cycle_ms(phases)
    assert settings.cycle_min_ms <= total <= settings.cycle_max_ms


def test_busier_approach_gets_a_longer_green():
    junction = _junction(T_JUNCTION)
    wider = tuple(
        a.model_copy(update={"lanes": 3}) if a.id == "N-in" else a for a in junction.approaches
    )
    junction = junction.model_copy(update={"approaches": wider})
    phases = generate_phases(junction, _settings())
    north = [p for p in phases if any(m.startswith("N-") for m in p.movement_ids)][0]
    others = [p for p in phases if p is not north]
    assert all(north.configured_green_ms >= o.configured_green_ms for o in others)


def test_only_signalised_junctions_get_phases():
    junction = _junction(FOUR_WAY).model_copy(update={"kind": IntersectionKind.UNSIGNALISED})
    with pytest.raises(PhasingError):
        generate_phases(junction, _settings())


def test_unserved_movement_fails_loudly(monkeypatch):
    import core.phasing as phasing

    monkeypatch.setattr(phasing, "_split_plan", lambda junction: [("N-to-S",)])
    monkeypatch.setattr(phasing, "_paired_plan", lambda junction, settings: None)
    with pytest.raises(PhasingError, match="not served"):
        generate_phases(_junction(T_JUNCTION), _settings())


def test_junction_with_no_movements_to_serve_fails_loudly():
    empty = _junction(T_JUNCTION).model_copy(update={"movements": ()})
    with pytest.raises(PhasingError):
        generate_phases(empty, _settings())


def test_cycle_too_short_for_the_phases_fails_loudly():
    settings = _settings().model_copy(
        update={"cycle_min_s": 10, "cycle_max_s": 10, "cycle_target_s": 10}
    )
    with pytest.raises(PhasingError):
        generate_phases(_junction(FIVE_WAY), settings)


def test_generation_is_deterministic():
    first = generate_phases(_junction(FOUR_WAY), _settings())
    second = generate_phases(_junction(FOUR_WAY), _settings())
    assert first == second


_ANGLES = st.lists(
    st.integers(min_value=0, max_value=17),
    min_size=3,
    max_size=6,
    unique=True,
).map(lambda steps: [step * 20.0 for step in steps])


@settings(max_examples=60, deadline=None)
@given(angles=_ANGLES)
def test_random_junction_shapes_always_produce_valid_plans(angles: list[float]):
    bearings = {f"A{i}": b for i, b in enumerate(angles)}
    junction = _junction(bearings)
    phases = generate_phases(junction, _settings())
    _assert_valid(junction, phases)
    total: SimTimeMs = cycle_ms(phases)
    assert _settings().cycle_min_ms <= total <= _settings().cycle_max_ms
