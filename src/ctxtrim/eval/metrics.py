"""Metrics of PLAN.md 8 that apply in Phase 2, each defined once here.

  conversational pruning rate  1 - conv_after / conv_before for one cut point. Both are
      LiveContext.conversational_tokens of the prefix after the fixed tool layer: "before" with
      no annotations, "after" with the policy's, counting stubs, summaries and the state block
      (approx_tokens). Native thinking is excluded (pilot_entries). Averaged over cut points as
      a plain mean of the per-cut rates.
  per cut point, from the item classes (ctxtrim.eval.checks):
      strict no-impact       every need is present or captured;
      no-impact with recall  no need is lost;
      stale content          for some superseded pair the old side is present and the new side
                             is neither present nor captured;
      dropped current state  some current_state item is neither present nor captured.
  rates: the share of cut points where the flag holds.
"""

from __future__ import annotations

KEPT = ("present", "captured")


def pruning_rate(conv_before: int, conv_after: int) -> float:
    return 0.0 if conv_before == 0 else 1 - conv_after / conv_before


def cut_flags(classes: dict[str, str]) -> dict[str, bool]:
    """classes: item ref -> class, refs as in checks.items ("need:", "state:", "sup:<n>:old|new")."""
    def of(prefix):
        return [c for r, c in classes.items() if r.startswith(prefix)]
    pairs = {r.rsplit(":", 1)[0] for r in classes if r.startswith("sup:")}
    return {
        "strict_no_impact": all(c in KEPT for c in of("need:")),
        "no_impact_recall": all(c != "lost" for c in of("need:")),
        "stale_content": any(classes[p + ":old"] == "present" and classes[p + ":new"] not in KEPT for p in pairs),
        "dropped_current_state": any(c not in KEPT for c in of("state:")),
    }


def class_counts(classes: dict[str, str]) -> dict[str, dict[str, int]]:
    """Counts per item kind (need, state, old, new) and class."""
    out = {k: {c: 0 for c in ("present", "captured", "recoverable", "lost")} for k in ("need", "state", "old", "new")}
    for ref, c in classes.items():
        kind = ref.split(":")[0] if not ref.startswith("sup:") else ref.rsplit(":", 1)[1]
        out[kind][c] += 1
    return out


def summarize(rows: list[dict]) -> dict:
    """rows: one per cut point with "pruning_rate" and the four flags of cut_flags."""
    n = len(rows)

    def rate(flag):
        return sum(r[flag] for r in rows) / n if n else None
    return {"cut_points": n,
            "mean_pruning_rate": sum(r["pruning_rate"] for r in rows) / n if n else None,
            "strict_no_impact_rate": rate("strict_no_impact"),
            "no_impact_recall_rate": rate("no_impact_recall"),
            "stale_content_rate": rate("stale_content"),
            "dropped_current_state_rate": rate("dropped_current_state")}
