"""scripts/make_review_packets.py on synthetic data. No model calls."""

import importlib.util
import json
from pathlib import Path

from ctxtrim.log import make_entry
from test_eval import CUTS, ES, needs_reply

_spec = importlib.util.spec_from_file_location(
    "make_review_packets", Path(__file__).resolve().parents[1] / "scripts" / "make_review_packets.py")
mrp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mrp)


def test_excerpt_short_message_whole():
    out = mrp.excerpt(make_entry("m0007", None, "user", "please use postgres here"), "use postgres")
    assert out.startswith(mrp.FENCE + "\n[m0007] USER:\n") and out.endswith(mrp.FENCE + "\n")
    assert "please >>>use postgres<<< here" in out and "omitted" not in out


def test_excerpt_long_message_window_around_anchor():
    W = mrp.EXCERPT_WINDOW_CHARS
    text = "a" * 2000 + "ANCHOR" + "b" * 3000
    out = mrp.excerpt(make_entry("m0009", None, "assistant", text), "ANCHOR")
    assert "[m0009] ASSISTANT:" in out
    assert f"[... {2000 - W} chars omitted ...]" in out and f"[... {3000 - W} chars omitted ...]" in out
    assert "a" * W + ">>>ANCHOR<<<" + "b" * W + "\n[..." in out and "a" * (W + 1) not in out


def test_excerpt_long_message_without_anchor():
    F = mrp.EXCERPT_FULL_CHARS
    text = "c" * F + "d" * 500
    for anchor in (None, "not here"):
        out = mrp.excerpt(make_entry("m0002", None, "user", text), anchor)
        assert "c" * F + "\n[... 500 chars omitted ...]" in out and "d" not in out.split("omitted")[0]
        assert ">>>" not in out


def make_packets(tmp_path, monkeypatch):
    many = [(c.__class__(**{**c.__dict__, "cut_id": f"s{n:03d}.c1"}), ES) for n, c in
            enumerate(CUTS * 6)]
    recs = {c.cut_id: {**needs_reply(c.cut_before), "meta": {"status": "ok"}} for c, _ in many}
    monkeypatch.setattr(mrp, "split_cuts", lambda split: (many, []))
    monkeypatch.setattr(mrp.needs, "load_records", lambda: recs)
    assert mrp.needs_packets(tmp_path) == 0
    return tmp_path / "needs"


def test_marked_packet_order(tmp_path, monkeypatch):
    text = next(make_packets(tmp_path, monkeypatch).glob("04_*.md")).read_text()
    how, cont, rec = text.index("## How to review"), text.index("## Continuation"), text.index("# Model's record")
    app = text.index("# Appendix: full prefix")
    assert how < cont < rec < text.index("## Missing") < app
    assert ">>>" in text[how:cont]  # the marker is explained
    assert "### n1: storage is postgres" in text[rec:app]
    assert "[m0005] USER:\nactually >>>use postgres<<< instead" in text[rec:app]  # excerpt under the claim
    assert "[m0001] USER:" in text[app:] and "[m0001] USER:" not in text[cont:rec]


def test_blind_packet_has_no_record(tmp_path, monkeypatch):
    d = make_packets(tmp_path, monkeypatch)
    blind = next(d.glob("01_*_BLIND.md")).read_text()
    for s in ("storage is postgres", "sqlite was first", "decision:db", ">>>", "[ ] right"):
        assert s not in blind
    assert blind.index("## Continuation") < blind.index("## Prefix") < blind.index("Your list")
    after = next(d.glob("01_*_OPEN_AFTER.md")).read_text()
    assert "[m0005] USER:\nactually >>>use postgres<<< instead" in after and mrp.FENCE in after
    assert "old:\n" in after and "## Missing" in after and "## Continuation" not in after


def test_needs_packets_blind_first(tmp_path, monkeypatch):
    many = [(c.__class__(**{**c.__dict__, "cut_id": f"s{n:03d}.c1"}), ES) for n, c in
            enumerate(CUTS * 6)]
    recs = {c.cut_id: {**needs_reply(c.cut_before), "meta": {"status": "ok"}} for c, _ in many}
    monkeypatch.setattr(mrp, "split_cuts", lambda split: (many, []))
    monkeypatch.setattr(mrp.needs, "load_records", lambda: recs)
    assert mrp.needs_packets(tmp_path) == 0
    sel = json.loads((tmp_path / "selection.json").read_text())
    assert len(sel["needs"]) == 10 and sel["blind"] == sel["needs"][:3]
    assert sel["needs"] == mrp.choose([c.cut_id for c, _ in many], 10)  # seeded, reproducible
    files = sorted(p.name for p in (tmp_path / "needs").iterdir())
    assert len(files) == 13 and sum("BLIND" in f for f in files) == 3
    blind = next((tmp_path / "needs").glob("01_*_BLIND.md")).read_text()
    assert "storage is postgres" not in blind and "Your list" in blind
    other = next((tmp_path / "needs").glob("04_*.md")).read_text()
    assert "storage is postgres" in other and "[ ] right" in other


def test_check_packets(tmp_path):
    f = tmp_path / "recency-n_turns=1.dev.jsonl"
    dec = {"item_id": "i1", "ref": "need:n2", "text": "sqlite was first", "captured": False, "cue": True,
           "pointer_covers": True, "class": "recoverable"}
    f.write_text("\n".join(json.dumps({"cut_id": f"s{n:03d}.c1", "notes": ["a [points to: m0001]"],
                                       "decisions": [dec, dec]}) for n in range(10)) + "\n")
    assert mrp.check_packets(tmp_path / "out", [str(f)]) == 0
    text = (tmp_path / "out" / "checks.md").read_text()
    assert text.count("## ") == 15 and "captured = False, cue = True" in text
