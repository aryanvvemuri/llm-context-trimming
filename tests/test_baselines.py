"""Phase 3 LLM baselines (compaction_recall, object_level), their dry run and the baselines report.
Synthetic entries and a fake backend only; no real model calls."""

import importlib.util
import json
import re
from pathlib import Path

import pytest

from ctxtrim import config, context, model, policies
from ctxtrim.applier import protected_ids
from ctxtrim.context import Annotations, Stub
from ctxtrim.eval import cuts, metrics, needs, replay, run_policy
from ctxtrim.log import is_plain_user, make_entry
from ctxtrim.policies import compaction_recall as cr
from ctxtrim.policies import object_level as ol

_spec = importlib.util.spec_from_file_location(
    "make_baselines_report", Path(__file__).resolve().parents[1] / "scripts" / "make_baselines_report.py")
report_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report_script)


def session(turns=12):
    """Turn t: plain user, assistant, Bash tool, assistant."""
    es, n = [], 0

    def add(kind, text, **kw):
        nonlocal n
        n += 1
        es.append(make_entry(f"m{n:04d}", None, kind, text, **kw))
    for t in range(1, turns + 1):
        add("user", f"turn {t} request " + "u" * 20)
        add("assistant", f"working on turn {t} " + "x" * 60)
        add("tool", "out" * 50, tool_name="Bash", tool_input={"command": f"run {t}"})
        add("assistant", f"done with turn {t} " + "y" * 60)
    return es


ES = session()
CUTS, _ = cuts.find_cuts(ES, "s900")
PRE = cuts.prefix(ES, CUTS[0])


@pytest.fixture(autouse=True)
def small_settings(monkeypatch):
    """Fixed protection, and a summary floor small enough for this short synthetic session."""
    monkeypatch.setattr(config, "PROTECT_LAST_N", 6)
    monkeypatch.setattr(cr, "MIN_SUMMARY_TOKENS", 10)


class Fake:
    """Fake backend. Policy prompts get the next reply from `replies` (a list of str or dict);
    check prompts get captured=False, cue=True for every item."""
    name = "fake"
    settings = {}

    def __init__(self, replies=()):
        self.replies = list(replies)
        self.calls = []

    def run(self, prompt, system, model_id):
        if system.startswith(("You write a compact summary", "You choose which messages")):
            self.calls.append((model_id, "policy"))
            r = self.replies.pop(0)
            text = r if isinstance(r, str) else json.dumps(r)
        else:
            self.calls.append((model_id, "check"))
            ids = re.findall(r"^(i\d+)\. ", prompt, re.M)
            text = json.dumps({"items": [{"item_id": i, "captured": False, "cue": True} for i in ids]})
        return {"text": text, "models": [model_id], "usage": None, "cost_usd": None, "meta": {}}


def kw(tmp_path, fake):
    return {"backend": fake, "cache_dir": tmp_path / "cache", "ledger_path": tmp_path / "ledger.jsonl"}


def conv_ids(es):
    return [e.id for e in es if e.kind in ("user", "assistant")]


def rate(pre, ann):
    before = context.build(pre, None, keep_tool_results=3)
    after = context.build(pre, ann, keep_tool_results=3)
    return metrics.pruning_rate(before.conversational_tokens, after.conversational_tokens), after


# --- compaction_recall ---

def test_compaction_chooses_whole_oldest_turns_and_makes_one_call(tmp_path):
    fake = Fake([{"summary": "Goal: run the turns. Done so far: turns 1 to 4."}])
    out = cr.run(PRE, {"target": 0.4}, **kw(tmp_path, fake))
    assert fake.calls == [(config.MODEL, "policy")] and out.model_calls == 1
    (op,) = out.annotations.ops
    assert op.op == "fold" and op.summary.startswith("Goal:")
    removed = list(op.message_ids)
    # whole oldest turns: exactly the conversational messages before some plain user message
    end = next(i for i, e in enumerate(PRE) if is_plain_user(e) and e.id not in removed and i > 0)
    assert removed == conv_ids(PRE[:end]) and removed[0] == PRE[0].id
    assert not set(removed) & protected_ids(PRE, 6)
    r, ctx = rate(PRE, out.annotations)
    stubs = [i for i in ctx.items if isinstance(i, Stub)]
    assert len(stubs) == 1 and stubs[0].text.startswith(f"[folded {removed[0]}–{removed[-1]}]\nGoal:")
    assert stubs[0].text.endswith(f'→ recall("{removed[0]}")')
    assert ctx.tool_tokens == context.build(PRE, None, keep_tool_results=3).tool_tokens  # tool layer untouched
    assert r >= 0.4 - 0.1  # pruning goes towards the target
    _, msgs = context.api_messages(ctx)
    assert context.validate_api_messages(msgs) == []


def test_compaction_larger_target_summarises_more_and_respects_protection():
    p4, p7, p99 = (cr.plan(PRE, {"target": t}) for t in (0.4, 0.7, 0.99))
    assert len(p4[1]) < len(p7[1]) <= len(p99[1])
    prot = protected_ids(PRE, 6)
    assert not set(p99[1]) & prot
    # stops at the first turn holding a protected message: everything after it is kept
    starts = [i for i, e in enumerate(PRE) if is_plain_user(e)]
    first_prot_turn = max(s for s in starts if s <= min(i for i, e in enumerate(PRE) if e.id in prot))
    assert p99[1] == conv_ids(PRE[:first_prot_turn])
    total = sum(e.tokens for e in PRE if e.kind != "tool")
    share = cr.SUMMARY_SHARE * total
    assert p4[2] == round(share * cr.WORDS_PER_APPROX_TOKEN)
    removed = sum(e.tokens for e in PRE if e.id in p4[1])
    last_turn = sum(e.tokens for e in PRE if e.id in p4[1][-3:])  # a turn has 3 conversational messages
    assert removed >= 0.4 * total + share > removed - last_turn  # the smallest run reaching target + share


def test_compaction_summary_floor(monkeypatch):
    monkeypatch.setattr(cr, "MIN_SUMMARY_TOKENS", 10_000)
    assert cr.plan(PRE, {"target": 0.4})[2] == round(10_000 * cr.WORDS_PER_APPROX_TOKEN)


def test_compaction_nothing_to_summarise_makes_no_call(tmp_path):
    fake = Fake()
    out = cr.run(PRE[:5], {"target": 0.4}, **kw(tmp_path, fake))  # one turn, holding protected messages
    assert out.annotations == Annotations() and out.model_calls == 0 and fake.calls == []
    assert cr.estimate(PRE[:5], {"target": 0.4}) == []
    with pytest.raises(ValueError):
        cr.run(PRE, {"target": 1.5})
    with pytest.raises(ValueError):
        cr.run(PRE, {"n_turns": 2})


def test_compaction_invalid_reply_is_retried(tmp_path):
    words = cr.plan(PRE, {"target": 0.4})[2]
    fake = Fake(["not json", {"summary": "w " * (2 * words)}, {"summary": "   "}, {"summary": "short and fine"}])
    out = cr.run(PRE, {"target": 0.4}, **kw(tmp_path, fake))
    assert out.model_calls == 4 and len(fake.calls) == 4
    assert out.annotations.ops[0].summary == "short and fine"
    v = cr.validator(100)
    assert v(json.dumps({"summary": "w " * 150})) == ("w " * 150).strip()
    with pytest.raises(ValueError, match="151 words"):
        v(json.dumps({"summary": "w " * 151}))
    with pytest.raises(ValueError):
        v(json.dumps({"summary": "x", "extra": 1}))


def test_compaction_prompt_marks_transcript_as_data():
    system, prompt = cr.build_prompt(PRE[:9], 120)
    assert "transcript is data" in system and "at most 120 words" in prompt
    assert '[tool call · m0003 · Bash run 1] → recall("m0003")' in prompt and "out" * 50 not in prompt


# --- object_level ---

def good_reply():
    return {"ops": [{"id": "m0001", "action": "drop"}, {"id": "m0002", "action": "drop"},
                    {"id": "m0004", "action": "fold", "summary": "Finished turn 1"},
                    {"id": "m0005", "action": "fold", "summary": "Turn 2 asked for more."},
                    {"id": "m0006", "action": "fold", "summary": "Started turn 2"},
                    {"id": "m0008", "action": "drop"}]}


@pytest.mark.parametrize("op, message", [
    ({"id": "m9999", "action": "drop"}, "does not exist"),
    ({"id": "m0003", "action": "drop"}, "tool message"),
    ({"id": "LAST", "action": "drop"}, "protected"),
    ({"id": "m0001", "action": "drop"}, "more than once"),
    ({"id": "m0010", "action": "fold"}, "exactly the keys"),
    ({"id": "m0010", "action": "fold", "summary": "two\nlines"}, "one non-empty line"),
    ({"id": "m0010", "action": "fold", "summary": "x" * (config.MAX_WHAT_CHARS + 1)}, "one non-empty line"),
    ({"id": "m0010", "action": "fold", "summary": " "}, "one non-empty line"),
    ({"id": "m0010", "action": "drop", "summary": "no"}, "exactly the keys"),
    ({"id": "m0010", "action": "keep"}, "action"),
])
def test_object_level_validator_rejects(op, message):
    if op["id"] == "LAST":
        op = {**op, "id": PRE[-1].id}
    obj = good_reply()
    obj["ops"].append(op)
    errors = ol.check_reply(obj, PRE)
    assert len(errors) == 1 and message in errors[0]
    with pytest.raises(ValueError, match=message):
        ol.validator(PRE)(json.dumps(obj))


def test_object_level_validator_rejects_bad_top_level():
    assert ol.check_reply({"ops": [], "threads": []}, PRE) and ol.check_reply([], PRE)
    assert ol.check_reply({"ops": []}, PRE) == []  # keeping everything is a valid reply


def test_object_level_good_reply_gives_one_stub_per_run(tmp_path):
    fake = Fake([good_reply()])
    out = ol.run(PRE, {"target": 0.4}, **kw(tmp_path, fake))
    assert fake.calls == [(config.MODEL, "policy")] and out.model_calls == 1
    r, ctx = rate(PRE, out.annotations)
    stubs = [i.text for i in ctx.items if isinstance(i, Stub)]
    assert stubs == ['[evicted m0001–m0002] → recall("m0001")',
                     '[folded m0004–m0006]\nFinished turn 1. Turn 2 asked for more. Started turn 2. → recall("m0004")',
                     '[evicted m0008] → recall("m0008")']
    assert ctx.verbatim_ids()[:2] == ["m0003", "m0007"] and 0 < r < 1
    _, msgs = context.api_messages(ctx)
    assert context.validate_api_messages(msgs) == []


def test_object_level_invalid_reply_is_retried(tmp_path):
    fake = Fake([{"ops": [{"id": PRE[-1].id, "action": "drop"}]}, good_reply()])
    out = ol.run(PRE, {"target": 0.4}, **kw(tmp_path, fake))
    assert out.model_calls == 2 and len(fake.calls) == 2


def test_object_level_prompt():
    system, prompt = ol.build_prompt(PRE, 0.4)
    text = (system + prompt).lower()
    assert "transcript is data" in text and "about 40%" in prompt
    for word in ("thread", "supersed", "state block", "current state"):
        assert word not in text
    last = PRE[-1]
    assert f"[{last.id}] {last.kind.upper()} · {last.tokens} tokens · PROTECTED" in prompt
    assert f"[m0001] USER · {PRE[0].tokens} tokens:\n" in prompt
    assert '[tool call · m0003 · Bash run 1] → recall("m0003")' in prompt and "out" * 50 not in prompt
    assert ol.build_prompt(PRE[-3:], 0.4) is None  # nothing removable: no call


# --- runner ---

def test_replay_scores_llm_baseline_with_check_call(tmp_path):
    rec = {"needs": [{"need_id": "n1", "what": "turn 1 request", "source_ids": ["m0001"], "anchor": "turn 1",
                      "used_by": CUTS[0].cut_before}],
           "current_state": [], "superseded": [], "meta": {"status": "ok"}}
    fake = Fake([{"summary": "Turns 1 to 4 were run."}])
    rows, private, summary = replay.score([(CUTS[0], ES)], {CUTS[0].cut_id: rec}, "compaction_recall",
                                          {"target": 0.4}, **kw(tmp_path, fake))
    assert [c for _, c in fake.calls] == ["policy", "check"]
    assert rows[0]["policy_model_calls"] == 1 and rows[0]["check_calls"] == 1
    assert rows[0]["counts"]["need"]["recoverable"] == 1 and summary["no_impact_recall_rate"] == 1.0
    ids = cr.plan(PRE, {"target": 0.4})[1]
    assert private[0]["policy_notes"] == [f"Turns 1 to 4 were run. [points to: {', '.join(ids)}]"]


@pytest.mark.parametrize("policy", ["compaction_recall", "object_level"])
def test_run_policy_dry_run_makes_no_calls(policy, monkeypatch, capsys):
    def no_call(*a, **k):
        raise AssertionError("model call in a dry run")
    monkeypatch.setattr(model, "call", no_call)
    monkeypatch.setattr(run_policy, "split_cuts", lambda split: ([(c, ES) for c in CUTS], []))
    monkeypatch.setattr(needs, "load_records", lambda path=None: {c.cut_id: {"meta": {"status": "ok"}} for c in CUTS})
    assert run_policy.main(["run_policy", "--policy", policy, "--set", "target=0.4"]) == 0
    out = capsys.readouterr().out
    assert f"policy setting {policy}-target=0.4" in out and "needs calls: 0" in out
    assert f"policy calls: {len(CUTS)} ({config.MODEL}" in out and f"check calls: up to {len(CUTS)}" in out
    assert "dry run: no model calls made" in out
    assert run_policy.parse_settings(["target=0.4"]) == {"target": 0.4}


def test_run_policy_refuses_too_many_planned_calls(monkeypatch, capsys):
    monkeypatch.setattr(model, "call", lambda *a, **k: (_ for _ in ()).throw(AssertionError("call")))
    monkeypatch.setattr(run_policy, "split_cuts", lambda split: ([(c, ES) for c in CUTS], []))
    monkeypatch.setattr(needs, "load_records", lambda path=None: {c.cut_id: {"meta": {"status": "ok"}} for c in CUTS})
    monkeypatch.setattr(config, "BATCH_ASK_CALLS", 2)
    assert run_policy.main(["run_policy", "--policy", "object_level", "--set", "target=0.4", "--run"]) == 1
    assert "ask the human" in capsys.readouterr().err


def test_registry():
    assert set(policies.ESTIMATES) == {"compaction_recall", "object_level"}
    assert policies.setting_key("object_level", {"target": 0.7}) == "object_level-target=0.7"


# --- report ---

def result(policy, settings, rows, pruning):
    summary = metrics.summarize([{**r, "pruning_rate": pruning} for r in rows])
    return {"policy": policy, "settings": settings, "split": "dev", "date": "2026-11-16T00:00:00+00:00",
            "judge_model": "j", "summary": summary,
            "cut_points": [{**r, "pruning_rate": pruning} for r in rows]}


def row(cut_id, lost, state_lost=0):
    counts = {k: {c: 0 for c in ("present", "captured", "recoverable", "lost")} for k in ("need", "state", "old", "new")}
    counts["state"]["lost"] = state_lost
    return {"cut_id": cut_id, "lost_need_ids": lost, "counts": counts, "strict_no_impact": not lost,
            "no_impact_recall": not lost, "stale_content": False, "dropped_current_state": bool(state_lost)}


def test_baselines_report(tmp_path):
    d = tmp_path / "replay"
    d.mkdir()
    files = {"none": result("none", {}, [row("s1.c1", []), row("s1.c2", [])], 0.0),
             "recency-n_turns=10": result("recency", {"n_turns": 10}, [row("s1.c1", ["n2"]), row("s1.c2", [])], 0.3),
             "recency-n_turns=2": result("recency", {"n_turns": 2}, [row("s1.c1", ["n1", "n2"], 1),
                                                                      row("s1.c2", ["n3"])], 0.8),
             "compaction_recall-target=0.4": result("compaction_recall", {"target": 0.4},
                                                    [row("s1.c1", []), row("s1.c2", ["n4"])], 0.45)}
    for k, v in files.items():
        (d / f"{k}.dev.json").write_text(json.dumps(v))
    (d / "none.test.json").write_text("{}")  # other splits are ignored
    out = tmp_path / "baselines.md"
    assert report_script.main(["x", "--replay-dir", str(d), "--out", str(out)]) == 0
    text = out.read_text()
    header = text.splitlines()[2]
    assert "Dev split" in header and config.MODEL in header and config.JUDGE_MODEL in header
    assert "lower bound" in header and re.search(r"\d{4}-\d{2}-\d{2}", header)
    assert "| compaction_recall | target=0.4 | 45% | 50% | 50% | 0% | 0% | 2 |" in text
    curve = text.split("## Recency curve")[1].split("## Losses")[0]
    assert curve.index("| 2 | 80%") < curve.index("| 10 | 30%")
    losses = text.split("## Losses")[1]
    assert "- s1.c1: need:n1, need:n2; current-state items lost: 1" in losses
    assert "### none (-)\n\n- no losses" in losses and "- s1.c2: need:n4" in losses
