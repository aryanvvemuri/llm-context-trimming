"""One model interface (PLAN.md 4.3): system + prompt in, text out, with an on-disk call cache
and an append-only call ledger.

    result = call(prompt, system=..., model=config.MODEL, purpose="content_filter")
    result.text, result.model_reported, result.date, result.usage, result.cached

Cache: one JSON file per call under config.CACHE_DIR, keyed by sha256 of
{backend, backend settings that affect output, model, system, prompt}. A call whose key is in
the cache is served from it without touching the backend. Failed calls are never cached.
Ledger: config.CALL_LEDGER_PATH, one JSON line per call (cache hit, miss or error) with date,
backend, model requested/reported, token usage, reported cost, duration, purpose and the cache
key. No prompt or response text goes in the ledger.

Backends implement `name`, `settings` (dict, part of the cache key) and
`run(prompt, system, model) -> {"text", "models", "usage", "cost_usd", "meta"}`, raising
ModelError on any failure. The only real backend is ClaudeCodeBackend.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from ctxtrim import config

MAX_STDIN_BYTES = 10 * 1024 * 1024  # Claude Code caps piped stdin at 10 MB (code.claude.com/docs/en/headless)


class ModelError(RuntimeError):
    """A model call failed; nothing was cached."""


@dataclass(frozen=True)
class ModelResult:
    text: str
    model_requested: str
    model_reported: list[str]     # model IDs the backend says it used
    date: str                     # UTC ISO time of the backend call (for a cache hit: of the original call)
    usage: dict | None            # token usage as reported by the backend
    cost_usd: float | None        # cost as reported by the backend (an estimate on a subscription plan)
    backend: str
    cache_key: str
    cached: bool
    meta: dict = field(default_factory=dict)


def cache_key(backend_name: str, settings: dict, model: str, system: str, prompt: str) -> str:
    blob = json.dumps({"backend": backend_name, "settings": settings, "model": model,
                       "system": system, "prompt": prompt}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _append_ledger(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def call(prompt: str, *, system: str, model: str, purpose: str = "", backend=None,
         cache_dir: Path | None = None, ledger_path: Path | None = None) -> ModelResult:
    """Run one model call through the configured backend, or serve it from the cache."""
    backend = backend or get_backend(config.require("BACKEND"))
    cache_dir = Path(cache_dir or config.CACHE_DIR)
    ledger_path = Path(ledger_path or config.CALL_LEDGER_PATH)
    key = cache_key(backend.name, backend.settings, model, system, prompt)
    path = cache_dir / key[:2] / f"{key}.json"
    base = {"ts": _now(), "purpose": purpose, "backend": backend.name, "model_requested": model, "cache_key": key}
    if path.exists():
        rec = json.loads(path.read_text(encoding="utf-8"))
        req = rec["request"]
        if (req["model"], req["system"], req["prompt"], req["backend"], req["settings"]) != (
                model, system, prompt, backend.name, backend.settings):
            raise ModelError(f"cache entry {key} does not match its request")
        _append_ledger(ledger_path, {**base, "status": "cache_hit", "model_reported": rec["response"]["models"],
                                     "original_date": rec["date"], "usage": None, "cost_usd": None})
        r = rec["response"]
        return ModelResult(r["text"], model, r["models"], rec["date"], r["usage"], r["cost_usd"],
                           backend.name, key, True, r.get("meta", {}))
    date = _now()
    t0 = time.monotonic()
    try:
        r = backend.run(prompt, system, model)
    except ModelError as e:
        _append_ledger(ledger_path, {**base, "status": "error", "error": str(e)[:300],
                                     "duration_s": round(time.monotonic() - t0, 1)})
        raise
    if not isinstance(r.get("text"), str) or not r["text"].strip():
        _append_ledger(ledger_path, {**base, "status": "error", "error": "empty output"})
        raise ModelError("backend returned empty output")
    rec = {"date": date, "request": {"backend": backend.name, "settings": backend.settings, "model": model,
                                     "system": system, "prompt": prompt},
           "response": {"text": r["text"], "models": r["models"], "usage": r["usage"],
                        "cost_usd": r["cost_usd"], "meta": r.get("meta", {})}}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    _append_ledger(ledger_path, {**base, "ts": date, "status": "cache_miss", "model_reported": r["models"],
                                 "usage": r["usage"], "cost_usd": r["cost_usd"],
                                 "duration_s": round(time.monotonic() - t0, 1)})
    return ModelResult(r["text"], model, r["models"], date, r["usage"], r["cost_usd"], backend.name, key, False,
                       r.get("meta", {}))


def get_backend(name: str):
    if name == "claude_code":
        return ClaudeCodeBackend()
    raise ModelError(f"backend {name!r} is not implemented")


class ClaudeCodeBackend:
    """Claude Code in non-interactive mode (`claude -p`) on the human's existing plan.

    Each call: a fresh empty temp directory as cwd (deleted afterwards); the prompt on stdin;
    the default system prompt replaced (--system-prompt); no built-in tools (--tools ""); no
    MCP servers (--strict-mcp-config, none given); no user/project/local settings
    (--setting-sources ""); no CLAUDE.md, skills, plugins, hooks, custom commands/agents,
    output styles or auto memory (--safe-mode, plus CLAUDE_CODE_DISABLE_CLAUDE_MDS=1 and
    CLAUDE_CODE_DISABLE_AUTO_MEMORY=1); no skills or slash commands (--disable-slash-commands);
    no session saved to disk (--no-session-persistence, CLAUDE_CODE_SKIP_PROMPT_HISTORY=1);
    one turn (--max-turns 1). The child environment drops every ANTHROPIC_* and CLAUDE*
    variable inherited from the caller, so no API key, base URL, effort or bare-mode setting
    leaks in and the call uses the plan login. stream-json output is parsed: the init event
    must list no tools, MCP servers or plugins, and the result event gives text, models, usage.
    Not controllable with documented flags: managed (policy) settings still apply.
    """

    name = "claude_code"
    FLAGS_VERSION = 1  # bump when the command line changes in a way that could change output

    def __init__(self, bin_path: str | None = None, timeout_s: float | None = None, effort: str | None = None):
        self.bin = bin_path or config.require("CLAUDE_BIN")
        self.timeout_s = timeout_s or config.MODEL_TIMEOUT_S
        self.effort = effort if effort is not None else config.EFFORT
        self.settings = {"flags_version": self.FLAGS_VERSION, "effort": self.effort}

    def command(self, system: str, model: str) -> list[str]:
        cmd = [self.bin, "-p", "--output-format", "stream-json", "--verbose",
               "--model", model, "--system-prompt", system,
               "--tools", "", "--strict-mcp-config", "--setting-sources", "",
               "--safe-mode", "--disable-slash-commands", "--no-session-persistence", "--max-turns", "1"]
        if self.effort is not None:
            cmd += ["--effort", self.effort]
        return cmd

    @staticmethod
    def environment() -> dict:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE"))}
        env.update({"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                    "CLAUDE_CODE_SKIP_PROMPT_HISTORY": "1", "DISABLE_AUTOUPDATER": "1",
                    "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1"})
        return env

    def run(self, prompt: str, system: str, model: str) -> dict:
        data = prompt.encode("utf-8")
        if len(data) > MAX_STDIN_BYTES:
            raise ModelError(f"prompt is {len(data)} bytes; Claude Code caps stdin at {MAX_STDIN_BYTES}")
        cwd = tempfile.mkdtemp(prefix="ctxtrim-call-")
        try:
            try:
                p = subprocess.run(self.command(system, model), input=data, cwd=cwd, env=self.environment(),
                                   capture_output=True, timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                raise ModelError(f"timeout after {self.timeout_s}s")
            leftovers = os.listdir(cwd)
        finally:
            shutil.rmtree(cwd, ignore_errors=True)
        return parse_stream(p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace"),
                            model, leftovers)


def parse_stream(returncode: int, stdout: str, stderr: str, model: str, leftovers: list | None = None) -> dict:
    """Parse `claude -p --output-format stream-json --verbose` output; raise ModelError on any problem."""
    events = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    results = [e for e in events if e.get("type") == "result"]
    if returncode != 0:
        detail = results[-1].get("result") if results else stderr.strip()[-300:]
        raise ModelError(f"claude exited {returncode}: {str(detail)[:300]}")
    if not results:
        raise ModelError("no result event in output")
    res = results[-1]
    if res.get("is_error") or res.get("subtype") != "success":
        raise ModelError(f"error result: subtype={res.get('subtype')} {str(res.get('result'))[:300]}")
    inits = [e for e in events if e.get("type") == "system" and e.get("subtype") == "init"]
    if not inits:
        raise ModelError("no init event in output")
    init = inits[0]
    # Plugins compiled into the Claude Code binary (path "builtin") are always listed and cannot
    # be turned off by a flag; they are tolerated as long as they contribute no tools, skills or
    # slash commands. Measured 2026-11-16: about 620 input tokens of fixed overhead per call.
    leaks = {k: init.get(k) for k in ("tools", "mcp_servers", "skills", "slash_commands") if init.get(k)}
    foreign = [p for p in init.get("plugins") or [] if not (isinstance(p, dict) and p.get("path") == "builtin")]
    if foreign:
        leaks["plugins"] = foreign
    if leaks:
        raise ModelError(f"call was not clean: {leaks}")
    if leftovers:
        raise ModelError(f"working directory not empty after the call: {leftovers}")
    text = res.get("result")
    if not isinstance(text, str) or not text.strip():
        raise ModelError("empty result text")
    model_usage = res.get("modelUsage") or {}
    models = sorted(model_usage) or ([init["model"]] if init.get("model") else [])
    if not any(m == model or m.startswith(model + "[") for m in models):
        raise ModelError(f"requested {model} but backend reports {models}")
    meta = {"init_model": init.get("model"), "claude_code_version": init.get("claude_code_version"),
            "num_turns": res.get("num_turns"), "duration_api_ms": res.get("duration_api_ms"),
            "model_usage": model_usage, "init_keys": {k: (len(v) if isinstance(v, list) else v)
                                                      for k, v in init.items()
                                                      if k in ("tools", "mcp_servers", "plugins", "skills",
                                                               "slash_commands", "agents", "output_style",
                                                               "permissionMode", "apiKeySource")}}
    return {"text": text, "models": models, "usage": res.get("usage"), "cost_usd": res.get("total_cost_usd"),
            "meta": meta}
