import asyncio
from pathlib import Path

import pytest

from core.city_loader import load_city
from core.config import load_settings
from sim.engine import Simulation
from sim.runner import RealtimeRunner

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def _simulation() -> Simulation:
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    city = load_city(CONFIG_DIR / "city.json", settings)
    return Simulation(city, settings, seed=4417)


@pytest.mark.asyncio
async def test_runner_advances_simulation_over_real_time():
    sim = _simulation()
    runner = RealtimeRunner(sim, speed_multiplier=50.0)  # fast-forward for a quick test
    task = asyncio.create_task(runner.run(poll_interval_s=0.01))
    await asyncio.sleep(0.2)
    runner.stop()
    await task
    assert sim.clock.now() > 0


@pytest.mark.asyncio
async def test_runner_pause_stops_advancing():
    sim = _simulation()
    runner = RealtimeRunner(sim, speed_multiplier=50.0)
    task = asyncio.create_task(runner.run(poll_interval_s=0.01))
    await asyncio.sleep(0.1)
    runner.pause()
    paused_at = sim.clock.now()
    await asyncio.sleep(0.1)
    assert sim.clock.now() == paused_at
    runner.resume()
    await asyncio.sleep(0.1)
    runner.stop()
    await task
    assert sim.clock.now() > paused_at


@pytest.mark.asyncio
async def test_runner_set_speed_changes_pace():
    sim = _simulation()
    runner = RealtimeRunner(sim, speed_multiplier=1.0)
    runner.set_speed(100.0)
    assert runner.pacer.speed_multiplier == 100.0
