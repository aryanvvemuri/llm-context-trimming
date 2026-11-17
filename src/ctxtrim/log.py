"""Append-only session log (PLAN.md 5.1) and thread/annotation records (5.2).

A log is a JSONL file. Each line is one record with a "record" field:
  "entry"  : a log message, fields from 5.1 (id, ts, kind, tool_name, tool_input, content, tokens)
  "thread" : a thread record, fields from 5.2 (thread_id, title, status, message_ids)
  "annotation": any other appended note (free-form "data" dict)
There is no edit or delete API: records are only appended. A later thread record with the
same thread_id supersedes the earlier one when read (`latest_threads`); nothing is rewritten.

Token counts use `approx_tokens`, the single place where tokens are estimated.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

KINDS = ("user", "assistant", "reasoning", "tool")
THREAD_STATUSES = ("active", "resolved", "abandoned")


def approx_tokens(text: str) -> int:
    """Approximate token count: ceil(characters / 4). No tokenizer, no API call.

    This is the only token estimate in the project; swap this function to change it.
    """
    return math.ceil(len(text) / 4)


def tool_input_text(tool_input) -> str:
    """Canonical text of a tool input, used for token counts."""
    return "" if tool_input is None else json.dumps(tool_input, ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class Entry:
    id: str
    ts: str | None
    kind: str
    content: str
    tool_name: str | None = None
    tool_input: dict | None = None
    tokens: int = 0

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown kind {self.kind!r}")
        if (self.kind == "tool") != (self.tool_name is not None):
            raise ValueError("tool_name must be set exactly for kind 'tool'")


def make_entry(id: str, ts: str | None, kind: str, content: str, tool_name=None, tool_input=None) -> Entry:
    """Build an entry with tokens = approx_tokens(content) + approx_tokens(tool input)."""
    tokens = approx_tokens(content) + (approx_tokens(tool_input_text(tool_input)) if kind == "tool" else 0)
    return Entry(id, ts, kind, content, tool_name, tool_input, tokens)


@dataclass(frozen=True)
class Thread:
    thread_id: str
    title: str
    status: str
    message_ids: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.status not in THREAD_STATUSES:
            raise ValueError(f"unknown thread status {self.status!r}")


@dataclass(frozen=True)
class Annotation:
    data: dict


_TYPES = {"entry": Entry, "thread": Thread, "annotation": Annotation}
_NAMES = {cls: name for name, cls in _TYPES.items()}


class Log:
    """Append-only JSONL log. Opening never truncates; writes only append."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, record: Entry | Thread | Annotation) -> None:
        if isinstance(record, Entry) and record.id in {e.id for e in self.entries()}:
            raise ValueError(f"duplicate entry id {record.id}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"record": _NAMES[type(record)], **asdict(record)}, ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def records(self) -> list[Entry | Thread | Annotation]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                out.append(_TYPES[d.pop("record")](**d))
        return out

    def entries(self) -> list[Entry]:
        return [r for r in self.records() if isinstance(r, Entry)]

    def latest_threads(self) -> dict[str, Thread]:
        """Thread records by id; a later record for the same id wins."""
        return {r.thread_id: r for r in self.records() if isinstance(r, Thread)}


def pilot_entries(entries: list[Entry]) -> list[Entry]:
    """The entries the pilot uses: everything except native thinking blocks (kind "reasoning").

    Human decision (2026-11-16, PLAN.md 4.2): native thinking blocks are excluded everywhere
    (context, pruning rates, needs lists). This is the one place that exclusion is made; the
    normalized logs keep the reasoning entries. Reasoning written in ordinary assistant text
    is kept (it is part of the assistant entries).
    """
    return [e for e in entries if e.kind != "reasoning"]


# User entries that are not typed requests: interrupt markers, slash commands and their output,
# `!` shell inputs (the adapter keeps them as user entries with these prefixes).
NON_PLAIN_PREFIXES = ("[Request interrupted", "<command-", "<bash-input>")


def is_plain_user(e: Entry) -> bool:
    """A human-typed user turn: a user entry that is not an interrupt marker, slash command or `!`
    input. The one definition of a "plain" user turn (data/README.md, cut points, recency turns)."""
    return e.kind == "user" and not e.content.lstrip().startswith(NON_PLAIN_PREFIXES)


def write_new_log(path: str | Path, entries: list[Entry]) -> Log:
    """Create a log from entries. Refuses to touch an existing file."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{path} exists; logs are never rewritten")
    ids = [e.id for e in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate entry ids")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps({"record": "entry", **asdict(e)}, ensure_ascii=False) + "\n")
    return Log(path)
