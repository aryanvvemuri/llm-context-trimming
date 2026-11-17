"""Conversation-only rendering of messages, shared by every model call that reads a session
(content filter, needs lists, review packets).

USER and ASSISTANT text with message ids; every tool message as a one-line placeholder
(tool_layer.placeholder); native thinking excluded (pilot_entries).
"""

from __future__ import annotations

from ctxtrim import tool_layer
from ctxtrim.log import Entry, pilot_entries


def render_message(e: Entry) -> str:
    if e.kind == "tool":
        return tool_layer.placeholder(e)
    return f"[{e.id}] {e.kind.upper()}:\n{e.content}"


def conversation_view(entries: list[Entry]) -> tuple[str, list[str]]:
    """Conversation-only rendering of `entries` and the ids it shows, in order."""
    es = pilot_entries(entries)
    return "\n\n".join(render_message(e) for e in es), [e.id for e in es]
