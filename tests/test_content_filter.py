"""Tests for scripts/content_filter.py: view rendering, reply validation, reject-and-retry. No real model calls."""

import importlib.util
import json
from pathlib import Path

import pytest

from ctxtrim.log import make_entry, write_new_log

_spec = importlib.util.spec_from_file_location(
    "content_filter_script", Path(__file__).resolve().parents[1] / "scripts" / "content_filter.py")
cf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cf)

ENTRIES = [make_entry("m0001", None, "user", "use sqlite"),
           make_entry("m0002", None, "reasoning", "SECRET THINKING"),
           make_entry("m0003", None, "assistant", "ok, sqlite"),
           make_entry("m0004", None, "tool", "BIG OUTPUT", "Bash", {"command": "pytest\n-x"}),
           make_entry("m0005", None, "user", "actually use postgres")]
IDS = ["m0001", "m0003", "m0004", "m0005"]


def test_view_excludes_thinking_and_uses_tool_placeholders():
    view, ids = cf.conversation_view(ENTRIES)
    assert ids == IDS
    assert "SECRET THINKING" not in view and "BIG OUTPUT" not in view
    assert "[m0001] USER:\nuse sqlite" in view and "[m0003] ASSISTANT:\nok, sqlite" in view
    assert 'Bash pytest -x] → recall("m0004")' in view


def test_prompt_file_has_both_parts_and_placeholder():
    system, template = cf.load_prompt()
    assert "constraint_changed" in system and "NOT supersession" in system
    assert "{{TRANSCRIPT}}" in template and "{{TRANSCRIPT}}" not in system


def good(**kw):
    r = {"kind": "constraint_changed", "old": "m0001", "new": "m0005", "reason": "sqlite replaced by postgres"}
    r.update(kw)
    return r


def test_parse_valid_and_empty_and_fenced():
    assert cf.parse_reply(json.dumps({"reversals": [good()]}), IDS) == [good()]
    assert cf.parse_reply('{"reversals": []}', IDS) == []
    assert cf.parse_reply('```json\n{"reversals": []}\n```', IDS) == []


@pytest.mark.parametrize("reply,why", [
    ("not json", "not valid JSON"),
    ('{"reversals": [], "extra": 1}', "top level"),
    (json.dumps({"reversals": [good(kind="other")]}), "unknown kind"),
    (json.dumps({"reversals": [good(old="m0002")]}), "not in the transcript"),  # thinking id is not shown
    (json.dumps({"reversals": [good(old="m0005", new="m0001")]}), "does not precede"),
    (json.dumps({"reversals": [good(old="m0003", new="m0003")]}), "does not precede"),
    (json.dumps({"reversals": [good(reason="a\nb")]}), "one non-empty line"),
    (json.dumps({"reversals": [dict(good(), extra=1)]}), "keys must be exactly"),
])
def test_parse_rejects(reply, why):
    with pytest.raises(ValueError, match=why):
        cf.parse_reply(reply, IDS)


def test_filter_session_retries_with_changed_prompt(tmp_path, monkeypatch):
    sid = "s1"
    write_new_log(tmp_path / sid / "log.jsonl", ENTRIES)
    monkeypatch.setattr(cf.config, "NORMALIZED_DIR", tmp_path)
    replies = iter(["oops", json.dumps({"reversals": [good()]})])
    prompts = []

    class R:
        def __init__(self, text):
            self.text, self.model_reported, self.date = text, ["claude-sonnet-5-5"], "d"

    def fake_call(prompt, *, system, model, purpose):
        prompts.append(prompt)
        return R(next(replies))

    monkeypatch.setattr(cf.model, "call", fake_call)
    system, template = cf.load_prompt()
    rec = cf.filter_session(sid, system, template)
    assert rec["status"] == "ok" and rec["attempts"] == 2 and rec["reversals"] == [good()]
    assert len(prompts) == 2 and prompts[0] != prompts[1] and "rejected: not valid JSON" in prompts[1]


def test_filter_session_gives_up_after_max_attempts(tmp_path, monkeypatch):
    write_new_log(tmp_path / "s1" / "log.jsonl", ENTRIES)
    monkeypatch.setattr(cf.config, "NORMALIZED_DIR", tmp_path)
    calls = []

    class R:
        text, model_reported, date = "bad", [], "d"

    monkeypatch.setattr(cf.model, "call", lambda prompt, **kw: calls.append(prompt) or R())
    rec = cf.filter_session("s1", *cf.load_prompt())
    assert rec["status"] == "invalid" and len(calls) == cf.MAX_ATTEMPTS and len(set(calls)) == cf.MAX_ATTEMPTS
