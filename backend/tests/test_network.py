import asyncio
from pathlib import Path

from controller.intersection_controller import IntersectionController
from core.general_city_loader import load_general_city
from core.config import Settings, load_settings
from core.models import Command, CommandType
from core.rng import RngFactory
from sim.network import SimNetwork

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
    network = SimNetwork(settings, rng, controllers)
    return city, controllers, network


def test_latency_applied():
    settings = _settings(network_latency_base_ms=50, network_latency_jitter_ms=0)
    city, controllers, network = _build(settings)
    network.ping("A1", now=0)
    network.step(49)
    assert network.drain_pongs() == []
    network.step(50)
    assert len(network.drain_pongs()) == 1


def test_drop_probability_statistically_correct():
    settings = _settings(network_latency_base_ms=0, network_latency_jitter_ms=0)
    city, controllers, network = _build(settings)
    network.set_drop("A1", 0.5)
    for i in range(2000):
        network.ping("A1", now=i)
    delivered = 0
    for now in range(2000):
        network.step(now)
        delivered += len(network.drain_pongs())
    assert 800 < delivered < 1200  # ~50% of 2000, generous tolerance


def test_duplicate_probability_statistically_correct():
    settings = _settings(network_latency_base_ms=0, network_latency_jitter_ms=0)
    city, controllers, network = _build(settings)
    network.set_duplicate("A1", 1.0)  # always duplicate
    for i in range(100):
        network.ping("A1", now=i)
    delivered = 0
    for now in range(100):
        network.step(now)
        delivered += len(network.drain_pongs())
    assert delivered == 200  # every ping duplicated


def test_reordering_can_occur():
    settings = _settings(network_latency_base_ms=50, network_latency_jitter_ms=40)
    city, controllers, network = _build(settings)
    for _ in range(50):
        network.ping("A1", now=0)  # all sent at the same instant, jitter decides order
    arrival_order: list[int] = []
    for now in range(200):
        network.step(now)
        arrival_order.extend([now] * len(network.drain_pongs()))
    # If jitter had no effect, all 50 would arrive at exactly the same tick.
    assert len(set(arrival_order)) > 1


def test_disconnect_blocks_delivery_both_ways():
    settings = _settings(network_latency_base_ms=0, network_latency_jitter_ms=0)
    city, controllers, network = _build(settings)
    network.disconnect("A1")
    network.ping("A1", now=0)
    network.step(0)
    assert network.drain_pongs() == []
    assert network.controller_status("A1").value == "UNREACHABLE"

    network.reconnect("A1")
    network.ping("A1", now=0)
    network.step(0)
    assert len(network.drain_pongs()) == 1
    assert network.controller_status("A1").value == "ONLINE"


def test_delivery_order_stable_for_equal_times():
    settings = _settings(network_latency_base_ms=10, network_latency_jitter_ms=0)
    city, controllers, network = _build(settings)
    for _ in range(5):
        network.ping("A1", now=0)
    network.step(10)
    pongs = network.drain_pongs()
    assert len(pongs) == 5
    # All five share the same deliver_at; the global sequence counter must
    # still break the tie deterministically, in send order.
    seqs = [int(p.message_id.removeprefix("ping-")) for p in pongs]
    assert seqs == sorted(seqs)


def test_command_ack_flows_through_network():
    city, controllers, network = _build()
    cmd = Command(
        id="cmd-1",
        type=CommandType.PREEMPT,
        intersection_id="A1",
        plan_version=1,
        idempotency_key="v1:A1:0001",
        created_at=0,
    )
    asyncio.run(network.send(cmd))
    network.step(1000)
    acks = network.drain_acks()
    assert len(acks) == 1
    assert acks[0].idempotency_key == "v1:A1:0001"
