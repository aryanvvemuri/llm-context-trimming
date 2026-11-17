"""Phase 0 statistics filter and content-filter candidate ranking.

Usage: python -I scripts/stats.py
Reads data/normalized/<session_id>/{log.jsonl,report.json} for each normalized session in
data/selection/sample.json; writes data/selection/stats.json, data/selection/candidates.json
and data/README.md (IDs and numbers only; users and repos appear as group labels U.., R..).
When data/splits.json exists (scripts/select_final.py), the README also gets a "Selected sessions"
section; rerun this script after select_final.py.

Counts (approx_tokens, chars/4), on `pilot_entries` (native thinking excluded, human decision c):
  user, assistant (text); tool = tool messages (input + result) before the tool layer,
  tool_after = the same after the amended tool layer (a cleared message is one stub line).
  conv = user + assistant; conv_share_after = conv / (conv + tool_after).
  Visible native-thinking text is counted separately for the README reasoning line only.
Eligibility (human decisions d-f): Claude Code transcript format, >= 25 `plain` user turns in
the normalized log, no fork points (rewound branches).
Ranking for the content filter (see rank_candidates).
"""

from __future__ import annotations

import json
import statistics
import sys

from ctxtrim import config, tool_layer
from ctxtrim.log import Log, approx_tokens, is_plain_user, pilot_entries, tool_input_text

MIN_USER_TURNS = 25
CONTENT_FILTER_CAP = 60
PER_GROUP_CAP = 2
CANDIDATES_PATH = config.DATA_DIR / "selection" / "candidates.json"


def session_stats(sid: str) -> dict:
    d = config.NORMALIZED_DIR / sid
    all_entries = Log(d / "log.jsonl").entries()
    report = json.loads((d / "report.json").read_text())
    entries = pilot_entries(all_entries)
    keep = config.require("KEEP_TOOL_RESULTS")
    after = tool_layer.apply(entries, keep)
    tok = {"user": 0, "assistant": 0, "tool": 0, "tool_after": 0, "tool_after_results_only": 0}
    cleared = tool_layer.cleared_ids(entries, keep)
    for e, a in zip(entries, after):
        if e.kind == "tool":
            tok["tool"] += e.tokens
            tok["tool_after"] += a.tokens
            # PLAN.md 4.1 as originally written (results cleared, inputs kept), for reference only.
            tok["tool_after_results_only"] += (a.tokens + approx_tokens(tool_input_text(e.tool_input))
                                               if e.id in cleared else e.tokens)
        else:
            tok[e.kind] += e.tokens
    thinking = sum(e.tokens for e in all_entries if e.kind == "reasoning")
    conv = tok["user"] + tok["assistant"]
    users = [e for e in entries if e.kind == "user"]
    plain = [e for e in users if is_plain_user(e)]
    th = report["thinking_blocks"]

    def share(c, rest):
        return round(c / (c + rest), 4) if c + rest else 0.0

    return {
        "session_id": sid,
        "user_turns": len(users),
        "user_turns_plain": len(plain),
        "tokens": tok,
        "conv_tokens": conv,
        "thinking_visible_tokens": thinking,
        "conv_share_before": share(conv, tok["tool"]),
        "conv_share_after": share(conv, tok["tool_after"]),
        "conv_share_after_results_only": share(conv, tok["tool_after_results_only"]),
        "conv_share_after_incl_thinking": share(conv + thinking, tok["tool_after"]),
        "tool_calls": sum(1 for e in entries if e.kind == "tool"),
        "thinking_blocks": th.get("blocks", 0),
        "thinking_with_text": th.get("with_text", 0),
        "thinking_with_signature": th.get("with_signature", 0),
        "fork_points": report["fork_points"],
        "compact_boundaries": report["compact_boundaries"],
        "models": report["models"],
    }


def eligible(r: dict) -> bool:
    return r["user_turns_plain"] >= MIN_USER_TURNS and r["fork_points"] == 0


def feasible(rows: list[dict], cap: int = PER_GROUP_CAP) -> list[dict]:
    """Greedy pick in the given order with at most `cap` per user key and per repo."""
    u, r, out = {}, {}, []
    for x in rows:
        if u.get(x["user_key"], 0) < cap and r.get(x["repo_id"], 0) < cap:
            u[x["user_key"]] = u.get(x["user_key"], 0) + 1
            r[x["repo_id"]] = r.get(x["repo_id"], 0) + 1
            out.append(x)
    return out


def pct_rank(values: list[float]) -> list[float]:
    """Percentile rank in [0, 1] of each value (ties share the mean rank)."""
    order = sorted(values)
    n = len(values)
    if n == 1:
        return [1.0]
    pos = {}
    for i, v in enumerate(order):
        pos.setdefault(v, []).append(i)
    return [statistics.mean(pos[v]) / (n - 1) for v in values]


def rank_candidates(rows: list[dict], cap: int = CONTENT_FILTER_CAP) -> list[dict]:
    """Content-filter candidates (PLAN.md 7 Phase 0 step 2; rule proposed 2026-11-16).

    Among eligible sessions: score = mean of the percentile ranks of conversational tokens after
    the tool layer and of conversational share after the tool layer (equal weight; both higher
    is better). Ties broken by session_id. Walk in score order and take a session only if its
    user key and its repo each have fewer than PER_GROUP_CAP sessions taken so far; stop at `cap`.
    """
    el = [r for r in rows if eligible(r)]
    if not el:
        return []
    a = pct_rank([r["conv_tokens"] for r in el])
    b = pct_rank([r["conv_share_after"] for r in el])
    scored = [dict(r, score=round((x + y) / 2, 4)) for r, x, y in zip(el, a, b)]
    scored.sort(key=lambda r: (-r["score"], r["session_id"]))
    return feasible(scored)[:cap]


def quartiles(xs: list[float]) -> list[float]:
    return statistics.quantiles(xs, n=4) if len(xs) >= 2 else [xs[0]] * 3


def main() -> int:
    sel = json.loads(config.SELECTION_PATH.read_text())
    skipped_path = config.DATA_DIR / "selection" / "format_skipped.json"
    skipped = json.loads(skipped_path.read_text()) if skipped_path.exists() else {}
    rows = []
    for s in sel["sessions"]:
        if s["session_id"] in skipped:
            continue
        r = session_stats(s["session_id"])
        r.update(prompt_count_metadata=s["prompt_count"], user_key=s["user_key"], repo_id=s["repo_id"])
        rows.append(r)
    out = config.DATA_DIR / "selection" / "stats.json"
    out.write_text(json.dumps({"keep_tool_results": config.KEEP_TOOL_RESULTS, "sessions": rows}, indent=1) + "\n")
    cands = rank_candidates(rows)
    CANDIDATES_PATH.write_text(json.dumps({
        "rule": rank_candidates.__doc__.strip(),
        "candidates": [{k: c[k] for k in ("session_id", "user_key", "repo_id", "score", "conv_tokens",
                                         "conv_share_after", "user_turns_plain")} for c in cands]},
        indent=1) + "\n")
    shares = [r["conv_share_after"] for r in rows]
    el = [r for r in rows if eligible(r)]
    print("normalized", len(rows), "format-skipped", len(skipped))
    print("mean share", round(statistics.mean(shares), 4), "median", round(statistics.median(shares), 4))
    print("plain>=25", sum(r["user_turns_plain"] >= MIN_USER_TURNS for r in rows), "eligible (e)+(f)", len(el),
          "under cap 2", len(feasible(sorted(el, key=lambda r: r["session_id"]))))
    print("candidates", len(cands))
    write_readme(rows, skipped, sel, cands)
    return 0


def write_readme(rows: list[dict], skipped: dict, sel: dict, cands: list[dict]) -> None:
    ulab = {k: f"U{i + 1:03d}" for i, k in enumerate(sorted({r["user_key"] for r in rows}))}
    rlab = {k: f"R{i + 1:03d}" for i, k in enumerate(sorted({r["repo_id"] for r in rows}))}
    shares = [r["conv_share_after"] for r in rows]
    plain25 = [r for r in rows if r["user_turns_plain"] >= MIN_USER_TURNS]
    el = [r for r in rows if eligible(r)]
    el_shares = [r["conv_share_after"] for r in el]
    el_conv = [r["conv_tokens"] for r in el]
    tot = {k: sum(r["tokens"][k] for r in rows) for k in rows[0]["tokens"]}
    think = sum(r["thinking_visible_tokens"] for r in rows)
    th_blocks = sum(r["thinking_blocks"] for r in rows)
    th_text = sum(r["thinking_with_text"] for r in rows)
    th_sig = sum(r["thinking_with_signature"] for r in rows)
    cand_ids = {c["session_id"]: i for i, c in enumerate(cands, 1)}
    L = []
    w = L.append
    w("# Data: Phase 0 candidate statistics")
    w("")
    w("Generated by `scripts/stats.py`. IDs and numbers only; no session text. Users and repositories")
    w("are shown as group labels (U.., R..); the mapping is in `data/selection/` (git-ignored).")
    w("")
    w("- Source: SWE-chat (`SALT-NLP/SWE-chat`, ODC-BY 1.0), revision `202b071f18e03c79df5a7287565ce2ebc5ee7756`, raw")
    w("  Claude Code transcripts under `transcripts*/`. Sample: `data/selection/sample.json` (seed 20261115;")
    w(f"  {sel['n_chosen']} sessions, {sel['total_bytes']} bytes: the first 67 from the 300 MiB budget plus the extension")
    w("  of 2026-11-16, same seed and walk, budget raised by 1 GiB; <= 3 per user key and per repo, files > 15 MB skipped).")
    w(f"- {len(skipped)} sampled transcripts are not in Claude Code format although the metadata says agent")
    w("  \"Claude Code\"; they are not normalized and not counted (`data/selection/format_skipped.json`):")
    w("  " + ", ".join(f"`{k}` ({v})" for k, v in skipped.items()) + ".")
    w("- Normalized by `src/ctxtrim/adapters/swe_chat_claude_code.py` into `data/normalized/<session_id>/log.jsonl`.")
    w("- **Token counts are approximate**: `ctxtrim.log.approx_tokens` = ceil(characters / 4), the only token")
    w("  estimate in the project (no tokenizer, no API). Swap that one function to change it.")
    w(f"- Fixed tool layer (PLAN.md 4.1) with KEEP_TOOL_RESULTS = {config.KEEP_TOOL_RESULTS} (approved by the human 2026-11-16).")
    w("  **Amendment (2026-11-16, approved by the human):** when the rule clears a tool message, one stub line")
    w("  replaces the whole tool message, input and result (PLAN.md 4.1 as written cleared only the result).")
    w("  Which messages are cleared is unchanged; the most recent KEEP_TOOL_RESULTS tool messages stay complete.")
    w("- User turns = user messages in the log (human-typed prompts incl. slash commands, `!` inputs, interrupt")
    w("  markers, queued prompts). `plain` excludes slash commands, `!` inputs and interrupt markers.")
    w("  `meta pc` = the metadata `prompt_count` used by the metadata filter; it often disagrees with the log.")
    w("- Conversational share after the tool layer = (user + assistant) / (user + assistant + tool messages after")
    w("  the layer). Native thinking is excluded (human decision, see below); `+think` adds visible thinking")
    w("  text for reference only. Reasoning written in ordinary assistant text is counted under assistant.")
    w("- Eligibility (human decisions 2026-11-16): Claude Code sessions only; >= 25 `plain` user turns counted")
    w("  in the normalized log; no rewound branches (fork points = 0).")
    w("")
    w("## Reasoning line")
    w("")
    w(f"- Native thinking blocks: {th_blocks}; with non-empty text: {th_text}; empty (signature only): {th_blocks - th_text};"
      f" with a signature: {th_sig}.")
    w(f"- Visible thinking text: {think} approx tokens; user + assistant text: {tot['user'] + tot['assistant']}.")
    w("  Empty blocks have unknown size (the text is withheld, only an encrypted signature is stored).")
    w("- Reasoning in ordinary assistant text: present inside the assistant count; not separable.")
    w("- Provider rules (platform.claude.com/docs/en/build-with-claude/thinking): the Claude models in these sessions")
    w("  keep prior turns' thinking in context; blocks must be passed back unmodified (modified blocks get a")
    w("  400 error); prior turns' thinking may be omitted outside a tool-use turn.")
    w("- **Decision (human, 2026-11-16): native thinking blocks are excluded everywhere** (context, pruning rates,")
    w("  needs lists). Code: `ctxtrim.log.pilot_entries`. The normalized logs keep the reasoning entries.")
    w("")
    w("## Summary")
    w("")
    w(f"- Normalized sessions: {len(rows)}. Mean conversational share after the tool layer: {statistics.mean(shares):.3f};"
      f" median {statistics.median(shares):.3f}. (Reference, results-only rule as PLAN.md 4.1 was written: mean"
      f" {statistics.mean(r['conv_share_after_results_only'] for r in rows):.3f}.)")
    w(f"- Pooled tokens: user {tot['user']}, assistant {tot['assistant']}, tool messages {tot['tool']} before /"
      f" {tot['tool_after']} after the tool layer; visible thinking (excluded) {think}.")
    w(f"- >= {MIN_USER_TURNS} plain user turns (e): {len(plain25)}. Of these, no fork points (f): {len(el)}"
      f" (eligible). Eligible mean share {statistics.mean(el_shares):.3f}, median {statistics.median(el_shares):.3f}.")
    w(f"- Eligible and selectable with at most {PER_GROUP_CAP} per user and per repo (greedy in session_id order,"
      f" a lower bound on the maximum): {len(feasible(sorted(el, key=lambda r: r['session_id'])))};"
      f" distinct users {len({r['user_key'] for r in el})}, distinct repos {len({r['repo_id'] for r in el})}.")
    q = quartiles(el_conv)
    w(f"- Conversational tokens (user + assistant) per eligible session: median {statistics.median(el_conv):.0f},"
      f" quartiles {q[0]:.0f} / {q[1]:.0f} / {q[2]:.0f}, min {min(el_conv)}, max {max(el_conv)}.")
    qs = quartiles(el_shares)
    w(f"- Conversational share per eligible session: min {min(el_shares):.3f}, quartiles {qs[0]:.3f} / {qs[1]:.3f} /"
      f" {qs[2]:.3f}, max {max(el_shares):.3f}.")
    w(f"- Candidates ({len(cands)}): conversational tokens min {min(c['conv_tokens'] for c in cands)}, median"
      f" {statistics.median(c['conv_tokens'] for c in cands):.0f}; share min {min(c['conv_share_after'] for c in cands):.3f},"
      f" median {statistics.median(c['conv_share_after'] for c in cands):.3f}.")
    selected_section(w, rows, sel)
    w("")
    w(f"## Content-filter candidates (cap {CONTENT_FILTER_CAP})")
    w("")
    w("Rule (proposed by the implementing agent, 2026-11-16): among eligible sessions, score = mean of the")
    w("percentile ranks of conversational tokens and of conversational share after the tool layer; walk in")
    w(f"score order, taking a session only while its user and its repo each have < {PER_GROUP_CAP} taken; stop at {CONTENT_FILTER_CAP}.")
    w("Full list with user/repo keys: `data/selection/candidates.json`.")
    w("")
    w("| rank | session_id | user | repo | plain turns | conv tokens | share after | score |")
    w("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(cands, 1):
        w(f"| {i} | {r['session_id']} | {ulab[r['user_key']]} | {rlab[r['repo_id']]} | {r['user_turns_plain']} |"
          f" {r['conv_tokens']} | {r['conv_share_after']:.3f} | {r['score']:.3f} |")
    w("")
    w("## All normalized sessions")
    w("")
    w("Tokens are approx_tokens. tool: tool messages (input + result) before -> after the tool layer. elig: e+f. cand: rank.")
    w("")
    w("| session_id | user | repo | user turns (plain) | meta pc | user | assistant | thinking (blocks/with text) |"
      " tool | share before | share after | +think | forks | compactions | elig | cand |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: r["session_id"]):
        t = r["tokens"]
        w(f"| {r['session_id']} | {ulab[r['user_key']]} | {rlab[r['repo_id']]} | {r['user_turns']} ({r['user_turns_plain']}) |"
          f" {r['prompt_count_metadata']} | {t['user']} | {t['assistant']} |"
          f" {r['thinking_visible_tokens']} ({r['thinking_blocks']}/{r['thinking_with_text']}) |"
          f" {t['tool']} -> {t['tool_after']} | {r['conv_share_before']:.3f} | {r['conv_share_after']:.3f} |"
          f" {r['conv_share_after_incl_thinking']:.3f} | {r['fork_points']} | {r['compact_boundaries']} |"
          f" {'y' if eligible(r) else ''} | {cand_ids.get(r['session_id'], '')} |")
    (config.DATA_DIR / "README.md").write_text("\n".join(L) + "\n")


def selected_section(w, rows: list[dict], sel: dict) -> None:
    """'Selected sessions' (PLAN.md 7 Phase 0 step 4): data/splits.json (scripts/select_final.py) and
    content-filter counts. IDs and numbers only. Skipped when no split exists yet."""
    cf_path = config.DATA_DIR / "selection" / "content_filter.jsonl"
    if not config.SPLITS_PATH.exists() or not cf_path.exists():
        return
    sp = json.loads(config.SPLITS_PATH.read_text())
    cf = [json.loads(x) for x in cf_path.read_text().splitlines() if x.strip()]
    by_id = {r["session_id"]: r for r in rows}
    src = {s["session_id"]: s["transcript_path"].split("/")[0] for s in sel["sessions"]}
    ok = [r for r in cf if r["status"] == "ok"]
    nrev = [len(r["reversals"]) for r in ok]
    kinds = {}
    for r in ok:
        for x in r["reversals"]:
            kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
    attempts = {}
    for r in cf:
        attempts[r["attempts"]] = attempts.get(r["attempts"], 0) + 1
    models = sorted({m for r in ok for m in (r.get("model") or [])})
    w("")
    w("## Selected sessions")
    w("")
    w(f"18 sessions chosen by `scripts/select_final.py` (seed {sp['seed']}); split in `data/splits.json`,")
    w("read only through `ctxtrim.splits.load_split`. **The test split is untouched until the final run.**")
    w("")
    w(f"- Content filter (`scripts/content_filter.py`, MODEL {', '.join(models)} via the `{config.BACKEND}` backend,"
      f" default effort): {len(cf)} candidates; status "
      + ", ".join(f"{k} {v}" for k, v in sorted(sp["content_filter_status"].items()))
      + "; attempts " + ", ".join(f"{k}: {v}" for k, v in sorted(attempts.items())) + ".")
    w(f"  Validated reversals per ok session: min {min(nrev)}, median {statistics.median(nrev):.0f}, max {max(nrev)};"
      f" total {sum(nrev)}; by kind " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])) + ".")
    w(f"- Eligible (status ok and >= {sp['min_reversals']} reversals): {sp['n_eligible']}. Draw: seeded shuffle,"
      f" at most {sp['per_group_cap']} per user key and per repo, stop at 18. Split by user (and repo):"
      f" dev {sp['n_dev']}, test {sp['n_test']}; repos straddling the splits: {sp['repos_straddling_splits']}.")
    w("- Source: SWE-chat at the revision above; the column gives the transcript directory.")
    w("- Reasoning: native thinking blocks are excluded (decision above); the thinking column is reported for")
    w("  reference only (blocks / with visible text, and visible text in approx tokens). Reasoning written in")
    w("  ordinary assistant text is inside the assistant count.")
    w("")
    w("| pilot | session_id | split | source | plain user turns | user tok | assistant tok | tool tok before -> after |"
      " conv tok | share after | thinking (blocks/with text; tok, excluded) | reversals |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for s in sp["sessions"]:
        r = by_id[s["session_id"]]
        t = r["tokens"]
        w(f"| {s['pilot_id']} | {s['session_id']} | {s['split']} | SWE-chat `{src[s['session_id']]}` |"
          f" {r['user_turns_plain']} | {t['user']} | {t['assistant']} | {t['tool']} -> {t['tool_after']} |"
          f" {r['conv_tokens']} | {r['conv_share_after']:.3f} |"
          f" {r['thinking_blocks']}/{r['thinking_with_text']}; {r['thinking_visible_tokens']} | {s['reversals']} |")
    w("")
    for lab in ("dev", "test"):
        xs = [by_id[s["session_id"]] for s in sp["sessions"] if s["split"] == lab]
        w(f"- {lab}: {len(xs)} sessions; conv tokens median {statistics.median(x['conv_tokens'] for x in xs):.0f};"
          f" share after mean {statistics.mean(x['conv_share_after'] for x in xs):.3f};"
          f" reversals total {sum(s['reversals'] for s in sp['sessions'] if s['split'] == lab)}.")


if __name__ == "__main__":
    sys.exit(main())
