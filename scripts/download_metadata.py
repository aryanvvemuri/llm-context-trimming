"""Download SWE-chat metadata tables (sessions, repositories) and the dataset card.

Usage: python -I scripts/download_metadata.py
Requires the `data` extra and Hugging Face access to the gated dataset.
Never downloads conversations.parquet or the transcript trees.
"""

from __future__ import annotations

import sys
from pathlib import Path

from huggingface_hub import hf_hub_download

REPO_ID = "SALT-NLP/SWE-chat"
# Pinned dataset revision (commit sha on the Hub, 2026-10-03), so reruns get the same data.
REVISION = "202b071f18e03c79df5a7287565ce2ebc5ee7756"
FILES = ["README.md", "sessions.parquet", "repositories.parquet"]
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "sessions" / "swe_chat"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        path = hf_hub_download(REPO_ID, name, repo_type="dataset", revision=REVISION, local_dir=OUT_DIR)
        print(name, Path(path).stat().st_size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
