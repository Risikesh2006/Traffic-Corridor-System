"""Hypothesis property test (PRD Phase 3 Part D2): random command sequences
against a single controller must never violate any safety invariant —
regardless of valid/garbage phases, hold durations, plan version ordering,
repeated idempotency keys, forged/missing approvals, or FLASHING_RED.

Run the full 10,000-example profile with:
    HYPOTHESIS_PROFILE=thorough pytest tests/test_safety_properties.py
"""

from __future__ import annotations

from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from chaos.invariants import InvariantChecker
from controller.intersection_controller import IntersectionController
from core.general_city_loader import load_general_city
from core.config import load_settings
from core.models import Command, CommandType
from core.security import sign

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
GRID_PATH = CONFIG_DIR / "cities" / "grid.city.json"
SECRET = "property-test-secret"


def _b2_phase_ids() -> list[str]:
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    city = load_general_city(GRID_PATH, settings)
    return [p.id for p in city.intersection("B2").phases]


_tick_op = st.tuples(st.just("tick"), st.integers(min_value=1, max_value=5000))
_flashing_op = st.tuples(st.just("flashing"))
_command_op = st.tuples(
    st.just("command"),
    st.sampled_from([*_b2_phase_ids(), "GHOST_PHASE", ""]),
    st.integers(min_value=-5000, max_value=120_000),  # hold_until offset from now
    st.integers(min_value=0, max_value=10),  # plan_version
    st.integers(min_value=0, max_value=4),  # idempotency key index — small range forces repeats
    st.sampled_from(["correct", "wrong", "missing"]),  # approval
)

_ops = st.lists(st.one_of(_tick_op, _flashing_op, _command_op), min_size=1, max_size=150)


def _build():
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    city = load_general_city(GRID_PATH, settings)
    node = city.intersection("B2")
    controller = IntersectionController(
        node, hmac_secret=SECRET, max_hold_ms=settings.max_hold_ms, ped_walk_ms=settings.ped_walk_ms
    )
    checker = InvariantChecker(city, settings, {"B2": controller}, hmac_secret=SECRET)
    return controller, checker


@given(ops=_ops)
def test_controller_guard_invariants_hold_under_random_commands(ops: list[tuple]) -> None:
    controller, checker = _build()
    now = 0

    for op in ops:
        kind = op[0]
        if kind == "tick":
            now += op[1]
            controller.tick(now)
        elif kind == "flashing":
            controller.state_machine.enter_flashing("property-test", now)
        else:
            _, phase_id, hold_offset, plan_version, key_idx, approval_kind = op
            cmd = Command(
                id=f"cmd-{now}-{key_idx}-{plan_version}",
                type=CommandType.PREEMPT,
                intersection_id="B2",
                plan_version=plan_version,
                idempotency_key=f"v{plan_version}:B2:{key_idx:04d}",
                payload={"phase_id": phase_id, "hold_until": now + hold_offset},
                created_at=now,
            )
            if approval_kind == "correct":
                cmd = cmd.model_copy(update={"approval": sign(cmd, SECRET)})
            elif approval_kind == "wrong":
                cmd = cmd.model_copy(update={"approval": sign(cmd, "a-different-secret")})
            # "missing" leaves approval as None.
            controller.handle_message(cmd, now)
        checker.on_tick(now)

    checker.assert_clean()
