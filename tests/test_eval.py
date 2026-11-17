"""Phase 2 replay evaluator on synthetic data with a fake judge backend. No real model calls."""

import json
import re

import pytest

from ctxtrim import config, context, policies
from ctxtrim.context import Annotations, LiveContext, Op, StateUpdate
from ctxtrim.eval import checks, common, cuts, metrics, needs, replay, run_policy
from ctxtrim.log import is_plain_user, make_entry
from ctxtrim.policies import recency
from ctxtrim.policies.base import PolicyOutput

K = 3


def session(turns=10):
    """Turn t: plain user, assistant, Bash tool, assistant. Turn 1 says SQLite, turn 2 Postgres.
    An interrupt marker follows turn 5's user message."""
    es, n = [], 0

    def add(kind, text, **kw):
        nonlocal n
        n += 1
        es.append(make_entry(f"m{n:04d}", None, kind, text, **kw))
    for t in range(1, turns + 1):
        add("user", {1: "use sqlite for storage", 2: "actually use postgres instead"}.get(t, f"turn {t} request"))
        if t == 5:
            add("user", "[Request interrupted by user]")
        add("assistant", f"working on turn {t} " + "x" * 40)
        add("tool", "out" * 50, tool_name="Bash", tool_input={"command": f"run {t}"})
        add("assistant", f"done with turn {t} " + "y" * 40)
    return es


ES = session()
CUTS, PROBLEMS = cuts.find_cuts(ES, "s900")


def needs_reply(cut_before):
    return {"needs": [{"need_id": "n1", "what": "storage is postgres", "source_ids": ["m0005"],
                       "anchor": "use postgres", "used_by": cut_before},
                      {"need_id": "n2", "what": "sqlite was first", "source_ids": ["m0001"],
                       "anchor": "sqlite", "used_by": cut_before}],
            "current_state": [{"key": "decision:db", "what": "postgres", "source_ids": ["m0005"],
                               "anchor": "postgres"}],
            "superseded": [{"old": "m0001", "new": "m0005", "what": "sqlite replaced by postgres"}]}


class Judge:
    """Fake backend: answers needs prompts with needs_reply, check prompts with fixed answers."""
    name = "fake"
    settings = {}

    def __init__(self, captured=False, cue=False, needs_text=None):
        self.captured, self.cue, self.needs_text = captured, cue, needs_text
        self.calls = []

    def run(self, prompt, system, model):
        if system.startswith("You write a needs record"):
            self.calls.append("needs")
            cut = re.search(r"just before message (m\d+)", prompt).group(1)
            text = self.needs_text if self.needs_text is not None else json.dumps(needs_reply(cut))
        else:
            self.calls.append("check")
            ids = re.findall(r"^(i\d+)\. ", prompt, re.M)
            text = json.dumps({"items": [{"item_id": i, "captured": self.captured, "cue": self.cue} for i in ids]})
        return {"text": text, "models": [model], "usage": None, "cost_usd": None, "meta": {}}


def kw(tmp_path, judge):
    return {"backend": judge, "cache_dir": tmp_path / "cache", "ledger_path": tmp_path / "ledger.jsonl"}


# --- cut points ---

def test_plain_user_turns():
    assert is_plain_user(ES[0])
    for text in ("[Request interrupted by user]", "<command-name>/clear</command-name>", "<bash-input>ls</bash-input>"):
        assert not is_plain_user(make_entry("x", None, "user", text))
    assert not is_plain_user(ES[1])


def test_cut_points_before_plain_user_turns_at_fractions():
    assert PROBLEMS == [] and [c.cut_id for c in CUTS] == ["s900.c1", "s900.c2"]
    pos = {e.id: n for n, e in enumerate(ES)}
    for c, f in zip(CUTS, (0.5, 0.8)):
        assert is_plain_user(ES[pos[c.cut_before]]) and pos[c.cut_before] == c.index
        assert c.prefix_tokens == sum(e.tokens for e in ES[:c.index]) >= f * c.total_tokens
        # no earlier plain user turn already reaches the fraction
        earlier = [i for i in range(1, c.index) if is_plain_user(ES[i])]
        assert all(sum(e.tokens for e in ES[:i]) < f * c.total_tokens for i in earlier)
    assert cuts.find_cuts(ES, "s900") == (CUTS, PROBLEMS)  # deterministic


def test_cut_skips_non_plain_user_messages():
    # a session where the only user message after 50% is an interrupt marker until the end
    es = [make_entry("m0001", None, "user", "start"), make_entry("m0002", None, "assistant", "a" * 400),
          make_entry("m0003", None, "user", "[Request interrupted by user]"),
          make_entry("m0004", None, "assistant", "b")]
    cs, probs = cuts.find_cuts(es, "s1")
    assert cs == [] and len(probs) == 2 and "no plain user turn" in probs[0]


def test_coinciding_cuts_and_missing_continuation_are_reported():
    es = [make_entry("m0001", None, "user", "start"), make_entry("m0002", None, "assistant", "a" * 400),
          make_entry("m0003", None, "user", "next"), make_entry("m0004", None, "assistant", "b")]
    cs, probs = cuts.find_cuts(es, "s1")
    assert [c.cut_before for c in cs] == ["m0003"] and "same cut as s1.c1" in probs[0]
    cs, probs = cuts.find_cuts(es[:3], "s1")
    assert cs == [] and "no continuation" in probs[0]


def test_prefix_and_continuation():
    c = CUTS[0]
    assert cuts.prefix(ES, c) == ES[:c.index]
    cont = cuts.continuation(ES, c, K)
    assert cont[0].id == c.cut_before
    assert sum(is_plain_user(e) for e in cont) == K
    after = ES[c.index + len(cont):]
    assert not after or is_plain_user(after[0])
    # fewer than K+1 plain turns left: to the end
    assert cuts.continuation(ES, CUTS[1], 50) == ES[CUTS[1].index:]


# --- needs records ---

def errors(obj, cut=None):
    return needs.check_record(obj, cut or CUTS[0], ES, K)


def test_valid_needs_record():
    assert errors(needs_reply(CUTS[0].cut_before)) == []


@pytest.mark.parametrize("change, message", [
    (lambda r: r.pop("superseded"), "schema"),
    (lambda r: r["needs"][0].pop("anchor"), "schema"),
    (lambda r: r["needs"][0].update(source_ids=["m9999"]), "does not exist"),
    (lambda r: r["needs"][0].update(source_ids=["m0003"], anchor="out"), "tool message"),
    (lambda r: r["current_state"][0].update(source_ids=["m0040"]), "not before the cut"),
    (lambda r: r["needs"][0].update(used_by="m0002"), "continuation"),
    (lambda r: r["needs"][0].update(used_by="m0040"), "continuation"),
    (lambda r: r["needs"][0].update(anchor="use Postgres"), "exact substring"),
    (lambda r: r["needs"][0].update(anchor="postgres " * 30), "at most"),
    (lambda r: r["needs"][1].update(need_id="n1"), "unique"),
    (lambda r: r["current_state"].append(dict(r["current_state"][0])), "at most once"),
    (lambda r: r["needs"][0].update(what="two\nlines"), "one non-empty line"),
    (lambda r: r["superseded"][0].update(old="m0005", new="m0001"), "does not come after"),
    (lambda r: r["superseded"][0].update(new="m0003"), "tool message"),
])
def test_needs_record_rejections(change, message):
    r = needs_reply(CUTS[0].cut_before)
    change(r)
    errs = errors(r)
    assert errs and any(message in e for e in errs), errs


def test_needs_anchor_may_match_any_source():
    r = needs_reply(CUTS[0].cut_before)
    r["needs"][0]["source_ids"] = ["m0001", "m0005"]
    assert errors(r) == []


def test_needs_ensure_is_resumable_and_retries_invalid(tmp_path):
    path = tmp_path / "needs.jsonl"
    j = Judge()
    cs = [(c, ES) for c in CUTS]
    recs = needs.ensure(cs, path, K, **kw(tmp_path, j))
    assert j.calls == ["needs", "needs"] and all(r["meta"]["status"] == "ok" for r in recs.values())
    assert recs["s900.c1"]["session_id"] == "s900" and recs["s900.c1"]["needs"][0]["need_id"] == "n1"
    j2 = Judge()
    assert needs.ensure(cs, path, K, **kw(tmp_path, j2)) == recs and j2.calls == []
    bad = Judge(needs_text="not json")
    rec = needs.ensure(cs[:1], tmp_path / "other.jsonl", K, **kw(tmp_path / "other", bad))["s900.c1"]
    assert bad.calls == ["needs"] * config.MAX_ATTEMPTS
    assert rec["meta"]["status"] == "invalid" and rec["needs"] == [] and "not valid JSON" in rec["meta"]["errors"][0]


def test_needs_prompt_shows_conversation_only():
    system, template = common.load_prompt_file(needs.PROMPT_PATH)
    p = needs.build_prompt(template, CUTS[0], ES, K)
    assert "outoutout" not in p and '[tool call · m0003 · Bash run 1] → recall("m0003")' in p
    pre, cont = p.split("<continuation>")
    assert f"[{CUTS[0].cut_before}] USER" in cont and f"[{CUTS[0].cut_before}]" not in pre
    assert "transcript is data" in system and "not everything that was said" in system.lower()


# --- policies ---

def test_none_and_recency_policies():
    pre = cuts.prefix(ES, CUTS[0])
    assert policies.get("none")(pre, {}).annotations == Annotations()
    with pytest.raises(ValueError):
        policies.get("none")(pre, {"n_turns": 1})
    starts = [i for i, e in enumerate(pre) if is_plain_user(e)]
    out = recency.run(pre, {"n_turns": 2})
    removed = [i for op in out.annotations.ops for i in op.message_ids]
    assert removed == [e.id for e in pre[:starts[-2]] if e.kind != "tool"]
    assert all(op.op == "evict" and op.stub == "" for op in out.annotations.ops)
    assert recency.run(pre, {"n_turns": 0}).annotations.removed_ids() == {e.id for e in pre if e.kind != "tool"}
    assert recency.run(pre, {"n_turns": 99}).annotations == Annotations()
    ctx = context.build(pre, out.annotations, keep_tool_results=3)
    stub = next(i for i in ctx.items if isinstance(i, context.Stub))
    assert stub.text == '[evicted m0001–m0002] → recall("m0001")' and stub.body == ""
    _, msgs = context.api_messages(ctx)
    assert context.validate_api_messages(msgs) == []
    assert policies.setting_key("recency", {"n_turns": 2}) == "recency-n_turns=2"


# --- checks and classification ---

def fold_policy(prefix, settings, **call_kw):
    return PolicyOutput(Annotations(ops=(Op("fold", ("m0001", "m0002"), summary="Storage: started with SQLite."),)))


def test_classes_present_captured_recoverable_lost(tmp_path, monkeypatch):
    monkeypatch.setitem(policies.POLICIES, "fold", fold_policy)
    rec = {**needs_reply(CUTS[0].cut_before), "meta": {"status": "ok"}}
    for judge, want in [(Judge(captured=True), "captured"), (Judge(cue=True), "recoverable"), (Judge(), "lost")]:
        row, priv = replay.score_cut(CUTS[0], ES, rec, "fold", {}, **kw(tmp_path / want, judge))
        assert judge.calls == ["check"] and row["check_calls"] == 1
        # n1 and the state item cite m0005 (verbatim); n2 and the old side cite m0001 (folded)
        assert row["counts"]["need"] == {"present": 1, "captured": int(want == "captured"),
                                         "recoverable": int(want == "recoverable"), "lost": int(want == "lost")}
        assert row["counts"]["state"]["present"] == 1 and row["counts"]["new"]["present"] == 1
        assert row["counts"]["old"][want] == 1
        assert row["strict_no_impact"] == (want == "captured")
        assert row["no_impact_recall"] == (want != "lost")
        assert row["lost_need_ids"] == (["n2"] if want == "lost" else [])
        assert not row["stale_content"] and not row["dropped_current_state"]
        assert [d["ref"] for d in priv["decisions"]] == ["need:n2", "sup:0:old"]


def test_check_prompt_is_plain_and_policy_neutral():
    ann = Annotations(threads=(context.Thread("t1", "Which database", "resolved", ["m0001", "m0002"]),),
                      ops=(Op("fold", ("m0001", "m0002"), summary="SQLite first.", lesson="Not SQLite."),),
                      state_updates=(StateUpdate("decision:db", "postgres", "m0005", "m0001"),))
    pre = cuts.prefix(ES, CUTS[0])
    ctx = context.build(pre, ann, keep_tool_results=3)
    assert "folded" in ctx.text() and "Which database" in ctx.text()
    p = checks.prepare(needs_reply(CUTS[0].cut_before), ctx)
    assert checks.notes(ctx) == ["decision:db: postgres [points to: m0005, m0001]",
                                 "SQLite first. Lesson: Not SQLite. [points to: m0001, m0002]"]
    for word in ("folded", "evicted", "thread", "t1", "Which database", "resolved", "recall(", "fold", "recency",
                 "none", "trimmer", "policy"):
        assert word not in p.prompt, word
    assert "N1. decision:db: postgres [points to: m0005, m0001]" in p.prompt
    assert "i1. sqlite was first" in p.prompt and "i2. Earlier side of: sqlite replaced by postgres" in p.prompt


def test_bare_stubs_only_are_classified_in_code(tmp_path):
    pre = cuts.prefix(ES, CUTS[0])
    ctx = context.build(pre, recency.run(pre, {"n_turns": 1}).annotations, keep_tool_results=3)
    p = checks.prepare(needs_reply(CUTS[0].cut_before), ctx)
    assert p.prompt is None
    j = Judge(captured=True)
    d = checks.decide(p, ctx, **kw(tmp_path, j))
    assert j.calls == [] and d["calls"] == 0
    assert d["classes"] == {"need:n1": "lost", "need:n2": "lost", "state:0": "lost",
                            "sup:0:old": "lost", "sup:0:new": "lost"}


def test_recoverable_needs_a_pointer_in_code(tmp_path):
    # a hand-made context where m0001 is simply gone (no stub covers it) but a note has text
    pre = cuts.prefix(ES, CUTS[0])
    full = context.build(pre, Annotations(state_updates=(StateUpdate("db", "postgres", "m0005"),)),
                         keep_tool_results=3)
    ctx = LiveContext(tuple(i for i in full.items if getattr(i, "id", None) != "m0001"), full.state,
                      full.state_text)
    rec = needs_reply(CUTS[0].cut_before)
    p = checks.prepare(rec, ctx)
    d = checks.decide(p, ctx, **kw(tmp_path, Judge(cue=True)))
    assert d["classes"]["need:n2"] == "lost"
    item = next(i for i in checks.items(rec) if i.ref == "need:n2")
    assert not checks.pointer_covers(item, ctx) and checks.pointer_covers(item, full)


def test_present_needs_anchor_and_every_source():
    ctx = context.build(cuts.prefix(ES, CUTS[0]), None, keep_tool_results=3)
    item = checks.Item("need:x", "need", "w", ("m0001", "m0005"), "postgres")
    assert checks.is_present(item, ctx)
    assert not checks.is_present(checks.Item("need:x", "need", "w", ("m0001",), "postgres"), ctx)
    assert not checks.is_present(checks.Item("need:x", "need", "w", ("m0001", "m9999"), "sqlite"), ctx)


def test_check_reply_validation():
    v = checks.validator(2)
    ok = {"items": [{"item_id": "i1", "captured": True, "cue": False}, {"item_id": "i2", "captured": False, "cue": True}]}
    assert set(v(json.dumps(ok))) == {"i1", "i2"}
    for bad in [{"items": ok["items"][:1]}, {"items": ok["items"] + [ok["items"][0]]},
                {"items": [{**ok["items"][0], "captured": "yes"}, ok["items"][1]]},
                {"items": ok["items"], "extra": 1}]:
        with pytest.raises(ValueError):
            v(json.dumps(bad))


# --- metrics ---

def test_metrics():
    assert metrics.pruning_rate(200, 50) == 0.75 and metrics.pruning_rate(0, 0) == 0.0
    f = metrics.cut_flags({"need:n1": "present", "need:n2": "recoverable", "state:0": "captured",
                           "sup:0:old": "present", "sup:0:new": "lost", "sup:1:old": "lost", "sup:1:new": "lost"})
    assert f == {"strict_no_impact": False, "no_impact_recall": True, "stale_content": True,
                 "dropped_current_state": False}
    f = metrics.cut_flags({"need:n1": "lost", "state:0": "recoverable", "sup:0:old": "present",
                           "sup:0:new": "captured"})
    assert f == {"strict_no_impact": False, "no_impact_recall": False, "stale_content": False,
                 "dropped_current_state": True}
    assert metrics.cut_flags({}) == {"strict_no_impact": True, "no_impact_recall": True, "stale_content": False,
                                     "dropped_current_state": False}
    rows = [{"pruning_rate": 0.5, **f}, {"pruning_rate": 0.1, **metrics.cut_flags({})}]
    s = metrics.summarize(rows)
    assert s["cut_points"] == 2 and abs(s["mean_pruning_rate"] - 0.3) < 1e-12
    assert s["strict_no_impact_rate"] == 0.5 and s["dropped_current_state_rate"] == 0.5


# --- acceptance (PLAN.md Phase 2) ---

def test_none_scores_full_at_zero_pruning_then_recency_makes_no_needs_calls(tmp_path):
    cs = [(c, ES) for c in CUTS]
    path = tmp_path / "needs.jsonl"
    j = Judge()
    rows, private, summary = run_policy.run(cs, "none", {}, path, **kw(tmp_path, j))
    assert j.calls == ["needs", "needs"]  # one per cut point, no check call
    assert summary["mean_pruning_rate"] == 0.0
    assert summary["strict_no_impact_rate"] == 1.0 and summary["no_impact_recall_rate"] == 1.0
    assert summary["stale_content_rate"] == 0.0 and summary["dropped_current_state_rate"] == 0.0
    assert all(r["check_calls"] == 0 and r["pruning_rate"] == 0.0 for r in rows)
    j.calls.clear()
    rows, _, summary = run_policy.run(cs, "recency", {"n_turns": 1}, path, **kw(tmp_path, j))
    assert "needs" not in j.calls and j.calls == []  # bare stubs: no check call either
    assert summary["mean_pruning_rate"] > 0 and summary["strict_no_impact_rate"] == 0.0
    assert all(r["lost_need_ids"] == ["n1", "n2"] for r in rows)
    # the model cache alone also prevents repeats: a fresh needs file, same inputs -> cache hits
    led = tmp_path / "ledger.jsonl"
    before = len(led.read_text().splitlines())
    needs.ensure(cs, tmp_path / "fresh.jsonl", K, **kw(tmp_path, j))
    assert j.calls == [] and len(led.read_text().splitlines()) == before + 2


def test_outputs_hold_ids_and_numbers_only(tmp_path):
    cs = [(c, ES) for c in CUTS]
    rows, private, summary = run_policy.run(cs, "recency", {"n_turns": 1}, tmp_path / "n.jsonl",
                                            **kw(tmp_path, Judge()))
    out = run_policy.write_outputs("recency-n_turns=1", "dev", "recency", {"n_turns": 1}, rows, private, summary,
                                   [], results_dir=tmp_path / "results", private_dir=tmp_path / "private")
    text = out.read_text()
    for s in ("sqlite", "postgres", "storage", "turn 1"):
        assert s not in text


def test_run_policy_refuses_test_split_and_parses_settings(capsys):
    assert run_policy.main(["run_policy", "--policy", "none", "--split", "test"]) == 2
    assert needs.main(["needs", "--split", "test"]) == 2
    assert run_policy.parse_settings(["n_turns=4", "x=a"]) == {"n_turns": 4, "x": "a"}
