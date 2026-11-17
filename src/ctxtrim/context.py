"""Build the live context from log entries plus accepted annotations (PLAN.md 4, 5.3, 5.4).

Pipeline (`build`):
  1. pilot_entries: native thinking excluded (ctxtrim.log.pilot_entries).
  2. Fixed tool layer: tool_layer.apply(entries, keep_tool_results); keep_tool_results=None skips it.
  3. Accepted ops: each fold/evict becomes one Stub at the position of the first message it
     removes; the other removed messages disappear; surviving messages keep their order.
  4. State block: one line per key, latest value only, with its source pointer (5.4).

The result is a `LiveContext`: the ordered items (Entry or Stub) plus the state lines, which
later evaluation code can inspect (`verbatim_ids`, `covered_by`, `state_sources`), a plain-text
rendering (`text`) and token counts split into conversational and tool tokens (PLAN.md 4.1).
Accounting: user and assistant entries, stubs and the state block are conversational tokens
(stubs and the state block replace conversation); tool entries, after the tool layer, are tool
tokens. Every count uses ctxtrim.log.approx_tokens (entries carry their own `tokens`).

`Annotations` is the in-memory record of accepted trimmer output (threads, supersessions, ops,
state updates), accumulated across incremental runs. Only ctxtrim.applier adds to it after
validation. It serialises to log records (Thread and Annotation) via `records`/`from_records`,
but nothing here writes a log: evaluation runs on prefixes in memory.

`api_messages` renders the context as a Messages API request body; `validate_api_messages`
checks it. See their docstrings for the rules and the source.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ctxtrim import tool_layer
from ctxtrim.log import Annotation, Entry, Thread, approx_tokens, pilot_entries, tool_input_text

SUPERSESSION_KINDS = ("decision_revised", "constraint_changed", "plan_revised", "hypothesis_refuted",
                      "abandoned", "question_resolved", "restated")
OP_KINDS = ("fold", "evict")


@dataclass(frozen=True)
class Supersession:
    old: str
    new: str
    kind: str
    reason: str


@dataclass(frozen=True)
class Op:
    op: str                      # "fold" or "evict"
    message_ids: tuple[str, ...]
    summary: str = ""            # fold
    lesson: str = ""             # fold, optional
    stub: str = ""               # evict


@dataclass(frozen=True)
class StateUpdate:
    key: str
    value: str
    source: str
    supersedes: str | None = None


@dataclass(frozen=True)
class Annotations:
    """Accepted annotations. Immutable: `add` returns a new object.

    Threads: one per thread_id; a later output for the same id sets title and status, and its
    message_ids are added to the earlier ones (membership accumulates, never shrinks).
    """
    threads: tuple[Thread, ...] = ()
    supersessions: tuple[Supersession, ...] = ()
    ops: tuple[Op, ...] = ()
    state_updates: tuple[StateUpdate, ...] = ()

    def add(self, threads=(), supersessions=(), ops=(), state_updates=()) -> Annotations:
        merged = {t.thread_id: t for t in self.threads}
        for t in threads:
            old_ids = merged[t.thread_id].message_ids if t.thread_id in merged else []
            ids = list(old_ids) + [i for i in t.message_ids if i not in old_ids]
            merged[t.thread_id] = Thread(t.thread_id, t.title, t.status, ids)
        return Annotations(tuple(merged.values()), self.supersessions + tuple(supersessions),
                           self.ops + tuple(ops), self.state_updates + tuple(state_updates))

    def removed_ids(self) -> set[str]:
        return {i for op in self.ops for i in op.message_ids}

    @staticmethod
    def records(threads=(), supersessions=(), ops=(), state_updates=()) -> list:
        """Log records (append-only) for one accepted output. Threads must be the merged ones."""
        out: list = [Thread(t.thread_id, t.title, t.status, list(t.message_ids)) for t in threads]
        out += [Annotation({"type": "supersession", "old": s.old, "new": s.new, "kind": s.kind,
                            "reason": s.reason}) for s in supersessions]
        out += [Annotation({"type": "op", "op": o.op, "message_ids": list(o.message_ids), "summary": o.summary,
                            "lesson": o.lesson, "stub": o.stub}) for o in ops]
        out += [Annotation({"type": "state_update", "key": u.key, "value": u.value, "source": u.source,
                            "supersedes": u.supersedes}) for u in state_updates]
        return out

    @staticmethod
    def from_records(records) -> Annotations:
        """Rebuild from records in log order (a later Thread record for an id replaces the earlier)."""
        threads: dict[str, Thread] = {}
        sups, ops, ups = [], [], []
        for r in records:
            if isinstance(r, Thread):
                threads[r.thread_id] = Thread(r.thread_id, r.title, r.status, list(r.message_ids))
            elif isinstance(r, Annotation):
                d = dict(r.data)
                kind = d.pop("type", None)
                if kind == "supersession":
                    sups.append(Supersession(**d))
                elif kind == "op":
                    ops.append(Op(d["op"], tuple(d["message_ids"]), d["summary"], d["lesson"], d["stub"]))
                elif kind == "state_update":
                    ups.append(StateUpdate(**d))
        return Annotations(tuple(threads.values()), tuple(sups), tuple(ops), tuple(ups))


@dataclass(frozen=True)
class Stub:
    """Placeholder for the messages one op removed (PLAN.md 5.3)."""
    op: str
    message_ids: tuple[str, ...]  # in log order; the pointer is the first
    role: str        # "user" or "assistant": role of the first removed message (see api_messages)
    text: str
    tokens: int
    body: str = ""   # the policy's own text, no header or pointer: stub_body(op); "" for a bare stub


def _id_range(ids) -> str:
    return ids[0] if len(ids) == 1 else f"{ids[0]}–{ids[-1]}"


def stub_body(op: Op) -> str:
    """The text an op leaves in context besides header and pointer: fold summary (+ lesson),
    evict stub text. Empty for a bare evict (stub text "", as the recency baseline leaves)."""
    if op.op == "fold":
        return op.summary + (f" Lesson: {op.lesson}" if op.lesson else "")
    return op.stub


def render_stub(op: Op, annotations: Annotations) -> str:
    """5.3 format. Header names the first thread (in accumulation order) that contains a removed
    message, and a label: the kind of the first supersession whose `old` is removed (underscores
    as spaces), else that thread's status. A fold has a second line: summary, lesson, pointer.
    An evict is one line: header, stub text (omitted when empty: a bare stub), pointer.
    """
    ids = op.message_ids
    covered = set(ids)
    head = f"[{'folded' if op.op == 'fold' else 'evicted'} {_id_range(ids)}"
    thread = next((t for t in annotations.threads if covered & set(t.message_ids)), None)
    if thread is not None:
        head += f' · thread {thread.thread_id} "{thread.title}"'
    sup = next((s for s in annotations.supersessions if s.old in covered), None)
    label = sup.kind.replace("_", " ") if sup else (thread.status if thread else None)
    if label:
        head += f" · {label}"
    head += "]"
    pointer = f'→ recall("{ids[0]}")'
    if op.op == "fold":
        return f"{head}\n{stub_body(op)} {pointer}"
    return " ".join(p for p in (head, op.stub, pointer) if p)


def current_state(annotations: Annotations) -> tuple[StateUpdate, ...]:
    """Latest update per key, keys in order of first appearance."""
    latest: dict[str, StateUpdate] = {}
    for u in annotations.state_updates:
        latest[u.key] = u
    return tuple(latest.values())


def render_state(lines) -> str:
    """5.4 format; the key is shown as given. Empty string when there are no lines."""
    if not lines:
        return ""
    out = ["## Current state"]
    for u in lines:
        ptr = u.source + (f", supersedes {u.supersedes}" if u.supersedes else "")
        out.append(f"- {u.key}: {u.value}  [{ptr}]")
    return "\n".join(out)


def _render_entry(e: Entry) -> str:
    """Text of one entry as the model sees it. A tool message is one piece of text holding input
    and result; a cleared one (tool_input None after the tool layer) is its one-line stub."""
    if e.kind != "tool" or e.tool_input is None:
        return e.content
    return f"[tool call · {e.id} · {e.tool_name}] input: {tool_input_text(e.tool_input)}\nresult:\n{e.content}"


@dataclass(frozen=True)
class LiveContext:
    items: tuple[Entry | Stub, ...]
    state: tuple[StateUpdate, ...] = ()
    state_text: str = ""

    def verbatim_ids(self) -> list[str]:
        return [i.id for i in self.items if isinstance(i, Entry)]

    def covered_by(self) -> dict[str, Stub]:
        """Removed message id -> the stub (fold summary or evict stub) that replaced it."""
        return {mid: s for s in self.items if isinstance(s, Stub) for mid in s.message_ids}

    def state_sources(self) -> dict[str, list[StateUpdate]]:
        """Message id -> state lines that point to it (as source or as superseded)."""
        out: dict[str, list[StateUpdate]] = {}
        for u in self.state:
            for mid in (u.source, u.supersedes):
                if mid:
                    out.setdefault(mid, []).append(u)
        return out

    @property
    def state_tokens(self) -> int:
        return approx_tokens(self.state_text) if self.state_text else 0

    @property
    def tool_tokens(self) -> int:
        return sum(i.tokens for i in self.items if isinstance(i, Entry) and i.kind == "tool")

    @property
    def conversational_tokens(self) -> int:
        """User and assistant entries + stubs + state block."""
        return self.state_tokens + sum(i.tokens for i in self.items
                                       if isinstance(i, Stub) or i.kind in ("user", "assistant"))

    def text(self) -> str:
        """Plain-text rendering: state block, then each item. Entries get a header line with
        id and kind; stubs are shown as rendered."""
        parts = [self.state_text] if self.state_text else []
        for i in self.items:
            if isinstance(i, Stub):
                parts.append(i.text)
            else:
                parts.append(f"[{i.id} · {i.kind}]\n{_render_entry(i)}")
        return "\n\n".join(parts)


def build(entries: list[Entry], annotations: Annotations | None = None, *,
          keep_tool_results: int | None) -> LiveContext:
    """Live context for `entries` (a log or a prefix of one). keep_tool_results=None disables the
    tool layer (used only by tests). Never mutates its inputs. Raises ValueError if an op names an
    id not in the pilot entries (the applier rejects such output before it is accepted)."""
    annotations = annotations or Annotations()
    es = pilot_entries(entries)
    if keep_tool_results is not None:
        es = tool_layer.apply(es, keep_tool_results)
    pos = {e.id: n for n, e in enumerate(es)}
    first_of: dict[str, Op] = {}
    removed: set[str] = set()
    for op in annotations.ops:
        missing = [i for i in op.message_ids if i not in pos]
        if missing:
            raise ValueError(f"op names ids not in the entries: {missing}")
        op = replace(op, message_ids=tuple(sorted(op.message_ids, key=pos.__getitem__)))  # log order
        first_of[op.message_ids[0]] = op
        removed.update(op.message_ids)
    items: list[Entry | Stub] = []
    for e in es:
        if e.id in first_of:
            op = first_of[e.id]
            text = render_stub(op, annotations)
            role = "user" if e.kind == "user" else "assistant"
            items.append(Stub(op.op, op.message_ids, role, text, approx_tokens(text), stub_body(op)))
        elif e.id not in removed:
            items.append(e)
    state = current_state(annotations)
    return LiveContext(tuple(items), state, render_state(state))


def api_messages(ctx: LiveContext, system_prompt: str = "") -> tuple[str, list[dict]]:
    """Render as a Messages API request: (system, messages).

    system: `system_prompt` and the state block, joined by a blank line ("" when both are empty;
    the caller then omits the `system` parameter, whose text blocks have minLength 1).
    messages: one {"role", "content": str} per turn. user entries -> "user"; assistant entries ->
    "assistant"; tool entries -> "assistant", as plain text holding input and result (or the
    one-line stub), so there are no tool_use/tool_result blocks and no pairing to break; a stub
    takes the role of the first message it removed (a stub can never cover a tool message).
    Consecutive same-role items are merged into one turn, joined by a blank line, so roles always
    alternate. The caller appends the next user turn when the context ends with an assistant turn
    (current models reject a final assistant turn: prefill is unsupported, see
    validate_api_messages).
    """
    system = "\n\n".join(p for p in (system_prompt, ctx.state_text) if p)
    msgs: list[dict] = []
    for i in ctx.items:
        if isinstance(i, Stub):
            role, text = i.role, i.text
        else:
            role, text = ("user" if i.kind == "user" else "assistant"), _render_entry(i)
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n\n" + text
        else:
            msgs.append({"role": role, "content": text})
    return system, msgs


def validate_api_messages(messages: list[dict]) -> list[str]:
    """Errors (empty list = valid) for a messages list made by `api_messages`.

    Rules, from https://platform.claude.com/docs/en/api/messages/create and
    https://platform.claude.com/docs/en/build-with-claude/working-with-messages (read 2026-11-16):
      - roles are "user" or "assistant" (the system prompt goes in the top-level `system` field);
      - "Our models are trained to operate on alternating user and assistant conversational
        turns"; consecutive same-role turns are combined by the API. We require strict
        alternation (api_messages merges, so this holds by construction);
      - text content blocks have minLength 1; we require non-whitespace text in every turn;
      - the first message must be "user": NOT stated in those pages; a conservative choice;
      - not checked here: a final assistant turn is prefill, which returns a 400 on Claude 4.6
        and later models; trimming never changes the last turn (the last messages are
        protected), and the caller appends the next user turn.
    No tool_use/tool_result blocks are produced, so tool pairing rules do not apply.
    """
    errors = []
    if not messages:
        return ["no messages"]
    for n, m in enumerate(messages):
        if m.get("role") not in ("user", "assistant"):
            errors.append(f"message {n}: role {m.get('role')!r} is not user or assistant")
        if not isinstance(m.get("content"), str) or not m["content"].strip():
            errors.append(f"message {n}: empty content")
        if n and m.get("role") == messages[n - 1].get("role"):
            errors.append(f"message {n}: role {m.get('role')} does not alternate")
    if messages[0].get("role") != "user":
        errors.append("first message is not a user message")
    return errors
