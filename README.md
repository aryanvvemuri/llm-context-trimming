# context-trimmer

A research pilot on trimming the conversational part of a long-running coding agent's context: remove messages that a later message has replaced (a revised decision, a refuted explanation, a dropped plan), and compare that against simpler baselines.

**Status (2026-11-17): Phases 0 to 3 of [PLAN.md](PLAN.md) are done. The trimmer itself (Phase 4) is not built.** The pilot is paused at the plan's decision point, and the baseline results below argue against building it. Everything here is measured on 6 development sessions (12 cut points). The 12 test sessions have not been touched.

## Summary of findings

- **The prize is small.** Conversation (user and assistant text) is about 17% of the raw context in these sessions. Of that conversation, a median of 22% sits in messages that were later replaced, and 12% once messages that are still needed are excluded. That is roughly 2 to 4% of the raw context.
- **The failure the trimmer targets is rare.** Baselines almost never leave a stale message in context without its replacement: 0 of 12 cut points for every baseline and setting except one (3 of 12).
- **A simple baseline already loses little.** Summarising the oldest turns and letting the agent recall the originals removes 43 to 72% of conversation and loses 5 to 9% of the items the agent goes on to need.
- **The scoring labels did not pass review.** See [Limitations](#limitations).

## Method

- **Data.** 18 Claude Code sessions from [SWE-chat](https://huggingface.co/datasets/SALT-NLP/SWE-chat) (ODC-BY 1.0), chosen by a seeded draw from sessions with at least 25 user turns and at least 5 reversals. Split by user and repository: 6 dev, 12 test. Selection statistics are in [data/README.md](data/README.md).
- **Fixed tool layer.** Every policy sees the same tool-output handling: the 3 most recent tool messages stay whole, older ones become a one-line stub. Policies only act on user and assistant messages.
- **Cut points.** Two per session. At each, the session is split into a prefix (what the policy trims) and the real continuation (the next 3 user turns).
- **Needs records.** For each cut point a judge model (`claude-opus-5-5`) reads the prefix and the continuation and lists: what the continuation needs from the prefix, the decisions and constraints in force at the cut, and which earlier messages were replaced by later ones. Each item points to its source messages.
- **Replay scoring.** A policy trims the prefix. Each needed item is then classed as present (source still in context), captured (a note preserves it), recoverable (a note points back to the original, which can be recalled), or lost.
- **Policies.** `none`; `recency` (keep the last N turns); `compaction_recall` (summarise the oldest turns in one note with a pointer back); `object_level` (a model chooses keep, fold or drop per message). The model-based policies use `claude-sonnet-5-5`.

## Results (dev, 12 cut points)

| Policy | Setting | Conversation removed | Cut points with no need lost | Needed items lost | State items lost | Stale content left |
|---|---|---|---|---|---|---|
| none | - | 0% | 100% | 0% | 0% | 0% |
| recency | last 40 turns | 2% | 92% | n/a | n/a | 0% |
| recency | last 20 turns | 23% | 67% | 10% | 19% | 0% |
| compaction_recall | target 40% | 43% | 58% | 5% | 1% | 0% |
| recency | last 10 turns | 59% | 8% | 40% | 53% | 0% |
| object_level | target 40% | 63% | 8% | 24% | 17% | 0% |
| compaction_recall | target 70% | 72% | 42% | 9% | 2% | 0% |
| recency | last 5 turns | 77% | 0% | n/a | n/a | 0% |
| object_level | target 70% | 79% | 8% | 27% | 18% | 25% |
| recency | last 2 turns | 88% | 0% | n/a | n/a | 0% |

- **Cut points with no need lost** fails a cut point if a single needed item is lost, so it is much harsher than the per-item columns.
- **Stale content left** is the share of cut points where a replaced message stays in context and its replacement does not. It is a lower bound.
- `object_level` overshot its 40% target.
- "n/a": the per-item rate was not computed for that setting.

The full table, the strict (no recall) metric and per-cut-point loss lists are in [results/baselines.md](results/baselines.md). Raw results per policy are in [results/replay/](results/replay/).

## Limitations

- **Needs records failed their review twice.** The plan required a reviewer to agree with 7 of 10 records; 0 of 10 agreed in both rounds. In round 2, 92% of listed items were right, and the lists of needs were close to complete, but the lists of replaced messages were not. The records were accepted on that basis (see the amendments in PLAN.md), so the stale-content numbers are lower bounds.
- **The reviewer was a model, not a person.** All reviews the plan assigned to a human were done by `claude-opus-5-5`, the same model as the judge.
- **The check judge is too strict.** A review of 15 of its decisions found 4 wrong, all on whether a note points back to its original. This understates "no need lost" for the policies that leave notes. The check prompt has not been fixed.
- **Small sample.** 6 sessions, 12 cut points, one dataset, one agent.
- **Token counts are approximate** (characters / 4).

## Cost

207 model calls and 8.0M input tokens through the pipeline (about $43 at list price), logged in [results/model_calls.jsonl](results/model_calls.jsonl). Coding and review agents used roughly 3M more tokens outside that log.

## What is in this repository

- `src/ctxtrim/`: log schema, tool layer, context builder, applier, model backend with call cache, policies, evaluation (`eval/`), SWE-chat adapter.
- `scripts/`: data download, selection, normalisation, statistics, review packets, baseline report.
- `tests/`: 460 unit tests.
- `data/splits.json`: session ids and the dev/test split.
- `results/`: baseline report, per-policy results, call ledger, and [CHANGELOG.md](results/CHANGELOG.md), which records every decision and deviation.

**Not included:** session text, and anything derived from it (normalised logs, needs records, judge decisions, review packets). SWE-chat is gated; the scripts below rebuild the local data from session ids.

## Setup

Requires Python 3.11+.

```
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev,data]"
.venv/bin/python -m pytest -q
```

Rebuilding the data needs Hugging Face access to `SALT-NLP/SWE-chat`:

```
.venv/bin/python -I scripts/download_metadata.py
.venv/bin/python -I scripts/select_sample.py
.venv/bin/python -I scripts/download_transcripts.py
.venv/bin/python -I scripts/normalize.py
.venv/bin/python -I scripts/stats.py
.venv/bin/python -I scripts/content_filter.py         # dry run; --run makes the calls
.venv/bin/python -I scripts/select_final.py
```

Model calls go through the `claude_code` backend, which needs a logged-in Claude Code CLI (set `CTXTRIM_CLAUDE_BIN` if `claude` is not on PATH). Every evaluation command is a dry run that prints a call and token estimate unless `--run` is given:

```
.venv/bin/python -m ctxtrim.eval.needs --split dev
.venv/bin/python -m ctxtrim.eval.run_policy --help
.venv/bin/python -I scripts/make_baselines_report.py
```
