"""Adapter: raw Claude Code transcript JSONL (as shipped in SWE-chat) -> log entries (PLAN.md 5.1).

One log message = one user message, one assistant text block, one thinking block (kind
"reasoning"), or one tool call together with its result (paired by tool_use id; the entry
sits at the tool call's position, even when the file writes the result line first). Raw
files are only read.

Kept (what the main agent's context contains and the pilot studies):
  user       : human-typed prompts (string content or text blocks), including slash-command
               invocations (<command-name>...), `!` shell inputs (<bash-input>), and
               "[Request interrupted by user...]" markers (the SWE-chat card counts these as
               user prompts too), plus prompts typed while the agent was busy and delivered as
               `queued_command` attachments (commandMode "prompt"). Text parts are joined with "\n".
  assistant  : each non-empty assistant text block.
  reasoning  : each thinking block with non-empty text.
  tool       : each tool_use / server_tool_use with its tool_result. content = result text
               (string content as is; list content = its text blocks joined with "\n").
Dropped, each recorded by category (see CATEGORY_NOTES), nothing silently:
  harness/system-injected user content, compaction summaries, meta messages, synthetic
  API-error assistant messages, empty or redacted thinking, images, tool calls with no result,
  orphan or duplicate tool results, duplicate lines (same uuid), sidechain messages, and every
  non-message line (progress, system events, attachments, file snapshots, queue ops, titles...).
Conversation branches (rewinds) are not resolved: entries are kept in file order and the
number of fork points is reported.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ctxtrim.log import Entry, approx_tokens, make_entry, tool_input_text

# User text that starts with one of these tags is harness output, not human-typed prose.
SYSTEM_TAGS = ("<task-notification>", "<local-command-stdout>", "<local-command-stderr>",
               "<local-command-caveat>", "<bash-stdout>", "<bash-stderr>", "<ci-monitor-event>",
               "<system-reminder>")

CATEGORY_NOTES = {
    "duplicate_uuid": "line repeats a uuid already seen in the file (resume/rewrite copies)",
    "sidechain": "isSidechain message (subagent); not in the main agent's context",
    "compact_summary": "compaction summary user message (isCompactSummary); the original messages are kept",
    "meta": "isMeta user message (skill/command expansion, caveats, reminders, hook feedback)",
    "system_injected:*": "harness-generated user content (task notifications, command stdout, origin/promptSource=system)",
    "synthetic_assistant": "isApiErrorMessage / model '<synthetic>' assistant message (not model output)",
    "assistant_text_empty": "assistant text block with empty text",
    "thinking_empty": "thinking block whose text is empty (signature only)",
    "thinking_redacted": "redacted_thinking block (no readable text)",
    "user_image": "image block in a user message (the message's text is kept)",
    "user_message_no_text": "user message with no text at all (e.g. image only)",
    "text_in_tool_result_entry": "text block inside a user entry that carries tool results",
    "tool_result_nontext:*": "non-text part of a tool result (image, tool_reference); result text is kept",
    "tool_use_no_result": "tool call never paired with a result (interrupted); dropped with its input",
    "tool_use_duplicate_id": "tool call whose id was already seen",
    "tool_result_orphan": "tool result whose tool call id never appears (results written before their call are paired)",
    "tool_result_duplicate": "second result for an already-paired tool call",
    "assistant_block:*": "other assistant block types",
    "line:*": "non-message line type (progress, system, attachment, file-history-snapshot, queue-operation, ...)",
}


def _text_of(c) -> str:
    """Text of a tool result's content: a string as is, a list's text blocks joined with "\\n"."""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _user_category(e: dict, text: str) -> str | None:
    """None if the user message is human-typed (kept), else the drop category."""
    if e.get("isSidechain"):
        return "sidechain"
    if e.get("isCompactSummary") or e.get("isVisibleInTranscriptOnly"):
        return "compact_summary"
    if e.get("isMeta"):
        return "meta"
    kind = (e.get("origin") or {}).get("kind")
    if kind not in (None, "human"):
        return f"system_injected:origin_{kind}"
    if e.get("promptSource") == "system":
        return "system_injected:prompt_source_system"
    stripped = text.lstrip()
    for tag in SYSTEM_TAGS:
        if stripped.startswith(tag):
            return "system_injected:" + tag.strip("<>")
    return None


class _Builder:
    def __init__(self):
        self.items: list[dict] = []          # pending log messages in order
        self.tools: dict[str, dict] = {}     # tool_use id -> item
        self.dropped: list[dict] = []        # {"line", "block", "category", "tokens"}
        self.early_results: dict = {}        # tool_use id -> (line, block, result) seen before its call

    def drop(self, line: int, block: int, category: str, text: str = "") -> None:
        self.dropped.append({"line": line, "block": block, "category": category, "tokens": approx_tokens(text)})

    def add(self, kind: str, ts, content: str, refs: list, **kw) -> dict:
        item = {"kind": kind, "ts": ts, "content": content, "refs": refs, **kw}
        self.items.append(item)
        return item


def _user_entry(b: _Builder, n: int, e: dict) -> None:
    c = e["message"].get("content")
    blocks = [{"type": "text", "text": c}] if isinstance(c, str) else (c or [])
    if e.get("isSidechain"):
        for i, x in enumerate(blocks):
            b.drop(n, i if isinstance(c, list) else -1, "sidechain", x.get("text") or _text_of(x.get("content")))
        return
    has_results = any(isinstance(x, dict) and x.get("type") == "tool_result" for x in blocks)
    if has_results:
        for i, x in enumerate(blocks):
            if x.get("type") == "tool_result":
                _tool_result(b, n, i, x)
            elif x.get("type") == "text":
                b.drop(n, i, "text_in_tool_result_entry", x.get("text", ""))
            else:
                b.drop(n, i, "user_" + str(x.get("type")))
        return
    texts = [(i, x["text"]) for i, x in enumerate(blocks) if x.get("type") == "text"]
    joined = "\n".join(t for _, t in texts)
    category = _user_category(e, joined)
    for i, x in enumerate(blocks):
        if x.get("type") != "text":
            b.drop(n, i if isinstance(c, list) else -1, category or "user_" + str(x.get("type")))
    if category is not None:
        for i, t in texts:
            b.drop(n, i if isinstance(c, list) else -1, category, t)
        return
    if not texts:
        b.drop(n, -1, "user_message_no_text")
        return
    b.add("user", e.get("timestamp"), joined, [[n, i if isinstance(c, list) else -1] for i, _ in texts])


def _tool_result(b: _Builder, n: int, i: int, x: dict) -> None:
    item = b.tools.get(x.get("tool_use_id"))
    text = _text_of(x.get("content"))
    if item is None:
        # Some files write a result line before its tool call; hold it until the call appears.
        if x.get("tool_use_id") in b.early_results:
            b.drop(n, i, "tool_result_duplicate", text)
        else:
            b.early_results[x.get("tool_use_id")] = (n, i, x)
        return
    if item["has_result"]:
        b.drop(n, i, "tool_result_duplicate", text)
        return
    item["has_result"] = True
    item["content"] = text
    item["refs"].append([n, i])
    if isinstance(x.get("content"), list):
        for x2 in x["content"]:
            if isinstance(x2, dict) and x2.get("type") != "text":
                b.drop(n, i, "tool_result_nontext:" + str(x2.get("type")))


def _assistant_entry(b: _Builder, n: int, e: dict, thinking: Counter) -> None:
    m = e["message"]
    blocks = m.get("content")
    if isinstance(blocks, str):
        blocks = [{"type": "text", "text": blocks}]
    synthetic = e.get("isApiErrorMessage") or m.get("model") == "<synthetic>"
    for i, x in enumerate(blocks or []):
        t = x.get("type")
        if t == "thinking":
            thinking["blocks"] += 1
            thinking["with_signature"] += bool(x.get("signature"))
            thinking["with_text"] += bool(x.get("thinking", "").strip())
        if e.get("isSidechain"):
            b.drop(n, i, "sidechain", x.get("text") or x.get("thinking") or "")
        elif synthetic:
            b.drop(n, i, "synthetic_assistant", x.get("text", ""))
        elif t == "text":
            if x.get("text", "") == "":
                b.drop(n, i, "assistant_text_empty")
            else:
                b.add("assistant", e.get("timestamp"), x["text"], [[n, i]])
        elif t == "thinking":
            if x.get("thinking", "").strip():
                b.add("reasoning", e.get("timestamp"), x["thinking"], [[n, i]])
            else:
                b.drop(n, i, "thinking_empty", x.get("thinking", ""))
        elif t == "redacted_thinking":
            b.drop(n, i, "thinking_redacted")
        elif t in ("tool_use", "server_tool_use"):
            if x.get("id") in b.tools:
                b.drop(n, i, "tool_use_duplicate_id", tool_input_text(x.get("input")))
            else:
                b.tools[x["id"]] = b.add("tool", e.get("timestamp"), "", [[n, i]],
                                         tool_name=x.get("name"), tool_input=x.get("input"), has_result=False)
                if x["id"] in b.early_results:
                    _tool_result(b, *b.early_results.pop(x["id"]))
        elif t == "tool_result":  # server-side tool result inside the assistant message
            _tool_result(b, n, i, x)
        else:
            b.drop(n, i, "assistant_block:" + str(t))


def _attachment(b: _Builder, n: int, e: dict) -> None:
    a = e.get("attachment") or {}
    kind = (a.get("origin") or {}).get("kind")
    if (a.get("type") == "queued_command" and a.get("commandMode") == "prompt" and not a.get("isMeta")
            and kind in (None, "human") and not e.get("isSidechain")):
        p = a.get("prompt")
        text = p if isinstance(p, str) else _text_of(p)
        if text:
            b.add("user", a.get("timestamp") or e.get("timestamp"), text, [[n, -1]])
            return
    b.drop(n, -1, "line:attachment:" + str(a.get("type")), json.dumps(a, ensure_ascii=False))


def fork_points(lines: list[dict]) -> int:
    """Parents with two or more children that start different turns (rewinds / edits)."""
    children: dict[str, list[dict]] = {}
    for e in lines:
        if e.get("parentUuid") and e.get("type") in ("user", "assistant"):
            children.setdefault(e["parentUuid"], []).append(e)
    n = 0
    for cs in children.values():
        prompts = [c for c in cs if c["type"] == "user" and not (
            isinstance(c["message"].get("content"), list)
            and any(x.get("type") == "tool_result" for x in c["message"]["content"]))]
        replies = {c["message"].get("id") for c in cs if c["type"] == "assistant"}
        n += (len(prompts) + len(replies)) >= 2
    return n


def format_problem(path: str | Path) -> str | None:
    """None if the file looks like a raw Claude Code transcript, else a short reason.

    Some SWE-chat sessions labelled agent == "Claude Code" ship transcripts in other agents'
    formats. Required here: every line is JSON, there is at least one user/assistant line, and
    every user/assistant line carries "uuid" and "sessionId". This is a gate, not a converter.
    """
    n = 0
    with open(path, encoding="utf-8") as f:
        for s in f:
            try:
                e = json.loads(s)
            except json.JSONDecodeError:
                return "not_jsonl"
            if isinstance(e, dict) and e.get("type") in ("user", "assistant"):
                n += 1
                if "uuid" not in e or "sessionId" not in e:
                    return "user_or_assistant_line_without_uuid_or_sessionId"
    return None if n else "no_user_or_assistant_lines"


def convert(path: str | Path) -> tuple[list[Entry], dict[str, list], list[dict], dict]:
    """Parse one raw transcript.

    Returns (entries, sources, dropped, report):
      sources : entry id -> [[line, block], ...] raw positions (block -1 = whole string/attachment)
      dropped : one record per dropped item {"line", "block", "category", "tokens"} (no text)
      report  : counts by category, thinking-block counts, fork points.
    """
    with open(path, encoding="utf-8") as f:
        lines = [json.loads(s) for s in f]
    b = _Builder()
    thinking = Counter()
    seen = set()
    for n, e in enumerate(lines):
        t = e.get("type")
        u = e.get("uuid")
        if u is not None and u in seen:
            c = e["message"].get("content") if isinstance(e.get("message"), dict) else None
            for i in range(len(c)) if isinstance(c, list) else [-1]:
                b.drop(n, i, "duplicate_uuid")
            continue
        if u is not None:
            seen.add(u)
        if t == "user" and isinstance(e.get("message"), dict):
            _user_entry(b, n, e)
        elif t == "assistant" and isinstance(e.get("message"), dict):
            _assistant_entry(b, n, e, thinking)
        elif t == "attachment":
            _attachment(b, n, e)
        elif t == "system":
            b.drop(n, -1, f"line:system:{e.get('subtype')}", str(e.get("content") or ""))
        else:
            b.drop(n, -1, f"line:{t}")
    for ln, bi, x in b.early_results.values():
        b.drop(ln, bi, "tool_result_orphan", _text_of(x.get("content")))
    entries, sources = [], {}
    for item in b.items:
        if item["kind"] == "tool" and not item["has_result"]:
            ln, bi = item["refs"][0]
            b.drop(ln, bi, "tool_use_no_result", tool_input_text(item["tool_input"]))
            continue
        eid = f"m{len(entries) + 1:04d}"
        entries.append(make_entry(eid, item["ts"], item["kind"], item["content"],
                                  item.get("tool_name"), item.get("tool_input")))
        sources[eid] = item["refs"]
    by_cat: dict[str, dict] = {}
    for d in b.dropped:
        c = by_cat.setdefault(d["category"], {"count": 0, "tokens": 0})
        c["count"] += 1
        c["tokens"] += d["tokens"]
    report = {
        "raw_lines": len(lines),
        "kept": dict(Counter(e.kind for e in entries)),
        "dropped": dict(sorted(by_cat.items())),
        "thinking_blocks": dict(thinking),
        "fork_points": fork_points(lines),
        "compact_boundaries": sum(1 for e in lines if e.get("type") == "system" and e.get("subtype") == "compact_boundary"),
        "models": dict(Counter(e["message"].get("model") for e in lines
                               if e.get("type") == "assistant" and isinstance(e.get("message"), dict))),
    }
    return entries, sources, b.dropped, report
