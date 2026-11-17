"""Named settings for the pilot (PLAN.md 4.1, 4.3, 6, 7).

Values left as None are not decided yet. Code that needs a setting must call
`require(name)` so an unset value fails loudly instead of using a guess.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# --- Models and backend (PLAN.md 4.3). Chosen by the human on 2026-11-15. ---
MODEL: str | None = "claude-sonnet-5-5"        # trimmer, LLM baselines, label proposer, live recall agent
JUDGE_MODEL: str | None = "claude-opus-5-5"  # needs lists and check calls; must differ from MODEL
BACKEND: str | None = "claude_code"      # "claude_code" or "anthropic_api"
BACKENDS = ("claude_code", "anthropic_api")
# Model IDs confirmed 2026-11-16 against platform.claude.com/docs/en/about-claude/models/overview
# (Claude API IDs `claude-sonnet-5-5`, `claude-opus-5-5`; dateless IDs are pinned snapshots).

# claude_code backend (src/ctxtrim/model.py). The CLI is not on PATH on the human's machine;
# set CTXTRIM_CLAUDE_BIN to the `claude` executable, else `claude` on PATH is used.
CLAUDE_BIN: str | None = os.environ.get("CTXTRIM_CLAUDE_BIN") or shutil.which("claude")
MODEL_TIMEOUT_S = 900          # per call; a timeout raises and nothing is cached
EFFORT: str | None = None      # None = no --effort flag (Claude Code's default); part of the cache key

# --- Fixed tool-result layer (PLAN.md 4.1, amended 2026-11-16). Approved by the human 2026-11-16. ---
KEEP_TOOL_RESULTS: int | None = 3

# --- Trimming rules and trigger (PLAN.md 4.2, Phase 4 step 3). ---
# PROTECT_LAST_N: provisional, pending human approval (set 2026-11-16 by the orchestrator).
# Counted in messages over pilot entries (tool messages included, native thinking excluded).
# Code takes it as a parameter (ctxtrim.applier); this is only the default callers pass.
PROTECT_LAST_N: int | None = 6
# Not yet set:
TRIM_TRIGGER_TOKENS: int | None = None
MIN_SAVINGS_FRACTION: float | None = None

# --- Phase 2 replay evaluator (PLAN.md 7 Phase 2). Set 2026-11-16 by the implementing agent. ---
CUT_FRACTIONS = (0.5, 0.8)  # cut points per session, as shares of the session's tokens (plan)
# Continuation shown to the needs judge: the next K plain user turns from the cut, each with
# everything up to the next plain user turn. Provisional, pending the human.
NEEDS_CONTINUATION_TURNS: int | None = 3
MAX_ATTEMPTS = 5            # needs and check calls: first try + retries with the error appended
MAX_ANCHOR_CHARS = 200      # needs anchors must be short exact quotes
MAX_WHAT_CHARS = 300        # one-line descriptions in needs records
REAL_TOKENS_PER_APPROX = 2  # dry-run estimate: measured real tokens are about 2 x chars/4
BATCH_ASK_CALLS = 100       # PLAN.md 4.3: ask before any batch of more than 100 calls

# --- Paths (PLAN.md 6). ---
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
SESSIONS_DIR = DATA_DIR / "sessions"      # raw, never edited
SWE_CHAT_DIR = SESSIONS_DIR / "swe_chat"  # downloaded SWE-chat files (pinned revision, see scripts/)
SELECTION_PATH = DATA_DIR / "selection" / "sample.json"  # Phase 0 sample: IDs and numbers only
SPLITS_PATH = DATA_DIR / "splits.json"  # Phase 0 dev/test split (tracked; read only via ctxtrim.splits)
NORMALIZED_DIR = DATA_DIR / "normalized"
LABELS_DIR = DATA_DIR / "labels"
NEEDS_PATH = LABELS_DIR / "needs.jsonl"  # session-derived text (what, anchor): git-ignored for now
PRIVATE_LABELS_DIR = LABELS_DIR / "private"  # model text about sessions (check items, notes): git-ignored
REVIEW_DIR = DATA_DIR / "review"           # human review packets (session text): git-ignored
RESULTS_DIR = REPO_ROOT / "results"
REPLAY_RESULTS_DIR = RESULTS_DIR / "replay"  # per policy setting: IDs and numbers only
CHANGELOG_PATH = RESULTS_DIR / "CHANGELOG.md"
CACHE_DIR = REPO_ROOT / ".cache" / "model_calls"  # on-disk model call cache (holds prompts; git-ignored)
CALL_LEDGER_PATH = RESULTS_DIR / "model_calls.jsonl"  # append-only: one line per call, no prompt text


def require(name: str):
    """Return a setting, raising if it has not been set."""
    value = globals().get(name)
    if value is None:
        raise RuntimeError(f"config.{name} is not set; see PLAN.md and ask the human")
    return value
