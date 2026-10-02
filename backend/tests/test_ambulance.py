from pathlib import Path

from controller.intersection_controller import IntersectionController
from core.city_loader import load_city
from core.config import Settings, load_settings
from core.models import Priority, SignalPlanEntry
from core.rng import RngFactory
from sim.ambulance import AmbulanceSim, AmbulanceState
from sim.traffic import TrafficModel

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def _settings(**overrides) -> Settings:
    base = load_settings(CONFIG_DIR / "settings.yaml")
    return base.model_copy(update=overrides)


def _held_green_city(phase_id: str, held_node_id: str, green_s: float = 100_000):
    settings = _settings()
    city = load_city(CONFIG_DIR / "city.json", settings)
    new_intersections = []
    for node in city.intersections:
        if node.id != held_node_id:
            new_intersections.append(node)
            continue
        new_plan = [
            SignalPlanEntry(
                phase_id=e.phase_id,
                green_ms=round(green_s * 1000) if e.phase_id == phase_id else e.green_ms,
            )
            for e in node.default_plan
        ]
        new_intersections.append(node.model_copy(update={"default_plan": new_plan}))
    return city.model_copy(update={"intersections": new_intersections})


def _build(city=None, settings: Settings | None = None, seed: int = 4417):
    settings = settings or _settings()
    city = city or load_city(CONFIG_DIR / "city.json", settings)
    controllers = {n.id: IntersectionController(n) for n in city.intersections}
    rng = RngFactory(seed)
    traffic = TrafficModel(city, settings, rng, controllers)
    amb_sim = AmbulanceSim(city, settings, traffic, controllers)
    return city, controllers, traffic, amb_sim


def _advance_all(controllers: dict, now: int) -> None:
    for c in controllers.values():
        c.tick(now)


def test_passes_on_green_with_no_queue():
    # A2 -> B2 feeds B2's W approach. Hold EW_THROUGH green at B2.
    city = _held_green_city("EW_THROUGH", "B2")
    settings = _settings()
    city, controllers, traffic, amb_sim = _build(city, settings)
    now = 0
    _advance_all(controllers, now)
    # Drive B2 onto EW_THROUGH.
    dt = 100
    while controllers["B2"].state_machine.current_phase_id != "EW_THROUGH":
        now += dt
        _advance_all(controllers, now)

    amb = amb_sim.add("AMB-1", ["A2", "B2", "C2"], Priority.P1, now)
    road_len = city.segment("A2->B2").length_m
    speed = city.segment("A2->B2").free_flow_speed_mps * settings.ambulance_speed_factor
    ticks_needed = int(road_len / speed / (dt / 1000)) + 5
    for _ in range(ticks_needed):
        now += dt
        _advance_all(controllers, now)
        traffic.discharge(now, dt)
        amb_sim.step(now, dt)
        if amb.route_index >= 1:
            break
    assert amb.total_stops == 0


def test_stops_at_red():
    city, controllers, traffic, amb_sim = _build()
    # B2 starts on NS_THROUGH (green), so EW (A2->B2's direction) is red.
    now = 0
    _advance_all(controllers, now)
    amb = amb_sim.add("AMB-1", ["A2", "B2", "C2"], Priority.P1, now)
    road_len = city.segment("A2->B2").length_m
    speed = city.segment("A2->B2").free_flow_speed_mps * amb_sim.settings.ambulance_speed_factor
    dt = 100
    ticks_needed = int(road_len / speed / (dt / 1000)) + 5
    for _ in range(ticks_needed):
        now += dt
        _advance_all(controllers, now)
        traffic.discharge(now, dt)
        amb_sim.step(now, dt)
    assert amb.state == AmbulanceState.STOPPED_AT_RED
    assert amb.total_stops == 1


def test_waits_for_queue_ahead_then_passes():
    city = _held_green_city("EW_THROUGH", "B2")
    settings = _settings()
    city, controllers, traffic, amb_sim = _build(city, settings)
    segment_id = "A2->B2"
    seg = traffic.segments[segment_id]
    for _ in range(30):
        seg.queue.append(0)

    now = 0
    dt = 100
    while controllers["B2"].state_machine.current_phase_id != "EW_THROUGH":
        now += dt
        _advance_all(controllers, now)

    amb = amb_sim.add("AMB-1", ["A2", "B2", "C2"], Priority.P1, now)
    road_len = city.segment(segment_id).length_m
    speed = city.segment(segment_id).free_flow_speed_mps * settings.ambulance_speed_factor
    ticks_to_stopline = int(road_len / speed / (dt / 1000)) + 2
    for _ in range(ticks_to_stopline):
        now += dt
        _advance_all(controllers, now)
        traffic.discharge(now, dt)
        amb_sim.step(now, dt)

    assert amb.state == AmbulanceState.WAITING_FOR_QUEUE
    assert amb.total_stops == 1

    # Keep running until the queue clears and the ambulance passes onto the
    # next segment (B2->C2).
    for _ in range(2000):
        now += dt
        _advance_all(controllers, now)
        traffic.discharge(now, dt)
        amb_sim.step(now, dt)
        if amb.current_segment_id == "B2->C2":
            break
    assert amb.current_segment_id == "B2->C2"
    assert amb.state != AmbulanceState.WAITING_FOR_QUEUE


def test_arrives_and_tracks_travel_time():
    city = _held_green_city("EW_THROUGH", "B2")
    settings = _settings()
    city, controllers, traffic, amb_sim = _build(city, settings)
    now = 0
    dt = 100
    while controllers["B2"].state_machine.current_phase_id != "EW_THROUGH":
        now += dt
        _advance_all(controllers, now)

    amb = amb_sim.add("AMB-1", ["A2", "B2"], Priority.P1, now)
    for _ in range(2000):
        now += dt
        _advance_all(controllers, now)
        traffic.discharge(now, dt)
        amb_sim.step(now, dt)
        if amb.state == AmbulanceState.ARRIVED:
            break
    assert amb.state == AmbulanceState.ARRIVED
    assert amb.arrival_time is not None
    assert amb.arrival_time > amb.start_time


def test_set_route_from_current_position():
    city, controllers, traffic, amb_sim = _build()
    now = 0
    amb = amb_sim.add("AMB-1", ["A6", "A5", "A4"], Priority.P1, now)
    amb_sim.set_route("AMB-1", ["A6", "B6", "B5"])
    assert amb.route == ["A6", "B6", "B5"]
