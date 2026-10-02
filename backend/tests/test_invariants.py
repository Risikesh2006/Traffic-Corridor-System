from pathlib import Path

from chaos.invariants import InvariantChecker
from controller.intersection_controller import IntersectionController
from core.city_loader import load_city
from core.config import load_settings
from core.models import Command, CommandAck, CommandAckStatus, CommandType
from core.security import sign

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
SECRET = "test-secret"


def _city():
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    return load_city(CONFIG_DIR / "city.json", settings), settings


def _setup():
    city, settings = _city()
    controllers = {
        n.id: IntersectionController(n, hmac_secret=SECRET, max_hold_ms=settings.max_hold_ms)
        for n in city.intersections
    }
    checker = InvariantChecker(city, settings, controllers, hmac_secret=SECRET)
    return city, settings, controllers, checker


def test_clean_run_reports_zero_violations():
    city, settings, controllers, checker = _setup()
    now = 0
    for _ in range(600):  # 60s
        now += settings.sim_tick_ms
        for c in controllers.values():
            c.tick(now)
        checker.on_tick(now)
    assert checker.violations() == []


def test_detects_max_hold_violation():
    city, settings, controllers, checker = _setup()
    controller = controllers["B2"]
    # Force an approach to appear red far longer than max_hold allows.
    controller.state_machine.red_since["E"] = 0
    now = settings.max_hold_ms + settings.sim_tick_ms * 10
    controller.tick(now)
    checker.on_tick(now)
    violations = [v for v in checker.violations() if v.invariant == "INV-06"]
    assert violations


def test_detects_duplicate_execution():
    city, settings, controllers, checker = _setup()
    controller = controllers["B2"]
    cmd = Command(
        id="cmd-1",
        type=CommandType.PREEMPT,
        intersection_id="B2",
        plan_version=1,
        idempotency_key="v1:B2:0001",
        payload={"phase_id": "NS_THROUGH", "hold_until": 10_000},
        created_at=0,
    )
    cmd = cmd.model_copy(update={"approval": sign(cmd, SECRET)})
    ack = CommandAck(
        command_id=cmd.id,
        intersection_id="B2",
        idempotency_key=cmd.idempotency_key,
        status=CommandAckStatus.EXECUTED,
        timestamp=0,
    )
    # Simulate a hypothetical guard bug: the same key logged EXECUTED twice.
    controller.command_log.append((cmd, ack))
    controller.command_log.append((cmd, ack))
    checker.on_tick(0)
    violations = [v for v in checker.violations() if v.invariant == "INV-09"]
    assert violations


def test_detects_invalid_approval_on_executed_command():
    city, settings, controllers, checker = _setup()
    controller = controllers["B2"]
    cmd = Command(
        id="cmd-2",
        type=CommandType.PREEMPT,
        intersection_id="B2",
        plan_version=1,
        idempotency_key="v1:B2:0002",
        payload={"phase_id": "NS_THROUGH", "hold_until": 10_000},
        created_at=0,
        approval="forged-not-a-real-signature",
    )
    ack = CommandAck(
        command_id=cmd.id,
        intersection_id="B2",
        idempotency_key=cmd.idempotency_key,
        status=CommandAckStatus.EXECUTED,
        timestamp=0,
    )
    # Simulate a hypothetical guard bug: executed despite a bad signature.
    controller.command_log.append((cmd, ack))
    checker.on_tick(0)
    violations = [v for v in checker.violations() if v.invariant == "INV-10"]
    assert violations


def test_detects_plan_version_decrease():
    city, settings, controllers, checker = _setup()
    controller = controllers["B2"]
    controller._applied_plan_version = 5  # noqa: SLF001 - simulating a hypothetical bug
    checker.on_tick(0)
    controller._applied_plan_version = 2  # noqa: SLF001
    checker.on_tick(0)
    violations = [v for v in checker.violations() if v.invariant == "INV-08"]
    assert violations


def test_assert_clean_raises_readable_report():
    city, settings, controllers, checker = _setup()
    controllers["B2"].state_machine.red_since["E"] = 0
    now = settings.max_hold_ms + settings.sim_tick_ms * 10
    controllers["B2"].tick(now)
    checker.on_tick(now)
    try:
        checker.assert_clean()
        raise AssertionError("expected assert_clean to raise")
    except AssertionError as exc:
        assert "INV-06" in str(exc)
