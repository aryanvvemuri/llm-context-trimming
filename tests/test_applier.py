import copy
import json

import pytest

from ctxtrim.applier import InvalidOutput, apply, protected_ids, validate
from ctxtrim.context import Annotations, Op, StateUpdate, Stub, Supersession, build
from ctxtrim.log import Entry, Thread, approx_tokens, make_entry

P = 3  # protect_last_n in these tests
K = 3  # keep_tool_results


def session():
    u = lambda i, t: make_entry(i, None, "user", t)
    a = lambda i, t: make_entry(i, None, "assistant", t)
    return [u("m0001", "Use SQLite for storage."),
            a("m0002", "OK, SQLite it is."),
            make_entry("r0001", None, "reasoning", "native thinking"),
            make_entry("m0003", None, "tool", "schema", "Read", {"file_path": "/db.py"}),
            u("m0004", "Actually use Postgres: we have concurrent writers."),
            a("m0005", "Switching to Postgres."),
            a("m0006", "The auth test fails because of clock mocking."),
            make_entry("m0007", None, "tool", "still fails", "Bash", {"command": "pytest --real-clock"}),
            a("m0008", "Clock mocking ruled out: fails with a real clock too."),
            u("m0009", "Now add tests."),
            a("m0010", "Adding tests in tests/test_db.py, covering inserts and updates."),
            make_entry("m0011", None, "tool", "ok", "Write", {"file_path": "/t.py"}),
            a("m0012", "Done."),
            u("m0013", "Thanks, run the suite.")]


def out(threads=(), supersessions=(), ops=(), state_updates=()):
    return {"threads": list(threads), "supersessions": list(supersessions), "ops": list(ops),
            "state_updates": list(state_updates)}


def errs(output, annotations=None, entries=None, protect=P):
    return validate(entries or session(), annotations or Annotations(), output, protect_last_n=protect,
                    keep_tool_results=K)


def codes(output, **kw):
    return sorted({e.split(":")[0] for e in errs(output, **kw)})


GOOD = out(
    threads=[{"thread_id": "t1", "title": "Which database", "status": "resolved",
              "message_ids": ["m0001", "m0002", "m0004", "m0005"]},
             {"thread_id": "t2", "title": "Why the auth test fails", "status": "active",
              "message_ids": ["m0006", "m0007", "m0008"]}],
    supersessions=[{"old": "m0001", "new": "m0004", "kind": "constraint_changed", "reason": "user switched db"},
                   {"old": "m0006", "new": "m0008", "kind": "hypothesis_refuted", "reason": "real clock fails too"}],
    ops=[{"message_ids": ["m0005", "m0001", "m0002"], "op": "fold", "summary": "SQLite replaced by Postgres."},
         {"message_ids": ["m0006"], "op": "fold", "summary": "Thought: clock mocking.",
          "lesson": "Not clock mocking; do not revisit."},
         {"message_ids": ["m0010"], "op": "evict", "stub": "Test-writing details."}],
    state_updates=[{"key": "decision:database", "value": "SQLite", "source": "m0001"},
                   {"key": "decision:database", "value": "Postgres (concurrent writers)", "source": "m0004",
                    "supersedes": "m0001"}])


def test_valid_multi_op_output():
    es = session()
    es_before = list(es)
    good = copy.deepcopy(GOOD)
    accepted, records = apply(es, Annotations(), GOOD, protect_last_n=P, keep_tool_results=K)
    assert GOOD == good and es == es_before  # inputs not mutated
    assert Annotations.from_records(records) == accepted
    ctx = build(es, accepted, keep_tool_results=K)
    shape = [i.id if isinstance(i, Entry) else (i.op, i.message_ids) for i in ctx.items]
    assert shape == [("fold", ("m0001", "m0002", "m0005")), "m0003", "m0004", ("fold", ("m0006",)), "m0007",
                     "m0008", "m0009", ("evict", ("m0010",)), "m0011", "m0012", "m0013"]
    stubs = [i for i in ctx.items if isinstance(i, Stub)]
    assert stubs[0].role == "user"
    assert stubs[0].text == ('[folded m0001–m0005 · thread t1 "Which database" · constraint changed]\n'
                             'SQLite replaced by Postgres. → recall("m0001")')
    assert stubs[1].text == ('[folded m0006 · thread t2 "Why the auth test fails" · hypothesis refuted]\n'
                             'Thought: clock mocking. Lesson: Not clock mocking; do not revisit. → recall("m0006")')
    assert stubs[2].text == '[evicted m0010] Test-writing details. → recall("m0010")'
    assert ctx.state_text == ("## Current state\n"
                              "- decision:database: Postgres (concurrent writers)  [m0004, supersedes m0001]")
    survivors = [i for i in ctx.items if isinstance(i, Entry)]
    assert ctx.conversational_tokens == (approx_tokens(ctx.state_text) + sum(s.tokens for s in stubs)
                                         + sum(e.tokens for e in survivors if e.kind != "tool"))
    assert ctx.tool_tokens == build(es, None, keep_tool_results=K).tool_tokens
    assert ctx.covered_by()["m0002"] is stubs[0]
    assert "m0004" in ctx.verbatim_ids() and "m0001" not in ctx.verbatim_ids()


def test_json_string_accepted_and_invalid_json_rejected():
    assert errs(json.dumps(GOOD)) == []
    assert codes("{not json") == ["schema"]


@pytest.mark.parametrize("bad", [
    [],
    {"threads": [], "ops": [], "state_updates": []},
    {**out(), "extra": []},
    {**out(), "ops": {}},
    out(ops=[{"message_ids": ["m0002"], "op": "delete", "stub": "x"}]),
    out(ops=[{"message_ids": [], "op": "evict", "stub": "x"}]),
    out(supersessions=[{"old": "m0001", "new": "m0004", "kind": "replaced", "reason": "r"}]),
    out(threads=[{"thread_id": "t1", "title": "x", "status": "open", "message_ids": []}]),
    out(state_updates=[{"key": "k", "value": "", "source": "m0001"}]),
])
def test_schema_errors(bad):
    assert codes(bad) == ["schema"]


def test_unknown_ids():
    assert codes(out(ops=[{"message_ids": ["m9999"], "op": "evict", "stub": "x"}])) == ["unknown_id"]
    assert codes(out(threads=[{"thread_id": "t", "title": "x", "status": "resolved",
                               "message_ids": ["m9999"]}])) == ["unknown_id"]
    # native thinking entries are not addressable
    assert codes(out(ops=[{"message_ids": ["r0001"], "op": "evict", "stub": "x"}])) == ["unknown_id"]


def test_protected_last_n_and_current_user_request():
    assert protected_ids(session(), 3) == {"m0011", "m0012", "m0013"}
    assert protected_ids(session(), 0) == {"m0013"}
    assert codes(out(ops=[{"message_ids": ["m0012"], "op": "evict", "stub": "x"}])) == ["protected"]
    es = session()[:-1]  # last user message is now m0009, outside the last 3
    up = [{"key": "k", "value": "v", "source": "m0009"}]
    assert codes(out(ops=[{"message_ids": ["m0009"], "op": "evict", "stub": "x"}], state_updates=up),
                 entries=es) == ["protected"]


def test_user_message_needs_state_source():
    op = [{"message_ids": ["m0009"], "op": "evict", "stub": "x"}]
    assert codes(out(ops=op)) == ["user_message"]
    assert codes(out(ops=op, state_updates=[{"key": "k", "value": "v", "source": "m0010"}])) == ["user_message"]
    assert errs(out(ops=op, state_updates=[{"key": "task", "value": "add tests", "source": "m0009"}])) == []
    accepted = Annotations(state_updates=(StateUpdate("task", "add tests", "m0009"),))
    assert errs(out(ops=op), annotations=accepted) == []
    # `supersedes` counts as pointing to the message (5.4 "[m0019, supersedes m0008]")
    assert errs(out(ops=op, state_updates=[{"key": "task", "value": "v", "source": "m0010",
                                            "supersedes": "m0009"}])) == []
    # only the latest line per key is rendered: an overwritten update does not count
    assert codes(out(ops=op, state_updates=[{"key": "task", "value": "add tests", "source": "m0009"},
                                            {"key": "task", "value": "v2", "source": "m0010"}])) == ["user_message"]
    assert codes(out(ops=op, state_updates=[{"key": "task", "value": "v2", "source": "m0010"}]),
                 annotations=accepted) == ["user_message"]


def test_state_updates_may_not_orphan_an_earlier_removed_user_message():
    accepted = Annotations(ops=(Op("evict", ("m0009",), stub="x"),),
                           state_updates=(StateUpdate("task", "add tests", "m0009"),))
    orphan = out(state_updates=[{"key": "task", "value": "run the suite", "source": "m0013"}])
    errors = errs(orphan, annotations=accepted)
    assert [e.split(":")[0] for e in errors] == ["user_message"] and "m0009" in errors[0]
    assert errs(out(state_updates=[{"key": "task", "value": "run the suite", "source": "m0013",
                                    "supersedes": "m0009"}]), annotations=accepted) == []
    assert errs(out(state_updates=[{"key": "other", "value": "v", "source": "m0013"}]), annotations=accepted) == []


def test_removed_twice_and_already_removed():
    assert codes(out(ops=[{"message_ids": ["m0010"], "op": "evict", "stub": "x"},
                          {"message_ids": ["m0010"], "op": "evict", "stub": "y"}])) == ["double_removal"]
    accepted = Annotations(ops=(Op("evict", ("m0010",), stub="x"),))
    assert codes(out(ops=[{"message_ids": ["m0010"], "op": "evict", "stub": "y"}]),
                 annotations=accepted) == ["already_removed"]


def test_fold_needs_summary_evict_needs_stub():
    assert codes(out(ops=[{"message_ids": ["m0010"], "op": "fold", "summary": "  "}])) == ["fold_summary"]
    assert codes(out(ops=[{"message_ids": ["m0010"], "op": "fold"}])) == ["fold_summary"]
    assert codes(out(ops=[{"message_ids": ["m0010"], "op": "evict", "stub": ""}])) == ["evict_stub"]


def test_refuted_and_abandoned_must_be_folded_with_lesson():
    sup = [{"old": "m0006", "new": "m0008", "kind": "hypothesis_refuted", "reason": "r"}]
    assert codes(out(supersessions=sup, ops=[{"message_ids": ["m0006"], "op": "evict", "stub": "x"}])) \
        == ["lesson_required"]
    assert codes(out(supersessions=sup, ops=[{"message_ids": ["m0006"], "op": "fold", "summary": "s"}])) \
        == ["lesson_required"]
    # supersession accepted in an earlier run counts too
    accepted = Annotations(supersessions=(Supersession("m0006", "m0008", "abandoned", "r"),))
    assert codes(out(ops=[{"message_ids": ["m0006"], "op": "evict", "stub": "x"}]), annotations=accepted) \
        == ["lesson_required"]
    # member of an abandoned thread
    th = [{"thread_id": "t9", "title": "x", "status": "abandoned", "message_ids": ["m0010"]}]
    assert codes(out(threads=th, ops=[{"message_ids": ["m0010"], "op": "evict", "stub": "x"}])) \
        == ["lesson_required"]
    assert errs(out(threads=th, ops=[{"message_ids": ["m0010"], "op": "fold", "summary": "s",
                                      "lesson": "l"}])) == []


def test_supersession_checks():
    s = lambda old, new, reason="r": out(supersessions=[{"old": old, "new": new, "kind": "restated",
                                                         "reason": reason}])
    assert codes(s("m0002", "m9999")) == ["supersession_new_missing"]
    assert codes(s("m0005", "m0002")) == ["supersession_order"]
    assert codes(s("m0005", "m0005")) == ["supersession_order"]
    assert codes(s("m0002", "m0005", reason=" ")) == ["supersession_reason"]
    assert errs(s("m0002", "m0005")) == []


def test_thread_permit():
    active = {"thread_id": "t2", "title": "x", "status": "active", "message_ids": ["m0008", "m0010"]}
    op = [{"message_ids": ["m0010"], "op": "evict", "stub": "x"}]
    assert codes(out(threads=[active], ops=op)) == ["thread_permit"]
    # every thread must permit: resolved t1 does not override active t2
    resolved = {"thread_id": "t1", "title": "y", "status": "resolved", "message_ids": ["m0010"]}
    assert codes(out(threads=[active, resolved], ops=op)) == ["thread_permit"]
    assert errs(out(threads=[{**active, "status": "resolved"}], ops=op)) == []
    # old side of a supersession (earlier accepted) is permitted in an active thread
    accepted = Annotations(supersessions=(Supersession("m0010", "m0012", "restated", "r"),))
    assert errs(out(threads=[active], ops=op), annotations=accepted) == []
    # thread accepted earlier as active, resolved by this output
    earlier = Annotations(threads=(Thread("t2", "x", "active", ["m0010"]),))
    assert codes(out(ops=op), annotations=earlier) == ["thread_permit"]
    assert errs(out(threads=[{**active, "status": "resolved", "message_ids": []}], ops=op),
                annotations=earlier) == []


def test_tool_messages_cannot_be_targeted():
    assert codes(out(ops=[{"message_ids": ["m0007"], "op": "evict", "stub": "x"}])) == ["tool_message"]


def test_state_update_unknown_source():
    assert codes(out(state_updates=[{"key": "k", "value": "v", "source": "m9999"}])) == ["state_source"]
    assert codes(out(state_updates=[{"key": "k", "value": "v", "source": "m0001",
                                     "supersedes": "m9999"}])) == ["state_source"]


def test_invalid_api_sequence_rejected():
    es = [make_entry("m0000", None, "assistant", "I start the session.")] + session()
    assert codes(out(ops=[{"message_ids": ["m0010"], "op": "evict", "stub": "x"}]), entries=es) == ["api_invalid"]


def test_whole_output_rejected_with_all_errors():
    bad = copy.deepcopy(GOOD)
    bad["ops"].append({"message_ids": ["m0012"], "op": "evict", "stub": "x"})
    bad["ops"].append({"message_ids": ["m0003"], "op": "evict", "stub": "x"})
    accepted = Annotations()
    with pytest.raises(InvalidOutput) as e:
        apply(session(), accepted, bad, protect_last_n=P, keep_tool_results=K)
    assert sorted(x.split(":")[0] for x in e.value.errors) == ["protected", "tool_message"]
    assert accepted == Annotations()


def test_incremental_runs_accumulate():
    es = session()
    a1, _ = apply(es, Annotations(), GOOD, protect_last_n=P, keep_tool_results=K)
    second = out(threads=[{"thread_id": "t2", "title": "Why the auth test fails", "status": "resolved",
                           "message_ids": ["m0009"]}],
                 ops=[{"message_ids": ["m0008"], "op": "fold", "summary": "Real clock also fails."}])
    a2, records = apply(es, a1, second, protect_last_n=P, keep_tool_results=K)
    assert [t.message_ids for t in a2.threads if t.thread_id == "t2"] == [["m0006", "m0007", "m0008", "m0009"]]
    assert len(a2.ops) == 4 and a1.ops == a2.ops[:3]
    assert records[0] == Thread("t2", "Why the auth test fails", "resolved", ["m0006", "m0007", "m0008", "m0009"])
