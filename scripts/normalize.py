"""Normalize the sampled raw transcripts into the log schema.

Usage: python -I scripts/normalize.py [OUT_DIR]   (default data/normalized)
For each session in data/selection/sample.json writes OUT_DIR/<session_id>/:
  log.jsonl     log entries (PLAN.md 5.1); created once, never rewritten (refuses if present)
  sources.json  entry id -> raw [line, block] positions
  dropped.jsonl one record per dropped raw item: line, block, category, tokens (no text)
  report.json   counts by category, thinking blocks, fork points
Transcripts that are not in Claude Code format (adapter.format_problem) are not normalized;
they are listed in data/selection/format_skipped.json (human decision d: Claude Code only).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ctxtrim import config
from ctxtrim.adapters import swe_chat_claude_code as adapter
from ctxtrim.log import write_new_log

FORMAT_SKIPPED = config.DATA_DIR / "selection" / "format_skipped.json"


def main(argv: list[str]) -> int:
    out_root = Path(argv[1]) if len(argv) > 1 else config.NORMALIZED_DIR
    sel = json.loads(config.SELECTION_PATH.read_text())
    skipped = {}
    for s in sel["sessions"]:
        raw = config.SWE_CHAT_DIR / s["transcript_path"]
        problem = adapter.format_problem(raw)
        if problem is not None:
            skipped[s["session_id"]] = problem
            print("not Claude Code format, skipped:", s["session_id"], problem)
            continue
        out = out_root / s["session_id"]
        if (out / "log.jsonl").exists():
            print("exists, skipped:", s["session_id"])
            continue
        entries, sources, dropped, report = adapter.convert(raw)
        write_new_log(out / "log.jsonl", entries)
        (out / "sources.json").write_text(json.dumps(sources) + "\n")
        (out / "dropped.jsonl").write_text("".join(json.dumps(d) + "\n" for d in dropped))
        (out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
        print(s["session_id"], len(entries))
    if out_root == config.NORMALIZED_DIR:
        FORMAT_SKIPPED.write_text(json.dumps(skipped, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
