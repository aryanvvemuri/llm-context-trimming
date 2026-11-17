# Changelog

Each experiment: date, config, outcome.

## 2026-11-15: Phase 0 start (licence check, reconnaissance, scaffold)

- Config: no models or backend chosen yet; no model calls made; no data downloaded.
- Licence check of `SALT-NLP/SWE-chat` (HF dataset card, HF API metadata, ODC-BY 1.0 text, arXiv 2604.20779v2). Card licence tag: `odc-by`. Dataset is gated (`gated: auto`); terms were not accepted. Report sent to the human for approval (Phase 0 ASK).
- Reconnaissance from card and API metadata only.
- Repo scaffold created (`pyproject.toml`, `README.md`, layout from PLAN.md 6, `src/ctxtrim/config.py`). `git init` run; nothing committed.
- Outcome: waiting for the human's licence decision and choice of backend and models.
- Human decision (2026-11-15): BACKEND = `claude_code`; MODEL = `claude-sonnet-5-5`; JUDGE_MODEL = `claude-opus-5-5`. Model IDs still to be confirmed against provider docs before the first call.
- Human decision (2026-11-15): licence report approved. SWE-chat (ODC-BY 1.0) accepted for this research. Release form: labels, session IDs and a download script only; no session text.

## 2026-11-16: Phase 0 data (metadata filter, sample, adapter, statistics filter)

- Config: no model calls. Dataset revision `202b071f18e03c79df5a7287565ce2ebc5ee7756`. KEEP_TOOL_RESULTS = 3 (provisional, pending human approval). Tokens = ceil(chars/4) (`ctxtrim.log.approx_tokens`).
- Environment: uv, CPython 3.12.15 (arm64). Runtime deps: none. Extras: `dev` (pytest), `data` (huggingface_hub, pyarrow). `.gitignore` excludes `.venv`, `.cache`, `data/sessions/**`, `data/normalized/**` (except `.gitkeep`).
- Downloaded `sessions.parquet`, `repositories.parquet`, dataset card. 17,968 sessions; 12,417 Claude Code.
- Orchestrator decision (overrulable by the human): Claude Code sessions only.
- Metadata filter: Claude Code, `prompt_count` >= 25, `kind` == "", transcript present, repo licence not missing/"NONE" -> pool 1,178 sessions (8.15 GB of transcripts).
- Sample: seeded shuffle (seed 20261115), <= 3 per user key and per repo, skip > 15 MB, hard stop at a 300 MiB budget -> 67 sessions, 311,443,991 bytes (stopped by the byte budget, not by the 150 cap).
- Adapter `swe_chat_claude_code` + round-trip test: 67/67 sessions pass (no content lost; every raw item kept byte-exact or recorded as dropped by category).
- Finding: metadata `prompt_count` disagrees with user prompts in the raw transcripts (exact for 2/67; 27/67 have < 25 user turns in the raw log).
- Native thinking: 6,773 blocks, 1,438 with text, 5,335 empty (signature only). Recommended: exclude (pending human).
- Statistics filter: mean conversational share after the tool layer 0.225 (median 0.180) over 67; 0.251 over the 40 with >= 25 raw user turns. Tool inputs are 68% of pooled post-layer tokens. Candidates with >= 25 user turns and share >= 0.25 / 0.20 / 0.15: 16 / 23 / 27.
- Outcome: Phase 0 stopped before the content filter. ASKs to the human: share bar and the < 18 / < 25% conditions, prompt_count discrepancy, byte budget, KEEP_TOOL_RESULTS, thinking decision, adapter decisions. See `data/README.md`.
- Review (2026-11-16, orchestrator): Phase 0 code reviewed, 86 tests pass. Recomputed conversational share: 0.225 mean with tool results cleared only (plan 4.1 as written); 0.566 if a cleared tool message also drops its input. Decision on this, native thinking, and KEEP_TOOL_RESULTS pending with the human. data/selection/ added to .gitignore.

## 2026-11-16: Human decisions; enlarged sample; candidate ranking; model interface; content filter (not run)

- Human decisions (2026-11-16):
  a. Fixed tool layer amendment: when the 4.1 rule clears a tool message, one stub line replaces the whole tool message (input and result). Which messages are cleared is unchanged; the most recent KEEP_TOOL_RESULTS tool messages stay complete. Note added under PLAN.md 4.1 (original text unchanged); stated in `data/README.md`. Code: `tool_layer.apply`.
  b. KEEP_TOOL_RESULTS = 3 approved.
  c. Native thinking blocks excluded everywhere (context, pruning rates, needs lists); visible-thinking tokens reported in `data/README.md` only. Code: `ctxtrim.log.pilot_entries` (the one place); logs keep the reasoning entries.
  d. Claude Code sessions only.
  e. ">= 25 user turns" counted in the normalized log with the `plain` count (no slash commands, `!` inputs, interrupt markers).
  f. Sessions with rewound branches (fork_points > 0) are left out.
  g. Sample extended: same seed and walk, budget raised by 1 GiB (300 MiB -> 1324 MiB), MAX_SESSIONS 150 -> 1000 so the byte budget stops the walk, <= 3 per user key and per repo, files > 15 MB skipped.
- Sample: 295 sessions, 1,386,061,153 bytes; the first 67 are a prefix (checked by `select_sample.py`); 228 new, 1,074,617,162 bytes downloaded, no size mismatch. Backup of the first sample: `data/selection/sample_v1_67.json`.
- 7 of the 228 new transcripts are not in Claude Code format although metadata says agent "Claude Code" (other agents' JSONL, one non-JSONL file). New gate `adapter.format_problem`; they are not normalized (`data/selection/format_skipped.json`). 221 new sessions normalized into new dirs; the 67 existing normalized outputs were regenerated in a scratch dir and are byte-identical (nothing overwritten). Round-trip test: 288/288 pass.
- Statistics (amended tool layer, thinking excluded): mean conversational share after the layer 0.580, median 0.585 over 288 (0.229 with the results-only rule as 4.1 was written). Plain user turns >= 25: 152; and fork_points = 0: 99 eligible (mean share 0.614, median 0.590). Selectable at most 2 per user and per repo (greedy): 93. Conversational tokens per eligible session: median 15,630, quartiles 9,302 / 15,630 / 26,269 (min 2,710, max 69,540).
- Content-filter candidates: 60, ranked by mean percentile rank of conversational tokens and share, at most 2 per user and per repo (`data/selection/candidates.json`, rule in `scripts/stats.py:rank_candidates`; proposed, pending human approval).
- Model interface `src/ctxtrim/model.py` (one `call`, on-disk cache keyed by backend, backend settings, model, system and prompt; append-only ledger `results/model_calls.jsonl`) and the `claude_code` backend. Model IDs `claude-sonnet-5-5`, `claude-opus-5-5` confirmed in Anthropic's models overview.
- Smoke test: not completed. The first call (claude-sonnet-5-5) failed before any model request: "Not logged in · Please run /login" (no Claude Code CLI login on this machine; the CLI is not on PATH, only the copy bundled with the desktop app). Nothing cached; ledger has 1 error line. No Opus call attempted.
- Content filter `scripts/content_filter.py` + `scripts/content_filter_prompt.md` written, not run. Dry run: 60 calls (MODEL, + up to 120 retries), 2,209,856 approx input tokens (median 36,098, max 99,769 per call), 0 sessions too large for one call. Per-call backend overhead unknown until the smoke test runs.
- Tests: 345 pass.
- Review (2026-11-16, orchestrator): tool-layer amendment, pilot_entries, model.py (claude_code backend, cache, ledger) and content filter script reviewed; 345 tests pass. No real model call has succeeded yet (CLI not logged in). Candidate ranking rule accepted by the orchestrator, flagged to the human.
- 2026-11-16 (orchestrator): CLI logged in by the human. Smoke test passed for claude-sonnet-5-5 and claude-opus-5-5 (backend reports the requested IDs; no tools, MCP servers, skills or slash commands; repeat call served from cache). Fixed overhead about 630 input tokens per call from Claude Code itself. model.py leak check changed to tolerate plugins compiled into the binary (path 'builtin'), still rejecting any other plugin, skill or slash command; test added (346 pass). Content filter started: 60 candidates, MODEL=claude-sonnet-5-5, default effort. First two calls: 95k and 177k real input tokens (real tokens are about 1.6-2.1x the chars/4 estimate).

## 2026-11-16: Content filter outcome; final selection of 18; dev/test split (Phase 0 steps 2, 4, 5)

- Content filter config: `scripts/content_filter.py` + `scripts/content_filter_prompt.md`; MODEL `claude-sonnet-5-5` (reported by the backend for every ok record) via the `claude_code` backend, default effort (no `--effort`), 60 candidates from `data/selection/candidates.json`, up to 3 attempts per session (a retry appends the validation error). Calls made 2026-11-16 18:32-18:42 UTC.
- Content filter outcome: 60 records; 57 ok, 3 invalid after 3 attempts (validation errors over all 9 failed attempts: 6 "not valid JSON (Extra data)", i.e. a JSON object followed by prose; 3 "old id does not precede new id"). Attempts: 1 for 52, 2 for 5, 3 for 3. Validated reversals per ok session: min 1, median 9, max 22, total 537. Ledger for the run: 71 calls (60 + 11 retries), all cache misses, 0 errors; 4,889,953 input tokens (input + cache creation + cache read), 55,124 output tokens; reported list cost $19.88 (about 2.2x the chars/4 dry-run estimate of 2.21M).
- Selection rule (orchestrator decision, `scripts/select_final.py`): eligible = status ok and >= 5 validated reversals (54 sessions). Seeded draw, seed **20261116**: eligible sorted by session_id, `random.Random(20261116).shuffle`, walk and take a session only while its user key (user_id, else `owner:` + owner_id) and its repo each have < 2 taken; stop at 18 (reached). Not chosen by reversal count. Pilot ids s001..s018 in draw order.
- Split by user (seed 20261116, fresh `random.Random`): sessions grouped into components linked by shared user key or repo, components shuffled, dev = first subset (depth-first, in that order) summing to 6. Result: dev s002, s003, s011, s013, s015, s017; test s001, s004-s010, s012, s014, s016, s018. Users in both splits: 0; repos in both: 0 (no fallback needed). Dev: 5 users, 5 repos; test: 11 users, 10 repos.
- Outputs: `data/splits.json` (tracked: pilot id, session_id, split, numbers; no user/repo ids, no text); `data/selection/final_selection.json` (git-ignored, adds user key and repo). `data/README.md` regenerated with a "Selected sessions" section (`scripts/stats.py`; stats.json and candidates.json regenerated byte-identical).
- **Test split guard:** `ctxtrim.splits.load_split(name)` is the single way to list a split's sessions. The test split is untouched from now until the final run.
- Acceptance (Phase 0): all 18 load via `ctxtrim.log.Log`; the real-data round-trip test covers them (new test `test_round_trip_covers_every_selected_session`) and passes. Tests: 353 pass (`.venv/bin/python -m pytest -q`). No model calls made in this step.
- Review (2026-11-16, orchestrator): final selection and split reviewed (353 tests pass; data/splits.json holds IDs and numbers only). Phase 0 accepted: licence approved, all 18 sessions load, round-trip passes. Budget so far: 77 calls, 4.89M input tokens, $19.88 list. Phase 1 (context builder, applier) started; PROTECT_LAST_N provisional 6 pending the human.

## 2026-11-16: Phase 1 step 1: context builder and applier

- Config: no model calls. KEEP_TOOL_RESULTS = 3. PROTECT_LAST_N = 6 (messages over pilot entries; provisional, pending human approval; passed as a parameter, config holds the default only). TRIM_TRIGGER_TOKENS and MIN_SAVINGS_FRACTION left unset.
- `src/ctxtrim/context.py`: `build(entries, annotations, keep_tool_results)` = pilot_entries -> tool_layer.apply -> accepted ops (one stub per op at the first removed message; ids in log order) -> state block (5.4, latest value per key, keys in first-appearance order, key shown as given). `LiveContext` exposes items (Entry or Stub), `verbatim_ids`, `covered_by`, `state_sources`, `text()`, and tokens split into conversational (user + assistant entries + stubs + state block) and tool. `Annotations` (immutable; thread membership accumulates across runs, title/status from the latest) serialises to Thread/Annotation log records; nothing writes a log. `api_messages` / `validate_api_messages`: system = system prompt + state block; tool messages rendered as assistant text; a stub takes the role of its first removed message; same-role items merged. Rules checked against platform.claude.com/docs/en/api/messages/create and /build-with-claude/working-with-messages.
- `src/ctxtrim/applier.py`: validates a 5.5 output and returns new Annotations plus the log records for that output; any error rejects the whole output with every error listed. Rules defined here (pending human confirmation): a user message is removable only if some state update has it as `source`; a thread permits removing a member if the thread is resolved or abandoned or the member is the `old` side of a supersession; messages in no thread are not restricted by the thread rule; ops may not target tool messages.
- Acceptance: with no annotations, built context = pilot entries (tool layer off) and = tool_layer.apply output (tool layer on) on synthetic data and on the 6 dev sessions; base contexts of the 6 dev sessions are valid API sequences. Applier tests cover every rejection case. Tests: 392 pass (`.venv/bin/python -m pytest -q`).
- Review (2026-11-16, orchestrator): Phase 1 context builder and applier reviewed (392 tests pass). Phase 1 accepted: no-annotation context equals the original session on synthetic data and the 6 dev sessions; applier rejection tests pass; repeated model call served from cache (verified with real calls). One fix requested: removed user messages must be pointed to by a visible state-block line (source or supersedes). Phase 2 evaluator code started (no model calls until prompts are reviewed).

## 2026-11-16: Applier review fix; Phase 2 replay evaluator (code only, no model calls)

- Applier `user_message` rule changed (orchestrator review): after merging an output, every removed user message (this output or accepted ops) must be pointed to by a rendered state line (latest per key) as `source` or `supersedes`; an output that removes such a message without that, or whose state updates orphan an earlier-removed one, is rejected.
- Shared code moved into the package: `log.is_plain_user` (was `scripts/stats.py` prefixes), `views.conversation_view` (was `scripts/content_filter.py`; rendering unchanged). `context.Stub.body` added; a bare evict renders as `[evicted mX] → recall("mX")`.
- Phase 2 code: `eval/cuts.py`, `eval/needs.py` + `needs_prompt.md`, `eval/checks.py` + `check_prompt.md`, `eval/metrics.py`, `eval/replay.py`, `eval/run_policy.py`, `policies/{base,none,recency}.py`, `scripts/make_review_packets.py`. Config: CUT_FRACTIONS (0.5, 0.8), NEEDS_CONTINUATION_TURNS = 3 (provisional), MAX_ATTEMPTS 3, MAX_ANCHOR_CHARS 200.
- Dev cut points: 12, no cut problems. Needs dry run: 12 JUDGE_MODEL calls, 258,932 approx input tokens (~518k real at 2x), per call 9,089 / 17,925 / 47,595 approx (min / median / max).
- `.gitignore`: data/labels/needs.jsonl (session-derived `what`/`anchor`; release form is the human's decision at Phase 6), data/labels/private/, data/review/.
- Tests: 429 pass. No model calls made.
- Review (2026-11-16, orchestrator): Phase 2 evaluator code, needs prompt and check prompt reviewed (429 tests pass). Orchestrator decisions: K=3 continuation turns kept (provisional); s015.c2 cut at 93.5% accepted (no plain user turn nearer 80%); cut placement stays on raw session tokens; recency changed to one bare stub per run of consecutive conversational messages (was one per message). Needs generation started on the 12 dev cut points: JUDGE_MODEL claude-opus-5-5, default effort, estimate ~520k real input tokens.
- 2026-11-16 (orchestrator): needs records generated for all 12 dev cut points (JUDGE_MODEL claude-opus-5-5, default effort, K=3): 21 calls incl. retries, 0.96M input tokens, $8.12 list. s011.c2 failed 5 attempts with the original retry prompt; changes made by the orchestrator: retry prompt now shows the rejected reply with its errors (common.call_validated), MAX_ATTEMPTS 3 -> 5, invalid needs records are retried on rerun (latest line per cut_id wins). After that s011.c2 validated on attempt 2. Acceptance: none = 0% pruning, 100% on both no-impact scores, 0 check calls; recency scored with no new needs calls. Dev recency (mean pruning / strict / recall-allowed / stale / dropped-state): N=10 0.589/0.25/0.25/0.0/0.833; N=5 0.772/0/0/0/1.0; N=2 0.878/0/0/0/1.0. Totals so far: 94 calls, 5.85M input tokens, $28.00 list. Human review packets written to data/review/needs/ (10 cut points, first 3 blind); review pending. Check-decision review deferred until an LLM baseline has produced check calls (none and recency make none).

## 2026-11-16: needs review packets re-laid out (human asked: "Reorganise first")

- Why: the first packets totalled ~890 KB with the full prefix first; far more than the plan's "about one hour".
- Change (Opus medium agent, reviewed line by line by the orchestrator): `scripts/make_review_packets.py` needs mode only. Marked packets (04 to 10): how-to, continuation, the model's record one item at a time with an excerpt of each source message (anchor marked `>>>...<<<`; messages over 1500 chars cut to 600 chars each side of the anchor, with `[... N chars omitted ...]`), missing/verdict boxes, then the full prefix as a reference appendix. Blind packets (01 to 03): continuation first, then prefix, then the blank form; no record text. OPEN_AFTER files: same per-item layout with excerpts.
- Unchanged: the 10 cut points and their order (same seed), the needs records, file names, `selection.json`, checks mode, the pass rule. No model calls.
- Tests: 434 pass (5 new in `tests/test_review_packets.py`).
- Reading before the appendix in the marked packets: 24 to 46 KB each, ~235 KB in total (was ~605 KB with the prefix first). Blind packets unchanged in size (52, 189, 33 KB): the prefix must still be read. Still likely more than one hour in total; reported to the human.
- Limitation told to the human: excerpts show whether listed items are right, not what is missing; the blind three are the main check on omissions.

## 2026-11-16: human review replaced by Opus medium review (human's decision)

- The human said no human review is required and to replace the human review parts with an Opus medium review. PLAN.md amended at Phase 2 (covers the needs review, the 15 check decisions and the later 10 supersession links). Other ASK stops and the DECISION POINT unchanged (orchestrator's reading of the instruction; told to the human).
- Protocol kept: 10 dev cut points, first 3 blind, same pass rule (9-10 continue, 7-8 ASK, <7 fix prompt and redo). One fresh Opus medium agent per cut point; reviews written to `data/review/needs/opus_review/` (git-ignored).
- Limitation for the report: reviewer and judge are the same model (`claude-opus-5-5`); the review no longer limits shared blind spots. These reviewer runs go through the Agent tool, not `ctxtrim.model`, so they are not in the call cache or ledger.

## 2026-11-16: Opus review of needs records, round 1: 0 of 10 agreed

- 10 fresh Opus medium reviewers (3 blind, 7 marked; all report reading the full prefix; blind ones report writing their own list first). Reviews in `data/review/needs/opus_review/` (git-ignored).
- Verdicts: 0 agreed, 10 not agreed. Record items reviewed: 173; judged wrong: 9 (incomplete sources; 2 ordinary-progress pairs listed as superseded). Missing items judged verdict-altering: 52 (most are superseded pairs: reworded user messages, agent claims later corrected, several stale messages for one replacement; then standing decisions absent from current_state; a few concrete facts used by the continuation).
- Orchestrator spot-check of 3 "missing superseded" claims against the logs: all 3 real.
- Rule applied (<7 agreed): fix the needs prompt, regenerate, review again. Only 12 dev cut points exist, so the second 10 cannot be fresh cut points; a new seeded draw of 10 is not needed and the same 10 are reviewed again by fresh reviewers (deviation from "a fresh 10", noted).
- Prompt changes (`needs_prompt.md`, by the orchestrator): needs include concrete facts relied on; current_state includes long-standing decisions; superseded must be exhaustive, with reworded user messages, corrected agent claims, and one pair per stale message; more examples of ordinary progress; a Sources rule (where stated in full, original over recap); "when unsure, leave it out" replaced by a completeness instruction.
- Old records kept as `data/labels/needs.v1.jsonl` (ignored). Regeneration: 12 calls, ~0.53M real input tokens estimated. Replay results for `none`/`recency` must be rescored after this.
- Reviewer caveat noted, no change made: a message can be the old side of a pair and also a source of a live need; the stale flag fires only when the new side is not kept, so keeping both is not penalised.

## 2026-11-16: Opus review of needs records, round 2: 0 of 10 agreed; stopped, ASK the human

- Regenerated all 12 dev records with the revised prompt: all valid (4 needed a retry). Totals over 12 records, round 1 -> round 2: needs 75 -> 96, current_state 75 -> 129, superseded 48 -> 123. Round 1 packets and reviews kept in `data/review/needs_round1/` (ignored).
- Round 2 review, same 10 cut points, 10 fresh Opus medium reviewers: 0 agreed. Items reviewed 287; judged wrong 24 (8%; round 1: 9 of 173, 5%). Missing items judged verdict-altering: 93 (round 1: 52). Of the 93, about 8 are needs, about 12 current_state, the rest superseded pairs.
- Orchestrator's reading: (1) item precision is high in both rounds (92 to 95%); (2) needs lists are close to complete (about 1 missing need per cut point); (3) the superseded list has no reachable "complete" state under the current definition: the more pairs the record lists, the more the reviewers find (every answered question, every restatement of a stale fact), and reviewers contradict each other across rounds (a pair one reviewer asked for in round 1 was judged wrong, "floated, not adopted", in round 2). The "nothing verdict-altering missing" bar therefore cannot be met by rewording the prompt.
- Not done: a third prompt revision (would be tuning against an unreachable bar). Replay results in `results/replay/` were scored with the round 1 records and are now out of date; rescore after the human decides.
- Reviewer cost (Agent tool, outside the ledger): about 2.4M subagent tokens over the two rounds. Needs regeneration: 12 calls plus 6 retries.

## 2026-11-16: size of the prize on dev (human asked "is it worth continuing")

- Computed from the round 2 needs records, 12 dev cut points, approx tokens, conversational messages of the prefix (no model calls):
  conversational tokens at the cut: median 10.7k, mean 12.9k; conversation as share of raw prefix (tool text included): 0.04 to 0.29, median 0.17.
  share of conversational tokens in messages marked old side of a superseded pair: median 0.22, mean 0.24 (range 0.08 to 0.42);
  same, excluding messages that are also a source of a need or current_state item: median 0.12, mean 0.15;
  share in messages that are a source of a need or current_state item: median 0.35; of a need only: 0.20.
- Caveats: superseded lists are incomplete (review), so the stale share is a lower bound; 6 sessions; message-level only.
- Orchestrator's recommendation to the human: do not build the trimmer as planned; see conversation.

## 2026-11-16: human chose to continue; round 2 needs records accepted

- The orchestrator recommended stopping (small prize). The human replied "Lets continue". Read as: continue the plan, accepting the round 2 records under the precision-based rule the orchestrator had proposed (PLAN.md Amendment 2). Needs prompt frozen. Stale-content rate to be reported as a lower bound.
- Next: rescore `none` and `recency` against the round 2 records (no model calls), then Phase 3 baselines.

## 2026-11-16: Phase 3 baselines built and reviewed; dev runs started

- Rescored against round 2 records (no model calls): `none` 0% pruning, 100%/100%; recency N=40/20/10/5/2: pruning 0.02/0.23/0.59/0.77/0.88, no-need-lost 0.92/0.67/0.08/0/0, dropped current state 0.08/0.67/1/1/1, stale content 0 throughout. Round 1 results kept in `results/replay_round1/`.
- Opus medium agent built `policies/compaction_recall.py`, `policies/object_level.py` (+ prompts), `scripts/make_baselines_report.py`, `tests/test_baselines.py`; 460 tests pass. Orchestrator read both policies and both prompts in full: accepted without edits. Agent also fixed `applier.protected_ids` for prefixes shorter than N (no effect on real prefixes), added `model_id` to `common.call_validated`, an `estimate` hook, and LLM-policy dry runs in `run_policy`.
- Design choices accepted: compaction summary budget = max(150, 5% of conversational tokens); `target` setting picks whole oldest turns; object_level joins adjacent fold summaries into one stub; an empty op list is valid.
- Batches (each at most 24 calls, under the 100-call ASK limit): compaction_recall and object_level at target 0.4 and 0.7 on dev; estimated policy input about 1.5M real tokens in total, plus up to 48 check calls.

## 2026-11-16: Phase 3 dev results; check review; DECISION POINT (ASK)

- Runs (dev, 12 cut points, MODEL claude-sonnet-5-5, judge claude-opus-5-5): 48 policy calls, 48 check calls, all valid. `results/baselines.md` written.
- Per cut point (pruning / strict / no-need-lost / stale content / dropped current state):
  compaction_recall 0.4: 0.43 / 0.25 / 0.58 / 0 / 0.33; compaction_recall 0.7: 0.72 / 0.08 / 0.42 / 0 / 0.92;
  object_level 0.4: 0.63 / 0 / 0.08 / 0 / 1.0 (overshot its target); object_level 0.7: 0.79 / 0 / 0.08 / 0.25 / 0.92;
  recency N=20: 0.23 / 0.67 / 0.67 / 0 / 0.67; N=10: 0.59 / 0.08 / 0.08 / 0 / 1.0.
- Per item (share lost; needs, state): compaction_recall 0.4: 0.05, 0.01; 0.7: 0.09, 0.02; object_level 0.4: 0.24, 0.17; 0.7: 0.27, 0.18; recency N=20: 0.10, 0.19; N=10: 0.40, 0.53.
- Check review (one Opus medium reviewer, 15 seeded decisions of 1047): 4 judged wrong, all on "cue", all judge-too-strict; 0 disagreements on "captured". Rule (more than 3 wrong: fix the check prompt, repeat) is triggered. Not yet done: held for the human's decision at the decision point. Effect if uncorrected: "recoverable" is undercounted, so no-need-lost scores of note-leaving policies are understated; strict scores are unaffected.
- Decision point per PLAN: stale content without its replacement is rare for every baseline (0 of 12 cut points, except object_level 0.7 at 3 of 12; lower bound). Dropped current state is common per cut point but rare per item for compaction_recall (1 to 2% of state items).
- Budget to date through the ledger: 207 calls, 8.04M input tokens, $42.54 list. Outside the ledger: reviewer and coding agents, roughly 3M tokens.
- Orchestrator's recommendation: stop before Phase 4.

## 2026-11-17: published

- Human asked to push everything to github.com/aryanvvemuri/llm-context-trimming with results in the README. README rewritten (status, findings, dev table, limitations, cost). `.gitignore` now also excludes `data/labels/needs.v1.jsonl` (session-derived text) and `.DS_Store`. Session text and everything derived from it stay local, per the "labels only" release decision; needs records are not released yet (they hold `what` and `anchor` text).
