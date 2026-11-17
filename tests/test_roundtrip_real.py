"""Round-trip test over every downloaded and normalized SWE-chat session (Phase 0 acceptance).

Skipped when no data is present (data is never committed). Run after scripts/normalize.py.
"""

import json

import pytest

from ctxtrim import config
from roundtrip import check_session

_SKIPPED_PATH = config.DATA_DIR / "selection" / "format_skipped.json"
_SKIPPED = json.loads(_SKIPPED_PATH.read_text()) if _SKIPPED_PATH.exists() else {}
# Sessions whose transcript is not in Claude Code format are not normalized (scripts/normalize.py).
SESSIONS = ([s for s in json.loads(config.SELECTION_PATH.read_text())["sessions"] if s["session_id"] not in _SKIPPED]
            if config.SELECTION_PATH.exists() else [])
PRESENT = [s for s in SESSIONS if (config.NORMALIZED_DIR / s["session_id"] / "log.jsonl").exists()]


@pytest.mark.skipif(not PRESENT, reason="no normalized sessions on this machine")
def test_all_sampled_sessions_are_normalized():
    assert len(PRESENT) == len(SESSIONS)


@pytest.mark.skipif(not PRESENT or not config.SPLITS_PATH.exists(), reason="no split or no normalized sessions")
def test_round_trip_covers_every_selected_session():
    from ctxtrim.log import Log
    from ctxtrim.splits import load_split
    selected = load_split("dev") + load_split("test")
    assert len(selected) == 18
    assert {s["session_id"] for s in selected} <= {s["session_id"] for s in PRESENT}
    for s in selected:
        assert Log(config.NORMALIZED_DIR / s["session_id"] / "log.jsonl").entries()


@pytest.mark.parametrize("s", PRESENT, ids=lambda s: s["session_id"][:8])
def test_no_content_lost(s):
    problems = check_session(config.SWE_CHAT_DIR / s["transcript_path"], config.NORMALIZED_DIR / s["session_id"])
    assert problems == [], problems[:10]
