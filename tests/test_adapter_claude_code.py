"""Adapter tests on a small synthetic Claude Code transcript (no real session text)."""

import json

from ctxtrim.adapters import swe_chat_claude_code as adapter
from ctxtrim.log import write_new_log
from roundtrip import check_session


def U(uuid, content, **kw):
    return {"type": "user", "uuid": uuid, "timestamp": "t", "message": {"role": "user", "content": content}, **kw}


def A(uuid, blocks, mid="msg1", **kw):
    return {"type": "assistant", "uuid": uuid, "timestamp": "t",
            "message": {"id": mid, "role": "assistant", "model": "m", "content": blocks}, **kw}


LINES = [
    {"type": "file-history-snapshot", "messageId": "x", "snapshot": {}},
    U("u1", "please fix the bug"),                                              # user (kept)
    U("u2", "<local-command-stdout>ok</local-command-stdout>"),                 # system_injected
    U("u3", [{"type": "text", "text": "expanded skill"}], isMeta=True),         # meta
    A("a1", [{"type": "thinking", "thinking": "let me think", "signature": "s"}]),  # reasoning (kept)
    A("a2", [{"type": "thinking", "thinking": "", "signature": "s"}]),          # thinking_empty
    A("a3", [{"type": "text", "text": "I will read it"}]),                      # assistant (kept)
    U("r0", [{"type": "tool_result", "tool_use_id": "t2", "content": "early result"}]),  # written before its call
    A("a4", [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/f"}}]),
    A("a5", [{"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "ls"}}]),
    U("r1", [{"type": "tool_result", "tool_use_id": "t1",
              "content": [{"type": "text", "text": "line1"}, {"type": "image", "source": {}},
                          {"type": "text", "text": "line2"}]}]),
    U("r1", [{"type": "tool_result", "tool_use_id": "t1", "content": "dup"}]),  # duplicate uuid
    A("a6", [{"type": "tool_use", "id": "t3", "name": "Edit", "input": {"file_path": "/f"}}]),  # no result
    {"type": "progress", "uuid": "p1", "data": {"type": "bash_progress"}},
    {"type": "system", "uuid": "s1", "subtype": "compact_boundary"},
    U("c1", "This session is being continued ...", isCompactSummary=True),     # compact_summary
    U("u4", [{"type": "text", "text": "and"}, {"type": "image", "source": {}}, {"type": "text", "text": "this"}]),
    {"type": "attachment", "uuid": "q1", "attachment": {"type": "queued_command", "prompt": "also this",
                                                          "commandMode": "prompt"}},
    {"type": "attachment", "uuid": "q2", "attachment": {"type": "queued_command", "prompt": "<task-notification>",
                                                          "commandMode": "task-notification"}},
    U("u5", "<task-notification>done</task-notification>"),
    U("u6", "hi", origin={"kind": "task-notification"}),
    A("a7", [{"type": "text", "text": "API Error"}], mid="x", isApiErrorMessage=True),
    U("r9", [{"type": "tool_result", "tool_use_id": "nope", "content": "orphan"}]),
]


def write(tmp_path):
    raw = tmp_path / "raw.jsonl"
    raw.write_text("".join(json.dumps(x) + "\n" for x in LINES))
    entries, sources, dropped, report = adapter.convert(raw)
    norm = tmp_path / "norm"
    write_new_log(norm / "log.jsonl", entries)
    (norm / "sources.json").write_text(json.dumps(sources))
    (norm / "dropped.jsonl").write_text("".join(json.dumps(d) + "\n" for d in dropped))
    return raw, norm, entries, report


def test_entries_and_order(tmp_path):
    _, _, entries, _ = write(tmp_path)
    got = [(e.kind, e.content, e.tool_name) for e in entries]
    assert got == [
        ("user", "please fix the bug", None),
        ("reasoning", "let me think", None),
        ("assistant", "I will read it", None),
        ("tool", "line1\nline2", "Read"),
        ("tool", "early result", "Bash"),
        ("user", "and\nthis", None),
        ("user", "also this", None),
    ]
    assert [e.id for e in entries] == [f"m{i:04d}" for i in range(1, 8)]
    assert entries[3].tool_input == {"file_path": "/f"}


def test_dropped_categories_counted(tmp_path):
    _, _, _, report = write(tmp_path)
    d = {k: v["count"] for k, v in report["dropped"].items()}
    assert d == {
        "line:file-history-snapshot": 1,
        "system_injected:local-command-stdout": 1,
        "meta": 1,
        "thinking_empty": 1,
        "tool_result_nontext:image": 1,
        "duplicate_uuid": 1,
        "tool_use_no_result": 1,
        "line:progress": 1,
        "line:system:compact_boundary": 1,
        "compact_summary": 1,
        "user_image": 1,
        "line:attachment:queued_command": 1,
        "system_injected:task-notification": 1,
        "system_injected:origin_task-notification": 1,
        "synthetic_assistant": 1,
        "tool_result_orphan": 1,
    }
    assert report["thinking_blocks"] == {"blocks": 2, "with_signature": 2, "with_text": 1}


def test_round_trip_checker_passes(tmp_path):
    raw, norm, _, _ = write(tmp_path)
    assert check_session(raw, norm) == []


def test_round_trip_checker_catches_lost_and_altered_content(tmp_path):
    raw, norm, _, _ = write(tmp_path)
    lines = (norm / "log.jsonl").read_text().splitlines()
    altered = json.loads(lines[2])
    altered["content"] += "!"
    (norm / "log.jsonl").write_text("\n".join([lines[0], lines[1], json.dumps(altered)] + lines[3:]) + "\n")
    assert any("does not match" in p for p in check_session(raw, norm))
    dropped = (norm / "dropped.jsonl").read_text().splitlines()
    (norm / "dropped.jsonl").write_text("\n".join(dropped[1:]) + "\n")  # lose one dropped record
    assert any("vanished" in p for p in check_session(raw, norm))


def test_raw_file_untouched(tmp_path):
    raw = tmp_path / "raw.jsonl"
    raw.write_text("".join(json.dumps(x) + "\n" for x in LINES))
    before = raw.read_bytes()
    adapter.convert(raw)
    assert raw.read_bytes() == before


def test_format_problem_gate(tmp_path):
    good = tmp_path / "good.jsonl"
    good.write_text(json.dumps({"type": "user", "uuid": "u", "sessionId": "s", "message": {"content": "x"}}) + "\n")
    assert adapter.format_problem(good) is None
    other = tmp_path / "other.jsonl"
    other.write_text(json.dumps({"type": "assistant", "message": {"content": []}}) + "\n")
    assert adapter.format_problem(other) == "user_or_assistant_line_without_uuid_or_sessionId"
    none = tmp_path / "none.jsonl"
    none.write_text(json.dumps({"type": "response_item", "payload": {}}) + "\n")
    assert adapter.format_problem(none) == "no_user_or_assistant_lines"
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{\n  \"a\": 1\n}\n")
    assert adapter.format_problem(bad) == "not_jsonl"
