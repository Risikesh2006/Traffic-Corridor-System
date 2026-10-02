import json
import subprocess
import sys
from pathlib import Path

from core.city_loader import load_city
from core.config import load_settings
from core.models import Priority
from sim.engine import Simulation

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
BACKEND_DIR = Path(__file__).resolve().parent.parent


def _build(seed: int = 4417) -> Simulation:
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    city = load_city(CONFIG_DIR / "city.json", settings)
    sim = Simulation(city, settings, seed)
    sim.add_ambulance("AMB-1", ["A6", "A5", "A4", "B4", "C4"], Priority.P1)
    return sim


def test_same_seed_same_hash_same_process():
    sim1 = _build(seed=4417)
    sim1.run_for(5 * 60 * 1000)
    hash1 = sim1.state_hash()

    sim2 = _build(seed=4417)
    sim2.run_for(5 * 60 * 1000)
    hash2 = sim2.state_hash()

    assert hash1 == hash2


def test_different_seed_different_hash():
    sim1 = _build(seed=1)
    sim1.run_for(60_000)
    sim2 = _build(seed=2)
    sim2.run_for(60_000)
    assert sim1.state_hash() != sim2.state_hash()


def test_snapshot_is_json_serialisable():
    sim = _build()
    sim.run_for(10_000)
    snapshot = sim.snapshot()
    serialised = json.dumps(snapshot)
    assert json.loads(serialised) == snapshot


def test_determinism_across_subprocess():
    """The same hash must be reproducible in a fresh Python process, not
    just a fresh object in the same interpreter (rules out any hidden
    cross-run global state, e.g. PYTHONHASHSEED-dependent behaviour)."""
    script = (
        f"import sys; sys.path.insert(0, {str(BACKEND_DIR)!r})\n"
        "from pathlib import Path\n"
        "from core.city_loader import load_city\n"
        "from core.config import load_settings\n"
        "from core.models import Priority\n"
        "from sim.engine import Simulation\n"
        f"settings = load_settings(Path({str(CONFIG_DIR)!r}) / 'settings.yaml')\n"
        f"city = load_city(Path({str(CONFIG_DIR)!r}) / 'city.json', settings)\n"
        "sim = Simulation(city, settings, 4417)\n"
        "sim.add_ambulance('AMB-1', ['A6', 'A5', 'A4', 'B4', 'C4'], Priority.P1)\n"
        "sim.run_for(60_000)\n"
        "print(sim.state_hash())\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    subprocess_hash = result.stdout.strip()

    sim = _build(seed=4417)
    sim.run_for(60_000)
    assert sim.state_hash() == subprocess_hash
