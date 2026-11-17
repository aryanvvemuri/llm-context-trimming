"""The fixed tool-result layer (PLAN.md 4.1). Applied identically under every policy.

Rule, exactly:
  1. A tool result is cleared when a later call re-reads or edits the same path
     (path tools below, same path) or re-runs the same command (Bash, identical command string).
  2. All tool results except the most recent `keep` tool results are cleared.
Amendment (2026-11-16, approved by the human): a cleared tool message is replaced as a whole,
input and result, by a one-line stub (tool_input becomes None). Which messages are cleared is
unchanged. The most recent `keep` tool messages stay complete (input and result).
Nothing else is done: no summarising, no size thresholds, no other tools.
"""

from __future__ import annotations

from dataclasses import replace

from ctxtrim.log import Entry, approx_tokens

PATH_TOOLS = {"Read": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
              "Write": "file_path", "NotebookEdit": "notebook_path"}
COMMAND_TOOLS = {"Bash": "command"}


def _target(e: Entry) -> tuple[str, str] | None:
    """("path", p) or ("command", c) for tools the rule covers, else None."""
    inp = e.tool_input if isinstance(e.tool_input, dict) else {}
    if e.tool_name in PATH_TOOLS and isinstance(inp.get(PATH_TOOLS[e.tool_name]), str):
        return ("path", inp[PATH_TOOLS[e.tool_name]])
    if e.tool_name in COMMAND_TOOLS and isinstance(inp.get(COMMAND_TOOLS[e.tool_name]), str):
        return ("command", inp[COMMAND_TOOLS[e.tool_name]])
    return None


def stub_text(e: Entry, reason: str) -> str:
    t = _target(e)
    what = " ".join(t[1].split())[:80] if t else ""
    return f'[tool call cleared · {e.id} · {e.tool_name} {what} · {reason}] → recall("{e.id}")'


def cleared_ids(entries: list[Entry], keep: int) -> dict[str, str]:
    """Map of tool entry id -> reason ("superseded" or "old") for every result the rule clears."""
    tools = [e for e in entries if e.kind == "tool"]
    out: dict[str, str] = {}
    seen_later: set[tuple[str, str]] = set()
    for e in reversed(tools):
        t = _target(e)
        if t is not None:
            if t in seen_later:
                out[e.id] = "superseded"
            seen_later.add(t)
    recent = {e.id for e in tools[-keep:]} if keep > 0 else set()
    for e in tools:
        if e.id not in recent and e.id not in out:
            out[e.id] = "old"
    return out


def placeholder(e: Entry) -> str:
    """One-line placeholder for a tool message in conversation-only views (PLAN.md 4.1, 7 Phase 2)."""
    t = _target(e)
    what = " ".join(t[1].split())[:80] if t else ""
    return f'[tool call · {e.id} · {e.tool_name} {what}] → recall("{e.id}")'


def apply(entries: list[Entry], keep: int) -> list[Entry]:
    """Return a copy of `entries` where each cleared tool message (input and result) is one stub line.

    The stub becomes the content, tool_input becomes None, tokens = approx_tokens(stub).
    """
    cleared = cleared_ids(entries, keep)
    out = []
    for e in entries:
        if e.id in cleared:
            stub = stub_text(e, cleared[e.id])
            e = replace(e, content=stub, tool_input=None, tokens=approx_tokens(stub))
        out.append(e)
    return out
