import pytest

from ctxtrim import config, tool_layer
from ctxtrim.context import (Annotations, Op, StateUpdate, Stub, api_messages, build, render_state,
                             validate_api_messages)
from ctxtrim.log import Entry, Log, Thread, approx_tokens, make_entry, pilot_entries
from ctxtrim.splits import load_split


def session():
    """Synthetic session: user/assistant/tool messages plus one native thinking entry."""
    es = [make_entry("m0001", None, "user", "Use SQLite for storage."),
          make_entry("m0002", None, "assistant", "OK, I will use SQLite."),
          make_entry("r0001", None, "reasoning", "native thinking"),
          make_entry("m0003", None, "tool", "file body", "Read", {"file_path": "/a"}),
          make_entry("m0004", None, "tool", "file body 2", "Read", {"file_path": "/a"}),
          make_entry("m0005", None, "assistant", "Done reading."),
          make_entry("m0006", None, "user", "Thanks.")]
    return es


def test_no_annotations_tool_layer_off_equals_pilot_entries():
    es = session()
    ctx = build(es, None, keep_tool_results=None)
    assert ctx.items == tuple(pilot_entries(es))
    assert "r0001" not in ctx.verbatim_ids()
    assert ctx.state == () and ctx.state_text == ""


def test_no_annotations_tool_layer_on_equals_tool_layer_output():
    es = session()
    ctx = build(es, Annotations(), keep_tool_results=3)
    assert ctx.items == tuple(tool_layer.apply(pilot_entries(es), 3))
    assert ctx.items[2].tool_input is None  # m0003 cleared: m0004 re-reads /a


def test_token_split():
    es = session()
    ctx = build(es, None, keep_tool_results=3)
    conv = sum(e.tokens for e in es if e.kind in ("user", "assistant"))
    assert ctx.conversational_tokens == conv
    assert ctx.tool_tokens == sum(e.tokens for e in ctx.items if e.kind == "tool")


def test_build_does_not_mutate_inputs():
    es = session()
    before = list(es)
    ann = Annotations(ops=(Op("evict", ("m0002",), stub="x"),))
    build(es, ann, keep_tool_results=3)
    assert es == before


def test_stub_at_first_removed_position_and_render():
    es = session()
    ann = Annotations(threads=(Thread("t1", "Storage", "resolved", ["m0002", "m0005"]),),
                      ops=(Op("fold", ("m0005", "m0002"), summary="Read the file.", lesson="L."),))
    ctx = build(es, ann, keep_tool_results=None)
    assert [i.id if isinstance(i, Entry) else i.message_ids for i in ctx.items] == \
        ["m0001", ("m0002", "m0005"), "m0003", "m0004", "m0006"]  # ids in log order
    stub = ctx.items[1]
    assert stub.role == "assistant"
    assert stub.text == '[folded m0002–m0005 · thread t1 "Storage" · resolved]\nRead the file. Lesson: L. → recall("m0002")'
    assert stub.tokens == approx_tokens(stub.text)
    assert ctx.covered_by() == {"m0005": stub, "m0002": stub}


def test_state_block_latest_per_key_in_first_appearance_order():
    ups = (StateUpdate("goal", "ship it", "m0001"), StateUpdate("decision:db", "SQLite", "m0001"),
           StateUpdate("decision:db", "Postgres", "m0006", "m0001"))
    ann = Annotations(state_updates=ups)
    ctx = build(session(), ann, keep_tool_results=None)
    assert ctx.state_text == ("## Current state\n- goal: ship it  [m0001]\n"
                              "- decision:db: Postgres  [m0006, supersedes m0001]")
    assert ctx.state_sources() == {"m0001": [ups[0], ups[2]], "m0006": [ups[2]]}
    assert ctx.conversational_tokens == approx_tokens(ctx.state_text) + sum(
        e.tokens for e in session() if e.kind in ("user", "assistant"))
    assert render_state(()) == ""


def test_annotations_records_round_trip_and_thread_merge():
    a = Annotations().add(threads=[Thread("t1", "A", "active", ["m1"])])
    a = a.add(threads=[Thread("t1", "A2", "resolved", ["m2", "m1"])],
              ops=[Op("evict", ("m1",), stub="s")], state_updates=[StateUpdate("k", "v", "m2")])
    assert a.threads == (Thread("t1", "A2", "resolved", ["m1", "m2"]),)
    assert Annotations.from_records(Annotations.records(a.threads, a.supersessions, a.ops, a.state_updates)) == a


def test_api_messages_merge_roles_and_render_tools_as_assistant_text():
    es = session()
    ann = Annotations(ops=(Op("evict", ("m0002",), stub="gone"),), state_updates=(StateUpdate("k", "v", "m0001"),))
    system, msgs = api_messages(build(es, ann, keep_tool_results=3), system_prompt="SYS")
    assert system == "SYS\n\n## Current state\n- k: v  [m0001]"
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[1]["content"].startswith('[evicted m0002] gone → recall("m0002")\n\n[tool call cleared · m0003')
    assert '[tool call · m0004 · Read] input: {"file_path": "/a"}\nresult:\nfile body 2' in msgs[1]["content"]
    assert validate_api_messages(msgs) == []


def test_validate_api_messages_errors():
    assert validate_api_messages([]) == ["no messages"]
    errs = validate_api_messages([{"role": "assistant", "content": "a"}, {"role": "assistant", "content": " "},
                                  {"role": "system", "content": "s"}])
    assert "first message is not a user message" in errs
    assert any("does not alternate" in e for e in errs)
    assert any("empty content" in e for e in errs)
    assert any("is not user or assistant" in e for e in errs)


DEV = [r for r in load_split("dev") if (config.NORMALIZED_DIR / r["session_id"] / "log.jsonl").exists()] \
    if config.SPLITS_PATH.exists() else []


@pytest.mark.skipif(len(DEV) != 6, reason="dev sessions not normalized on this machine")
@pytest.mark.parametrize("row", DEV, ids=[r["pilot_id"] for r in DEV])
def test_dev_session_without_annotations_equals_original(row):
    es = Log(config.NORMALIZED_DIR / row["session_id"] / "log.jsonl").entries()
    pilot = pilot_entries(es)
    assert build(es, None, keep_tool_results=None).items == tuple(pilot)
    ctx = build(es, None, keep_tool_results=config.KEEP_TOOL_RESULTS)
    assert ctx.items == tuple(tool_layer.apply(pilot, config.KEEP_TOOL_RESULTS))
    assert not any(isinstance(i, Stub) for i in ctx.items)
    assert validate_api_messages(api_messages(ctx)[1]) == []
