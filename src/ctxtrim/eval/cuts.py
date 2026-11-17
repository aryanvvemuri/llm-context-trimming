"""Cut points (PLAN.md 7 Phase 2): two per session, at about 50% and 80% of the session's tokens,
just before a user turn.

Definitions (one place):
  token basis  cumulative `tokens` of pilot_entries (native thinking excluded) in the log, before
               the fixed tool layer (Entry.tokens, approx_tokens).
  cut target   for fraction f, the first plain user message (log.is_plain_user: no interrupt
               markers, slash commands or `!` inputs) whose preceding tokens are >= f x total.
               cut_before = its id; cut_id = "<pilot_id>.c1" (f = 0.5) or ".c2" (f = 0.8).
  prefix       the pilot entries strictly before cut_before. Policies run on the prefix only.
  request      the cut_before message: the current user request the agent answers next. It is
               the first message of the continuation and is in no policy's input.
  continuation the next K plain user turns from the cut (the request is the first), each with
               everything up to the next plain user turn; to the end of the session when fewer
               than K+1 plain turns remain.
Problems are reported, never patched: no plain user turn after the target (no cut), a cut equal
to the previous one (dropped), a request with nothing after it (dropped).
"""

from __future__ import annotations

from dataclasses import dataclass

from ctxtrim import config
from ctxtrim.log import Entry, Log, is_plain_user, pilot_entries
from ctxtrim.splits import load_split


@dataclass(frozen=True)
class Cut:
    cut_id: str
    pilot_id: str
    cut_before: str
    fraction: float
    index: int          # position of cut_before in pilot_entries
    prefix_tokens: int  # tokens before the cut (token basis above)
    total_tokens: int


def find_cuts(entries: list[Entry], pilot_id: str,
              fractions=config.CUT_FRACTIONS) -> tuple[list[Cut], list[str]]:
    """(cuts, problems) for one session. Deterministic."""
    es = pilot_entries(entries)
    total = sum(e.tokens for e in es)
    before, run = [], 0
    for e in es:
        before.append(run)
        run += e.tokens
    cuts, problems = [], []
    for n, f in enumerate(fractions, 1):
        cut_id = f"{pilot_id}.c{n}"
        j = next((i for i, e in enumerate(es) if i > 0 and is_plain_user(e) and before[i] >= f * total), None)
        if j is None:
            problems.append(f"{cut_id}: no plain user turn after {f:.0%} of tokens; no cut")
            continue
        if cuts and cuts[-1].index == j:
            problems.append(f"{cut_id}: same cut as {cuts[-1].cut_id} ({es[j].id}); dropped")
            continue
        if j == len(es) - 1:
            problems.append(f"{cut_id}: request {es[j].id} is the last message; no continuation; dropped")
            continue
        cuts.append(Cut(cut_id, pilot_id, es[j].id, f, j, before[j], total))
    return cuts, problems


def prefix(entries: list[Entry], cut: Cut) -> list[Entry]:
    es = pilot_entries(entries)
    assert es[cut.index].id == cut.cut_before
    return es[:cut.index]


def continuation(entries: list[Entry], cut: Cut, k: int) -> list[Entry]:
    """The request and the following messages up to (not including) the (k+1)-th plain user turn."""
    es = pilot_entries(entries)[cut.index:]
    plain = [i for i, e in enumerate(es) if is_plain_user(e)]
    end = plain[k] if len(plain) > k else len(es)
    return es[:end]


def session_entries(row: dict) -> list[Entry]:
    return Log(config.NORMALIZED_DIR / row["session_id"] / "log.jsonl").entries()


def split_cuts(split: str) -> tuple[list[tuple[Cut, list[Entry]]], list[str]]:
    """Cuts of every session in a split (via ctxtrim.splits.load_split), with the session's entries."""
    out, problems = [], []
    for row in load_split(split):
        entries = session_entries(row)
        cuts, probs = find_cuts(entries, row["pilot_id"])
        out += [(c, entries) for c in cuts]
        problems += probs
    return out, problems
