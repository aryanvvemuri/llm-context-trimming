"""Needs lists (PLAN.md 5.6, 7 Phase 2): one JUDGE_MODEL call per cut point, written once and
independent of any policy.

    python -m ctxtrim.eval.needs [--split dev] [--run] [--final-run]

Dry run by default (prints the estimate); --run makes the calls for cut points not yet in
config.NEEDS_PATH (resumable; the model cache also serves any repeated call). The test split is
refused unless --final-run is given.

Input per call (prompt: needs_prompt.md): the conversation-only view (ctxtrim.views) of the
prefix and of the continuation (cuts.continuation, K = config.NEEDS_CONTINUATION_TURNS).
Output: {"needs", "current_state", "superseded"} per 5.6, validated by `validate_record`; an
invalid reply is retried with the errors appended (common.call_validated).

File: one JSON line per cut point: cut_id, session_id (the pilot id, as in the 5.6 example),
cut_before, needs, current_state, superseded, and "meta" (status ok/invalid, attempts, errors,
model reported, date, K). An invalid record has empty lists and is never scored. The file holds
session-derived text (what, anchor) and is git-ignored until the human decides the release form.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from ctxtrim import config
from ctxtrim.eval import common
from ctxtrim.eval.cuts import Cut, continuation, prefix, split_cuts
from ctxtrim.log import Entry, approx_tokens, pilot_entries
from ctxtrim.views import conversation_view

PROMPT_PATH = Path(__file__).with_name("needs_prompt.md")
_TOP = {"needs", "current_state", "superseded"}
_NEED = {"need_id", "what", "source_ids", "anchor", "used_by"}
_STATE = {"key", "what", "source_ids", "anchor"}
_SUP = {"old", "new", "what"}


def build_prompt(template: str, cut: Cut, entries: list[Entry], k: int) -> str:
    pre, _ = conversation_view(prefix(entries, cut))
    cont, _ = conversation_view(continuation(entries, cut, k))
    return common.fill(template, CUT_BEFORE=cut.cut_before, PREFIX=pre, CONTINUATION=cont)


def _line(x) -> bool:
    return isinstance(x, str) and bool(x.strip()) and "\n" not in x and len(x) <= config.MAX_WHAT_CHARS


def check_record(obj, cut: Cut, entries: list[Entry], k: int) -> list[str]:
    """Every error in a needs record (empty = valid). Rules:
    schema exact keys and types; need_id and key unique; `what` one line <= MAX_WHAT_CHARS;
    every id exists in the session; source_ids, old, new lie before the cut and are user or
    assistant messages (not tool); used_by lies in the continuation shown; anchor is non-empty,
    <= MAX_ANCHOR_CHARS and an exact substring of at least one of its source messages;
    superseded.new comes after old."""
    if not isinstance(obj, dict) or set(obj) != _TOP or not all(
            isinstance(obj[x], list) and all(isinstance(i, dict) for i in obj[x]) for x in _TOP):
        return [f"schema: top level must be an object with exactly {sorted(_TOP)}, each a list of objects"]
    es = pilot_entries(entries)
    pos = {e.id: n for n, e in enumerate(es)}
    by_id = {e.id: e for e in es}
    cont = {e.id for e in continuation(entries, cut, k)}
    errors: list[str] = []

    def source(i, where):
        if not isinstance(i, str) or i not in pos:
            errors.append(f"{where}: id {i!r} does not exist")
        elif pos[i] >= cut.index:
            errors.append(f"{where}: {i} is not before the cut ({cut.cut_before})")
        elif by_id[i].kind == "tool":
            errors.append(f"{where}: {i} is a tool message; sources must be user or assistant messages")
        else:
            return True
        return False

    def sourced(item, where):
        ids = item["source_ids"]
        if not (isinstance(ids, list) and ids):
            errors.append(f"{where}: source_ids must be a non-empty list of ids")
            return
        ok = [i for i in ids if source(i, where)]
        a = item["anchor"]
        if not (isinstance(a, str) and a.strip() and len(a) <= config.MAX_ANCHOR_CHARS):
            errors.append(f"{where}: anchor must be a non-empty string of at most {config.MAX_ANCHOR_CHARS} chars")
        elif len(ok) == len(ids) and not any(a in by_id[i].content for i in ids):
            errors.append(f"{where}: anchor {a[:60]!r} is not an exact substring of any of its source messages")

    seen = set()
    for n, x in enumerate(obj["needs"]):
        where = f"needs[{n}]"
        if set(x) != _NEED:
            errors.append(f"schema: {where} keys must be exactly {sorted(_NEED)}")
            continue
        if not (isinstance(x["need_id"], str) and x["need_id"].strip()) or x["need_id"] in seen:
            errors.append(f"{where}: need_id must be a non-empty string, unique")
        seen.add(x["need_id"])
        if not _line(x["what"]):
            errors.append(f"{where}: what must be one non-empty line of at most {config.MAX_WHAT_CHARS} chars")
        sourced(x, where)
        if x["used_by"] not in cont:
            errors.append(f"{where}: used_by {x['used_by']!r} is not a message of the continuation shown")
    seen = set()
    for n, x in enumerate(obj["current_state"]):
        where = f"current_state[{n}]"
        if set(x) != _STATE:
            errors.append(f"schema: {where} keys must be exactly {sorted(_STATE)}")
            continue
        if not (isinstance(x["key"], str) and x["key"].strip()) or x["key"] in seen:
            errors.append(f"{where}: key must be a non-empty string, each key at most once")
        seen.add(x["key"])
        if not _line(x["what"]):
            errors.append(f"{where}: what must be one non-empty line of at most {config.MAX_WHAT_CHARS} chars")
        sourced(x, where)
    for n, x in enumerate(obj["superseded"]):
        where = f"superseded[{n}]"
        if set(x) != _SUP:
            errors.append(f"schema: {where} keys must be exactly {sorted(_SUP)}")
            continue
        if not _line(x["what"]):
            errors.append(f"{where}: what must be one non-empty line of at most {config.MAX_WHAT_CHARS} chars")
        old_ok = source(x["old"], where + ".old")
        new_ok = source(x["new"], where + ".new")
        if old_ok and new_ok and pos[x["new"]] <= pos[x["old"]]:
            errors.append(f"{where}: new {x['new']} does not come after old {x['old']}")
    return errors


def validator(cut: Cut, entries: list[Entry], k: int):
    def validate(text: str) -> dict:
        obj = common.parse_json_object(text)
        errors = check_record(obj, cut, entries, k)
        if errors:
            raise ValueError("; ".join(errors[:10]) + (f"; and {len(errors) - 10} more" if len(errors) > 10 else ""))
        return obj
    return validate


def make_record(cut: Cut, entries: list[Entry], k: int, **call_kw) -> dict:
    system, template = common.load_prompt_file(PROMPT_PATH)
    r = common.call_validated(build_prompt(template, cut, entries, k), system, "needs",
                              validator(cut, entries, k), **call_kw)
    v = r["value"] or {"needs": [], "current_state": [], "superseded": []}
    return {"cut_id": cut.cut_id, "session_id": cut.pilot_id, "cut_before": cut.cut_before, **v,
            "meta": {"status": "ok" if r["value"] is not None else "invalid", "attempts": r["attempts"],
                     "errors": r["errors"], "model": r["model"], "date": r["date"], "k": k}}


def load_records(path: Path | None = None) -> dict[str, dict]:
    path = Path(path or config.NEEDS_PATH)
    if not path.exists():
        return {}
    return {(d := json.loads(x))["cut_id"]: d for x in path.read_text(encoding="utf-8").splitlines() if x.strip()}


def ensure(cuts: list[tuple[Cut, list[Entry]]], path: Path | None = None, k: int | None = None,
           **call_kw) -> dict[str, dict]:
    """Records for every cut, making calls only for cut points not yet in the file (resumable)."""
    path = Path(path or config.NEEDS_PATH)
    k = k if k is not None else config.require("NEEDS_CONTINUATION_TURNS")
    have = load_records(path)
    for cut, entries in cuts:
        if have.get(cut.cut_id, {}).get("meta", {}).get("status") == "ok":
            continue  # an invalid record is retried; the new line wins on load (latest per cut_id)
        rec = make_record(cut, entries, k, **call_kw)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        have[cut.cut_id] = rec
    return {c.cut_id: have[c.cut_id] for c, _ in cuts}


def estimate(cuts: list[tuple[Cut, list[Entry]]], k: int) -> list[int]:
    """Approx input tokens (chars/4) per needs call: system + filled prompt."""
    system, template = common.load_prompt_file(PROMPT_PATH)
    return [approx_tokens(system) + approx_tokens(build_prompt(template, c, es, k)) for c, es in cuts]


def describe(sizes: list[int]) -> str:
    if not sizes:
        return "none"
    x = config.REAL_TOKENS_PER_APPROX
    return (f"total {sum(sizes)} approx (~{x * sum(sizes)} real); per call min {min(sizes)} /"
            f" median {int(statistics.median(sizes))} / max {max(sizes)} approx"
            f" (~{x * min(sizes)} / {int(x * statistics.median(sizes))} / {x * max(sizes)} real)")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--run", action="store_true", help="make the model calls (default: dry run)")
    ap.add_argument("--final-run", action="store_true", help="required for the test split")
    a = ap.parse_args(argv[1:])
    if a.split == "test" and not a.final_run:
        print("refusing the test split without --final-run", file=sys.stderr)
        return 2
    k = config.require("NEEDS_CONTINUATION_TURNS")
    cuts, problems = split_cuts(a.split)
    have = load_records()
    todo = [(c, es) for c, es in cuts if have.get(c.cut_id, {}).get("meta", {}).get("status") != "ok"]
    for p in problems:
        print("cut problem:", p)
    print(f"model: {common.judge_model()}; split {a.split}; cut points {len(cuts)}; records present"
          f" {len(cuts) - len(todo)}; calls {len(todo)} (plus up to {len(todo) * (config.MAX_ATTEMPTS - 1)}"
          f" retries); K = {k}")
    print("input tokens:", describe(estimate(todo, k)))
    if not a.run:
        print("dry run: no model calls made")
        return 0
    if len(todo) > config.BATCH_ASK_CALLS:
        print(f"more than {config.BATCH_ASK_CALLS} calls: ask the human first (PLAN.md 4.3)", file=sys.stderr)
        return 1
    recs = ensure(cuts, k=k)
    for cid, r in recs.items():
        print(cid, r["meta"]["status"], len(r["needs"]), len(r["current_state"]), len(r["superseded"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
