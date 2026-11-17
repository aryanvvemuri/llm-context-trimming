"""`object_level`: one MODEL call per cut marks whole messages fold or drop by how useful it
predicts each will be later; everything unlisted is kept (PLAN.md 7 Phase 3). No threads, no
supersession links, no state block.

Setting `target` (0 < target < 1): the share of conversational tokens the model is asked to
remove.

Input: every pilot entry of the prefix in order. Conversational messages (user, assistant) show
id, speaker, approx tokens (Entry.tokens) and text; protected ones (applier.protected_ids,
config.PROTECT_LAST_N) are marked as not removable. Tool messages are one-line placeholders
(tool_layer.placeholder) and are not candidates (the fixed tool layer owns them).

Reply: {"ops": [{"id": "m0012", "action": "fold", "summary": "..."}, {"id": "m0013", "action": "drop"}]},
checked by `check_reply`; an invalid reply is rejected whole and retried with the error list
(common.call_validated).

Result: consecutive removed messages with the same action form one run (a kept message, a tool
message or a change of action ends a run). A fold run becomes one fold op whose summary is the
run's one-line summaries joined in order; a drop run becomes one bare evict stub (base.bare_stubs:
pointer only), the form recency and the trimmer leave. No call when no message is removable.
"""

from __future__ import annotations

from pathlib import Path

from ctxtrim import config, tool_layer
from ctxtrim.applier import protected_ids
from ctxtrim.context import Annotations, Op
from ctxtrim.eval import common
from ctxtrim.log import approx_tokens, pilot_entries
from ctxtrim.policies.base import PolicyOutput, bare_stubs, check_settings

PROMPT_PATH = Path(__file__).with_name("object_level_prompt.md")
ACTIONS = ("fold", "drop")


def _conv(e) -> bool:
    return e.kind in ("user", "assistant")


def _target(settings: dict) -> float:
    check_settings(settings, {"target"})
    t = settings["target"]
    if not (isinstance(t, (int, float)) and 0 < t < 1):
        raise ValueError("target must be a number with 0 < target < 1")
    return t


def render(entries, protected: set[str]) -> str:
    out = []
    for e in entries:
        if not _conv(e):
            out.append(tool_layer.placeholder(e))
        else:
            mark = " · PROTECTED, not removable" if e.id in protected else ""
            out.append(f"[{e.id}] {e.kind.upper()} · {e.tokens} tokens{mark}:\n{e.content}")
    return "\n\n".join(out)


def build_prompt(prefix, target: float) -> tuple[str, str] | None:
    """(system, prompt), or None when no message is removable (then no call is made)."""
    es = pilot_entries(prefix)
    protected = protected_ids(prefix, config.require("PROTECT_LAST_N"))
    if not any(_conv(e) and e.id not in protected for e in es):
        return None
    total = sum(e.tokens for e in es if _conv(e))
    system, template = common.load_prompt_file(PROMPT_PATH)
    return system, common.fill(template, TARGET_PCT=str(round(100 * target)), TOTAL=str(total),
                               REMOVE=str(round(target * total)), MAX_CHARS=str(config.MAX_WHAT_CHARS),
                               MESSAGES=render(es, protected))


def check_reply(obj, prefix) -> list[str]:
    """Every error in a parsed reply (empty = valid): top level exactly {"ops": [...]}; each op
    has id and action; action is fold (with a non-empty one-line summary of at most
    MAX_WHAT_CHARS) or drop (no summary); the id exists in the prefix, is a user or assistant
    message, is not protected, and appears at most once."""
    if not (isinstance(obj, dict) and set(obj) == {"ops"} and isinstance(obj["ops"], list)):
        return ['top level must be {"ops": [...]} and nothing else']
    es = pilot_entries(prefix)
    kinds = {e.id: e.kind for e in es}
    protected = protected_ids(prefix, config.require("PROTECT_LAST_N"))
    errors, seen = [], set()
    for n, op in enumerate(obj["ops"]):
        where = f"ops[{n}]"
        if not isinstance(op, dict) or op.get("action") not in ACTIONS:
            errors.append(f"{where}: must be an object with action \"fold\" or \"drop\"")
            continue
        want = {"id", "action", "summary"} if op["action"] == "fold" else {"id", "action"}
        if set(op) != want:
            errors.append(f"{where}: a {op['action']} op has exactly the keys {sorted(want)}")
            continue
        i = op["id"]
        if not isinstance(i, str) or i not in kinds:
            errors.append(f"{where}: id {i!r} does not exist")
        elif kinds[i] not in ("user", "assistant"):
            errors.append(f"{where}: {i} is a tool message; only user and assistant messages can be removed")
        elif i in protected:
            errors.append(f"{where}: {i} is protected and cannot be removed")
        elif i in seen:
            errors.append(f"{where}: {i} is listed more than once")
        else:
            seen.add(i)
        if op["action"] == "fold":
            s = op["summary"]
            if not (isinstance(s, str) and s.strip() and "\n" not in s and len(s) <= config.MAX_WHAT_CHARS):
                errors.append(f"{where}: summary must be one non-empty line of at most {config.MAX_WHAT_CHARS} chars")
    return errors


def validator(prefix):
    def validate(text: str) -> dict[str, dict]:
        obj = common.parse_json_object(text)
        errors = check_reply(obj, prefix)
        if errors:
            raise ValueError("; ".join(errors[:10]) + (f"; and {len(errors) - 10} more" if len(errors) > 10 else ""))
        return {op["id"]: op for op in obj["ops"]}
    return validate


def annotations(prefix, chosen: dict[str, dict]) -> Annotations:
    """Ops for a valid reply (id -> op): one stub per run of consecutive messages with the same action."""
    runs: list[tuple[str, list[str]]] = []
    prev = None
    for e in pilot_entries(prefix):
        action = chosen[e.id]["action"] if e.id in chosen else None
        if action is not None:
            if action != prev:
                runs.append((action, []))
            runs[-1][1].append(e.id)
        prev = action
    folds = tuple(Op("fold", tuple(ids), summary=" ".join(_sentence(chosen[i]["summary"]) for i in ids))
                  for action, ids in runs if action == "fold")
    drops = bare_stubs([ids for action, ids in runs if action == "drop"]).ops
    return Annotations(ops=folds + drops)


def _sentence(s: str) -> str:
    s = s.strip()
    return s if s.endswith((".", "!", "?")) else s + "."


def estimate(prefix, settings: dict) -> list[int]:
    """Approx input tokens of each call `run` would make (0 or 1 call). No call."""
    p = build_prompt(prefix, _target(settings))
    return [] if p is None else [approx_tokens(p[0]) + approx_tokens(p[1])]


def run(prefix, settings: dict, **call_kw) -> PolicyOutput:
    p = build_prompt(prefix, _target(settings))
    if p is None:
        return PolicyOutput(Annotations())
    r = common.call_validated(p[1], p[0], "object_level", validator(prefix),
                              model_id=config.require("MODEL"), **call_kw)
    if r["value"] is None:
        raise RuntimeError(f"object_level reply invalid after {r['attempts']} attempts: {r['errors']}")
    return PolicyOutput(annotations(prefix, r["value"]), r["attempts"])
