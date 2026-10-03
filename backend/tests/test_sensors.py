from pathlib import Path

from controller.intersection_controller import IntersectionController
from core.general_city_loader import load_general_city
from core.config import Settings, load_settings
from core.models import SensorMetric
from core.rng import RngFactory
from sim.sensors import SimSensorFeed
from sim.traffic import TrafficModel

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
GRID_PATH = CONFIG_DIR / "cities" / "grid.city.json"


def _settings(**overrides) -> Settings:
    base = load_settings(CONFIG_DIR / "settings.yaml")
    return base.model_copy(update=overrides)


def _build(settings: Settings | None = None, seed: int = 4417):
    settings = settings or _settings()
    city = load_general_city(GRID_PATH, settings)
    controllers = {n.id: IntersectionController(n) for n in city.intersections}
    rng = RngFactory(seed)
    traffic = TrafficModel(city, settings, rng, controllers)
    sensors = SimSensorFeed(city, settings, rng, traffic)
    return city, traffic, sensors


def test_emits_at_configured_interval():
    settings = _settings(sensor_interval_s=5)
    city, traffic, sensors = _build(settings)
    now = 0
    dt = 1000
    for _ in range(4):  # 4s: before first interval
        now += dt
        sensors.step(now)
    assert sensors.drain_readings() == []
    now += dt  # 5s: first emission due
    sensors.step(now)
    readings = sensors.drain_readings()
    assert len(readings) == 2 * len(city.roads)  # density + queue count
    assert all(r.timestamp == 5000 for r in readings)


def test_sequence_numbers_increase():
    settings = _settings(sensor_interval_s=1)
    city, traffic, sensors = _build(settings)
    now = 0
    dt = 1000
    for _ in range(3):
        now += dt
        sensors.step(now)
    readings = sensors.drain_readings()
    segment_id = city.roads[0].id
    seqs = [
        r.sequence for r in readings if r.road_id == segment_id and r.metric == SensorMetric.DENSITY
    ]
    assert seqs == sorted(seqs)
    assert seqs == list(range(1, len(seqs) + 1))


def test_noise_applied():
    settings = _settings(sensor_interval_s=1, sensor_density_noise_std=5.0)
    city, traffic, sensors = _build(settings)
    segment_id = city.roads[0].id
    seg = traffic.segments[segment_id]
    for _ in range(20):
        seg.queue.append(0)

    now = 1000
    sensors.step(now)
    readings = [
        r
        for r in sensors.drain_readings()
        if r.road_id == segment_id and r.metric == SensorMetric.DENSITY
    ]
    assert len(readings) == 1
    road = city.segment(segment_id)
    true_density = 20 / ((road.length_m / 1000) * road.lanes)
    # With noise_std=5.0 the reading should very likely differ from the
    # exact true density (deterministic given the seed, but we just check
    # the noise mechanism actually perturbs the value at all).
    assert readings[0].value != true_density


def test_freeze_stops_readings():
    settings = _settings(sensor_interval_s=1)
    city, traffic, sensors = _build(settings)
    segment_id = city.roads[0].id
    sensors.freeze([segment_id])
    now = 1000
    sensors.step(now)
    readings = sensors.drain_readings()
    assert all(r.road_id != segment_id for r in readings)
    assert len(readings) == 2 * (len(city.roads) - 1)

    sensors.unfreeze([segment_id])
    now += 1000
    sensors.step(now)
    readings = sensors.drain_readings()
    assert any(r.road_id == segment_id for r in readings)
