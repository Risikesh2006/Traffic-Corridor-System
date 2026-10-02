from pathlib import Path

from core.city_loader import load_city
from core.clock import SimClock
from core.config import Settings, load_settings
from core.models import Command, CommandType, ControllerStatus
from core.security import verify
from safety.supervisor import IntersectionView, SafetySupervisor

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def _settings(**overrides) -> Settings:
    base = load_settings(CONFIG_DIR / "settings.yaml")
    return base.model_copy(update=overrides)


def _city(settings: Settings):
    return load_city(CONFIG_DIR / "city.json", settings)


def _supervisor(settings: Settings | None = None, secret: str = "test-secret"):
    settings = settings or _settings()
    city = _city(settings)
    clock = SimClock()
    return SafetySupervisor(city, settings, clock, secret=secret), clock


def _view(
    intersection_id="B2",
    status=ControllerStatus.ONLINE,
    reported_at=0,
    red_since=None,
) -> IntersectionView:
    return IntersectionView(
        intersection_id=intersection_id,
        controller_status=status,
        reported_at=reported_at,
        current_phase_id="NS_THROUGH",
        red_since=red_since or {},
    )


def _preempt(
    intersection_id="B2",
    phase_id="EW_THROUGH",
    hold_until=20_000,
    plan_version=1,
    idempotency_key="v1:B2:0001",
) -> Command:
    return Command(
        id=f"cmd-{idempotency_key}",
        type=CommandType.PREEMPT,
        intersection_id=intersection_id,
        plan_version=plan_version,
        idempotency_key=idempotency_key,
        payload={"phase_id": phase_id, "hold_until": hold_until},
        created_at=0,
    )


def test_malformed_idempotency_key_rejected():
    sup, clock = _supervisor()
    cmd = _preempt(idempotency_key="not-the-right-format")
    decision = sup.validate(cmd, _view())
    assert not decision.approved
    assert decision.reason_code.value == "MALFORMED"
    assert "MALFORMED" not in decision.explanation  # human-readable, not just the code
    assert "idempotency_key" in decision.explanation


def test_missing_hold_until_rejected():
    sup, clock = _supervisor()
    cmd = _preempt(hold_until=0)  # not > now
    decision = sup.validate(cmd, _view())
    assert not decision.approved
    assert decision.reason_code.value == "MALFORMED"


def test_unknown_target_rejected():
    sup, clock = _supervisor()
    cmd = _preempt(intersection_id="ZZ9")
    decision = sup.validate(cmd, _view(intersection_id="ZZ9"))
    assert not decision.approved
    assert decision.reason_code.value == "UNKNOWN_TARGET"


def test_unknown_phase_rejected():
    sup, clock = _supervisor()
    cmd = _preempt(phase_id="NOT_A_PHASE")
    decision = sup.validate(cmd, _view())
    assert not decision.approved
    assert decision.reason_code.value == "UNKNOWN_PHASE"


def test_uncontrollable_rejected_when_unreachable():
    sup, clock = _supervisor()
    cmd = _preempt()
    decision = sup.validate(cmd, _view(status=ControllerStatus.UNREACHABLE))
    assert not decision.approved
    assert decision.reason_code.value == "UNCONTROLLABLE"


def test_uncontrollable_rejected_when_flashing():
    sup, clock = _supervisor()
    cmd = _preempt()
    decision = sup.validate(cmd, _view(status=ControllerStatus.FLASHING))
    assert not decision.approved
    assert decision.reason_code.value == "UNCONTROLLABLE"


def test_stale_version_rejected():
    sup, clock = _supervisor()
    sup.validate(_preempt(plan_version=5, idempotency_key="v5:B2:0001"), _view())
    decision = sup.validate(_preempt(plan_version=3, idempotency_key="v3:B2:0002"), _view())
    assert not decision.approved
    assert decision.reason_code.value == "STALE_VERSION"


def test_hold_too_long_rejected():
    settings = _settings(lease_duration_s=30)
    sup, clock = _supervisor(settings)
    cmd = _preempt(hold_until=60_000)  # 60s > 30s lease limit
    decision = sup.validate(cmd, _view())
    assert not decision.approved
    assert decision.reason_code.value == "HOLD_TOO_LONG"


def test_max_hold_projection_rejected():
    settings = _settings(lease_duration_s=120, max_hold_s=30)
    sup, clock = _supervisor(settings)
    # EW has been red since t=0; requesting NS hold until t=40000 would
    # leave EW red for 40s, exceeding the 30s max hold.
    cmd = _preempt(phase_id="NS_THROUGH", hold_until=40_000)
    view = _view(red_since={"E": 0, "W": 0})
    decision = sup.validate(cmd, view)
    assert not decision.approved
    assert decision.reason_code.value == "MAX_HOLD_EXCEEDED"


def test_max_hold_projection_approved_when_within_budget():
    settings = _settings(lease_duration_s=120, max_hold_s=60)
    sup, clock = _supervisor(settings)
    cmd = _preempt(phase_id="NS_THROUGH", hold_until=20_000)
    view = _view(red_since={"E": 0, "W": 0})
    decision = sup.validate(cmd, view)
    assert decision.approved


def test_rate_limit():
    settings = _settings(command_rate_limit_per_s=2)
    sup, clock = _supervisor(settings)
    results = []
    for i in range(5):
        cmd = _preempt(idempotency_key=f"v1:B2:{i:04d}")
        results.append(sup.validate(cmd, _view()).approved)
    assert results[:2] == [True, True]
    assert results[2:] == [False, False, False]
    rejected = sup.validate(_preempt(idempotency_key="v1:B2:9999"), _view())
    assert rejected.reason_code.value == "RATE_LIMITED"


def test_stale_view_flagged_but_still_approved():
    settings = _settings(heartbeat_timeout_s=3)
    sup, clock = _supervisor(settings)
    clock.advance(10_000)  # now = 10s
    cmd = _preempt(hold_until=20_000)
    view = _view(reported_at=0)  # 10s old, well past the 3s heartbeat timeout
    decision = sup.validate(cmd, view)
    assert decision.approved
    assert decision.view_stale


def test_fresh_view_not_flagged_stale():
    sup, clock = _supervisor()
    cmd = _preempt(hold_until=20_000)
    decision = sup.validate(cmd, _view(reported_at=0))
    assert decision.approved
    assert not decision.view_stale


def test_approval_token_verifies_with_supervisors_secret():
    sup, clock = _supervisor(secret="shared-secret")
    cmd = _preempt(hold_until=20_000)
    decision = sup.validate(cmd, _view())
    assert decision.approved
    signed_cmd = cmd.model_copy(update={"approval": decision.approval})
    assert verify(signed_cmd, signed_cmd.approval, "shared-secret")
    assert not verify(signed_cmd, signed_cmd.approval, "wrong-secret")


def test_explanations_are_human_readable():
    sup, clock = _supervisor()
    decision = sup.validate(_preempt(phase_id="GHOST"), _view())
    assert "GHOST" in decision.explanation
    assert "B2" in decision.explanation


def test_decisions_emit_events():
    sup, clock = _supervisor()
    sup.validate(_preempt(hold_until=20_000), _view())
    sup.validate(_preempt(phase_id="GHOST", idempotency_key="v1:B2:0002"), _view())
    events = sup.drain_events()
    assert len(events) == 2
    assert events[0].type.value == "COMMAND_APPROVED"
    assert events[1].type.value == "COMMAND_REJECTED"
