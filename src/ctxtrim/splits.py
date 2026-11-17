"""The dev/test split (PLAN.md 7 Phase 0 step 5). `load_split` is the single way to list the
sessions of a split. The test split is untouched until the final run (PLAN.md Phase 4 FINAL RUN).
"""

from __future__ import annotations

import json
from pathlib import Path

from ctxtrim import config

SPLITS = ("dev", "test")


def load_split(name: str, path: str | Path | None = None) -> list[dict]:
    """Rows of data/splits.json for one split ("dev" or "test"), in pilot-id order."""
    if name not in SPLITS:
        raise ValueError(f"unknown split {name!r}; expected one of {SPLITS}")
    rows = json.loads(Path(path or config.SPLITS_PATH).read_text())["sessions"]
    return sorted((r for r in rows if r["split"] == name), key=lambda r: r["pilot_id"])
