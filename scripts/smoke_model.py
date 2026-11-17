"""Smoke test of the model backend: one trivial call per model (no session text), then the same
call again, which must come from the cache.

Usage: python -I scripts/smoke_model.py MODEL [MODEL ...]   (e.g. claude-sonnet-5-5)
"""

from __future__ import annotations

import json
import sys

from ctxtrim import model

SYSTEM = "You are a test endpoint. Reply with exactly the text requested and nothing else."
PROMPT = "Reply with the single word: pong"


def main(argv: list[str]) -> int:
    for m in argv[1:]:
        for attempt in (1, 2):
            r = model.call(PROMPT, system=SYSTEM, model=m, purpose="smoke_test")
            print(json.dumps({"attempt": attempt, "cached": r.cached, "text": r.text, "model_requested": r.model_requested,
                              "model_reported": r.model_reported, "date": r.date, "usage": r.usage,
                              "cost_usd": r.cost_usd, "meta": r.meta}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
