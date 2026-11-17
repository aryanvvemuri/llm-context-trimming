"""Policies scored by the replay evaluator. See base.py for the interface.

Implemented: none, recency (pure code); compaction_recall, object_level (one MODEL call per cut
point). The trimmer is not written yet.
"""

from __future__ import annotations

from ctxtrim.policies import compaction_recall, none, object_level, recency
from ctxtrim.policies.base import PolicyOutput, bare_stubs  # noqa: F401

POLICIES = {"none": none.run, "recency": recency.run,
            "compaction_recall": compaction_recall.run, "object_level": object_level.run}
# Policies that make model calls: name -> estimate(prefix, settings) (base.py).
ESTIMATES = {"compaction_recall": compaction_recall.estimate, "object_level": object_level.estimate}


def get(name: str):
    if name not in POLICIES:
        raise ValueError(f"unknown policy {name!r}; known: {sorted(POLICIES)}")
    return POLICIES[name]


def setting_key(name: str, settings: dict) -> str:
    """Stable name for a policy setting, e.g. 'recency-n_turns=4' (used in result file names)."""
    return "-".join([name] + [f"{k}={settings[k]}" for k in sorted(settings)])
