"""Model interface tests with a fake backend. No real model calls."""

import json

import pytest

from ctxtrim import model
from ctxtrim.model import ClaudeCodeBackend, ModelError, parse_stream


class FakeBackend:
    name = "fake"

    def __init__(self, text="hello", fail=False, settings=None):
        self.calls = []
        self.text, self.fail = text, fail
        self.settings = settings or {"effort": None}

    def run(self, prompt, system, model_id):
        self.calls.append((prompt, system, model_id))
        if self.fail:
            raise ModelError("boom")
        return {"text": self.text, "models": [model_id], "usage": {"input_tokens": 3, "output_tokens": 1},
                "cost_usd": 0.001, "meta": {}}


def _call(b, tmp_path, prompt="p", system="s", m="model-a"):
    return model.call(prompt, system=system, model=m, backend=b, purpose="test",
                      cache_dir=tmp_path / "cache", ledger_path=tmp_path / "ledger.jsonl")


def ledger(tmp_path):
    return [json.loads(x) for x in (tmp_path / "ledger.jsonl").read_text().splitlines()]


def test_repeated_call_is_served_from_cache(tmp_path):
    b = FakeBackend()
    r1 = _call(b, tmp_path)
    r2 = _call(b, tmp_path)
    assert len(b.calls) == 1
    assert (r1.cached, r2.cached) == (False, True)
    assert r2.text == r1.text and r2.date == r1.date and r2.model_reported == ["model-a"]
    assert [x["status"] for x in ledger(tmp_path)] == ["cache_miss", "cache_hit"]
    assert ledger(tmp_path)[0]["usage"] == {"input_tokens": 3, "output_tokens": 1}


def test_any_input_change_is_a_miss(tmp_path):
    b = FakeBackend()
    _call(b, tmp_path)
    _call(b, tmp_path, prompt="p2")
    _call(b, tmp_path, system="s2")
    _call(b, tmp_path, m="model-b")
    _call(FakeBackend(settings={"effort": "high"}), tmp_path)
    assert len(b.calls) == 4
    assert len({x["cache_key"] for x in ledger(tmp_path)}) == 5


def test_errors_and_empty_output_are_not_cached(tmp_path):
    with pytest.raises(ModelError):
        _call(FakeBackend(fail=True), tmp_path)
    with pytest.raises(ModelError):
        _call(FakeBackend(text="  "), tmp_path)
    b = FakeBackend()
    _call(b, tmp_path)
    assert len(b.calls) == 1  # the earlier failures left nothing in the cache
    assert [x["status"] for x in ledger(tmp_path)] == ["error", "error", "cache_miss"]


def test_ledger_has_no_prompt_text(tmp_path):
    _call(FakeBackend(text="RESPONSE_TEXT"), tmp_path, prompt="SECRET_PROMPT", system="SECRET_SYSTEM")
    raw = (tmp_path / "ledger.jsonl").read_text()
    assert "SECRET" not in raw and "RESPONSE_TEXT" not in raw


# --- claude_code backend: command line and output parsing (no process is started) ---

def test_command_keeps_prompt_out_of_arguments_and_disables_everything():
    b = ClaudeCodeBackend(bin_path="/x/claude", timeout_s=5)
    cmd = b.command("SYS", "claude-sonnet-5-5")
    assert cmd[0] == "/x/claude" and "-p" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5-5"
    assert cmd[cmd.index("--system-prompt") + 1] == "SYS" and "--append-system-prompt" not in cmd
    assert cmd[cmd.index("--tools") + 1] == "" and cmd[cmd.index("--setting-sources") + 1] == ""
    for flag in ("--strict-mcp-config", "--safe-mode", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in cmd
    assert "--mcp-config" not in cmd and "--effort" not in cmd


def test_environment_drops_inherited_claude_and_anthropic_vars(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")
    monkeypatch.setenv("CLAUDECODE", "1")
    env = ClaudeCodeBackend.environment()
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDE_CODE_SIMPLE" not in env and "CLAUDECODE" not in env
    assert env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1" and env["CLAUDE_CODE_SKIP_PROMPT_HISTORY"] == "1"


INIT = {"type": "system", "subtype": "init", "model": "claude-sonnet-5-5", "tools": [], "mcp_servers": [], "plugins": []}
RESULT = {"type": "result", "subtype": "success", "is_error": False, "result": "ok", "num_turns": 1,
          "usage": {"input_tokens": 10}, "total_cost_usd": 0.01,
          "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 10, "outputTokens": 2}}}


def stream(*events):
    return "\n".join(json.dumps(e) for e in events) + "\n"


def test_parse_success():
    r = parse_stream(0, stream(INIT, RESULT), "", "claude-sonnet-5-5", [])
    assert r["text"] == "ok" and r["models"] == ["claude-sonnet-5-5"] and r["usage"] == {"input_tokens": 10}


@pytest.mark.parametrize("rc,events,why", [
    (1, (INIT, dict(RESULT, is_error=True, result="Not logged in")), "exited"),
    (0, (INIT, dict(RESULT, is_error=True)), "error result"),
    (0, (INIT, dict(RESULT, subtype="error_max_turns")), "error result"),
    (0, (INIT,), "no result"),
    (0, (RESULT,), "no init"),
    (0, (dict(INIT, tools=["Bash"]), RESULT), "not clean"),
    (0, (dict(INIT, mcp_servers=[{"name": "x"}]), RESULT), "not clean"),
    (0, (INIT, dict(RESULT, result="")), "empty"),
    (0, (INIT, dict(RESULT, modelUsage={"claude-opus-5-5": {}})), "requested"),
])
def test_parse_failures_raise(rc, events, why):
    with pytest.raises(ModelError, match=why):
        parse_stream(rc, stream(*events), "", "claude-sonnet-5-5", [])


def test_parse_rejects_files_left_in_cwd():
    with pytest.raises(ModelError, match="not empty"):
        parse_stream(0, stream(INIT, RESULT), "", "claude-sonnet-5-5", ["x.txt"])


def test_oversized_prompt_raises_before_running(tmp_path):
    b = ClaudeCodeBackend(bin_path=str(tmp_path / "does-not-exist"), timeout_s=5)
    with pytest.raises(ModelError, match="caps stdin"):
        b.run("x" * (model.MAX_STDIN_BYTES + 1), "s", "m")


def test_builtin_plugins_tolerated_foreign_plugins_rejected():
    import pytest
    from ctxtrim.model import ModelError, parse_stream
    res = {"type": "result", "subtype": "success", "result": "ok", "modelUsage": {"claude-sonnet-5-5": {}}}

    def out(plugins, **extra):
        return "\n".join(json.dumps(e) for e in ({**INIT, "plugins": plugins, **extra}, res))

    builtin = [{"name": "x", "path": "builtin", "source": "x@builtin"}]
    assert parse_stream(0, out(builtin), "", "claude-sonnet-5-5")["text"] == "ok"
    with pytest.raises(ModelError):
        parse_stream(0, out(builtin + [{"name": "y", "path": "/somewhere"}]), "", "claude-sonnet-5-5")
    with pytest.raises(ModelError):
        parse_stream(0, out(builtin, skills=["s"]), "", "claude-sonnet-5-5")
