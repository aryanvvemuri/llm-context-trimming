import json

import pytest

from ctxtrim.log import Annotation, Entry, Log, Thread, approx_tokens, make_entry, pilot_entries, write_new_log


def test_approx_tokens_is_ceil_chars_over_4():
    assert approx_tokens("") == 0
    assert approx_tokens("abc") == 1
    assert approx_tokens("abcd") == 1
    assert approx_tokens("abcde") == 2


def test_make_entry_counts_content_and_tool_input():
    e = make_entry("m0001", None, "tool", "x" * 8, "Bash", {"command": "ls"})
    assert e.tokens == 2 + approx_tokens(json.dumps({"command": "ls"}, sort_keys=True))
    assert make_entry("m0002", None, "user", "x" * 9).tokens == 3


def test_entry_validation():
    with pytest.raises(ValueError):
        Entry("m1", None, "system", "x")
    with pytest.raises(ValueError):
        Entry("m1", None, "tool", "x")  # tool entries need a tool_name
    with pytest.raises(ValueError):
        Thread("t1", "x", "done", [])


def test_append_only_round_trip(tmp_path):
    log = Log(tmp_path / "log.jsonl")
    a = make_entry("m0001", "t", "user", "hello")
    log.append(a)
    log.append(Thread("t01", "Topic", "active", ["m0001"]))
    log.append(Annotation({"note": "x"}))
    log.append(Thread("t01", "Topic", "resolved", ["m0001"]))
    first_bytes = (tmp_path / "log.jsonl").read_bytes()
    log.append(make_entry("m0002", "t", "assistant", "hi"))
    # earlier bytes are untouched by a later append
    assert (tmp_path / "log.jsonl").read_bytes().startswith(first_bytes)
    assert [e.id for e in log.entries()] == ["m0001", "m0002"]
    assert log.entries()[0] == a
    assert log.latest_threads()["t01"].status == "resolved"
    assert len(log.records()) == 5


def test_no_edit_or_delete_api():
    assert not any(hasattr(Log, n) for n in ("edit", "update", "delete", "remove", "write", "rewrite"))


def test_duplicate_ids_rejected(tmp_path):
    log = Log(tmp_path / "log.jsonl")
    log.append(make_entry("m0001", None, "user", "a"))
    with pytest.raises(ValueError):
        log.append(make_entry("m0001", None, "user", "b"))


def test_write_new_log_refuses_existing(tmp_path):
    p = tmp_path / "log.jsonl"
    write_new_log(p, [make_entry("m0001", None, "user", "a")])
    with pytest.raises(FileExistsError):
        write_new_log(p, [])


def test_pilot_entries_drop_only_native_thinking():
    es = [make_entry("m0001", None, "user", "u"), make_entry("m0002", None, "reasoning", "think"),
          make_entry("m0003", None, "assistant", "a"), make_entry("m0004", None, "tool", "r", "Bash", {"command": "ls"})]
    assert [e.id for e in pilot_entries(es)] == ["m0001", "m0003", "m0004"]
    assert len(es) == 4  # input not changed
