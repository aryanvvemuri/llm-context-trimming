"""Phase 3 deliverable (PLAN.md 7 Phase 3): results/baselines.md from results/replay/*.dev.json.

Usage: python -I scripts/make_baselines_report.py [--replay-dir DIR] [--out FILE]

Reads only the replay result files (IDs and numbers); makes no model call. Writes:
  - a header line: dev split, date, model ids from config, stale-content rate is a lower bound;
  - one table row per policy and setting: mean conversational pruning rate, strict no-impact,
    no-impact with recall, stale-content rate, dropped-current-state rate, cut points;
  - the recency curve sorted by N;
  - per policy and setting, the losses per cut id as item refs ("need:<need_id>"; the result
    files list lost needs by id). Lost current-state items are given as a count, since the
    result files hold only counts for them.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from ctxtrim import config

COLS = ("mean_pruning_rate", "strict_no_impact_rate", "no_impact_recall_rate", "stale_content_rate",
        "dropped_current_state_rate")


def load(replay_dir: Path) -> list[dict]:
    out = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(replay_dir).glob("*.dev.json"))]
    order = {"none": 0, "recency": 1, "compaction_recall": 2, "object_level": 3}
    return sorted(out, key=lambda d: (order.get(d["policy"], 9), d["policy"], sorted(d["settings"].items())))


def setting(d: dict) -> str:
    return ", ".join(f"{k}={d['settings'][k]}" for k in sorted(d["settings"])) or "-"


def pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def report(results: list[dict], date: str) -> str:
    lines = ["# Phase 3 baselines (dev split)", "",
             f"Dev split; generated {date}; policy model `{config.MODEL}` (compaction_recall, object_level),"
             f" judge model `{config.JUDGE_MODEL}` (needs lists and checks). The stale-content rate is a lower"
             " bound: the superseded lists are known to be incomplete (PLAN.md 7 Phase 2, amendment 2).", "",
             "| policy | setting | pruning rate | strict no-impact | no-impact (recall) | stale content"
             " | dropped current state | cut points | run date |",
             "|---|---|---|---|---|---|---|---|---|"]
    for d in results:
        s = d["summary"]
        lines.append(f"| {d['policy']} | {setting(d)} | " + " | ".join(pct(s[c]) for c in COLS)
                     + f" | {s['cut_points']} | {d['date'][:10]} |")
    rec = sorted((d for d in results if d["policy"] == "recency"), key=lambda d: d["settings"]["n_turns"])
    lines += ["", "## Recency curve", "", "| N turns kept | pruning rate | strict no-impact | no-impact (recall)"
              " | stale content | dropped current state |", "|---|---|---|---|---|---|"]
    for d in rec:
        s = d["summary"]
        lines.append(f"| {d['settings']['n_turns']} | " + " | ".join(pct(s[c]) for c in COLS) + " |")
    lines += ["", "## Losses", "",
              "Lost needs as item refs per cut id; lost current-state items as a count. IDs only."]
    for d in results:
        lines += ["", f"### {d['policy']} ({setting(d)})", ""]
        any_loss = False
        for r in d["cut_points"]:
            refs = [f"need:{i}" for i in r["lost_need_ids"]]
            state_lost = r["counts"]["state"]["lost"]
            if refs or state_lost:
                any_loss = True
                lines.append(f"- {r['cut_id']}: {', '.join(refs) or 'no need lost'}"
                             + (f"; current-state items lost: {state_lost}" if state_lost else ""))
        if not any_loss:
            lines.append("- no losses")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay-dir", default=str(config.REPLAY_RESULTS_DIR))
    ap.add_argument("--out", default=str(config.RESULTS_DIR / "baselines.md"))
    a = ap.parse_args(argv[1:])
    results = load(Path(a.replay_dir))
    if not results:
        print(f"no *.dev.json files in {a.replay_dir}", file=sys.stderr)
        return 1
    Path(a.out).write_text(report(results, dt.date.today().isoformat()), encoding="utf-8")
    print("wrote", a.out, f"({len(results)} policy settings)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
