"""Score a policy at cut points against the needs records (PLAN.md 7 Phase 2).

Per cut point: run the policy on the prefix (cuts.prefix), build the live context with the fixed
tool layer (context.build, KEEP_TOOL_RESULTS), classify every item of the needs record
(ctxtrim.eval.checks), and compute the pruning rate and the four flags (ctxtrim.eval.metrics).
Needs records are read, never written here (needs.ensure writes them, once per cut point).
"""

from __future__ import annotations

from ctxtrim import config, context, policies
from ctxtrim.eval import checks, metrics
from ctxtrim.eval.cuts import Cut, prefix
from ctxtrim.log import Entry


def contexts(cut: Cut, entries: list[Entry], policy: str, settings: dict, **call_kw):
    """(context before, context after, PolicyOutput) for one cut point. call_kw goes to a policy
    that makes model calls."""
    keep = config.require("KEEP_TOOL_RESULTS")
    pre = prefix(entries, cut)
    out = policies.get(policy)(pre, settings, **call_kw)
    before = context.build(pre, None, keep_tool_results=keep)
    after = context.build(pre, out.annotations, keep_tool_results=keep)
    return before, after, out


def prepare_cut(cut: Cut, entries: list[Entry], record: dict, policy: str, settings: dict, **call_kw):
    """Everything up to the check call: (before, after, policy output, checks.Prepared)."""
    before, after, out = contexts(cut, entries, policy, settings, **call_kw)
    return before, after, out, checks.prepare(record, after)


def score_cut(cut: Cut, entries: list[Entry], record: dict, policy: str, settings: dict,
              **call_kw) -> tuple[dict, dict]:
    """(row of IDs and numbers, private record holding check text) for one cut point."""
    before, after, out, prep = prepare_cut(cut, entries, record, policy, settings, **call_kw)
    d = checks.decide(prep, after, **call_kw)
    flags = metrics.cut_flags(d["classes"])
    row = {"cut_id": cut.cut_id, "cut_before": cut.cut_before,
           "conv_tokens_before": before.conversational_tokens, "conv_tokens_after": after.conversational_tokens,
           "tool_tokens": after.tool_tokens,
           "pruning_rate": metrics.pruning_rate(before.conversational_tokens, after.conversational_tokens),
           "policy_model_calls": out.model_calls, "check_calls": d["calls"],
           "check_model": d["model"], "check_date": d["date"],
           "counts": metrics.class_counts(d["classes"]), **flags,
           "lost_need_ids": [r.removeprefix("need:") for r, c in d["classes"].items()
                             if r.startswith("need:") and c == "lost"]}
    private = {"cut_id": cut.cut_id, "policy_notes": checks.notes(after), "notes": prep.notes,
               "decisions": d["decisions"],
               "attempts": d["attempts"], "errors": d["errors"]}
    return row, private


def score(cuts: list[tuple[Cut, list[Entry]]], records: dict[str, dict], policy: str, settings: dict,
          **call_kw) -> tuple[list[dict], list[dict], dict]:
    """(rows, private records, summary) over cut points. Every cut needs a valid ("ok") record."""
    bad = [c.cut_id for c, _ in cuts if records.get(c.cut_id, {}).get("meta", {}).get("status") != "ok"]
    if bad:
        raise ValueError(f"no valid needs record for {bad}")
    rows, private = [], []
    for cut, entries in cuts:
        row, priv = score_cut(cut, entries, records[cut.cut_id], policy, settings, **call_kw)
        rows.append(row)
        private.append(priv)
    return rows, private, metrics.summarize(rows)
