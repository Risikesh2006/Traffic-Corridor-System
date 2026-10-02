"""HMAC approval tokens (PRD §10.8 FR-SAF, INV-10).

A command only executes if it carries a valid HMAC-SHA256 signature over its
own canonical fields, computed with a shared secret only the Safety
Supervisor holds. This lives in `core/` (not `safety/`) because both the
central supervisor (signs) and the field-side controller (verifies) need
it, and neither may import the other's package.
"""

from __future__ import annotations

import hashlib
import hmac

from core.models import Command

DEV_DEFAULT_SECRET = "dev-only-insecure-secret-do-not-use-in-production"


def _canonical_payload(
    command_type: str,
    intersection_id: str,
    phase_id: str | None,
    hold_until: int | None,
    plan_version: int,
    idempotency_key: str,
    issued_at: int,
) -> bytes:
    fields = (
        str(command_type),
        str(intersection_id),
        "" if phase_id is None else str(phase_id),
        "" if hold_until is None else str(hold_until),
        str(plan_version),
        str(idempotency_key),
        str(issued_at),
    )
    return "|".join(fields).encode("utf-8")


def sign_command(
    command_type: str,
    intersection_id: str,
    phase_id: str | None,
    hold_until: int | None,
    plan_version: int,
    idempotency_key: str,
    issued_at: int,
    secret: str,
) -> str:
    payload = _canonical_payload(
        command_type,
        intersection_id,
        phase_id,
        hold_until,
        plan_version,
        idempotency_key,
        issued_at,
    )
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def sign(command: Command, secret: str) -> str:
    payload_obj = command.payload
    phase_id_raw = payload_obj.get("phase_id") if isinstance(payload_obj, dict) else None
    hold_until_raw = payload_obj.get("hold_until") if isinstance(payload_obj, dict) else None
    phase_id = str(phase_id_raw) if phase_id_raw is not None else None
    hold_until = hold_until_raw if isinstance(hold_until_raw, int) else None
    return sign_command(
        command_type=str(command.type),
        intersection_id=command.intersection_id,
        phase_id=phase_id,
        hold_until=hold_until,
        plan_version=command.plan_version,
        idempotency_key=command.idempotency_key,
        issued_at=command.created_at,
        secret=secret,
    )


def verify(command: Command, approval: str | None, secret: str) -> bool:
    if not approval:
        return False
    expected = sign(command, secret)
    return hmac.compare_digest(expected, approval)
