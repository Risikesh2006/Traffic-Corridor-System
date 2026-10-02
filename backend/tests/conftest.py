"""Shared Hypothesis profiles (PRD Phase 3 Part D2).

`default` is fast, for local iteration. `thorough` runs >= 10,000 examples
for the safety-critical property and stateful tests — select it with:

    HYPOTHESIS_PROFILE=thorough pytest tests/test_safety_properties.py

Tests that need a fixed example count regardless of profile (e.g. the
Phase 2 controller cycling property test) use an explicit `@settings(...)`
decorator, which always overrides the loaded profile.
"""

from __future__ import annotations

import os

from hypothesis import HealthCheck
from hypothesis import settings as hyp_settings

hyp_settings.register_profile("default", max_examples=100, deadline=None)
hyp_settings.register_profile(
    "thorough",
    max_examples=10_000,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
hyp_settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
