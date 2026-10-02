"""Runtime invariant checker (PRD §12), reused by chaos testing later.

Observes the simulation after every tick (via the `SimulationObserver`
protocol from `core/`) and independently re-derives whether each safety
invariant held — including re-verifying HMAC approvals with its own copy
of the secret, rather than trusting the controller's own judgement. This
is deliberately NOT part of the safety-critical path; it's the harness
that proves the safety-critical path is working.
"""

from __future__ import annotations

from dataclasses import dataclass

from controller.intersection_controller import IntersectionController
from controller.state_machine import detect_conflict
from core.clock import SimTimeMs
from core.config import Settings
from core.models import City, CommandAckStatus, Phase, SignalState
from core.security import verify


@dataclass(frozen=True)
class Violation:
    tick: int
    at: SimTimeMs
    intersection_id: str | None
    invariant: str
    detail: str


class InvariantChecker:
    def __init__(
        self,
        city: City,
        settings: Settings,
        controllers: dict[str, IntersectionController],
        hmac_secret: str | None = None,
    ) -> None:
        self.city = city
        self.settings = settings
        self._controllers = controllers
        self._secret = hmac_secret if hmac_secret is not None else settings.hmac_secret

        self._phase_lookup: dict[str, dict[str, Phase]] = {
            node.id: {p.id: p for p in node.phases} for node in city.intersections
        }

        self._violations: list[Violation] = []
        self._tick_count = 0

        self._checked_transition_pairs: dict[str, int] = dict.fromkeys(controllers, 0)
        self._last_applied_version: dict[str, int] = dict.fromkeys(controllers, 0)
        self._processed_commands: dict[str, int] = dict.fromkeys(controllers, 0)
        self._seen_executed_keys: dict[str, set[str]] = {cid: set() for cid in controllers}

    # -- SimulationObserver protocol -------------------------------------

    def on_tick(self, now: SimTimeMs) -> None:
        self._tick_count += 1
        for node_id in sorted(self._controllers):
            controller = self._controllers[node_id]
            self._check_conflict(node_id, controller, now)
            self._check_transition_timings(node_id, controller, now)
            self._check_ped_clearance(node_id, controller, now)
            self._check_max_hold(node_id, controller, now)
            self._check_plan_version(node_id, controller)
            self._check_commands(node_id, controller)

    # -- INV-01: no conflicting movements green/amber simultaneously ------

    def _check_conflict(
        self, node_id: str, controller: IntersectionController, now: SimTimeMs
    ) -> None:
        view = controller.signal_view()
        active = {d for d, s in view.items() if s in (SignalState.GREEN, SignalState.AMBER)}
        matrix = self.city.intersection(node_id).conflict_matrix
        reason = detect_conflict(matrix, active)
        if reason is not None:
            self._record(node_id, now, "INV-01", reason)

    # -- INV-02/03/04: amber/all-red/min-green never shortened ------------

    def _check_transition_timings(
        self, node_id: str, controller: IntersectionController, now: SimTimeMs
    ) -> None:
        transitions = controller.state_machine.transitions
        start = self._checked_transition_pairs[node_id]
        end = len(transitions) - 1
        phases = self._phase_lookup[node_id]

        for i in range(start, end):
            cur, nxt = transitions[i], transitions[i + 1]
            duration = nxt.at - cur.at

            if nxt.to_state == SignalState.FLASHING_RED:
                # Entering the fail-safe override is deliberately allowed to
                # cut any state short — that's the whole point of a conflict
                # monitor forcing flashing red. Not a timing violation.
                continue

            if cur.to_state == SignalState.GREEN and cur.phase_id in phases:
                min_green = phases[cur.phase_id].min_green_ms
                if duration < min_green:
                    self._record(
                        node_id, cur.at, "INV-04",
                        f"phase {cur.phase_id} held green for {duration}ms, "
                        f"less than min_green_ms={min_green}",
                    )
            elif cur.to_state == SignalState.AMBER and cur.phase_id in phases:
                amber_ms = phases[cur.phase_id].amber_ms
                if duration < amber_ms:
                    self._record(
                        node_id, cur.at, "INV-02",
                        f"phase {cur.phase_id} amber lasted {duration}ms, "
                        f"less than amber_ms={amber_ms}",
                    )
            elif cur.to_state == SignalState.ALL_RED and nxt.phase_id in phases:
                all_red_ms = phases[nxt.phase_id].all_red_ms
                if duration < all_red_ms:
                    self._record(
                        node_id, cur.at, "INV-03",
                        f"all-red before {nxt.phase_id} lasted {duration}ms, "
                        f"less than all_red_ms={all_red_ms}",
                    )

        self._checked_transition_pairs[node_id] = max(start, end)

    # -- INV-05: pedestrian clearance never cut short ----------------------

    def _check_ped_clearance(
        self, node_id: str, controller: IntersectionController, now: SimTimeMs
    ) -> None:
        sm = controller.state_machine
        if sm.ped_service_end_at is not None and now < sm.ped_service_end_at:
            if sm.state not in (SignalState.GREEN, SignalState.FLASHING_RED):
                self._record(
                    node_id, now, "INV-05",
                    f"left GREEN at {now}ms while pedestrian service still active "
                    f"(ends {sm.ped_service_end_at}ms)",
                )

    # -- INV-06: no approach red longer than max_hold ----------------------

    def _check_max_hold(
        self, node_id: str, controller: IntersectionController, now: SimTimeMs
    ) -> None:
        if controller.state_machine.state == SignalState.FLASHING_RED:
            # A fail-safe condition, not a hold — every direction is "red"
            # indefinitely by design (ADR: no auto-recovery this phase),
            # which isn't the max-hold policy this invariant is about.
            return
        tolerance_ms = self.settings.sim_tick_ms  # one tick of slack, per PRD §12
        for direction, since in controller.state_machine.red_since.items():
            red_for = now - since
            if red_for > self.settings.max_hold_ms + tolerance_ms:
                self._record(
                    node_id, now, "INV-06",
                    f"approach {direction} red for {red_for}ms, exceeds max_hold_ms="
                    f"{self.settings.max_hold_ms} (+{tolerance_ms}ms tolerance)",
                )

    # -- INV-08: applied plan version never decreases ----------------------

    def _check_plan_version(self, node_id: str, controller: IntersectionController) -> None:
        current = controller.applied_plan_version
        last = self._last_applied_version[node_id]
        if current < last:
            self._record(
                node_id, 0, "INV-08", f"applied_plan_version decreased from {last} to {current}"
            )
        self._last_applied_version[node_id] = max(last, current)

    # -- INV-09/INV-10: no key executed twice; every EXECUTED ack had a ----
    # valid approval (re-verified independently, not trusting the guard) --

    def _check_commands(self, node_id: str, controller: IntersectionController) -> None:
        log = controller.command_log
        start = self._processed_commands[node_id]
        for i in range(start, len(log)):
            command, ack = log[i]
            if ack.status != CommandAckStatus.EXECUTED:
                continue
            seen = self._seen_executed_keys[node_id]
            if ack.idempotency_key in seen:
                self._record(
                    node_id, ack.timestamp, "INV-09",
                    f"idempotency_key {ack.idempotency_key} executed more than once",
                )
            seen.add(ack.idempotency_key)
            if not verify(command, command.approval, self._secret):
                self._record(
                    node_id, ack.timestamp, "INV-10",
                    f"command {command.id} executed without a valid approval",
                )
        self._processed_commands[node_id] = len(log)

    # -- reporting -----------------------------------------------------

    def _record(
        self, intersection_id: str | None, at: SimTimeMs, invariant: str, detail: str
    ) -> None:
        self._violations.append(Violation(self._tick_count, at, intersection_id, invariant, detail))

    def violations(self) -> list[Violation]:
        return list(self._violations)

    def assert_clean(self) -> None:
        if not self._violations:
            return
        lines = [f"{len(self._violations)} invariant violation(s):"]
        for v in self._violations[:50]:
            lines.append(
                f"  tick={v.tick} at={v.at}ms {v.intersection_id} {v.invariant}: {v.detail}"
            )
        if len(self._violations) > 50:
            lines.append(f"  ... and {len(self._violations) - 50} more")
        raise AssertionError("\n".join(lines))
