"""Tests for the eligibility and candidate-ranking rule in scripts/stats.py (synthetic rows only)."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("stats_script", Path(__file__).resolve().parents[1] / "scripts" / "stats.py")
stats = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stats)


def row(sid, user, repo, conv, share, plain=30, forks=0):
    return {"session_id": sid, "user_key": user, "repo_id": repo, "conv_tokens": conv,
            "conv_share_after": share, "user_turns_plain": plain, "fork_points": forks}


def test_eligibility_uses_plain_turns_and_forks():
    assert stats.eligible(row("a", "u", "r", 1, 0.5))
    assert not stats.eligible(row("a", "u", "r", 1, 0.5, plain=24))
    assert not stats.eligible(row("a", "u", "r", 1, 0.5, forks=1))


def test_rank_prefers_both_tokens_and_share_and_respects_cap():
    rows = [row("big_high", "u1", "r1", 900, 0.9), row("big_low", "u2", "r2", 800, 0.2),
            row("small_high", "u3", "r3", 100, 0.95), row("mid", "u4", "r4", 500, 0.6),
            row("u1_second", "u1", "r5", 850, 0.85), row("u1_third", "u1", "r6", 700, 0.8),
            row("forked", "u5", "r7", 1000, 0.99, forks=2)]
    out = [r["session_id"] for r in stats.rank_candidates(rows, cap=10)]
    assert out[0] == "big_high"
    assert "forked" not in out
    assert "u1_third" not in out  # third session of user u1 is never taken
    # scores: big_high .9, u1_second .7, small_high .5, u1_third .4 (capped), big_low .3, mid .2
    assert out == ["big_high", "u1_second", "small_high", "big_low", "mid"]
    assert stats.rank_candidates(rows, cap=2) == stats.rank_candidates(rows, cap=10)[:2]
