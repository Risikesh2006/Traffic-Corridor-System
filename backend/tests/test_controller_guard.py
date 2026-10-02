from pathlib import Path

from controller.intersection_controller import IntersectionController
from core.city_loader import load_city
from core.config import load_settings
from core.models import Command, CommandAckStatus, CommandType, SignalState
from core.security import sign

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
SECRET = "test-secret"


def _node():
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    city = load_city(CONFIG_DIR / "city.json", settings)
    return city.intersection("B2")


def _controller(max_hold_ms: int = 90_000) -> IntersectionController:
    return IntersectionController(_node(), hmac_secret=SECRET, max_hold_ms=max_hold_ms)


def _preempt(
    phase_id="EW_THROUGH",
    hold_until=30_000,
    plan_version=1,
    idempotency_key="v1:B2:0001",
    created_at=0,
    approve=True,
    secret=SECRET,
) -> Command:
    cmd = Command(
        id=f"cmd-{idempotency_key}",
        type=CommandType.PREEMPT,
        intersection_id="B2",
        plan_version=plan_version,
        idempotency_key=idempotency_key,
        payload={"phase_id": phase_id, "hold_until": hold_until},
        created_at=created_at,
    )
    if approve:
        cmd = cmd.model_copy(update={"approval": sign(cmd, secret)})
    return cmd


def test_unapproved_command_rejected():
    controller = _controller()
    cmd = _preempt(approve=False)
    ack = controller.handle_message(cmd, now=0)
    assert ack.status == CommandAckStatus.REJECTED
    assert ack.reason_code == "NOT_APPROVED"


def test_forged_approval_rejected():
    controller = _controller()
    cmd = _preempt(approve=True, secret="wrong-secret")
    ack = controller.handle_message(cmd, now=0)
    assert ack.status == CommandAckStatus.REJECTED
    assert ack.reason_code == "NOT_APPROVED"


def test_duplicate_returns_original_result_without_reexecuting():
    controller = _controller()
    cmd = _preempt()
    ack1 = controller.handle_message(cmd, now=0)
    assert ack1.status == CommandAckStatus.EXECUTED
    ack2 = controller.handle_message(cmd, now=5000)
    assert ack2.status == CommandAckStatus.DUPLICATE
    assert ack2.effective_at == ack1.effective_at  # same original outcome
    assert ack2.reason_code == ack1.reason_code


def test_stale_version_ignored():
    controller = _controller()
    first = _preempt(plan_version=5, idempotency_key="v5:B2:0001")
    controller.handle_message(first, now=0)
    stale = _preempt(plan_version=3, idempotency_key="v3:B2:0001")
    ack = controller.handle_message(stale, now=100)
    assert ack.status == CommandAckStatus.IGNORED_STALE_VERSION


def test_flashing_rejects_commands():
    controller = _controller()
    controller.state_machine.enter_flashing("test", now=0)
    cmd = _preempt()
    ack = controller.handle_message(cmd, now=0)
    assert ack.status == CommandAckStatus.REJECTED
    assert ack.reason_code == "FLASHING"


def test_unknown_phase_rejected():
    controller = _controller()
    cmd = _preempt(phase_id="NOT_A_PHASE")
    ack = controller.handle_message(cmd, now=0)
    assert ack.status == CommandAckStatus.REJECTED
    assert ack.reason_code == "UNKNOWN_PHASE"


def test_deferral_reports_correct_effective_at():
    controller = _controller()
    # B2 starts on NS_THROUGH; request EW_THROUGH immediately.
    cmd = _preempt(phase_id="EW_THROUGH", hold_until=200_000)
    ack = controller.handle_message(cmd, now=0)
    assert ack.status == CommandAckStatus.EXECUTED
    sm = controller.state_machine
    phase = sm.current_phase()
    target = sm.phase("EW_THROUGH")
    expected = phase.min_green_ms + phase.amber_ms + target.all_red_ms
    assert ack.effective_at == expected


def test_hold_expiry_returns_to_normal_plan():
    controller = _controller()
    cmd = _preempt(phase_id="NS_THROUGH", hold_until=5_000)  # already-current phase, short hold
    ack = controller.handle_message(cmd, now=0)
    assert ack.status == CommandAckStatus.EXECUTED
    assert controller.active_hold is not None

    now = 0
    for _ in range(200):
        now += 100
        controller.tick(now)
        if controller.active_hold is None:
            break
    assert controller.active_hold is None
    # Normal cycling should eventually move on past NS_THROUGH.
    for _ in range(2000):
        now += 100
        controller.tick(now)
        if controller.state_machine.current_phase_id == "EW_THROUGH":
            break
    assert controller.state_machine.current_phase_id == "EW_THROUGH"


def test_local_max_hold_serves_starved_approach():
    controller = _controller(max_hold_ms=3_000)  # small for a fast test
    # Hold NS_THROUGH (already current) for a long time; EW should starve
    # and get served despite the hold.
    cmd = _preempt(phase_id="NS_THROUGH", hold_until=60_000)
    ack = controller.handle_message(cmd, now=0)
    assert ack.status == CommandAckStatus.EXECUTED

    now = 0
    served_ew = False
    for _ in range(2000):
        now += 100
        controller.tick(now)
        if (
            controller.state_machine.current_phase_id == "EW_THROUGH"
            and controller.state_machine.state == SignalState.GREEN
        ):
            served_ew = True
            break
    assert served_ew, "starved EW approach was never served despite an active hold"
    # The hold should still exist (unless it expired) — it's interrupted, not cancelled.
    assert controller.active_hold is not None
