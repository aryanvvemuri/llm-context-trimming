"""Tests for the final selection and split in scripts/select_final.py (synthetic rows only)."""

import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest

from ctxtrim.splits import load_split

_spec = importlib.util.spec_from_file_location(
    "select_final_script", Path(__file__).resolve().parents[1] / "scripts" / "select_final.py")
sf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sf)


def pool(n_users=20, per_user=3, shared_repo_every=4):
    """Users with several sessions each; some repos shared across users."""
    rows = []
    for u in range(n_users):
        for k in range(per_user):
            repo = f"shared{u // shared_repo_every}" if k == 0 else f"r{u}_{k}"
            rows.append({"session_id": f"sess{u:02d}{k}", "user_key": f"u{u}", "repo_id": repo})
    return rows


def test_caps_respected_and_eighteen_chosen():
    rows, _ = sf.select(pool())
    assert len(rows) == 18
    assert max(Counter(r["user_key"] for r in rows).values()) <= 2
    assert max(Counter(r["repo_id"] for r in rows).values()) <= 2
    assert [r["pilot_id"] for r in rows] == [f"s{i:03d}" for i in range(1, 19)]


def test_split_sizes_and_no_user_or_repo_in_both():
    rows, straddle = sf.select(pool())
    dev = [r for r in rows if r["split"] == "dev"]
    test = [r for r in rows if r["split"] == "test"]
    assert (len(dev), len(test)) == (6, 12)
    assert not {r["user_key"] for r in dev} & {r["user_key"] for r in test}
    assert not {r["repo_id"] for r in dev} & {r["repo_id"] for r in test}
    assert straddle == []


def test_deterministic_and_independent_of_input_order():
    a = sf.select(pool())
    b = sf.select(list(reversed(pool())))
    assert a == b
    assert sf.select(pool(), seed=1) != a


def test_too_few_sessions_stops():
    with pytest.raises(sf.SelectionError):
        sf.draw(pool(n_users=5), n=18)  # 5 users x 2 = 10 at most


def test_repo_straddle_is_reported_not_hidden():
    pairs = [{"session_id": f"x{i:02d}", "user_key": f"u{i}", "repo_id": f"r{i // 2}", "pilot_id": f"s{i + 1:03d}"}
             for i in range(18)]
    # Components are repo pairs (size 2): dev = three whole pairs.
    labels, straddle = sf.split(pairs)
    assert labels.count("dev") == 6 and straddle == []
    odd = [{"session_id": f"y{i:02d}", "user_key": f"u{i}", "repo_id": "big" if i < 7 else f"r{i}",
            "pilot_id": f"s{i + 1:03d}"} for i in range(18)]
    # The 7-session repo component cannot be in dev (6), so dev comes from singletons; still clean.
    labels, straddle = sf.split(odd)
    assert labels.count("dev") == 6 and straddle == []
    chain = [{"session_id": f"z{i:02d}", "user_key": f"u{i // 2}", "repo_id": f"r{(i + 1) // 2}",
              "pilot_id": f"s{i + 1:03d}"} for i in range(18)]
    # One component of 18 linked by alternating users and repos: fallback to user grouping.
    labels, straddle = sf.split(chain)
    assert labels.count("dev") == 6 and straddle != []
    users = {lab: {r["user_key"] for r, l2 in zip(chain, labels) if l2 == lab} for lab in ("dev", "test")}
    assert not users["dev"] & users["test"]


def test_load_split(tmp_path):
    p = tmp_path / "splits.json"
    p.write_text(json.dumps({"sessions": [{"pilot_id": "s002", "split": "dev"}, {"pilot_id": "s001", "split": "test"},
                                          {"pilot_id": "s003", "split": "dev"}]}))
    assert [r["pilot_id"] for r in load_split("dev", p)] == ["s002", "s003"]
    assert [r["pilot_id"] for r in load_split("test", p)] == ["s001"]
    with pytest.raises(ValueError):
        load_split("all", p)
