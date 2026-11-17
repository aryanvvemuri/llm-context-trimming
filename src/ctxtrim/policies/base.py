"""What a policy is (PLAN.md 7 Phase 2-3).

A policy is a function `run(prefix, settings, **call_kw) -> PolicyOutput`:
  prefix    the log entries before the cut (cuts.prefix); the current user request is not in it;
  settings  a dict of the policy's settings (each policy checks its own keys);
  call_kw   passed to model.call by policies that make calls (tests pass a fake backend);
            pure-code policies ignore it;
  output    the Annotations it chose (ops, and for the trimmer threads, supersessions and state
            updates) and the number of model calls it made.
A policy that makes model calls also has `estimate(prefix, settings) -> list[int]`: the approx
input tokens of each call `run` would make, computed without a call (for the dry run).
The replay evaluator builds the live context with context.build(prefix, annotations,
keep_tool_results=KEEP_TOOL_RESULTS), so the fixed tool layer is applied identically for every
policy, and policies never touch tool messages.

The trimmer's annotations go through ctxtrim.applier. Baselines that are not the trimmer build
their Annotations directly (no applier): the applier's rules (threads, state block for user
messages) are the trimmer's own guarantees, and bare stubs have no state block. context.build
renders their ops into the same Stub items as the trimmer's.
"""

from __future__ import annotations

from dataclasses import dataclass

from ctxtrim.context import Annotations, Op


@dataclass(frozen=True)
class PolicyOutput:
    annotations: Annotations
    model_calls: int = 0


def bare_stubs(runs) -> Annotations:
    """One bare evict stub per run of message ids (pointer only, no text):
    '[evicted m0007–m0009] → recall("m0007")'."""
    return Annotations(ops=tuple(Op("evict", tuple(r), stub="") for r in runs))


def check_settings(settings: dict, keys: set[str]) -> None:
    if set(settings) != keys:
        raise ValueError(f"settings must have exactly the keys {sorted(keys)}, got {sorted(settings)}")
