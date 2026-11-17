"""`recency`: keep the last N turns; replace every older conversational message (user or
assistant) with a bare stub: a pointer, no summary text. One stub covers each run of
consecutive conversational messages (a tool message ends a run), so short messages do not
cost more as stubs than they did as text. Pure code. Tool messages are untouched (the fixed
tool layer owns them).

Turn: a plain user message (log.is_plain_user) and everything after it up to the next plain
user message. Messages before the first plain user message belong to the first turn. Turns are
counted in the prefix (pilot entries), so the last kept turn is the one before the current
request. N = 0 keeps no turn; N >= the number of turns keeps everything.
"""

from __future__ import annotations

from ctxtrim.log import is_plain_user, pilot_entries
from ctxtrim.policies.base import PolicyOutput, bare_stubs, check_settings


def run(prefix, settings: dict, **call_kw) -> PolicyOutput:
    check_settings(settings, {"n_turns"})
    n = settings["n_turns"]
    if not (isinstance(n, int) and n >= 0):
        raise ValueError("n_turns must be an integer >= 0")
    es = pilot_entries(prefix)
    starts = [i for i, e in enumerate(es) if is_plain_user(e)]
    if n == 0:
        keep_from = len(es)
    elif n >= len(starts):
        keep_from = 0
    else:
        keep_from = starts[-n]
    runs: list[list[str]] = []
    in_run = False
    for e in es[:keep_from]:
        if e.kind in ("user", "assistant"):
            if not in_run:
                runs.append([])
            runs[-1].append(e.id)
            in_run = True
        else:
            in_run = False
    return PolicyOutput(bare_stubs(runs))
