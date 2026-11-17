"""Human review packets for Phase 2 (PLAN.md 7 Phase 2 "Human review"). No model calls.

  python -I scripts/make_review_packets.py needs
      10 of the dev cut points (seeded choice, SEED, from the sorted cut ids), in that order.
      The first 3 are BLIND: the packet shows the continuation, then the prefix, then a blank
      form; the model's record is in a separate file to open only afterwards. The other 7 show
      a short "How to review", the continuation, then the model's record one item at a time
      (the claim, an excerpt of each source message with the anchor marked, a right/wrong box),
      a place for missing items, and the full prefix as a reference appendix at the end.
      The OPEN_AFTER record file has the same per-item layout. Needs records must exist
      (python -m ctxtrim.eval.needs --run).
  python -I scripts/make_review_packets.py checks FILE [FILE ...]
      15 check decisions (seeded choice) from data/labels/private/checks/*.jsonl files written
      by run_policy, with the notes the judge saw, the item, and its two answers to mark.

Everything is written under data/review/ (git-ignored: it holds session text). The choice of
cut points is also written there (selection.json) and printed.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

from ctxtrim import config
from ctxtrim.eval import needs
from ctxtrim.eval.cuts import continuation, prefix, split_cuts
from ctxtrim.log import Entry, pilot_entries
from ctxtrim.views import conversation_view

SEED = 20261116
N_NEEDS, N_BLIND, N_CHECKS = 10, 3, 15
FENCE = "~~~~~~~~"
EXCERPT_FULL_CHARS = 1500   # a source message up to this long is shown whole
EXCERPT_WINDOW_CHARS = 600  # else this many chars each side of the anchor


def choose(ids: list[str], n: int) -> list[str]:
    return random.Random(SEED).sample(sorted(ids), min(n, len(ids)))


def continuation_md(cut, entries, k) -> str:
    cont, _ = conversation_view(continuation(entries, cut, k))
    return (f"## Continuation (starts with the request {cut.cut_before}; next {k} user turns)\n\n"
            f"{FENCE}\n{cont}\n{FENCE}\n")


def prefix_md(cut, entries) -> str:
    pre, _ = conversation_view(prefix(entries, cut))
    return f"{FENCE}\n{pre}\n{FENCE}\n"


def omitted(n: int) -> str:
    return f"[... {n} chars omitted ...]"


def excerpt(e: Entry, anchor: str | None = None) -> str:
    """One message for the record: header (id, kind), then the whole content if it is at most
    EXCERPT_FULL_CHARS; else EXCERPT_WINDOW_CHARS each side of the anchor's first occurrence, or
    the first EXCERPT_FULL_CHARS when the anchor is not in this message. Cut sides get an omitted
    marker; the anchor is shown as >>>anchor<<<. Fenced; the text is only printed."""
    c = e.content
    has = bool(anchor) and anchor in c
    a, b = 0, len(c)
    if len(c) > EXCERPT_FULL_CHARS:
        if has:
            i = c.index(anchor)
            a, b = max(0, i - EXCERPT_WINDOW_CHARS), min(len(c), i + len(anchor) + EXCERPT_WINDOW_CHARS)
        else:
            b = EXCERPT_FULL_CHARS
    text = c[a:b].replace(anchor, f">>>{anchor}<<<") if has else c[a:b]
    text = (omitted(a) + "\n" if a else "") + text + ("\n" + omitted(len(c) - b) if b < len(c) else "")
    return f"{FENCE}\n[{e.id}] {e.kind.upper()}:\n{text}\n{FENCE}\n"


def record_md(rec: dict, by_id: dict[str, Entry]) -> str:
    """The model's record, one item at a time: claim, excerpts of its source messages, a box."""
    box = "- [ ] right  [ ] wrong  note:\n"

    def show(i, anchor=None):
        return excerpt(by_id[i], anchor) if i in by_id else f"(message {i} not found)\n"

    out = ["## Needs (things from the prefix the continuation uses)\n"]
    for x in rec["needs"]:
        out.append(f"### {x['need_id']}: {x['what']}\n\nsources {', '.join(x['source_ids'])};"
                   f" used by {x['used_by']}\n")
        out += [show(i, x["anchor"]) for i in x["source_ids"]] + [box]
    out.append("## Current state at the cut\n")
    for x in rec["current_state"]:
        out.append(f"### {x['key']}: {x['what']}\n\nsources {', '.join(x['source_ids'])}\n")
        out += [show(i, x["anchor"]) for i in x["source_ids"]] + [box]
    out.append("## Superseded before the cut\n")
    for x in rec["superseded"]:
        out.append(f"### {x['old']} -> {x['new']}: {x['what']}\n\nold:\n")
        out += [show(x["old"]), "new:\n", show(x["new"]), box]
    out.append("## Missing (anything the record should contain and does not)\n\n- \n\n"
               "## Would any change alter a verdict? (agreed = no)\n\n- [ ] agreed  [ ] not agreed\n")
    return "\n".join(out)


HOW_TO = """## How to review

Read the continuation first. Then, for each item of the model's record, check the claim against
the excerpt under it and tick right or wrong. In an excerpt, >>>text<<< marks the item's anchor
(the exact words the model quoted), and [... N chars omitted ...] marks message text left out.
The full prefix is at the end, for reference and for finding missing items. The cut point is
agreed when you would change nothing that could alter a verdict.
"""


BLIND_FORM = """## Your list (write before opening the model's record)

Needs: what from the prefix does the continuation use or depend on? (message ids if you can)

-

Current state at the cut (goal, constraints, decisions, plan; latest value only):

-

Superseded before the cut (old id -> new id, what):

-
"""


def needs_packets(out_dir: Path) -> int:
    k = config.require("NEEDS_CONTINUATION_TURNS")
    cuts, _ = split_cuts("dev")
    records = needs.load_records()
    by_id = {c.cut_id: (c, es) for c, es in cuts}
    missing = [c for c in by_id if records.get(c, {}).get("meta", {}).get("status") != "ok"]
    if missing:
        print(f"no valid needs record for {missing}; run the needs calls first", file=sys.stderr)
        return 1
    chosen = choose(list(by_id), N_NEEDS)
    d = out_dir / "needs"
    d.mkdir(parents=True, exist_ok=True)
    for n, cid in enumerate(chosen, 1):
        cut, es = by_id[cid]
        head = f"# Needs review {n:02d}: {cid} (cut before {cut.cut_before})\n\n"
        msgs = {e.id: e for e in pilot_entries(es)}
        rec = record_md(records[cid], msgs)
        pre = f"(everything before {cut.cut_before})\n\n" + prefix_md(cut, es)
        if n <= N_BLIND:
            (d / f"{n:02d}_{cid}_BLIND.md").write_text(
                head + "BLIND: write your list at the end before opening the model's record file.\n\n"
                + continuation_md(cut, es, k) + "\n## Prefix " + pre + "\n" + BLIND_FORM)
            (d / f"{n:02d}_{cid}_model_record_OPEN_AFTER.md").write_text(
                head + "In an excerpt, >>>text<<< marks the item's anchor and [... N chars omitted ...]"
                " marks message text left out.\n\n" + rec)
        else:
            (d / f"{n:02d}_{cid}.md").write_text(
                head + HOW_TO + "\n" + continuation_md(cut, es, k) + "\n# Model's record\n\n" + rec
                + "\n# Appendix: full prefix (reference only) " + pre)
    (out_dir / "selection.json").write_text(json.dumps({"seed": SEED, "needs": chosen,
                                                        "blind": chosen[:N_BLIND]}, indent=1) + "\n")
    print("needs review cut points (first 3 blind):", chosen)
    return 0


def check_packets(out_dir: Path, files: list[str]) -> int:
    decisions = []
    for f in files:
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            p = json.loads(line)
            decisions += [(Path(f).stem, p["cut_id"], p["notes"], dec) for dec in p["decisions"]]
    if not decisions:
        print("no check decisions in these files", file=sys.stderr)
        return 1
    rng = random.Random(SEED)
    chosen = rng.sample(decisions, min(N_CHECKS, len(decisions)))
    out = ["# Check decisions review\n",
           "For each: do the notes preserve the item (captured)? Do the notes' words point to it (cue)?"
           " Mark whether you agree with the judge.\n"]
    for n, (src, cid, notes, dec) in enumerate(chosen, 1):
        out.append(f"## {n:02d}: {cid} ({src}), item {dec['ref']}\n")
        out.append("Notes the judge saw:\n\n" + FENCE + "\n" + "\n".join(notes) + "\n" + FENCE + "\n")
        out.append(f"Item: {dec['text']}\n\nJudge: captured = {dec['captured']}, cue = {dec['cue']}"
                   f" (pointer covers the source in code: {dec['pointer_covers']}; class {dec['class']})\n")
        out.append("- [ ] agree captured  [ ] disagree\n- [ ] agree cue  [ ] disagree\n- note:\n")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "checks.md").write_text("\n".join(out))
    print("wrote", out_dir / "checks.md", f"({len(chosen)} of {len(decisions)} decisions)")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "needs":
        return needs_packets(config.REVIEW_DIR)
    if len(argv) >= 3 and argv[1] == "checks":
        return check_packets(config.REVIEW_DIR, argv[2:])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
