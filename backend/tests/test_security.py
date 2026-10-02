from core.models import Command, CommandType
from core.security import sign, verify


def _command(**overrides) -> Command:
    base = dict(
        id="cmd-1",
        type=CommandType.PREEMPT,
        intersection_id="A1",
        plan_version=1,
        idempotency_key="v1:A1:0001",
        payload={"phase_id": "NS_THROUGH", "hold_until": 30000},
        created_at=0,
    )
    base.update(overrides)
    return Command(**base)


def test_sign_then_verify_succeeds():
    cmd = _command()
    approval = sign(cmd, "secret-a")
    cmd = cmd.model_copy(update={"approval": approval})
    assert verify(cmd, cmd.approval, "secret-a")


def test_wrong_secret_fails():
    cmd = _command()
    approval = sign(cmd, "secret-a")
    cmd = cmd.model_copy(update={"approval": approval})
    assert not verify(cmd, cmd.approval, "secret-b")


def test_missing_approval_fails():
    cmd = _command()
    assert not verify(cmd, None, "secret-a")


def test_tampered_field_invalidates_token():
    cmd = _command()
    approval = sign(cmd, "secret-a")
    tampered = cmd.model_copy(update={"plan_version": 2, "approval": approval})
    assert not verify(tampered, tampered.approval, "secret-a")


def test_tampered_hold_until_invalidates_token():
    cmd = _command()
    approval = sign(cmd, "secret-a")
    tampered = cmd.model_copy(
        update={"payload": {"phase_id": "NS_THROUGH", "hold_until": 999999}, "approval": approval}
    )
    assert not verify(tampered, tampered.approval, "secret-a")
