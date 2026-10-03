import json
import math

from chaos.invariants import InvariantChecker
from core.models import Direction, Priority
from runtime.composition import build_runtime

ROUTE = "A6,B6,B5,C5,C4,D4,D3,E3,E2,F2,F1".split(",")


def _run(seed: int, seconds: int, with_checker: bool = False):
    rt = build_runtime(seed=seed, peak_multiplier=2.0)
    rt.add_ambulance("AMB-1", ROUTE, Priority.P1)
    checker = None
    if with_checker:
        checker = InvariantChecker(
            rt.sim.city, rt.settings, rt.sim.controllers, hmac_secret=rt.settings.hmac_secret
        )
        rt.sim.add_observer(checker)
    rt.run_for(seconds * 1000)
    return rt, checker


def test_tracker_follows_the_ambulance_with_gps() -> None:
    rt, _ = _run(seed=4417, seconds=90)
    truth = rt.sim.ambulances.ambulances["AMB-1"]
    snap = rt.central.tracker.snapshot("AMB-1", rt.sim.clock.now())
    error = math.hypot(snap.x - truth.x, snap.y - truth.y)
    assert error < 15.0  # GPS noise is 5 m; allow a generous 3-sigma-plus margin
    assert snap.uncertainty_m > 0


def test_world_state_is_populated_from_sensors_and_controller_reports() -> None:
    rt, _ = _run(seed=4417, seconds=60)
    now = rt.sim.clock.now()
    fresh = rt.central.world.freshness_summary(now)
    assert fresh.get("fresh", 0) > 0
    view = rt.central.world.supervisor_view("B5", now)
    assert view.intersection_id == "B5"
    assert view.controller_status.value == "ONLINE"


def test_safety_invariants_hold_with_the_central_system_attached() -> None:
    _, checker = _run(seed=4417, seconds=120, with_checker=True)
    assert checker is not None
    checker.assert_clean()


def test_same_seed_gives_identical_central_state() -> None:
    def fingerprint(rt) -> str:
        now = rt.sim.clock.now()
        payload = {
            "tracked": rt.central.tracker.snapshot("AMB-1", now).s_m,
            "freshness": rt.central.world.freshness_summary(now),
            "events": len(rt.central.bus.history()),
            "sim": rt.sim.state_hash(),
        }
        return json.dumps(payload, sort_keys=True)

    first, _ = _run(seed=4417, seconds=60)
    second, _ = _run(seed=4417, seconds=60)
    assert fingerprint(first) == fingerprint(second)


def test_queue_estimate_comes_from_live_stop_line_readings() -> None:
    rt, _ = _run(seed=4417, seconds=60)
    now = rt.sim.clock.now()
    estimate = rt.central.world.queue_estimate("B5", Direction.S, now)
    assert estimate is not None
    assert estimate.source.value in ("LIVE", "BLENDED")
    assert estimate.value >= 0.0
