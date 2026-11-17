"""Classify the items of a needs record against a live context (PLAN.md 7 Phase 2, 4.3).

Items: each need, each current_state item, and each side (old, new) of each superseded pair.
Classes, in order:
  present      code: every source message is in the context verbatim and the anchor is found in
               one of them (a superseded side has no anchor: its message is verbatim).
  captured     the check call says the notes preserve the content.
  recoverable  the check call says the notes give a cue AND, in code, a pointer covers every
               source message that is not verbatim. A pointer covers a message when the message
               is among a stub's ids or is the source or supersedes id of a state line.
  lost         otherwise.
One check call (JUDGE_MODEL, fresh context) per cut point and policy setting, holding only the
items that are not present and the context's notes in the plain format of `notes`. No call when
every item is present, or when the context has no note text at all (only bare stubs, or no
notes): then every item that is not present is lost, decided in code.

Plain format (policy-neutral, the same for every policy): one line per note, state lines first
(in state-block order), then stubs in context order: "<text> [points to: <ids>]". State text is
"<key>: <value>"; stub text is the policy's own summary/stub text (context.stub_body) with the
header ("[folded … · thread … · resolved]") and the recall() pointer dropped; a bare stub is the
pointer alone. No policy name, label or layout. Item ids are i1, i2, ... in record order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ctxtrim.context import LiveContext, Stub
from ctxtrim.eval import common
from ctxtrim.log import Entry, approx_tokens

PROMPT_PATH = Path(__file__).with_name("check_prompt.md")
CLASSES = ("present", "captured", "recoverable", "lost")


@dataclass(frozen=True)
class Item:
    ref: str                       # "need:<need_id>", "state:<n>", "sup:<n>:old", "sup:<n>:new"
    kind: str                      # need | state | old | new
    text: str                      # what the check is shown
    source_ids: tuple[str, ...]
    anchor: str | None


def items(record: dict) -> list[Item]:
    out = [Item(f"need:{x['need_id']}", "need", x["what"], tuple(x["source_ids"]), x["anchor"])
           for x in record["needs"]]
    out += [Item(f"state:{n}", "state", f"Current {x['key']}: {x['what']}", tuple(x["source_ids"]), x["anchor"])
            for n, x in enumerate(record["current_state"])]
    for n, x in enumerate(record["superseded"]):
        out.append(Item(f"sup:{n}:old", "old", f"Earlier side of: {x['what']}", (x["old"],), None))
        out.append(Item(f"sup:{n}:new", "new", f"Later side of: {x['what']}", (x["new"],), None))
    return out


def is_present(item: Item, ctx: LiveContext) -> bool:
    verbatim = {i.id: i for i in ctx.items if isinstance(i, Entry)}
    if not all(s in verbatim for s in item.source_ids):
        return False
    return item.anchor is None or any(item.anchor in verbatim[s].content for s in item.source_ids)


def pointed_ids(ctx: LiveContext) -> set[str]:
    return set(ctx.covered_by()) | set(ctx.state_sources())


def pointer_covers(item: Item, ctx: LiveContext) -> bool:
    verbatim = set(ctx.verbatim_ids())
    pointed = pointed_ids(ctx)
    return all(s in verbatim or s in pointed for s in item.source_ids)


def notes(ctx: LiveContext) -> list[str]:
    out = []
    for u in ctx.state:
        ptr = ", ".join(i for i in (u.source, u.supersedes) if i)
        out.append(f"{u.key}: {u.value} [points to: {ptr}]")
    for s in ctx.items:
        if isinstance(s, Stub):
            ptr = f"[points to: {', '.join(s.message_ids)}]"
            out.append(f"{s.body} {ptr}" if s.body.strip() else ptr)
    return out


def has_note_text(ctx: LiveContext) -> bool:
    return bool(ctx.state) or any(isinstance(s, Stub) and s.body.strip() for s in ctx.items)


@dataclass
class Prepared:
    """Everything decided in code, plus the check prompt when a call is needed."""
    items: list[Item]
    classes: dict[str, str]                    # ref -> class, for items already decided
    pending: list[Item] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    prompt: str | None = None
    system: str = ""

    def approx_tokens(self) -> int:
        return approx_tokens(self.system) + approx_tokens(self.prompt) if self.prompt else 0


def prepare(record: dict, ctx: LiveContext) -> Prepared:
    its = items(record)
    p = Prepared(its, {i.ref: "present" for i in its if is_present(i, ctx)})
    rest = [i for i in its if i.ref not in p.classes]
    if not rest or not has_note_text(ctx):
        p.classes.update({i.ref: "lost" for i in rest})
        return p
    p.pending, p.notes = rest, notes(ctx)
    p.system, template = common.load_prompt_file(PROMPT_PATH)
    p.prompt = common.fill(template, NOTES="\n".join(f"N{n}. {x}" for n, x in enumerate(p.notes, 1)),
                           ITEMS="\n".join(f"i{n}. {i.text}" for n, i in enumerate(rest, 1)))
    return p


def validator(n_items: int):
    want = [f"i{n}" for n in range(1, n_items + 1)]

    def validate(text: str) -> dict[str, dict]:
        obj = common.parse_json_object(text)
        if set(obj) != {"items"} or not isinstance(obj["items"], list):
            raise ValueError('top level must be {"items": [...]} and nothing else')
        got = {}
        for n, x in enumerate(obj["items"]):
            if not isinstance(x, dict) or set(x) != {"item_id", "captured", "cue"}:
                raise ValueError(f"items[{n}]: keys must be exactly item_id, captured, cue")
            if not (isinstance(x["captured"], bool) and isinstance(x["cue"], bool)):
                raise ValueError(f"items[{n}]: captured and cue must be true or false")
            if x["item_id"] in got:
                raise ValueError(f"items[{n}]: {x['item_id']!r} answered twice")
            got[x["item_id"]] = x
        if sorted(got) != sorted(want):
            raise ValueError(f"answer exactly the items {want}; got {sorted(got)}")
        return got
    return validate


def decide(p: Prepared, ctx: LiveContext, **call_kw) -> dict:
    """Make the check call if one is needed; return {"classes", "calls", "decisions", "model", "date",
    "attempts", "errors"}. decisions: per pending item, its ref, text and the two answers."""
    if p.prompt is None:
        return {"classes": dict(p.classes), "calls": 0, "decisions": [], "model": None, "date": None,
                "attempts": 0, "errors": []}
    r = common.call_validated(p.prompt, p.system, "check", validator(len(p.pending)), **call_kw)
    if r["value"] is None:
        raise RuntimeError(f"check call invalid after {r['attempts']} attempts: {r['errors']}")
    classes, decisions = dict(p.classes), []
    for n, item in enumerate(p.pending, 1):
        a = r["value"][f"i{n}"]
        if a["captured"]:
            classes[item.ref] = "captured"
        elif a["cue"] and pointer_covers(item, ctx):
            classes[item.ref] = "recoverable"
        else:
            classes[item.ref] = "lost"
        decisions.append({"item_id": f"i{n}", "ref": item.ref, "text": item.text, "captured": a["captured"],
                          "cue": a["cue"], "pointer_covers": pointer_covers(item, ctx), "class": classes[item.ref]})
    return {"classes": classes, "calls": r["attempts"], "decisions": decisions, "model": r["model"],
            "date": r["date"], "attempts": r["attempts"], "errors": r["errors"]}
