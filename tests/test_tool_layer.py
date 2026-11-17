from ctxtrim import tool_layer
from ctxtrim.log import approx_tokens, make_entry


def tool(i, name, content="RESULT", **inp):
    return make_entry(f"m{i:04d}", None, "tool", content, name, inp)


def user(i, text="u"):
    return make_entry(f"m{i:04d}", None, "user", text)


def test_reread_and_edit_clear_earlier_result():
    es = [tool(1, "Read", file_path="/a"), tool(2, "Edit", file_path="/a"), tool(3, "Read", file_path="/b")]
    assert tool_layer.cleared_ids(es, keep=10) == {"m0001": "superseded"}


def test_rerun_same_command_clears_earlier_only_if_identical():
    es = [tool(1, "Bash", command="pytest"), tool(2, "Bash", command="pytest -x"), tool(3, "Bash", command="pytest")]
    assert tool_layer.cleared_ids(es, keep=10) == {"m0001": "superseded"}


def test_path_and_command_do_not_mix_and_other_tools_ignored():
    es = [tool(1, "Read", file_path="ls"), tool(2, "Bash", command="ls"), tool(3, "Grep", pattern="x"),
          tool(4, "Grep", pattern="x")]
    assert tool_layer.cleared_ids(es, keep=10) == {}


def test_keep_most_recent_n():
    es = [tool(i, "Grep", pattern=str(i)) for i in range(1, 6)]
    es.insert(2, user(99))
    assert tool_layer.cleared_ids(es, keep=3) == {"m0001": "old", "m0002": "old"}
    assert set(tool_layer.cleared_ids(es, keep=0)) == {f"m{i:04d}" for i in range(1, 6)}


def test_superseded_inside_recent_window_still_cleared():
    es = [tool(1, "Read", file_path="/a"), tool(2, "Read", file_path="/a")]
    assert tool_layer.cleared_ids(es, keep=3) == {"m0001": "superseded"}


def test_apply_stub_replaces_input_and_result_keeps_others():
    # Amendment 2026-11-16: a cleared tool message is one stub line, input and result.
    es = [user(1, "hello"), tool(2, "Read", "BIG" * 100, file_path="/a"), tool(3, "Read", "new", file_path="/a")]
    out = tool_layer.apply(es, keep=3)
    assert out[0] == es[0] and out[2] == es[2]  # recent tool message stays complete, input included
    assert out[1].tool_input is None and out[1].tool_name == "Read"
    assert "\n" not in out[1].content and 'recall("m0002")' in out[1].content
    assert out[1].tokens == approx_tokens(out[1].content)
    assert es[1].content == "BIG" * 100 and es[1].tool_input == {"file_path": "/a"}  # input list not mutated


def test_apply_old_messages_lose_big_inputs_recent_keep_them():
    big = "x" * 4000
    es = [tool(i, "Write", "ok", file_path=f"/f{i}", text=big) for i in range(1, 6)]
    out = tool_layer.apply(es, keep=3)
    assert [e.tool_input is None for e in out] == [True, True, False, False, False]
    assert all(e.tokens < 100 for e in out[:2]) and all(e.tokens > 1000 for e in out[2:])
    assert [e.id for e in out] == [e.id for e in es]


def test_stub_is_one_line_even_for_multiline_command():
    e = tool(1, "Bash", command="echo a\n  echo b")
    out = tool_layer.apply([e, tool(2, "Bash", command="echo a\n  echo b")], keep=3)
    assert "\n" not in out[0].content and "echo a echo b" in out[0].content


def test_placeholder_is_one_line_with_pointer():
    p = tool_layer.placeholder(tool(7, "Bash", "out", command="ls\n-la"))
    assert "\n" not in p and 'recall("m0007")' in p and "Bash ls -la" in p
