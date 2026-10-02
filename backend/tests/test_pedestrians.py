from pathlib import Path

from controller.state_machine import IntersectionStateMachine
from core.city_loader import load_city
from core.config import load_settings
from core.models import SignalState

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def _node():
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    city = load_city(CONFIG_DIR / "city.json", settings)
    return city.intersection("B2")


def test_ped_call_on_current_green_extends_hold():
    node = _node()
    sm = IntersectionStateMachine(node, ped_walk_ms=5000)
    phase = sm.current_phase()  # NS_THROUGH, green at t=0
    sm.register_pedestrian_call(sm.current_phase_id, now=1000)
    assert sm.ped_service_end_at == 1000 + 5000 + phase.ped_clearance_ms

    # A request to switch away must be deferred past ped service, not just min green.
    other = next(p.id for p in node.phases if p.id != sm.current_phase_id)
    result = sm.request_phase_change(other, now=1000)
    assert result.earliest_green_at is not None
    min_expected = sm.ped_service_end_at + phase.amber_ms + sm.phase(other).all_red_ms
    assert result.earliest_green_at >= min_expected


def test_clearance_never_cut_short_when_driving_clock_forward():
    node = _node()
    sm = IntersectionStateMachine(node, ped_walk_ms=5000)
    sm.register_pedestrian_call(sm.current_phase_id, now=1000)
    service_end = sm.ped_service_end_at
    other = next(p.id for p in node.phases if p.id != sm.current_phase_id)
    sm.request_phase_change(other, now=1000)

    t = 0
    while t < service_end - 100:
        t += 100
        sm.tick(t)
        assert sm.state == SignalState.GREEN  # must still be serving pedestrians

    # Eventually it does leave green, no later than necessary.
    t = service_end
    sm.tick(t)
    assert sm.state in (SignalState.GREEN, SignalState.AMBER)


def test_pending_call_activates_on_next_green():
    node = _node()
    sm = IntersectionStateMachine(node, ped_walk_ms=5000)
    other = next(p.id for p in node.phases if p.id != sm.current_phase_id)
    sm.register_pedestrian_call(other, now=0)  # call for the NOT-currently-green phase
    assert sm.ped_service_end_at is None  # not active yet

    # Drive through to the other phase's green.
    t = 0
    for _ in range(2000):
        t += 100
        sm.tick(t)
        if sm.current_phase_id == other and sm.state == SignalState.GREEN:
            break
    assert sm.current_phase_id == other
    assert sm.ped_service_end_at is not None


def test_no_call_means_no_extension():
    node = _node()
    sm = IntersectionStateMachine(node, ped_walk_ms=5000)
    assert sm.ped_service_end_at is None
    phase = sm.current_phase()
    other = next(p.id for p in node.phases if p.id != sm.current_phase_id)
    result = sm.request_phase_change(other, now=100)
    # Without a ped call, only min green applies (matches non-pedestrian behaviour).
    expected = phase.min_green_ms + phase.amber_ms + sm.phase(other).all_red_ms
    assert result.earliest_green_at == expected
