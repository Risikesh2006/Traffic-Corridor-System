from core.models import Priority, RouteAckStatus
from sim.engine import Simulation
from tests.routing_fakes import city_for, settings_for_tests

ROUTE = ["A6", "A5", "A4", "B4", "C4"]


def _sim(**overrides: object) -> Simulation:
    settings = settings_for_tests(**overrides)
    sim = Simulation(city_for(settings), settings, seed=4417)
    sim.add_ambulance("AMB-1", ROUTE, Priority.P1)
    return sim


def _run_ms(sim: Simulation, ms: int) -> None:
    sim.run_for(ms)


def test_new_route_is_adopted_after_latency_and_reaction_delay():
    sim = _sim()
    sim.guidance.send_route("AMB-1", 2, ["A6", "A5", "A4", "B4", "B3"])
    _run_ms(sim, 1000)
    assert sim.ambulances.ambulances["AMB-1"].route == ROUTE
    _run_ms(sim, 2500)
    assert sim.ambulances.ambulances["AMB-1"].route == ["A6", "A5", "A4", "B4", "B3"]
    acks = sim.guidance.drain_acks()
    assert [a.status for a in acks] == [RouteAckStatus.ADOPTED]
    assert acks[0].route_version == 2


def test_older_route_versions_never_overwrite_newer_ones():
    sim = _sim()
    sim.guidance.send_route("AMB-1", 3, ["A6", "A5", "A4", "B4", "B3"])
    _run_ms(sim, 500)
    sim.guidance.send_route("AMB-1", 2, ["A6", "A5", "A4", "B4", "B2"])
    _run_ms(sim, 4000)
    assert sim.ambulances.ambulances["AMB-1"].route == ["A6", "A5", "A4", "B4", "B3"]
    statuses = {(a.route_version, a.status) for a in sim.guidance.drain_acks()}
    assert (3, RouteAckStatus.ADOPTED) in statuses


def test_route_that_diverges_after_the_ambulance_passed_is_refused():
    sim = _sim()
    _run_ms(sim, 60_000)
    amb = sim.ambulances.ambulances["AMB-1"]
    assert amb.route_index >= 1
    sim.guidance.send_route("AMB-1", 2, ["A6", "B6", "B5", "C5", "C4"])
    _run_ms(sim, 4000)
    acks = sim.guidance.drain_acks()
    assert any(a.status == RouteAckStatus.CANNOT_FOLLOW and a.route_version == 2 for a in acks)
    assert amb.route == ROUTE


def test_acks_are_cleared_once_drained():
    sim = _sim()
    sim.guidance.send_route("AMB-1", 2, ["A6", "A5", "A4", "B4", "B3"])
    _run_ms(sim, 4000)
    assert sim.guidance.drain_acks()
    assert sim.guidance.drain_acks() == []
