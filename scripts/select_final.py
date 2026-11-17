"""Phase 0 final selection and dev/test split (PLAN.md 7 Phase 0 steps 2 and 5; rule decided by
the orchestrator 2026-11-16).

Usage: python -I scripts/select_final.py

Inputs (git-ignored): data/selection/content_filter.jsonl, candidates.json (user key, repo),
stats.json (numbers).
Outputs:
  data/splits.json                     TRACKED: pilot id, session_id, split, numbers only.
  data/selection/final_selection.json  git-ignored: the same plus user key and repo.

Rule:
  1. Eligible: content-filter status "ok" and >= MIN_REVERSALS validated reversals.
  2. Draw: eligible sessions sorted by session_id, then random.Random(SEED).shuffle; walk the
     shuffled order and take a session only while its user key and its repo each have fewer than
     PER_GROUP_CAP taken. Stop at N_SESSIONS. Fewer than N_SESSIONS -> stop (PLAN ASK).
  3. Pilot ids s001..s018 in draw order.
  4. Split by user: sessions are grouped into connected components (two sessions are linked when
     they share a user key or a repo), so no user and no repo can straddle the splits. Components
     sorted by their first pilot id are shuffled with a fresh random.Random(SEED); dev is the first
     subset, in that order (depth-first search), whose sizes sum to N_DEV; the rest is test. If no
     such subset exists, the grouping falls back to user only and the straddling repos are
     reported (never silently).
"""

from __future__ import annotations

import json
import random
import statistics
import sys
from collections import Counter

from ctxtrim import config

SEED = 20261116
MIN_REVERSALS = 5
N_SESSIONS = 18
N_DEV = 6
PER_GROUP_CAP = 2
KINDS = ("decision_revised", "constraint_changed", "plan_revised", "hypothesis_refuted",
         "abandoned", "question_resolved", "restated")
SEL_DIR = config.DATA_DIR / "selection"
PRIVATE_PATH = SEL_DIR / "final_selection.json"


class SelectionError(RuntimeError):
    pass


def draw(eligible: list[dict], seed: int = SEED, n: int = N_SESSIONS, cap: int = PER_GROUP_CAP) -> list[dict]:
    """Seeded draw with at most `cap` per user key and per repo. Rows need session_id, user_key, repo_id."""
    order = sorted(eligible, key=lambda r: r["session_id"])
    random.Random(seed).shuffle(order)
    u, rp, out = Counter(), Counter(), []
    for r in order:
        if len(out) == n:
            break
        if u[r["user_key"]] < cap and rp[r["repo_id"]] < cap:
            u[r["user_key"]] += 1
            rp[r["repo_id"]] += 1
            out.append(r)
    if len(out) < n:
        raise SelectionError(f"only {len(out)} of {n} sessions can be chosen under the cap; ask the human")
    return out


def _components(rows: list[dict], keys: tuple[str, ...]) -> list[list[int]]:
    """Indices of rows grouped into connected components linked by equal values of any key."""
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first = {}
    for i, r in enumerate(rows):
        for k in keys:
            j = first.setdefault((k, r[k]), i)
            parent[find(i)] = find(j)
    groups = {}
    for i in range(len(rows)):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=lambda g: g[0])


def _subset(sizes: list[int], target: int) -> list[int] | None:
    """First subset (depth-first, in the given order) of positions whose sizes sum to target."""
    def go(i, left, chosen):
        if left == 0:
            return chosen
        if i == len(sizes) or left < 0:
            return None
        return go(i + 1, left - sizes[i], chosen + [i]) or go(i + 1, left, chosen)
    return go(0, target, [])


def split(chosen: list[dict], seed: int = SEED, n_dev: int = N_DEV) -> tuple[list[str], list[str]]:
    """Return (split label per row, repos that straddle the splits). Rows in pilot-id order."""
    for keys in (("user_key", "repo_id"), ("user_key",)):
        comps = _components(chosen, keys)
        random.Random(seed).shuffle(comps)
        pick = _subset([len(c) for c in comps], n_dev)
        if pick is not None:
            labels = ["test"] * len(chosen)
            for p in pick:
                for i in comps[p]:
                    labels[i] = "dev"
            repos = {}
            for r, lab in zip(chosen, labels):
                repos.setdefault(r["repo_id"], set()).add(lab)
            straddle = sorted(k for k, v in repos.items() if len(v) > 1)
            return labels, straddle
    raise SelectionError(f"no grouping by user gives a dev split of exactly {n_dev}; ask the human")


def select(eligible: list[dict], seed: int = SEED) -> tuple[list[dict], list[str]]:
    """Draw and split. Returns rows with pilot_id and split added, and straddling repos."""
    chosen = [dict(r, pilot_id=f"s{i:03d}") for i, r in enumerate(draw(eligible, seed), 1)]
    labels, straddle = split(chosen, seed)
    return [dict(r, split=lab) for r, lab in zip(chosen, labels)], straddle


def error_category(msg: str) -> str:
    if msg.startswith("not valid JSON"):
        return msg  # e.g. "not valid JSON (Extra data)"
    if "does not precede" in msg:
        return "old id does not precede new id"
    if "is not in the transcript" in msg:
        return "id not in the transcript"
    if "unknown kind" in msg:
        return "unknown kind"
    return msg.split(":", 1)[-1].strip()


def main() -> int:
    cf = [json.loads(x) for x in (SEL_DIR / "content_filter.jsonl").read_text().splitlines() if x.strip()]
    cands = {c["session_id"]: c for c in json.loads((SEL_DIR / "candidates.json").read_text())["candidates"]}
    stats = {s["session_id"]: s for s in json.loads((SEL_DIR / "stats.json").read_text())["sessions"]}
    assert len(cf) == len({r["session_id"] for r in cf}) and set(r["session_id"] for r in cf) <= set(cands)

    status = Counter(r["status"] for r in cf)
    invalid_errors = Counter(error_category(e) for r in cf if r["status"] == "invalid" for e in r["errors"])
    ok = [r for r in cf if r["status"] == "ok"]
    rev = {r["session_id"]: r["reversals"] for r in ok}
    eligible = [dict(session_id=r["session_id"], user_key=cands[r["session_id"]]["user_key"],
                     repo_id=cands[r["session_id"]]["repo_id"])
                for r in ok if len(r["reversals"]) >= MIN_REVERSALS]
    print(f"content filter: {len(cf)} records, {dict(status)}; eligible (ok, >= {MIN_REVERSALS}): {len(eligible)}")
    print("invalid sessions, validation errors by category (all attempts):", dict(invalid_errors))
    try:
        rows, straddle = select(eligible)
    except SelectionError as e:
        print("STOP:", e, file=sys.stderr)
        return 1
    if straddle:
        print(f"WARNING: {len(straddle)} repo(s) straddle the splits (user grouping fallback)", file=sys.stderr)

    def numbers(sid):
        s, rv = stats[sid], rev[sid]
        kinds = Counter(x["kind"] for x in rv)
        return {"user_turns_plain": s["user_turns_plain"], "conv_tokens": s["conv_tokens"],
                "conv_share_after": s["conv_share_after"], "reversals": len(rv),
                "reversals_by_kind": {k: kinds.get(k, 0) for k in KINDS}}

    public = [{"pilot_id": r["pilot_id"], "session_id": r["session_id"], "split": r["split"],
               **numbers(r["session_id"])} for r in rows]
    meta = {"seed": SEED, "rule": __doc__.split("Rule:", 1)[1].strip(), "min_reversals": MIN_REVERSALS,
            "per_group_cap": PER_GROUP_CAP, "n_content_filter": len(cf), "content_filter_status": dict(status),
            "n_eligible": len(eligible), "n_dev": sum(r["split"] == "dev" for r in rows),
            "n_test": sum(r["split"] == "test" for r in rows), "repos_straddling_splits": len(straddle)}
    config.SPLITS_PATH.write_text(json.dumps({**meta, "sessions": public}, indent=1) + "\n")
    PRIVATE_PATH.write_text(json.dumps({**meta, "repos_straddling": straddle, "sessions": [
        dict(p, user_key=r["user_key"], repo_id=r["repo_id"]) for p, r in zip(public, rows)]}, indent=1) + "\n")

    for p in public:
        print(p["pilot_id"], p["session_id"], p["split"], p["user_turns_plain"], p["conv_tokens"],
              p["conv_share_after"], p["reversals"])
    for lab in ("dev", "test"):
        sel = [r for r in rows if r["split"] == lab]
        uc, rc = Counter(r["user_key"] for r in sel), Counter(r["repo_id"] for r in sel)
        print(f"{lab}: {len(sel)} sessions, {len(uc)} users (sizes {sorted(Counter(uc.values()).items())}),"
              f" {len(rc)} repos (sizes {sorted(Counter(rc.values()).items())}),"
              f" median conv tokens {statistics.median(r_['conv_tokens'] for r_ in public if r_['split'] == lab):.0f}")
    users = {lab: {r["user_key"] for r in rows if r["split"] == lab} for lab in ("dev", "test")}
    repos = {lab: {r["repo_id"] for r in rows if r["split"] == lab} for lab in ("dev", "test")}
    print("users in both splits:", len(users["dev"] & users["test"]), "repos in both:", len(repos["dev"] & repos["test"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
