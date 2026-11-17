"""Download the raw transcripts listed in a selection file (default data/selection/sample.json).

Usage: python -I scripts/download_transcripts.py [selection.json]
Files land in data/sessions/swe_chat/<transcript_path>, at the pinned revision, and are
never modified afterwards. Each file's size is checked against the selection record.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "sessions" / "swe_chat"
REPO_ID = "SALT-NLP/SWE-chat"


def main(argv: list[str]) -> int:
    sel = json.loads(Path(argv[1] if len(argv) > 1 else ROOT / "data" / "selection" / "sample.json").read_text())
    bad = 0
    for i, s in enumerate(sel["sessions"], 1):
        path = Path(hf_hub_download(REPO_ID, s["transcript_path"], repo_type="dataset",
                                    revision=sel["revision"], local_dir=OUT_DIR))
        if path.stat().st_size != s["bytes"]:
            print("size mismatch", s["session_id"], file=sys.stderr)
            bad += 1
        print(f"{i}/{len(sel['sessions'])} {s['session_id']} {s['bytes']}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
