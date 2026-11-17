"""Score one policy setting on a split (PLAN.md 7 Phase 2-3).

    python -m ctxtrim.eval.run_policy --policy recency --set n_turns=4 [--split dev] [--run] [--final-run]
    python -m ctxtrim.eval.run_policy --policy compaction_recall --set target=0.4

Dry run by default: prints the cut points, the needs calls still to make, the policy's own
model calls (policies with an estimate hook, MODEL) and the check calls this setting needs,
with input-token estimates (approx = chars/4; real ~ 2 x approx). For a policy that makes
calls the dry run cannot know its output, so the check calls are an upper bound: one per cut
point. --run makes the calls: missing needs records first (needs.ensure, resumable; a second
policy finds them all present and makes no needs call), then per cut point the policy's calls
and one check call where one is needed.
The test split is refused unless --final-run is given. More than config.BATCH_ASK_CALLS planned
calls is refused (PLAN.md 4.3: ask the human).

Outputs: results/replay/<setting>.<split>.json: IDs and numbers only (per cut point: tokens,
pruning rate, class counts, the four flags, lost need ids; and the summary).
data/labels/private/checks/<setting>.<split>.jsonl (git-ignored): the notes and the check
decisions with item text, for the human review of check decisions.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from ctxtrim import config, policies
from ctxtrim.eval import needs, replay
from ctxtrim.eval.common import judge_model
from ctxtrim.eval.cuts import prefix, split_cuts


def parse_settings(pairs: list[str]) -> dict:
    """--set k=v pairs; integer values become ints, other numbers floats."""
    out = {}
    for p in pairs:
        k, _, v = p.partition("=")
        if v.lstrip("-").isdigit():
            out[k] = int(v)
        else:
            try:
                out[k] = float(v)
            except ValueError:
                out[k] = v
    return out


def run(cuts, policy: str, settings: dict, needs_path: Path | None = None, **call_kw):
    """Needs records (made only where missing), then scoring. Returns (rows, private, summary)."""
    records = needs.ensure(cuts, needs_path, **call_kw)
    return replay.score(cuts, records, policy, settings, **call_kw)


def write_outputs(key: str, split: str, policy: str, settings: dict, rows, private, summary, problems,
                  results_dir: Path | None = None, private_dir: Path | None = None) -> Path:
    results_dir = Path(results_dir or config.REPLAY_RESULTS_DIR)
    private_dir = Path(private_dir or config.PRIVATE_LABELS_DIR / "checks")
    results_dir.mkdir(parents=True, exist_ok=True)
    private_dir.mkdir(parents=True, exist_ok=True)
    out = results_dir / f"{key}.{split}.json"
    out.write_text(json.dumps({
        "policy": policy, "settings": settings, "split": split,
        "date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "policy_model": config.MODEL if policy in policies.ESTIMATES else None,
        "judge_model": config.JUDGE_MODEL, "keep_tool_results": config.KEEP_TOOL_RESULTS,
        "needs_continuation_turns": config.NEEDS_CONTINUATION_TURNS,
        "cut_problems": problems, "summary": summary, "cut_points": rows}, indent=1) + "\n")
    with (private_dir / f"{key}.{split}.jsonl").open("w", encoding="utf-8") as f:
        for p in private:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", required=True)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="policy setting")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--run", action="store_true", help="make the model calls (default: dry run)")
    ap.add_argument("--final-run", action="store_true", help="required for the test split")
    a = ap.parse_args(argv[1:])
    if a.split == "test" and not a.final_run:
        print("refusing the test split without --final-run", file=sys.stderr)
        return 2
    settings = parse_settings(a.set)
    key = policies.setting_key(a.policy, settings)
    k = config.require("NEEDS_CONTINUATION_TURNS")
    cuts, problems = split_cuts(a.split)
    for p in problems:
        print("cut problem:", p)
    records = needs.load_records()
    missing = [(c, es) for c, es in cuts if c.cut_id not in records]
    print(f"policy setting {key}; split {a.split}; cut points {len(cuts)}; judge {judge_model()}")
    print(f"needs calls: {len(missing)}; input tokens: {needs.describe(needs.estimate(missing, k))}")
    policy_sizes = []
    if a.policy in policies.ESTIMATES:
        est = policies.ESTIMATES[a.policy]
        policy_sizes = [s for c, es in cuts for s in est(prefix(es, c), settings)]
        print(f"policy calls: {len(policy_sizes)} ({config.require('MODEL')}; plus up to"
              f" {len(policy_sizes) * (config.MAX_ATTEMPTS - 1)} retries); input tokens: {needs.describe(policy_sizes)}")
        # The policy's output is unknown before its call, so the checks cannot be prepared.
        print(f"check calls: up to {len(cuts)} (one per cut point that keeps a need out of context"
              " verbatim and leaves note text); input tokens: unknown before the policy calls")
        n_checks, unknown = len(cuts), 0
    else:
        check_sizes = []
        for c, es in cuts:
            if c.cut_id in records and records[c.cut_id]["meta"]["status"] == "ok":
                _, _, _, prep = replay.prepare_cut(c, es, records[c.cut_id], a.policy, settings)
                if prep.prompt is not None:
                    check_sizes.append(prep.approx_tokens())
        unknown = len(cuts) - sum(1 for c, _ in cuts if c.cut_id in records)
        print(f"check calls: {len(check_sizes)} known" + (f" + up to {unknown} for cut points without needs records"
                                                           if unknown else "")
              + f"; input tokens: {needs.describe(check_sizes)}")
        n_checks = len(check_sizes)
    planned = len(missing) + len(policy_sizes) + n_checks + unknown
    if not a.run:
        print("dry run: no model calls made")
        return 0
    if planned > config.BATCH_ASK_CALLS:
        print(f"{planned} planned calls > {config.BATCH_ASK_CALLS}: ask the human first (PLAN.md 4.3)", file=sys.stderr)
        return 1
    rows, private, summary = run(cuts, a.policy, settings)
    out = write_outputs(key, a.split, a.policy, settings, rows, private, summary, problems)
    print(json.dumps(summary, indent=1))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
