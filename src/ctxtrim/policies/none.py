"""`none`: keep everything (the fixed tool layer still applies)."""

from __future__ import annotations

from ctxtrim.context import Annotations
from ctxtrim.policies.base import PolicyOutput, check_settings


def run(prefix, settings: dict, **call_kw) -> PolicyOutput:
    check_settings(settings, set())
    return PolicyOutput(Annotations())
