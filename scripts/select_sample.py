"""Phase 0 metadata filter and bounded, deterministic transcript sample.

Usage: python -I scripts/select_sample.py
Reads data/sessions/swe_chat/{sessions,repositories}.parquet, asks the Hub only
for file sizes (no content download), and writes data/selection/sample.json
(session IDs and numbers only).

Metadata filter (candidate pool):
  - agent == "Claude Code"
  - kind == ""  (excludes "imported", "agent_review", "agent_investigate")
  - prompt_count >= MIN_USER_TURNS  (prompt_count = non-continuation user turns, per the card)
  - non-empty transcript_path (a raw transcript exists)
  - repository licence_type not missing: not null, not "", not "NONE"
Sample: shuffle the pool with SEED, walk it in that order, skip transcripts over
MAX_BYTES, keep at most PER_USER per user key and PER_REPO per repo, stop at MAX_SESSIONS
or at the first accepted transcript that would take the total over BYTES_BUDGET (hard stop,
not skip-and-continue, so the byte budget does not bias the sample toward small files).
Extension (2026-11-16, human decision g): the same seed and walk with the budget raised by
EXTRA_BYTES, so the first sample (67 sessions, 300 MiB budget) is a prefix of the new one;
main() checks this against PREVIOUS (if present) and refuses to write otherwise.
User key = user_id, or "owner:<owner_id>" when user_id is null (31% of sessions have no user_id).
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq
from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "data" / "sessions" / "swe_chat"
OUT = ROOT / "data" / "selection" / "sample.json"
PREVIOUS = ROOT / "data" / "selection" / "sample_v1_67.json"  # copy of the first sample.json
REPO_ID = "SALT-NLP/SWE-chat"
REVISION = "202b071f18e03c79df5a7287565ce2ebc5ee7756"

MIN_USER_TURNS = 25
SEED = 20261115
MAX_SESSIONS = 1000  # was 150; raised so the byte budget is what stops the walk
PER_USER = 3
PER_REPO = 3
MAX_BYTES = 15 * 1024 * 1024
FIRST_BUDGET = 300 * 1024 * 1024  # first sample (2026-11-16, 67 sessions)
EXTRA_BYTES = 1024 * 1024 * 1024   # human decision g: about 1 GB of additional transcripts
BYTES_BUDGET = FIRST_BUDGET + EXTRA_BYTES
MISSING_LICENCE = (None, "", "NONE")


def user_key(s: dict) -> str:
    return s["user_id"] or "owner:" + s["owner_id"]


def candidate_pool() -> tuple[list[dict], dict]:
    cols = ["session_id", "repo_id", "owner_id", "user_id", "agent", "kind", "prompt_count", "transcript_path"]
    sessions = pq.read_table(SRC_DIR / "sessions.parquet", columns=cols).to_pylist()
    licence = {
        r["repo_id"]: r["license_type"]
        for r in pq.read_table(SRC_DIR / "repositories.parquet", columns=["repo_id", "license_type"]).to_pylist()
    }
    steps = Counter()
    pool = []
    for s in sessions:
        if s["agent"] != "Claude Code":
            steps["not_claude_code"] += 1
        elif (s["prompt_count"] or 0) < MIN_USER_TURNS:
            steps["under_min_user_turns"] += 1
        elif s["kind"] != "":
            steps["kind_" + s["kind"]] += 1
        elif not s["transcript_path"]:
            steps["no_transcript"] += 1
        elif licence.get(s["repo_id"]) in MISSING_LICENCE:
            steps["licence_missing"] += 1
        else:
            pool.append(s)
    steps["pool"] = len(pool)
    return sorted(pool, key=lambda s: s["session_id"]), dict(steps)


def file_sizes(paths: list[str]) -> dict[str, int]:
    api = HfApi()
    sizes = {}
    for i in range(0, len(paths), 100):
        for info in api.get_paths_info(REPO_ID, paths[i : i + 100], repo_type="dataset", revision=REVISION):
            sizes[info.path] = info.size
    return sizes


def main() -> int:
    pool, steps = candidate_pool()
    sizes = file_sizes([s["transcript_path"] for s in pool])
    order = pool[:]
    random.Random(SEED).shuffle(order)
    per_user, per_repo = Counter(), Counter()
    chosen, skipped = [], Counter()
    total, stopped_by_budget = 0, False
    for s in order:
        if len(chosen) >= MAX_SESSIONS:
            break
        size = sizes.get(s["transcript_path"])
        if size is None:
            skipped["size_unknown"] += 1
        elif size > MAX_BYTES:
            skipped["over_max_bytes"] += 1
        elif per_user[user_key(s)] >= PER_USER:
            skipped["user_cap"] += 1
        elif per_repo[s["repo_id"]] >= PER_REPO:
            skipped["repo_cap"] += 1
        elif total + size > BYTES_BUDGET:
            stopped_by_budget = True
            break
        else:
            total += size
            per_user[user_key(s)] += 1
            per_repo[s["repo_id"]] += 1
            chosen.append({
                "session_id": s["session_id"],
                "user_key": user_key(s),
                "repo_id": s["repo_id"],
                "prompt_count": s["prompt_count"],
                "transcript_path": s["transcript_path"],
                "bytes": size,
            })
    pool_sizes = sorted(sizes.get(s["transcript_path"]) or 0 for s in pool)
    pool_over = sum(1 for s in pool if (sizes.get(s["transcript_path"]) or 0) > MAX_BYTES)
    out = {
        "revision": REVISION,
        "seed": SEED,
        "params": {"min_user_turns": MIN_USER_TURNS, "max_sessions": MAX_SESSIONS, "per_user": PER_USER,
                   "per_repo": PER_REPO, "max_bytes": MAX_BYTES},
        "filter_steps": steps,
        "pool_transcripts_over_max_bytes": pool_over,
        "pool_total_bytes": sum(sizes.get(s["transcript_path"]) or 0 for s in pool),
        "pool_bytes_quartiles": [pool_sizes[len(pool_sizes) * q // 4] for q in (1, 2, 3)],
        "walk_skips": dict(skipped),
        "stopped_by_budget": stopped_by_budget,
        "budget_bytes": BYTES_BUDGET,
        "n_chosen": len(chosen),
        "total_bytes": total,
        "sessions": chosen,
    }
    print(json.dumps({k: v for k, v in out.items() if k != "sessions"}, indent=1))
    if PREVIOUS.exists():
        prev = [x["session_id"] for x in json.loads(PREVIOUS.read_text())["sessions"]]
        if [x["session_id"] for x in chosen[: len(prev)]] != prev:
            print("ERROR: the previous sample is not a prefix of the new one; not writing", file=sys.stderr)
            return 1
        new_bytes = sum(x["bytes"] for x in chosen[len(prev):])
        print(f"previous sample is a prefix: {len(prev)} sessions; new: {len(chosen) - len(prev)} sessions,"
              f" {new_bytes} bytes to download")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
