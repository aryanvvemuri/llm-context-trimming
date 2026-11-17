"""Validate a trimmer output (PLAN.md 5.5) and apply it to a copy of the accepted annotations.

    accepted, records = apply(entries, annotations, output, protect_last_n=..., keep_tool_results=...)

Plain code. It never edits a log and never mutates its inputs: `annotations` is immutable and a
new Annotations is returned, with `records` (log records for this output only) for a caller that
wants to append them. Nothing in this module writes a file.

Whole-output rejection: any error rejects the whole output (PLAN.md 5.5 "reject and retry on
invalid"); InvalidOutput.errors lists every error found. Schema errors are reported alone (the
other checks need a well-formed output). Each error starts with a code (e.g. "protected:").

Ids are those of pilot_entries(entries) (native thinking excluded). Rules, beyond the schema:
  unknown_id       every id named anywhere must exist (thread members, supersession `old`,
                   op ids, state `supersedes`).
  supersession_new_missing / supersession_order / supersession_reason
                   `new` must exist and come after `old`; `reason` must be non-empty.
  state_source     a state update's `source` must exist.
  tool_message     ops may not target tool messages (they belong to the fixed tool layer, 4.1).
  double_removal   an id appears twice among this output's ops.
  already_removed  an id was removed by an accepted op.
  protected        the last `protect_last_n` pilot entries and the last user message.
  user_message     after merging this output, every removed user message (removed by this output
                   or by an accepted op) must be pointed to by a line of the state block as it
                   will be rendered (latest value per key, context.current_state), as that line's
                   `source` or its `supersedes` (5.4: "[m0019, supersedes m0008]"). So an output
                   is rejected if it removes a user message no rendered line points to, or if its
                   state updates replace the only line pointing to an earlier-removed user message
                   (an older value of a key is not rendered, so it does not count). Code cannot
                   judge which user messages state a requirement, so the rule applies to every
                   user message.
  thread_permit    a message is removable only when every thread it belongs to permits it. A
                   thread permits removing a member when the thread is resolved or abandoned, or
                   when the member is the `old` side of a supersession (this output or accepted).
                   A message in no thread is not restricted by this rule. Threads are taken
                   after merging this output into the accepted ones.
  lesson_required  an op covering a member of an abandoned thread, or the `old` side of a
                   hypothesis_refuted or abandoned supersession, must be a fold with a non-empty
                   lesson (4.2: always folded with the lesson, never evicted).
  fold_summary / evict_stub
                   a fold needs a non-empty summary; an evict needs non-empty stub text.
  api_invalid      the resulting context must render to a valid Messages API sequence
                   (context.validate_api_messages). Checked only when no other error was found.
"""

from __future__ import annotations

import json

from ctxtrim import context
from ctxtrim.context import OP_KINDS, SUPERSESSION_KINDS, Annotations, Op, StateUpdate, Supersession
from ctxtrim.log import THREAD_STATUSES, Entry, Thread, pilot_entries

_TOP = {"threads", "supersessions", "ops", "state_updates"}
_THREAD_KEYS = {"thread_id", "title", "status", "message_ids"}
_SUP_KEYS = {"old", "new", "kind", "reason"}
_OP_KEYS = {"message_ids", "op", "summary", "lesson", "stub"}
_STATE_KEYS = {"key", "value", "source", "supersedes"}
_LESSON_KINDS = ("hypothesis_refuted", "abandoned")


class InvalidOutput(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def _nonempty_str(x) -> bool:
    return isinstance(x, str) and bool(x.strip())


def _id_list(x) -> bool:
    return isinstance(x, list) and all(isinstance(i, str) for i in x)


def parse(output) -> tuple[tuple, list[str]]:
    """Schema check. Returns ((threads, supersessions, ops, state_updates), errors)."""
    errors: list[str] = []
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except json.JSONDecodeError as e:
            return ((), (), (), ()), [f"schema: not valid JSON ({e})"]
    if not isinstance(output, dict) or set(output) != _TOP:
        keys = sorted(output) if isinstance(output, dict) else type(output).__name__
        return ((), (), (), ()), [f"schema: top level must be an object with keys {sorted(_TOP)}, got {keys}"]
    for k in _TOP:
        if not isinstance(output[k], list) or not all(isinstance(x, dict) for x in output[k]):
            errors.append(f"schema: {k} must be a list of objects")
    if errors:
        return ((), (), (), ()), errors

    threads, sups, ops, ups = [], [], [], []
    for n, t in enumerate(output["threads"]):
        where = f"threads[{n}]"
        if set(t) != _THREAD_KEYS:
            errors.append(f"schema: {where} keys must be {sorted(_THREAD_KEYS)}")
        elif not (_nonempty_str(t["thread_id"]) and _nonempty_str(t["title"]) and _id_list(t["message_ids"])):
            errors.append(f"schema: {where} needs non-empty thread_id and title and a list of message ids")
        elif t["status"] not in THREAD_STATUSES:
            errors.append(f"schema: {where} unknown thread status {t['status']!r}")
        elif t["thread_id"] in {x.thread_id for x in threads}:
            errors.append(f"schema: {where} duplicate thread_id {t['thread_id']}")
        else:
            threads.append(Thread(t["thread_id"], t["title"], t["status"], list(t["message_ids"])))
    for n, s in enumerate(output["supersessions"]):
        where = f"supersessions[{n}]"
        if set(s) != _SUP_KEYS:
            errors.append(f"schema: {where} keys must be {sorted(_SUP_KEYS)}")
        elif not all(isinstance(s[k], str) for k in _SUP_KEYS):
            errors.append(f"schema: {where} fields must be strings")
        elif s["kind"] not in SUPERSESSION_KINDS:
            errors.append(f"schema: {where} unknown supersession kind {s['kind']!r}")
        elif not s["reason"].strip():
            errors.append(f"supersession_reason: {where} ({s['old']} -> {s['new']}) has an empty reason")
        else:
            sups.append(Supersession(s["old"], s["new"], s["kind"], s["reason"]))
    for n, o in enumerate(output["ops"]):
        where = f"ops[{n}]"
        if not ({"message_ids", "op"} <= set(o) <= _OP_KEYS):
            errors.append(f"schema: {where} keys must include op and message_ids, within {sorted(_OP_KEYS)}")
        elif o["op"] not in OP_KINDS:
            errors.append(f"schema: {where} unknown op {o['op']!r}")
        elif not (_id_list(o["message_ids"]) and o["message_ids"]):
            errors.append(f"schema: {where} message_ids must be a non-empty list of ids")
        elif not all(o.get(k) is None or isinstance(o[k], str) for k in ("summary", "lesson", "stub")):
            errors.append(f"schema: {where} summary, lesson and stub must be strings")
        elif o["op"] == "fold" and not _nonempty_str(o.get("summary")):
            errors.append(f"fold_summary: {where} is a fold without a summary")
        elif o["op"] == "evict" and not _nonempty_str(o.get("stub")):
            errors.append(f"evict_stub: {where} is an evict without stub text")
        else:
            ops.append(Op(o["op"], tuple(o["message_ids"]), o.get("summary") or "", o.get("lesson") or "",
                          o.get("stub") or ""))
    for n, u in enumerate(output["state_updates"]):
        where = f"state_updates[{n}]"
        if not ({"key", "value", "source"} <= set(u) <= _STATE_KEYS):
            errors.append(f"schema: {where} keys must include key, value, source, within {sorted(_STATE_KEYS)}")
        elif not all(_nonempty_str(u[k]) for k in ("key", "value", "source")):
            errors.append(f"schema: {where} key, value and source must be non-empty strings")
        elif not (u.get("supersedes") is None or isinstance(u["supersedes"], str)):
            errors.append(f"schema: {where} supersedes must be a string or null")
        else:
            ups.append(StateUpdate(u["key"], u["value"], u["source"], u.get("supersedes") or None))
    return (tuple(threads), tuple(sups), tuple(ops), tuple(ups)), errors


def protected_ids(entries: list[Entry], protect_last_n: int) -> set[str]:
    """Last `protect_last_n` pilot entries plus the current user request (the last user message)."""
    es = pilot_entries(entries)
    out = {e.id for e in es[max(0, len(es) - protect_last_n):]} if protect_last_n > 0 else set()
    users = [e.id for e in es if e.kind == "user"]
    return out | set(users[-1:])


def validate(entries: list[Entry], annotations: Annotations, output, *, protect_last_n: int,
             keep_tool_results: int | None) -> list[str]:
    """All errors for `output` given the accepted `annotations` (empty list = valid)."""
    (threads, sups, ops, ups), errors = parse(output)
    if errors:
        return errors
    es = pilot_entries(entries)
    pos = {e.id: n for n, e in enumerate(es)}
    kind = {e.id: e.kind for e in es}
    merged = annotations.add(threads, sups, ops, ups)

    for t in threads:
        for i in t.message_ids:
            if i not in pos:
                errors.append(f"unknown_id: thread {t.thread_id} names {i}")
    for s in sups:
        if s.old not in pos:
            errors.append(f"unknown_id: supersession old {s.old}")
        if s.new not in pos:
            errors.append(f"supersession_new_missing: supersession {s.old} -> {s.new}: new does not exist")
        elif s.old in pos and pos[s.new] <= pos[s.old]:
            errors.append(f"supersession_order: supersession {s.old} -> {s.new}: new does not come after old")
    for u in ups:
        for i in (u.source, u.supersedes):
            if i is not None and i not in pos:
                errors.append(f"state_source: state update {u.key!r} names unknown id {i}")

    protected = protected_ids(es, protect_last_n)
    already = annotations.removed_ids()
    pointed = {i for u in context.current_state(merged) for i in (u.source, u.supersedes) if i}
    old_sides = {s.old for s in merged.supersessions}
    needs_lesson = ({s.old for s in merged.supersessions if s.kind in _LESSON_KINDS}
                    | {i for t in merged.threads if t.status == "abandoned" for i in t.message_ids})
    seen: set[str] = set()
    for n, op in enumerate(ops):
        where = f"ops[{n}]"
        for i in op.message_ids:
            if i not in pos:
                errors.append(f"unknown_id: {where} names {i}")
                continue
            if kind[i] == "tool":
                errors.append(f"tool_message: {where} targets tool message {i}")
            if i in seen:
                errors.append(f"double_removal: {where} removes {i} twice in this output")
            if i in already:
                errors.append(f"already_removed: {where} removes {i}, already removed")
            seen.add(i)
            if i in protected:
                errors.append(f"protected: {where} removes protected message {i}")
            if kind[i] == "user" and i not in pointed:
                errors.append(f"user_message: {where} removes user message {i}, which no line of the state block "
                              f"points to (as source or supersedes)")
            active = [t.thread_id for t in merged.threads if i in t.message_ids and t.status == "active"]
            if active and i not in old_sides:
                errors.append(f"thread_permit: {where} removes {i}, member of active thread(s) {active}, "
                              f"and {i} is not the old side of a supersession")
        if needs_lesson & set(op.message_ids) and not (op.op == "fold" and op.lesson.strip()):
            errors.append(f"lesson_required: {where} covers an abandoned approach or refuted hypothesis; "
                          f"it must be a fold with a lesson")

    for i in sorted(already, key=lambda x: pos.get(x, -1)):
        if kind.get(i) == "user" and i not in pointed:
            errors.append(f"user_message: after this output no line of the state block points to {i}, "
                          f"a user message removed earlier")

    if not errors:
        ctx = context.build(es, merged, keep_tool_results=keep_tool_results)
        _, msgs = context.api_messages(ctx)
        errors += [f"api_invalid: {e}" for e in context.validate_api_messages(msgs)]
    return errors


def apply(entries: list[Entry], annotations: Annotations, output, *, protect_last_n: int,
          keep_tool_results: int | None) -> tuple[Annotations, list]:
    """Validate, then return (new accepted annotations, log records for this output).

    Raises InvalidOutput listing every error; nothing is accepted in that case.
    """
    errors = validate(entries, annotations, output, protect_last_n=protect_last_n,
                      keep_tool_results=keep_tool_results)
    if errors:
        raise InvalidOutput(errors)
    (threads, sups, ops, ups), _ = parse(output)
    merged = annotations.add(threads, sups, ops, ups)
    new_ids = {t.thread_id for t in threads}
    records = Annotations.records([t for t in merged.threads if t.thread_id in new_ids], sups, ops, ups)
    return merged, records
