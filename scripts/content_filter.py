"""Phase 0 content filter (PLAN.md 7 Phase 0 step 2): one MODEL call per candidate session to
find visible reversals (the seven supersession kinds of PLAN.md 3).

Usage:
  python -I scripts/content_filter.py [--limit N]          dry run: print the estimate only
  python -I scripts/content_filter.py [--limit N] --run    make the calls (cap 60 candidates)

Candidates: data/selection/candidates.json (scripts/stats.py), in rank order.
Input per call: the conversation-only view of the session: USER and ASSISTANT text with message
ids, every tool message as a one-line placeholder, native thinking excluded (pilot_entries).
Prompt: scripts/content_filter_prompt.md (fixed system prompt + transcript).
Output: strict JSON {"reversals": [{"kind", "old", "new", "reason"}]}, validated in code; an
invalid reply is retried with the validation error appended (MAX_ATTEMPTS in total), so a retry
has a different input and is cached separately. Results go to data/selection/content_filter.jsonl
(git-ignored: reasons are model text about the session).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ctxtrim import config, model
from ctxtrim.log import Log, approx_tokens
from ctxtrim.views import conversation_view  # moved to the package; same rendering

CAP = 60
MAX_ATTEMPTS = 3
KINDS = ("decision_revised", "constraint_changed", "plan_revised", "hypothesis_refuted",
         "abandoned", "question_resolved", "restated")
MAX_REASON_CHARS = 300
# One call must fit: 1M-token context window (Sonnet 5.5) and Claude Code's 10 MB stdin cap.
# approx_tokens is chars/4; the current tokenizer is about 2.5 chars/token, so 400k approx
# tokens (1.6M chars) is about 640k real tokens, leaving room for the reply.
MAX_INPUT_APPROX_TOKENS = 400_000
PROMPT_PATH = Path(__file__).resolve().parent / "content_filter_prompt.md"
CANDIDATES_PATH = config.DATA_DIR / "selection" / "candidates.json"
OUT_PATH = config.DATA_DIR / "selection" / "content_filter.jsonl"


def load_prompt() -> tuple[str, str]:
    text = PROMPT_PATH.read_text(encoding="utf-8")
    system, prompt = text.split("=== PROMPT ===\n")
    return system.removeprefix("=== SYSTEM ===\n").strip(), prompt.strip()


def build_prompt(template: str, view: str) -> str:
    return template.replace("{{TRANSCRIPT}}", view)


def parse_reply(text: str, ids: list[str]) -> list[dict]:
    """Validate a reply; return the reversals or raise ValueError with a short reason."""
    s = text.strip()
    if s.startswith("```"):
        s = s.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        obj = json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON ({e.msg})")
    if not isinstance(obj, dict) or set(obj) != {"reversals"} or not isinstance(obj["reversals"], list):
        raise ValueError('top level must be {"reversals": [...]} and nothing else')
    pos = {m: i for i, m in enumerate(ids)}
    out = []
    for i, r in enumerate(obj["reversals"]):
        if not isinstance(r, dict) or set(r) != {"kind", "old", "new", "reason"}:
            raise ValueError(f"reversal {i}: keys must be exactly kind, old, new, reason")
        if r["kind"] not in KINDS:
            raise ValueError(f"reversal {i}: unknown kind {r['kind']!r}")
        for k in ("old", "new"):
            if r[k] not in pos:
                raise ValueError(f"reversal {i}: {k} id {r[k]!r} is not in the transcript")
        if pos[r["old"]] >= pos[r["new"]]:
            raise ValueError(f"reversal {i}: old {r['old']} does not precede new {r['new']}")
        if not isinstance(r["reason"], str) or not r["reason"].strip() or "\n" in r["reason"] \
                or len(r["reason"]) > MAX_REASON_CHARS:
            raise ValueError(f"reversal {i}: reason must be one non-empty line of at most {MAX_REASON_CHARS} chars")
        out.append(r)
    return out


def retry_prompt(prompt: str, attempt: int, error: str) -> str:
    return (prompt + f"\n\n[Attempt {attempt}: your previous reply was rejected: {error}."
            " Reply again with only the JSON object described in your instructions.]")


def filter_session(sid: str, system: str, template: str, purpose: str = "content_filter") -> dict:
    view, ids = conversation_view(Log(config.NORMALIZED_DIR / sid / "log.jsonl").entries())
    prompt = base = build_prompt(template, view)
    errors = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        r = model.call(prompt, system=system, model=config.require("MODEL"), purpose=purpose)
        try:
            rev = parse_reply(r.text, ids)
        except ValueError as e:
            errors.append(str(e))
            prompt = retry_prompt(base, attempt + 1, str(e))
            continue
        return {"session_id": sid, "status": "ok", "attempts": attempt, "reversals": rev,
                "model": r.model_reported, "date": r.date, "errors": errors}
    return {"session_id": sid, "status": "invalid", "attempts": MAX_ATTEMPTS, "reversals": [], "errors": errors}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=CAP, help=f"number of candidates (max {CAP})")
    ap.add_argument("--run", action="store_true", help="make the model calls (default: dry run)")
    a = ap.parse_args(argv[1:])
    limit = min(a.limit, CAP)
    cands = json.loads(CANDIDATES_PATH.read_text())["candidates"][:limit]
    system, template = load_prompt()
    fixed = approx_tokens(system) + approx_tokens(build_prompt(template, ""))
    sizes, too_large = [], []
    for c in cands:
        view, _ = conversation_view(Log(config.NORMALIZED_DIR / c["session_id"] / "log.jsonl").entries())
        n = approx_tokens(system) + approx_tokens(build_prompt(template, view))
        sizes.append(n)
        if n > MAX_INPUT_APPROX_TOKENS or len(build_prompt(template, view).encode()) > model.MAX_STDIN_BYTES:
            too_large.append((c["session_id"], n))
    print(f"model: {config.MODEL}; candidates: {len(cands)}; calls: {len(cands)} (plus up to"
          f" {len(cands) * (MAX_ATTEMPTS - 1)} retries on invalid replies)")
    print(f"input approx tokens (chars/4, excluding any backend overhead): total {sum(sizes)},"
          f" per call min {min(sizes)} / median {sorted(sizes)[len(sizes) // 2]} / max {max(sizes)};"
          f" fixed prompt part {fixed} per call")
    print(f"too large for one call (> {MAX_INPUT_APPROX_TOKENS} approx tokens or > 10 MB): {len(too_large)}"
          + "".join(f"\n  {s} {n}" for s, n in too_large))
    if not a.run:
        print("dry run: no model calls made")
        return 0
    if too_large:
        print("refusing to run: some sessions are too large for one call; ask the human", file=sys.stderr)
        return 1
    done = set()
    if OUT_PATH.exists():
        done = {json.loads(x)["session_id"] for x in OUT_PATH.read_text().splitlines()}
    for c in cands:
        if c["session_id"] in done:
            continue
        rec = filter_session(c["session_id"], system, template)
        with OUT_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(c["session_id"], rec["status"], len(rec["reversals"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
