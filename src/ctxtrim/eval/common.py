"""Shared pieces for model calls that read sessions (needs lists, checks, LLM baselines): prompt
files, the model call, strict JSON parsing, and reject-and-retry with the error appended.
"""

from __future__ import annotations

import json
from pathlib import Path

from ctxtrim import config, model


def load_prompt_file(path: Path) -> tuple[str, str]:
    """(system, prompt template) from a file with "=== SYSTEM ===" and "=== PROMPT ===" parts."""
    text = path.read_text(encoding="utf-8")
    system, prompt = text.split("=== PROMPT ===\n")
    return system.removeprefix("=== SYSTEM ===\n").strip(), prompt.strip()


def fill(template: str, **values: str) -> str:
    for k, v in values.items():
        template = template.replace("{{" + k + "}}", v)
    return template


def parse_json_object(text: str) -> dict:
    """The reply as a JSON object (an outer ``` fence is tolerated); ValueError otherwise."""
    s = text.strip()
    if s.startswith("```"):
        s = s.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        obj = json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON ({e.msg})")
    if not isinstance(obj, dict):
        raise ValueError("the reply must be one JSON object")
    return obj


def judge_model() -> str:
    judge = config.require("JUDGE_MODEL")
    if judge == config.MODEL:
        raise RuntimeError("JUDGE_MODEL must differ from MODEL (PLAN.md 4.3)")
    return judge


def call_validated(prompt: str, system: str, purpose: str, validate, *, model_id: str | None = None,
                   **call_kw) -> dict:
    """Call `model_id` (default JUDGE_MODEL; the LLM baselines pass MODEL); validate(text) returns
    the parsed value or raises ValueError. An invalid reply is retried with its error appended (a different input, so cached separately), at most
    config.MAX_ATTEMPTS calls in all.

    Returns {"value" (None if every attempt failed), "attempts", "errors", "model", "date"}.
    call_kw goes to model.call (tests pass a fake backend, cache_dir, ledger_path).
    """
    base, errors = prompt, []
    for attempt in range(1, config.MAX_ATTEMPTS + 1):
        r = model.call(prompt, system=system, model=model_id or judge_model(), purpose=purpose, **call_kw)
        try:
            value = validate(r.text)
        except ValueError as e:
            errors.append(str(e))
            # Show the rejected reply with its errors so the model repairs it instead of
            # starting again and making a different mistake.
            prompt = (base + f"\n\n[Attempt {attempt + 1}. Your previous reply, below, was rejected."
                      f"\n<previous_reply>\n{r.text}\n</previous_reply>"
                      f"\nErrors: {e}."
                      "\nCorrect every error, change nothing else, and reply again with only the"
                      " JSON object described in your instructions.]")
            continue
        return {"value": value, "attempts": attempt, "errors": errors, "model": r.model_reported, "date": r.date}
    return {"value": None, "attempts": config.MAX_ATTEMPTS, "errors": errors, "model": None, "date": None}
