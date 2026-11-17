"""Round-trip check (Phase 0 acceptance "no content lost"), shared by the real-data and fixture tests.

Reads the raw transcript independently of the adapter and checks, for every raw item
(each content block of a user/assistant line, a string message body, or a whole other line):
  - it is either covered by exactly one log entry (via sources.json) or listed in dropped.jsonl,
    never both and never neither;
  - for a covered item, its text is recoverable byte-exact from that entry:
      user        entry.content == "\\n".join(text of its raw parts, in order)
      assistant   entry.content == raw text block
      reasoning   entry.content == raw thinking text
      tool        entry.tool_name/tool_input == raw tool_use name/input,
                  entry.content == raw tool_result text (string, or text blocks joined with "\\n");
  - kept kinds are never silently dropped: a non-empty assistant text or thinking block may be
    dropped only under a listed non-content category (duplicate line, synthetic, sidechain).
"""

from __future__ import annotations

import json
from pathlib import Path

ALLOWED_DROPS_OF_TEXT = {"duplicate_uuid", "synthetic_assistant", "sidechain"}


def raw_items(lines: list[dict]) -> dict[tuple[int, int], dict]:
    items = {}
    for n, e in enumerate(lines):
        m = e.get("message") if e.get("type") in ("user", "assistant") else None
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list):
            for i, b in enumerate(c):
                items[(n, i)] = b
        elif isinstance(c, str):
            items[(n, -1)] = {"type": "text", "text": c}
        elif e.get("type") == "attachment" and (e.get("attachment") or {}).get("type") == "queued_command":
            p = e["attachment"].get("prompt")
            items[(n, -1)] = {"type": "text", "text": p if isinstance(p, str) else
                              "\n".join(x.get("text", "") for x in p if x.get("type") == "text")}
        else:
            items[(n, -1)] = {"type": "line:" + str(e.get("type"))}
    return items


def result_text(c) -> str:
    if isinstance(c, str):
        return c
    return "\n".join(b.get("text", "") for b in (c or []) if b.get("type") == "text")


def check_session(raw_path: Path, norm_dir: Path) -> list[str]:
    """Return a list of problems (empty = pass)."""
    lines = [json.loads(s) for s in raw_path.open(encoding="utf-8")]
    entries = {}
    for s in (norm_dir / "log.jsonl").open(encoding="utf-8"):
        d = json.loads(s)
        if d["record"] == "entry":
            entries[d["id"]] = d
    sources = json.loads((norm_dir / "sources.json").read_text())
    dropped = [json.loads(s) for s in (norm_dir / "dropped.jsonl").open(encoding="utf-8")]
    items = raw_items(lines)
    problems = []
    if set(sources) != set(entries):
        problems.append("sources.json ids differ from log ids")
    covered: dict[tuple[int, int], str] = {}
    for eid, refs in sources.items():
        for ln, bi in refs:
            if (ln, bi) in covered:
                problems.append(f"{(ln, bi)} covered twice")
            covered[(ln, bi)] = eid
    drop_cats: dict[tuple[int, int], set] = {}
    for d in dropped:
        drop_cats.setdefault((d["line"], d["block"]), set()).add(d["category"])
    for key, b in items.items():
        in_log, in_drop = key in covered, key in drop_cats
        cats = drop_cats.get(key, set())
        # A tool_result block may be kept and also carry a "tool_result_nontext:*" record for its images.
        if in_log and in_drop and not all(c.startswith("tool_result_nontext:") for c in cats):
            problems.append(f"{key} both kept and dropped {cats}")
        if not in_log and not in_drop:
            problems.append(f"{key} ({b.get('type')}) vanished: neither kept nor dropped")
        t = b.get("type")
        if not in_log and in_drop and not cats & ALLOWED_DROPS_OF_TEXT:
            if t == "text" and lines[key[0]].get("type") == "assistant" and b.get("text"):
                problems.append(f"{key} non-empty assistant text dropped as {cats}")
            if t == "thinking" and b.get("thinking", "").strip():
                problems.append(f"{key} non-empty thinking dropped as {cats}")
    for eid, e in entries.items():
        refs = [tuple(r) for r in sources[eid]]
        blocks = [items[r] for r in refs]
        if e["kind"] == "user":
            ok = all(b.get("type") == "text" for b in blocks) and e["content"] == "\n".join(b["text"] for b in blocks)
        elif e["kind"] == "assistant":
            ok = len(blocks) == 1 and blocks[0].get("type") == "text" and e["content"] == blocks[0]["text"]
        elif e["kind"] == "reasoning":
            ok = len(blocks) == 1 and blocks[0].get("type") == "thinking" and e["content"] == blocks[0]["thinking"]
        else:
            use, res = (blocks + [{}, {}])[:2]
            ok = (len(blocks) == 2 and use.get("type") in ("tool_use", "server_tool_use")
                  and res.get("type") == "tool_result" and res.get("tool_use_id") == use.get("id")
                  and e["tool_name"] == use.get("name") and e["tool_input"] == use.get("input")
                  and e["content"] == result_text(res.get("content")))
        if not ok:
            problems.append(f"{eid} ({e['kind']}) does not match its raw source {refs}")
    return problems
