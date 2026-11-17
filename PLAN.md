# PLAN (lightweight): Thread- and Supersession-Aware Trimming of Conversation Context in Long-Running Agents

You are the implementing agent. This file is self-contained: read all of it before writing code. Work through the phases in order. Do not start a phase until the previous one passes its acceptance check. Stop and ask the human at every point marked **ASK**.

This is a deliberately small pilot. Prefer the simplest thing that gives an honest number. Do not add evaluation machinery, baselines, models or labelling beyond what is written here.

---

## 1. Goal

Build and evaluate a system that keeps a long-running LLM agent's context small by removing **conversational and reasoning content** that is stale or superseded, without losing anything the agent later needs.

**The target is what the user and the agent say and think, not tool output.** Tool results are the easy case and are handled by one fixed rule (4.1) applied identically under every policy.

Three components:

1. **Append-only log**: the complete, never-edited record of the session.
2. **Trimmer**: a separate LLM that groups messages into conversational threads, detects which have been superseded, and emits structured operations that shrink the live context.
3. **Recall path**: stubs left in context plus a tool and a skill, so the main agent can fetch evicted content from the log.

The claim to test: **for conversation and reasoning content, trimming by thread and supersession loses less needed information, at the same pruning rate, than keeping the last N turns, summarising the whole history (compaction) or letting an LLM drop messages by predicted usefulness, when those alternatives are given the same recall tool.**

The outputs are a pilot result and a small releasable benchmark. With this few sessions the result will be suggestive, not conclusive; report it that way.

### Non-goals

- Smarter trimming of tool results.
- Cross-session memory, vector databases, knowledge graphs.
- Splitting messages into smaller spans. The unit is the whole message. (Future work.)
- Local or open-weight models, fine-tuning, distillation. (Future work.)
- A full live agent benchmark.
- Using the human's personal or work sessions. All data is public.

---

## 2. Background (what already exists)

- **Self-GC** (arXiv 2607.00692): a side-channel planner LLM decides, per context object, whether to fold, mask or prune, with recoverable storage. Evaluated by replaying real sessions. Closest prior work. It predicts future usefulness per object; it does not model which idea replaced which.
- **Focus / Active Context Compression** (arXiv 2601.07190): the agent consolidates learnings into a knowledge block and prunes raw history.
- **Mem0** (arXiv 2504.19413) and **Zep / Graphiti** (arXiv 2501.13956): cross-session memory with LLM-chosen updates and fact invalidation.
- **Compaction**: the standard production approach. When context is nearly full, an LLM summarises the history into one block.
- **OpenDev adaptive context compaction** (arXiv 2603.05344): compaction where the full history is written to a file the summary points to, so the agent can read details back. Reference design for the `compaction_recall` baseline.
- **Anthropic context editing** (https://platform.claude.com/docs/en/build-with-claude/context-editing): rule-based clearing of old tool results. Reference design for the fixed tool-result layer.

What this project adds: explicit **conversational threads** and **supersession links** between messages as the basis for trimming, an explicit **current-state block**, and a small labelled set of long sessions for measuring both.

---

## 3. Definitions

- **Message**: one user message, one assistant message, or one tool call with its result, as logged. Messages are the unit the trimmer works on.
- **Thread**: a set of messages about one topic or line of work (for example "which database to use", "why the auth test fails"). Threads interleave; a message may belong to more than one.
- **Supersession**: message B makes message A stale. Kinds:
  1. `decision_revised`: a later decision replaces an earlier one on the same question.
  2. `constraint_changed`: a user instruction or requirement overrides an earlier one.
  3. `plan_revised`: a newer plan or task list replaces an older one.
  4. `hypothesis_refuted`: an explanation that was later shown wrong or replaced.
  5. `abandoned`: an approach that was discussed or tried and dropped. Details are dead; the lesson is not.
  6. `question_resolved`: a clarification exchange or debate that reached an answer. The back-and-forth is dead; the answer is not.
  7. `restated`: the same content repeated later. The older or longer copy is redundant.
- **Not supersession** (the trimmer must not confuse these): an alternative that was floated but not adopted; a refinement that adds to an earlier statement without replacing it; a statement about a different scope ("use Postgres in prod" does not override "use SQLite in tests").
- **Live context**: what the main agent actually receives on its next call.
- **Stub**: a one-line placeholder left in live context for removed content, carrying a pointer into the log.
- **Fold**: replace messages with a short summary plus pointer. **Evict**: replace with a stub only.

---

## 4. Architecture

```
agent loop ──► append-only log (JSONL, never edited)
     │
     ▼
context builder ──► live context = system prompt + skill
     ▲                            + state block
     │                            + stubs
 trimmer (separate LLM)           + surviving messages, in original order
 emits operations only            + last N messages verbatim (protected)
     │
     ▼
 applier (plain code): validates and applies operations to a copy

main agent ──► recall(id) / search_log(query) ──► reads the log
```

### 4.1 Fixed tool-result layer

Before any policy runs, apply one fixed rule to tool results and keep it constant: clear a tool result to a stub when a later call re-reads or edits the same path or re-runs the same command, and clear all but the most recent `KEEP_TOOL_RESULTS` results. Policies and evaluation calls see each tool result as a one-line placeholder. Report tool tokens and conversational tokens separately.

> **Amendment (2026-11-16, approved by the human).** When the rule above clears a tool message, the one-line stub replaces the whole tool message, both the tool input and the result, not only the result. The rule for which messages are cleared is unchanged, and the most recent `KEEP_TOOL_RESULTS` tool messages stay complete. `KEEP_TOOL_RESULTS = 3` (approved).

### 4.2 Rules that must hold

- The log is append-only. Threads and annotations are appended as separate records.
- The trimmer outputs operations, never rewritten context. Only the applier changes live context.
- The last `PROTECT_LAST_N` messages and the current user request are never trimmed.
- Every removal leaves a stub with a pointer.
- **User words get extra protection.** A user message that states a requirement, constraint, preference or decision may be removed only if its content is captured in the state block with a pointer. When unsure, keep it.
- A message is removable only when every thread it belongs to permits it.
- After trimming, the message sequence must still be valid for the model API (role alternation, tool-call pairing).
- Abandoned approaches and refuted hypotheses are always folded with the lesson, never evicted.
- **What counts as reasoning.** "Reasoning" means agent reasoning text that is actually present in the context the agent would receive on its next call. That is always reasoning written in ordinary assistant messages. Provider-native thinking blocks count only if they persist in later turns' context in the collected sessions and the provider allows them to be removed; read the provider's current documentation, because some providers sign these blocks, forbid editing them, or drop them from earlier turns already. Phase 0 measures which case applies. If native blocks do not persist, exclude them everywhere: from context, from pruning rates and from the needs lists.

### 4.3 Models and cost

**Two Claude models behind one backend, both named in config:**

- `MODEL` (Claude Sonnet): the trimmer, the LLM baselines, the label proposer and the agent in the live recall check.
- `JUDGE_MODEL` (a different Claude model, the strongest available if usage allows): the needs lists and the check calls. A model must never grade text it wrote, so `JUDGE_MODEL` must differ from `MODEL`.

Confirm current model IDs from the provider's documentation before the first call. **ASK** the human which two models to use. Log the exact model ID and the date with every result; a hosted model cannot be pinned for others to rerun, so the record is the only way to say what produced a number.

**ASK** the human at the start which backend to use behind the single model interface:

- `claude_code`: Claude Code in non-interactive mode, drawing on the human's existing plan. Each call must be a clean model call: no tools, a fixed system prompt, an empty working directory, a fresh context.
- `anthropic_api`: the paid API, with a spending cap the human sets.

Read the current documentation for whichever is chosen before writing the backend.

Rules:

- Cache every model call on disk, keyed by model and full input. A rerun must not repeat a call whose input is unchanged.
- Before any batch of more than 100 calls, print the estimated calls and tokens and **ASK**.
- Expected total for the whole project: on the order of 1,200 calls and 15 to 25 million input tokens. If an estimate exceeds twice that, stop and **ASK**.
- Develop on 3 dev sessions. Run the full dev split only when a stage is finished.

**Known weakness: the judge is from the same model family.** Using a different model for the needs lists and checks stops a model grading its own text, but two Claude models can share blind spots. Limit the damage: decide as much as possible in code (5.6 anchors), run every check in a fresh context, and have the human review the needs lists and a sample of checks (Phase 2). State this limitation in the report.

**Blinding the checks.** Before a check call, render every policy's summaries, stubs and state lines in one plain format (text plus pointer, no policy-specific labels or layout), and do not name the policy. This hides most of the giveaway but not all of it: one summary block looks different from many stubs. Note that as a limitation.

---

## 5. Schemas

### 5.1 Log entry (one JSON object per line, `log.jsonl`)

```json
{"id": "m0042", "ts": "…", "kind": "user | assistant | reasoning | tool", "tool_name": null, "tool_input": null, "content": "…full text…", "tokens": 640}
```

### 5.2 Thread record

```json
{"thread_id": "t03", "title": "Which database to use", "status": "active | resolved | abandoned", "message_ids": ["m0008", "m0019"]}
```

### 5.3 Stub (rendered into live context)

```
[folded m0008–m0019 · thread t03 "Which database to use" · resolved]
Outcome: Postgres. SQLite rejected because of concurrent writers. → recall("m0008")
```

```
[folded m0052–m0058 · thread t04 "Why the auth test fails" · hypothesis refuted]
Thought the cause was clock mocking. Ruled out: failure reproduces with a real clock. Do not revisit without new evidence. → recall("m0052")
```

### 5.4 State block (rendered at the top of live context)

One line per current item, each with a source pointer. Only the latest value per item.

```
## Current state
- Goal: make the auth suite pass without changing the public API  [m0003]
- Constraint: do not modify the public API layer  [m0031]
- Decision: use Postgres, not SQLite  [m0019, supersedes m0008]
- Plan: (1) patch expiry check ✔ (2) add regression test ◻ (3) run full suite ◻  [m0063]
- Open question: should the fix be backported?  [m0070]
```

### 5.5 Trimmer output (strict JSON; reject and retry on invalid)

```json
{
  "threads": [{"thread_id": "t03", "title": "…", "status": "resolved", "message_ids": ["m0008"]}],
  "supersessions": [{"old": "m0008", "new": "m0019", "kind": "decision_revised", "reason": "…"}],
  "ops": [
    {"message_ids": ["m0008", "m0011"], "op": "fold", "summary": "…", "lesson": "…"},
    {"message_ids": ["m0045"], "op": "evict", "stub": "…"}
  ],
  "state_updates": [{"key": "decision:database", "value": "…", "source": "m0019", "supersedes": "m0008"}]
}
```

### 5.6 Needs record (one per cut point, `data/labels/needs.jsonl`)

Written once per cut point and independent of any policy. Scoring a policy reads this record.

```json
{
  "cut_id": "s012.c2",
  "session_id": "s012",
  "cut_before": "m0061",
  "needs": [
    {"need_id": "n1", "what": "Postgres was chosen over SQLite because of concurrent writers", "source_ids": ["m0019"], "anchor": "…short exact quote from the source…", "used_by": "m0072"}
  ],
  "current_state": [
    {"key": "decision:database", "what": "use Postgres", "source_ids": ["m0019"], "anchor": "…"}
  ],
  "superseded": [
    {"old": "m0008", "new": "m0019", "what": "SQLite replaced by Postgres"}
  ]
}
```

`anchor` is a short exact substring of the source, so code can test whether the content is still present verbatim.

---

## 6. Repository layout

```
context-trimmer/
  README.md
  pyproject.toml
  src/ctxtrim/
    log.py            # append-only log + thread/annotation records
    context.py        # build live context from log + annotations
    applier.py        # validate and apply trimmer ops
    tool_layer.py     # the fixed tool-result rule (4.1)
    model.py          # one model interface, chosen backend, on-disk call cache
    policies/
      none.py
      recency.py            # keep the last N turns
      compaction_recall.py  # summarise history + log pointer + recall tool
      object_level.py       # per-message keep/fold/drop by predicted usefulness
      llm_trimmer.py
    recall.py         # recall(id), search_log(query)
    skill/SKILL.md
    eval/
      needs.py        # one-time needs list per cut point
      replay.py       # scores a policy against needs lists
      checks.py       # small model check: does a summary keep a need; does a stub cue it
      metrics.py
      run_policy.py   # one command: score any policy on a split
    adapters/         # one module per session source format
  data/
    sessions/         # raw, untouched
    normalized/
    labels/
  results/
  tests/
```

Python 3.11+. Minimal dependencies. Unit tests for the log, applier, tool layer and scoring. Model name, thresholds and paths come from config.

---

## 7. Phases

### Phase 0: Data

1. **Licence check first.** The primary source is **SWE-chat** (`SALT-NLP/SWE-chat` on Hugging Face; arXiv 2604.20779): real sessions between developers and coding agents. Read its licence and report whether the sessions may be used for this research and whether they may be redistributed with added labels. If not redistributable, the benchmark ships labels, session identifiers and a download script. **ASK** the human to confirm before continuing, and before using any other dataset.
2. Select **18 sessions**:
   - Metadata filter: at least 25 user turns.
   - Statistics filter: after the fixed tool layer, conversation plus reasoning are a substantial share of context.
   - Content filter: one model call per remaining candidate (cap: 60 candidates) to find sessions with visible reversals. Keep those with at least a few.
   - At most 2 sessions from any one user or repository.
3. Write an adapter into the log schema (5.1). Raw files stay untouched.
4. Produce `data/README.md`: per session, source, user turns, tokens by kind, and conversational share after the fixed tool layer. Include a reasoning line: how many reasoning tokens persist in later turns' context, split into native thinking blocks and reasoning written in ordinary assistant text, and whether native blocks may be removed under the provider's rules. State the resulting decision (native blocks counted or excluded) per 4.2.
5. Split by user: **6 dev, 12 test**. The test split is untouched until the final run and becomes the released benchmark.

**ASK** before continuing if fewer than 18 suitable sessions are found, or if conversation and reasoning average under roughly 25% of context after the fixed tool layer.

Accept when: licence report approved; all sessions load; round-trip test shows no content lost.

### Phase 1: Log, model backend, context builder, applier

1. Implement the log, record types, fixed tool layer, context builder and applier. The applier rejects unknown IDs, protected messages, unprotected removal of user requirement messages, invalid message sequences and missing stubs.
2. Implement the model interface with the chosen backend and the call cache.

Accept when: with no annotations, the built context equals the original session; applier tests pass; a repeated model call is served from the cache.

### Phase 2: Replay evaluator

Build this before any trimmer.

**Cut points.** Two per session, at about 50% and 80% of the session's tokens, just before a user turn. That gives about 12 dev and 24 test cut points.

**Needs lists (one call per cut point, `JUDGE_MODEL`).** Give the model the conversation-only prefix and the next K turns of the real session. It writes one needs record (5.6). Validate in code: every ID exists and lies before the cut; every anchor is an exact substring of its source. Reject and retry invalid records.

**Scoring a policy at a cut point.** Run the policy on the prefix. Classify each need:

1. **Present**: the source message is still in context verbatim (anchor test, no model call).
2. **Captured**: not present, but a summary, stub or state-block line in context preserves the content.
3. **Recoverable**: not present or captured, but a pointer covers the source and the visible stub or summary text gives enough of a cue that the content is there. A bare pointer with no cue does not count.
4. **Lost**: none of the above.

Classes 2 and 3 need one small check call per cut point and policy setting, made with `JUDGE_MODEL` in a fresh context, containing only the needs that are not present plus the summaries, stubs and state block in the plain format from 4.3. The check must not be told which policy produced the text. Where a policy leaves bare stubs with no summary text (as `recency` does), classify in code: nothing is captured, and a bare pointer is not a cue.

From the classification:

- **Strict no-impact**: every need is present or captured.
- **No-impact (recall allowed)**: no need is lost.
- **Stale content**: for some `superseded` pair, the old item is present and the new one is neither present nor captured.
- **Dropped current state**: some `current_state` item is neither present nor captured.

**Amendment (2026-11-16, decided by the human):** no human review is required. Every review this plan assigns to the human (the 10 needs records here, the 15 check decisions, and the later check of 10 supersession links) is done instead by an Opus agent at medium effort, in a fresh context per item, with the same protocol (first 3 needs records blind) and the same pass rules. The other **ASK** stops and the **DECISION POINT** are unchanged. Consequence to state in the report: the reviewer is the same model as the judge (`claude-opus-5-5`), so this review checks care and consistency but does not limit shared blind spots the way a human review would.

**Amendment 2 (2026-11-16, the human said "Lets continue" after the review failed twice and after the orchestrator recommended stopping):** the round 2 needs records (revised prompt) are accepted without meeting the 7-of-10 rule. Basis: 92% of listed items judged right; needs lists close to complete (about one missing need per cut point). The superseded lists are known to be incomplete, so the stale-content rate is a lower bound and must be reported as such. The needs prompt is frozen as of this date.

**Human review (about one hour).** **ASK** the human to review the needs records for 10 dev cut points: the first 3 blind (the human writes what the continuation needs before seeing the model's record), the other 7 by marking the model's record right or wrong and adding anything missing. A cut point is agreed when the human would change nothing that could alter a verdict.

- 9 or 10 agreed: continue.
- 7 or 8: **ASK** whether to accept or fix the prompt.
- Fewer than 7: fix the needs prompt, regenerate, review a fresh 10.

Also **ASK** the human to review 15 check decisions (captured and cue). If more than 3 are wrong, fix the check prompt and repeat.

Accept when: needs records validate for every dev cut point; the `none` policy scores 100% on both no-impact scores at 0% pruning; the review passes; scoring a second policy makes no new needs-list calls.

### Phase 3: Baselines

All baselines act only on conversation and reasoning, after the fixed tool layer.

- `none`.
- `recency`: keep the last N turns, replace older turns with bare stubs. It is pure code and its scoring is pure code, so **sweep N fully** and report the whole curve.
- `compaction_recall`: summarise all but the last N turns into one block in a single call, plus a pointer to the log and the recall tool. Its strict score is what plain compaction without recall would get, so plain compaction needs no separate run.
- `object_level`: one LLM pass marks each whole message keep, fold or drop by how useful it predicts the message will be later, with no threads, no supersession links and no state block. It leaves the same stubs and pointers as the trimmer. This approximates the Self-GC approach and is the comparison that shows whether threads and supersession are the reason for any win, or whether any LLM choosing what to drop does as well.

**Pruning rates.** In this phase, run `compaction_recall` and `object_level` at two provisional settings (near 40% and 70% conversational pruning), which is enough for the decision point below. These are not the final comparison points. The trimmer removes only what is superseded or resolved, so its pruning rate has a ceiling set by each session and it may not reach a fixed target. The final comparison points are set by the trimmer's own rates in Phase 4, and the two LLM baselines are retuned to match them then.

Deliverable: `results/baselines.md` with, per policy and setting: pruning rate, both no-impact scores, stale-content rate, dropped-current-state rate, and the list of losses. Provide `eval/run_policy.py`.

**DECISION POINT.** Report how often the baselines leave superseded content in context without its replacement, and how often they drop a current decision, constraint or plan. **ASK** the human whether to continue. If the baselines rarely fail in these ways, the problem is small and the trimmer may not be worth building.

### Phase 4: LLM trimmer

1. Write the trimmer prompt. Input: indexed messages (ID, speaker, token count, text; tool results as one-line placeholders), existing threads, the state block. Output: the JSON in 5.5. Include the "not supersession" cases with examples. The trimmer works incrementally: each run reads the messages added since its last run plus existing thread titles, state block and stubs.
2. Two stages, evaluated on dev after each:
   a. threads + state block + `decision_revised`, `constraint_changed`, `plan_revised`;
   b. `hypothesis_refuted`, `abandoned`, `question_resolved`, `restated`.
3. Trigger: run only when live context exceeds `TRIM_TRIGGER_TOKENS`; apply a plan only if it removes at least `MIN_SAVINGS_FRACTION` of conversational tokens.
4. **Operating points.** The trimmer has two, and neither is tuned to a target rate:
   - *conservative*: act only on the stage a kinds (revised decisions, changed constraints, revised plans);
   - *full*: act on all seven kinds.
   Never make the trimmer cut live material to reach a pruning rate. Record the conversational pruning rate each operating point reaches on dev, per cut point and on average. These two average rates are the comparison points. Retune `compaction_recall` and `object_level` on dev to land on each of them, and read `recency` off its swept curve at the same rates. Report the spread of the trimmer's per-session rates as a result in itself: it shows how much of a session is stale.
5. Log every trimmer call in full.
6. Supersession labels, judged by `JUDGE_MODEL` with a small human spot-check (about 15 minutes of human time):
   - **Precision.** One proposer call per dev session (`MODEL`, with no access to trimmer output) proposes supersession links. Mix about 100 links from the proposer and the trimmer with the source hidden. `JUDGE_MODEL` accepts or rejects each in a fresh context, seeing the two messages and their surrounding turns but not who proposed the link, and for each rejection names the "not supersession" category. Report the trimmer's precision per kind and false supersessions by category, labelled as model-judged.
   - **Recall.** `JUDGE_MODEL` labels 1 dev session in full from scratch, with no proposals or trimmer output shown. Report how many of those links the trimmer found. Say plainly that one model-labelled session gives only a rough figure.
   - **Human spot-check.** **ASK** the human to judge 10 of the same links (at least 5), spread across kinds and across the judge's accepts and rejects, without seeing the judge's verdicts. Report agreement as a count. If the human disagrees with the judge on more than 2 of 10, stop and **ASK** before reporting any precision figure. A sample this small can only catch a badly wrong judge; it does not validate the precision numbers.

**Win rule (fix before the final run).** Before the test split is touched, write into `results/CHANGELOG.md` and **ASK** the human to approve: the two comparison points (the trimmer's conservative and full operating points, with baselines matched to their dev pruning rates); the comparisons that count (trimmer against `compaction_recall` and against `object_level` on no-impact with recall allowed; trimmer against every baseline on the strict score); the minimum margin; and that stale-content and dropped-current-state rates must not be worse.

**FINAL RUN.** On the untouched test split:

- Freeze the trimmer prompt and settings. Generate the test needs records with the prompt that passed review.
- Score every policy on the same cut points, with baseline settings fixed from dev. Report the pruning rate each policy actually reaches on test next to its scores; if a baseline lands more than a few points away from the trimmer's rate, say so and do not present the pair as matched. Run each model-based policy once; if the backend is not repeatable at fixed settings, run 3 times and average within each cut point.
- Report the per-cut-point differences between the trimmer and each baseline, with a 95% interval computed by resampling whole sessions.
- With 12 test sessions the interval will be wide. Report one of three outcomes: win rule met and interval excludes zero; win rule met but interval includes zero (suggestive); not met. Do not describe a suggestive result as a win.

Report to the human and stop.

### Phase 5: Live recall check (small)

Replay counts a loss as recoverable when a stub leads to it. This checks that the agent actually follows the pointer.

1. Implement `recall(id)` and `search_log(query)`, and write `skill/SKILL.md`: when the request touches a stubbed thread, follow the pointer before answering; never guess from a stub; treat the state block as current truth; never reopen a refuted hypothesis or abandoned approach without new information.
2. Take up to 10 cut points where a need was classed recoverable, for `llm_trimmer` and for `compaction_recall`. With about 36 cut points there may be fewer than 10 per policy; use what exists and report the count. Run the agent from the trimmed context on the real next user turn. Add the same number of cut points where nothing was needed.
3. Report recall hit rate and recall waste with counts. If the hit rate is low, make the strict score the headline number.

### Phase 6: Write-up and release

- `results/REPORT.md`: method, baselines, both scores at both pruning rates, the Phase 3 state-failure rates, per-kind supersession precision, the live recall check, failure analysis, the review results, total calls and tokens. Give the exact model ID and date behind every number. State the limitations plainly: 12 test sessions; sessions were selected because they contain reversals, so the result describes reversal-rich sessions and not typical ones; the judge model is from the same family as the trimmer; the checks are not fully blind; supersession precision and recall are judged by a model, with human agreement checked on only 10 links; message-level trimming only.
- Release the needs records, supersession labels, `eval/run_policy.py` and a short guide. Release sessions only in the form the licence allows. **ASK** before publishing anything.

---

## 8. Metrics (define once in `eval/metrics.py`)

| Metric | Definition |
|---|---|
| Conversational pruning rate | Conversation and reasoning tokens removed ÷ those tokens before trimming |
| Strict no-impact rate | Share of cut points where every need is present verbatim or captured |
| No-impact rate (recall allowed) | Share of cut points where no need is lost |
| Stale-content rate | Share of cut points where superseded content remains without its replacement |
| Dropped-current-state rate | Share of cut points where a current decision, constraint or plan is neither present nor captured |
| Supersession precision | Per kind, model-judged on the source-hidden sample; human agreement on 10 links reported alongside |
| Supersession recall | Against the one session the judge model labelled from scratch |
| False-supersession rate | By category: floated alternative, refinement, different scope |
| Recall hit rate / waste | Live: recalls made ÷ recalls needed; recalls made when none was needed |
| Cost | Total calls and tokens; trimmer calls and tokens per session in normal use |

Always report no-impact together with pruning rate, and the strict score next to the recall-allowed score.

---

## 9. Known risks

- **Small sample.** 12 test sessions cannot support a strong claim. Fix the win rule in advance and report suggestive results as suggestive.
- **Judge from the same family.** See 4.3. A separate judge model, code-based anchors, fresh-context checks and human review limit shared blind spots; they do not remove them.
- **Model-judged supersession accuracy.** The trimmer's precision and recall on supersession links are judged by `JUDGE_MODEL`, not by a person. The 10-link human spot-check can catch a badly wrong judge but cannot validate the figures. Report them as model-judged.
- **Checks not fully blind.** Plain rendering hides labels and layout, but the shape of the text still hints at the policy.
- **Selection bias.** Sessions are chosen for having reversals. The result does not describe typical sessions.
- **Unreachable pruning rates.** The trimmer cannot exceed the stale share of a session without cutting live material. Its own rates set the comparison points; baselines are matched to it, never the reverse.
- **Attribution.** Beating recency and compaction does not show that threads and supersession are the cause. The `object_level` baseline tests that.
- **Unpinned models.** A hosted model can change. Log the exact model ID and date with every result.
- **Ground truth from a model.** Every score rests on the needs records. Review them before trusting any number.
- **False supersession.** A floated alternative, refinement or differently scoped statement treated as a replacement. Require a reason and an existing `new` message for every link.
- **Losing the user's words.** Prefer quoting the user's wording in the state block; keep the original when unsure.
- **Credit for recall.** Compare against `compaction_recall`, report the strict score, and run the live recall check.
- **Small prize.** If tool output dominates context after the fixed layer, conversational trimming saves little. Phase 0 measures this.
- **Licence limits.** Settle the release form in Phase 0.

---

## 10. Working rules for the implementing agent

- Build the evaluator and baselines before the trimmer.
- Do not tune anything on the test split. Do not change the win rule after the final run has started.
- Do not improve tool-result handling beyond the fixed rule.
- Do not add baselines, models, sweeps or labelling beyond this plan without asking.
- Report negative and inconclusive results plainly.
- Never edit files in `data/sessions/` or any `log.jsonl`.
- Use public datasets only. Send session text only to the configured model backend.
- Never repeat a cached call. Follow the batch and budget limits in 4.3.
- Keep `results/CHANGELOG.md` with each experiment, its config and its outcome.
- Stop at every **ASK**, at the **DECISION POINT** and after the **FINAL RUN**.

---

## 11. Decisions the human must make (collected)

1. Backend: Claude Code on the existing plan, or the paid API with a spending cap; and which two Claude models to use as `MODEL` and `JUDGE_MODEL` (4.3).
2. Data source and release form after the licence check (Phase 0).
3. Whether to continue if too few sessions are found or conversation is a small share of context (Phase 0).
4. Approval of any batch over 100 calls (4.3).
5. Review work, about two hours in total: needs lists on 10 cut points and 15 check decisions (Phase 2); 5 to 10 supersession links as a spot-check on the judge model (Phase 4).
6. Whether to build the trimmer after seeing how often the baselines fail on state (decision point).
7. Approval of the win rule before the final run (Phase 4).
8. What may be published (Phase 6).

---

## 12. Deferred (do not build; list in the report as future work)

- Splitting messages into idea-level spans and removing parts of a message.
- A larger test set and a full sweep of pruning rates for the LLM baselines.
- A judge from a different model family, and an open-weight trimmer with fine-tuning.
- A full live test on long interactive tasks.
