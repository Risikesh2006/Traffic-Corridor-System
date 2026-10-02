"""'Test the tests' (PRD Phase 3 Part D5): deliberately disable ONE safety
check at a time and confirm something — the InvariantChecker, or a direct
assertion on the guard's own output — actually detects the resulting
violation. This proves the safety tests can catch real bugs, not just
pass vacuously.

Each mutation targets a different invariant and a different mechanism, so
together they also demonstrate the checker's independence from the
controller: it re-derives each invariant itself rather than trusting
whatever the (possibly broken) controller or guard reports.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaos.invariants import InvariantChecker
from controller import intersection_controller as controller_module
from controller.intersection_controller import IntersectionController
from controller.state_machine import IntersectionStateMachine
from core.city_loader import load_city
from core.config import load_settings
from core.models import Command, CommandAckStatus, CommandType
from core.security import sign

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
SECRET = "mutation-test-secret"


def _city_and_settings():
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    city = load_city(CONFIG_DIR / "city.json", settings)
    return city, settings


def _preempt(now: int, phase_id: str, hold_until: int, key: str, approve: bool = True) -> Command:
    cmd = Command(
        id=f"cmd-{key}",
        type=CommandType.PREEMPT,
        intersection_id="B2",
        plan_version=1,
        idempotency_key=key,
        payload={"phase_id": phase_id, "hold_until": hold_until},
        created_at=now,
    )
    if approve:
        cmd = cmd.model_copy(update={"approval": sign(cmd, SECRET)})
    return cmd


def test_mutation_disabled_approval_check_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: the controller's HMAC verification always passes, so a
    forged command executes. The checker independently re-verifies
    (INV-10) using its own copy of the secret and must catch it."""
    city, settings = _city_and_settings()
    controller = IntersectionController(city.intersection("B2"), hmac_secret=SECRET)
    checker = InvariantChecker(city, settings, {"B2": controller}, hmac_secret=SECRET)

    monkeypatch.setattr(controller_module, "verify", lambda command, approval, secret: True)

    forged = _preempt(0, "EW_THROUGH", 20_000, "v1:B2:0001", approve=False)
    ack = controller.handle_message(forged, now=0)
    assert ack.status == CommandAckStatus.EXECUTED  # the mutation let it through

    checker.on_tick(0)
    violations = [v for v in checker.violations() if v.invariant == "INV-10"]
    assert violations, "checker failed to catch an executed command with no valid approval"


def test_mutation_disabled_idempotency_store_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: the controller never remembers processed idempotency
    keys, so a duplicate command re-executes. The checker (INV-09) must
    catch the same key being EXECUTED twice."""
    city, settings = _city_and_settings()
    controller = IntersectionController(city.intersection("B2"), hmac_secret=SECRET)
    checker = InvariantChecker(city, settings, {"B2": controller}, hmac_secret=SECRET)

    monkeypatch.setattr(IntersectionController, "_remember", lambda self, key, ack: None)

    cmd = _preempt(0, "EW_THROUGH", 20_000, "v1:B2:0001")
    ack1 = controller.handle_message(cmd, now=0)
    ack2 = controller.handle_message(cmd, now=100)
    assert ack1.status == CommandAckStatus.EXECUTED
    assert ack2.status == CommandAckStatus.EXECUTED  # the mutation re-executed a duplicate

    checker.on_tick(100)
    violations = [v for v in checker.violations() if v.invariant == "INV-09"]
    assert violations, "checker failed to catch the same idempotency key executed twice"


def test_mutation_bypassed_min_green_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: the state machine's green-readiness floor always reports
    'ready now', so a phase change happens before min_green_ms elapses.
    The checker (INV-04) must catch the short green."""
    city, settings = _city_and_settings()
    controller = IntersectionController(city.intersection("B2"), hmac_secret=SECRET)
    checker = InvariantChecker(city, settings, {"B2": controller}, hmac_secret=SECRET)

    monkeypatch.setattr(IntersectionStateMachine, "_green_ready_at", lambda self, phase_id: 0)

    sm = controller.state_machine
    other = next(p.id for p in city.intersection("B2").phases if p.id != sm.current_phase_id)
    sm.request_phase_change(other, now=0)
    now = 0
    for _ in range(300):  # well past amber+all-red, nowhere near real min_green
        now += 100
        controller.tick(now)

    checker.on_tick(now)
    violations = [v for v in checker.violations() if v.invariant == "INV-04"]
    assert violations, "checker failed to catch a green phase shorter than min_green_ms"


def test_mutation_corrupted_amber_timing_is_detected() -> None:
    """Mutation: the controller is (hypothetically) running with a
    corrupted intersection config whose amber_ms was reduced — simulating
    a config-loading bug, not a monkeypatch, since amber_ms is read
    directly from Phase data rather than through a named method. The
    checker, built from the ORIGINAL correct city, must catch the
    mismatch as too-short amber (INV-02)."""
    city, settings = _city_and_settings()
    corrupted = city.intersection("B2").model_copy(deep=True)
    for phase in corrupted.phases:
        phase.amber_ms = 1  # 1ms amber instead of the configured ~4000ms

    controller = IntersectionController(corrupted, hmac_secret=SECRET)
    checker = InvariantChecker(city, settings, {"B2": controller}, hmac_secret=SECRET)

    sm = controller.state_machine
    other = next(p.id for p in corrupted.phases if p.id != sm.current_phase_id)
    sm.request_phase_change(other, now=0)
    now = 0
    for _ in range(400):
        now += 100
        controller.tick(now)

    checker.on_tick(now)
    violations = [v for v in checker.violations() if v.invariant == "INV-02"]
    assert violations, "checker failed to catch an amber interval shorter than configured"
