"""`compaction_recall`: summarise the oldest turns into one block in a single MODEL call, with a
pointer to the log and the recall tool (PLAN.md 7 Phase 3).

Setting `target` (0 < target < 1): the intended conversational pruning rate.

Turns as in recency: a plain user message and everything after it up to the next one; messages
before the first plain user message belong to the first turn. Conversational messages are user
and assistant entries of the prefix (pilot entries); tool messages are untouched (the fixed tool
layer owns them) and appear in the model's input only as one-line placeholders
(views.conversation_view).

Choice of turns: C = conversational tokens of the prefix; the summary budget is
B = max(MIN_SUMMARY_TOKENS, SUMMARY_SHARE x C) approx tokens, asked for as
round(B x WORDS_PER_APPROX_TOKEN) words. Eligible turns are the leading turns that hold no
protected message (applier.protected_ids, config.PROTECT_LAST_N: the last N messages and the
current user request). Take the smallest oldest run of eligible turns whose conversational
tokens reach target x C + B, or all eligible turns if they do not reach it. No eligible turn with
a conversational message: nothing is summarised and no call is made.

Result: one fold op over every conversational message of the chosen turns, its summary the
model's text. context.build renders it as one stub at the first removed message:
'[folded m0001–m0090]\\n<summary> → recall("m0001")'. Tool messages in that range stay where
they are, as for every policy.

Reply: {"summary": "..."}; non-empty, at most MAX_WORDS_FACTOR x the word budget; an invalid
reply is retried (common.call_validated).
"""

from __future__ import annotations

from pathlib import Path

from ctxtrim import config
from ctxtrim.applier import protected_ids
from ctxtrim.context import Annotations, Op
from ctxtrim.eval import common
from ctxtrim.log import approx_tokens, is_plain_user, pilot_entries
from ctxtrim.policies.base import PolicyOutput, check_settings
from ctxtrim.views import conversation_view

PROMPT_PATH = Path(__file__).with_name("compaction_recall_prompt.md")
SUMMARY_SHARE = 0.05          # summary budget as a share of the prefix's conversational tokens
MIN_SUMMARY_TOKENS = 150      # floor of the summary budget (approx tokens)
WORDS_PER_APPROX_TOKEN = 0.75  # budget in words = budget tokens x this
MAX_WORDS_FACTOR = 1.5        # validation: reply words <= this x word budget


def _conv(e) -> bool:
    return e.kind in ("user", "assistant")


def plan(prefix, settings: dict):
    """(entries to summarise, their conversational ids, word budget), or None when nothing can be
    summarised. Pure code; no call."""
    check_settings(settings, {"target"})
    target = settings["target"]
    if not (isinstance(target, (int, float)) and 0 < target < 1):
        raise ValueError("target must be a number with 0 < target < 1")
    es = pilot_entries(prefix)
    total = sum(e.tokens for e in es if _conv(e))
    budget = max(MIN_SUMMARY_TOKENS, SUMMARY_SHARE * total)
    protected = protected_ids(prefix, config.require("PROTECT_LAST_N"))
    starts = [i for i, e in enumerate(es) if is_plain_user(e)]
    bounds = [0] + starts[1:] + [len(es)]
    end, run = 0, 0
    for a, b in zip(bounds, bounds[1:]):
        if any(e.id in protected for e in es[a:b]):
            break
        end, run = b, run + sum(e.tokens for e in es[a:b] if _conv(e))
        if run >= target * total + budget:
            break
    ids = [e.id for e in es[:end] if _conv(e)]
    if not ids:
        return None
    return es[:end], ids, round(budget * WORDS_PER_APPROX_TOKEN)


def build_prompt(entries, words: int) -> tuple[str, str]:
    system, template = common.load_prompt_file(PROMPT_PATH)
    view, _ = conversation_view(entries)
    return system, common.fill(template, WORDS=str(words), TRANSCRIPT=view)


def validator(words: int):
    limit = int(MAX_WORDS_FACTOR * words)

    def validate(text: str) -> str:
        obj = common.parse_json_object(text)
        if set(obj) != {"summary"} or not isinstance(obj["summary"], str):
            raise ValueError('top level must be {"summary": "..."} and nothing else')
        s = obj["summary"].strip()
        if not s:
            raise ValueError("summary is empty")
        n = len(s.split())
        if n > limit:
            raise ValueError(f"summary has {n} words; at most {limit} allowed (budget {words})")
        return s
    return validate


def estimate(prefix, settings: dict) -> list[int]:
    """Approx input tokens of each call `run` would make (0 or 1 call). No call."""
    p = plan(prefix, settings)
    if p is None:
        return []
    system, prompt = build_prompt(p[0], p[2])
    return [approx_tokens(system) + approx_tokens(prompt)]


def run(prefix, settings: dict, **call_kw) -> PolicyOutput:
    p = plan(prefix, settings)
    if p is None:
        return PolicyOutput(Annotations())
    entries, ids, words = p
    system, prompt = build_prompt(entries, words)
    r = common.call_validated(prompt, system, "compaction_recall", validator(words),
                              model_id=config.require("MODEL"), **call_kw)
    if r["value"] is None:
        raise RuntimeError(f"compaction_recall reply invalid after {r['attempts']} attempts: {r['errors']}")
    return PolicyOutput(Annotations(ops=(Op("fold", tuple(ids), summary=r["value"]),)), r["attempts"])
