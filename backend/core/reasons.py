"""Shared reason-code vocabulary for command rejections/deferrals/duplicates,
used by BOTH the controller guard (field-side) and the Safety Supervisor
(central). Lives in `core/`, not `safety/`, specifically so the controller
never has to import the central `safety` package to share this vocabulary —
only the enum is shared, not any policy logic (ADR, Phase 3).
"""

from __future__ import annotations

from enum import StrEnum


class ReasonCode(StrEnum):
    MALFORMED = "MALFORMED"
    UNKNOWN_TARGET = "UNKNOWN_TARGET"
    UNKNOWN_PHASE = "UNKNOWN_PHASE"
    CONFLICTING_PHASE = "CONFLICTING_PHASE"
    UNCONTROLLABLE = "UNCONTROLLABLE"
    STALE_VERSION = "STALE_VERSION"
    HOLD_TOO_LONG = "HOLD_TOO_LONG"
    MAX_HOLD_EXCEEDED = "MAX_HOLD_EXCEEDED"
    RATE_LIMITED = "RATE_LIMITED"
    NOT_APPROVED = "NOT_APPROVED"
    FLASHING = "FLASHING"
    DUPLICATE = "DUPLICATE"
    TIMING_DEFERRED = "TIMING_DEFERRED"
    SUPERVISOR_UNAVAILABLE = "SUPERVISOR_UNAVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"


_TEMPLATES: dict[ReasonCode, str] = {
    ReasonCode.MALFORMED: "Command is malformed: {detail}",
    ReasonCode.UNKNOWN_TARGET: "Unknown intersection '{intersection_id}'",
    ReasonCode.UNKNOWN_PHASE: "Unknown phase '{phase_id}' at intersection '{intersection_id}'",
    ReasonCode.CONFLICTING_PHASE: "Phase '{phase_id}' contains mutually conflicting movements",
    ReasonCode.UNCONTROLLABLE: (
        "Intersection '{intersection_id}' is {status} and cannot be commanded"
    ),
    ReasonCode.STALE_VERSION: (
        "Plan version {plan_version} is not newer than the last applied version {applied_version}"
    ),
    ReasonCode.HOLD_TOO_LONG: (
        "Requested hold of {requested_s}s exceeds the per-command limit of "
        "{limit_s}s (renew instead)"
    ),
    ReasonCode.MAX_HOLD_EXCEEDED: (
        "Requested hold would exceed max hold of {limit_s}s for a conflicting approach"
    ),
    ReasonCode.RATE_LIMITED: "Rate limit exceeded for intersection '{intersection_id}' ({limit}/s)",
    ReasonCode.NOT_APPROVED: "Command carries no valid supervisor approval",
    ReasonCode.FLASHING: "Intersection '{intersection_id}' is in FLASHING_RED",
    ReasonCode.DUPLICATE: "Idempotency key '{idempotency_key}' already processed",
    ReasonCode.TIMING_DEFERRED: "Change deferred to the earliest safe time: {effective_at}",
    ReasonCode.SUPERVISOR_UNAVAILABLE: (
        "Safety Supervisor is unavailable; no new commands can be approved"
    ),
    ReasonCode.UNSUPPORTED: "Command type '{command_type}' is not supported",
}


def explain(code: ReasonCode, **kwargs: object) -> str:
    """A plain-English explanation for a reason code — the "why" log."""
    template = _TEMPLATES[code]
    try:
        return template.format(**kwargs)
    except KeyError:
        return template
