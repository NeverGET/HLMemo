"""Deterministic grounding checker of the ceiling experiment (PLAN §1 provenance, §3).

An arm's output for one packet is split into gradeable UNITS (``units_of``):

* E1: one ``fact`` unit per derived fact, and one ``supersede`` unit (``hiding``) per proposed
  supersession (it would hide or close an existing item);
* E2: one ``card_line`` unit per line of "now"/"decisions", one ``merge`` unit (``hiding``) per merge
  proposal, and one ``card`` unit per card (the E2 coverage check against the must-know facts);
* E3: one ``experience`` unit per cross-project experience;
* E4: one ``lesson`` unit per packet that did not abstain (checked by ``al_e4.check_lesson``: quotes
  inside one evidence quote of the cited episode, group/recurrence/date recount, cross-project rule).

``check_unit`` then verifies, without any model:

* every quote appears VERBATIM (whitespace-normalised, case-sensitive) in the text of the source it
  cites, as the model saw it (the packet); quotes of 12 to 300 characters;
* every cited handle is a quotable source of the packet (a context-only source, e.g. the previous
  card, or an unknown handle is flagged);
* invented references: decision ids and commit hashes that occur in no packet text (the capture
  validator's ``ungrounded_refs``), and item handles named in free text that the packet does not
  have;
* the job rules: E1 facts quote only the subject note's Decisions lines (never its Uncertain lines),
  supersession targets are listed candidates and quote the outdated statement verbatim; E2 merges
  quote every involved item; E3 lines quote at least two lessons of at least two projects.

``grounding_flags`` decide the deterministic grounded bit; ``structure_flags`` (E2: missing Open
section or as-of date, a card over the token budget) are reported, never part of grounding.
"""

from __future__ import annotations

import re
from typing import Any

import al_common as C

MIN_QUOTE = 12
MAX_QUOTE = 300
_WS = re.compile(r"\s+")
_ELLIPSIS = re.compile(r"\s*(?:…|\.\.\.)\s*")
_HANDLE = re.compile(r"(?<![\w/.-])([vx]\d{1,9})(?![\w])")
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: flags that make a unit not deterministically grounded
GROUNDING_FLAGS = frozenset(
    {
        "no_evidence",
        "unknown_source",
        "cites_context",
        "quote_too_short",
        "quote_not_verbatim",
        "invented_ref",
        "invented_handle",
        "not_from_subject",
        "outside_decisions",
        "from_uncertain",
        "target_not_candidate",
        "target_quote_not_verbatim",
        "duplicate_not_candidate",
        "merge_unknown_item",
        "merge_under_evidenced",
        "under_evidenced",
        "invented_version",
        "group_count_mismatch",
        "recurrence_mismatch",
        "dates_mismatch",
        "era_mismatch",
        "status_unsupported",
    }
)


def norm_ws(s: str) -> str:
    return _WS.sub(" ", s or "").strip()


def quote_status(quote: str, text: str) -> str:
    """``verbatim`` | ``too_short`` | ``near`` (only case/edge punctuation differ) | ``elided`` (the
    quote skips text with an ellipsis) | ``absent``. Only ``verbatim`` is grounded."""
    q, t = norm_ws(quote), norm_ws(text)
    if len(q) < MIN_QUOTE:
        return "too_short"
    if q in t:
        return "verbatim"
    ql, tl = q.lower(), t.lower()
    if ql.strip(" .…\"'") and ql.strip(" .…\"'") in tl:
        return "near"
    parts = [p for p in _ELLIPSIS.split(ql) if p.strip()]
    if len(parts) > 1 and all(p in tl for p in parts):
        return "elided"
    return "absent"


# --------------------------------------------------------------------------- packet index
class PacketIndex:
    """Fast lookups over one packet: sources by handle, the texts the model saw, E1 sections."""

    def __init__(self, packet: dict[str, Any]) -> None:
        self.packet = packet
        self.exp = packet["exp"]
        self.sources = {s["handle"]: s for s in packet.get("sources") or []}
        ctx = packet.get("context") or {}
        self.context_handles = {h for h in [ctx.get("previous_card_handle")] if h}
        self.context_handles |= {h for h, s in self.sources.items() if not s.get("quotable", True)}
        self.subject = ctx.get("subject")
        self.decisions_text = "\n".join(ctx.get("decisions") or [])
        self.uncertain_text = "\n".join(ctx.get("uncertain") or [])
        texts = [s.get("title", "") + "\n" + s.get("text", "") for s in self.sources.values()]
        texts += [str(ctx.get("previous_card") or ""), str(ctx.get("memory_map") or "")]
        self.all_text = "\n".join(texts)
        self.candidates = {h for h, s in self.sources.items() if s.get("role") == "candidate"}

    def project_of(self, hdl: str) -> str | None:
        s = self.sources.get(hdl)
        return None if s is None else s.get("project")


def invented_refs(text: str, idx: PacketIndex) -> tuple[list[str], list[str]]:
    """``(refs, handles)``: decision ids / commit hashes in ``text`` that no packet text contains
    (the capture validator's rule), and item handles in ``text`` that are neither packet handles nor
    present in any packet text."""
    from hlmemo.capture.summarize import ungrounded_refs

    refs = ungrounded_refs(text, idx.all_text)
    handles = sorted(
        {
            h
            for h in _HANDLE.findall(text)
            if h not in idx.sources and h not in idx.context_handles and h not in idx.all_text
        }
    )
    return refs, handles


# --------------------------------------------------------------------------- units
def _claims(value: Any) -> list[dict[str, Any]]:
    return [c for c in value or [] if isinstance(c, dict)]


def units_of(exp: str, packet_id: str, output: dict[str, Any]) -> list[dict[str, Any]]:
    """The gradeable units of one schema-valid output (module doc)."""
    units: list[dict[str, Any]] = []
    if exp == "E1":
        for i, fact in enumerate(output.get("facts") or [], start=1):
            base = {
                "title": fact.get("title", ""),
                "claims": _claims(fact.get("claims")),
                "as_of": fact.get("as_of", ""),
                "open_unknown": list(fact.get("open_unknown") or []),
                "duplicate_of": list(fact.get("duplicate_of") or []),
            }
            units.append({"uid": f"{packet_id}#f{i}", "type": "fact", "hiding": False, "content": base})
            for j, sup in enumerate(fact.get("supersedes") or [], start=1):
                units.append(
                    {
                        "uid": f"{packet_id}#f{i}.s{j}",
                        "type": "supersede",
                        "hiding": True,
                        "content": {
                            "fact": base,
                            "target": sup.get("target", ""),
                            "target_quote": sup.get("target_quote", ""),
                            "reason": sup.get("reason", ""),
                            "evidence": list(sup.get("evidence") or []),
                        },
                    }
                )
    elif exp == "E2":
        card = output.get("card") or {}
        n = 0
        for section in ("now", "decisions"):
            for claim in _claims(card.get(section)):
                n += 1
                units.append(
                    {
                        "uid": f"{packet_id}#c{n}",
                        "type": "card_line",
                        "hiding": False,
                        "content": {"section": section, **claim},
                    }
                )
        if not output.get("abstain"):
            units.append({"uid": f"{packet_id}#card", "type": "card", "hiding": False, "content": card})
        for i, m in enumerate(output.get("merges") or [], start=1):
            units.append({"uid": f"{packet_id}#m{i}", "type": "merge", "hiding": True, "content": dict(m)})
    elif exp == "E3":
        for i, x in enumerate(output.get("experiences") or [], start=1):
            units.append(
                {"uid": f"{packet_id}#x{i}", "type": "experience", "hiding": False, "content": dict(x)}
            )
    return units


def render_card(card: dict[str, Any]) -> str:
    """The card as an agent would read it (the token budget is measured on this)."""
    lines = ["Now:"]
    lines += [f"- {c.get('text', '')}" for c in _claims(card.get("now"))]
    lines.append("Decisions:")
    lines += [f"- {c.get('text', '')}" for c in _claims(card.get("decisions"))]
    lines.append("Open & unknown:")
    lines += [f"- {x}" for x in card.get("open_unknown") or []]
    lines.append(f"As of: {card.get('as_of', '')}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- checks
def _evidence(ev_list: Any, idx: PacketIndex, flags: set[str], *, e1_fact: bool = False) -> list[dict]:
    out = []
    for ev in ev_list or []:
        if not isinstance(ev, dict):
            continue
        src, quote = str(ev.get("source", "")).strip(), str(ev.get("quote", ""))
        row: dict[str, Any] = {"source": src, "quote": quote}
        if src in idx.context_handles:
            flags.add("cites_context")
            row["status"] = "context"
        elif src not in idx.sources:
            flags.add("unknown_source")
            row["status"] = "unknown_source"
        else:
            st = quote_status(quote, idx.sources[src]["text"])
            if st != "verbatim" and quote_status(quote, idx.sources[src].get("title", "")) == "verbatim":
                st = "verbatim"  # a title is part of what the model saw of the source
            row["status"] = st
            if st == "too_short":
                flags.add("quote_too_short")
            elif st != "verbatim":
                flags.add("quote_not_verbatim")
            if len(norm_ws(quote)) > MAX_QUOTE:
                row["long"] = True
            if e1_fact and st == "verbatim":
                if src != idx.subject:
                    flags.add("not_from_subject")
                elif quote_status(quote, idx.decisions_text) != "verbatim":
                    in_unc = quote_status(quote, idx.uncertain_text) == "verbatim"
                    flags.add("from_uncertain" if in_unc else "outside_decisions")
            elif e1_fact and src != idx.subject:
                flags.add("not_from_subject")
        out.append(row)
    return out


def _check_claims(claims: list[dict[str, Any]], idx: PacketIndex, flags: set[str], **kw: Any) -> list[dict]:
    checked = []
    for c in claims:
        ev = c.get("evidence") or []
        if not ev:
            flags.add("no_evidence")
        checked.append({"text": c.get("text", ""), "evidence": _evidence(ev, idx, flags, **kw)})
    return checked


def _free_text(unit: dict[str, Any]) -> str:
    """Every model-written text of a unit except the quotes (invented references are looked for
    here; a quote is checked against its source instead)."""
    parts: list[str] = []

    def walk(v: Any, key: str = "") -> None:
        if key in ("quote", "target_quote", "source", "target", "keep", "absorb", "duplicate_of"):
            return
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(x, k)
        elif isinstance(v, list):
            for x in v:
                walk(x, key)

    walk(unit.get("content"))
    return "\n".join(parts)


def check_unit(unit: dict[str, Any], idx: PacketIndex, *, card_tokens: int = 512) -> dict[str, Any]:
    flags: set[str] = set()
    structure: set[str] = set()
    t, c = unit["type"], unit["content"]
    checked: list[dict[str, Any]] = []
    if t == "fact":
        checked = _check_claims(c.get("claims") or [], idx, flags, e1_fact=True)
        if not c.get("claims"):
            flags.add("no_evidence")
        if any(h not in idx.candidates for h in c.get("duplicate_of") or []):
            flags.add("duplicate_not_candidate")
    elif t == "supersede":
        checked = [
            {"text": c.get("reason", ""), "evidence": _evidence(c.get("evidence"), idx, flags, e1_fact=True)}
        ]
        if not c.get("evidence"):
            flags.add("no_evidence")
        target = str(c.get("target", ""))
        if target not in idx.candidates:
            flags.add("target_not_candidate")
        elif quote_status(str(c.get("target_quote", "")), idx.sources[target]["text"]) != "verbatim":
            flags.add("target_quote_not_verbatim")
    elif t == "card_line":
        checked = _check_claims([c], idx, flags)
    elif t == "card":
        checked = []
        if not [x for x in c.get("open_unknown") or [] if str(x).strip()]:
            structure.add("missing_open_section")
        if not _ISO_DAY.match(str(c.get("as_of", "")).strip()):
            structure.add("missing_as_of")
        try:
            from hlmemo.core.budget import Meter

            if Meter().count_text(render_card(c)) > card_tokens:
                structure.add("over_budget")
        except Exception:  # noqa: BLE001 - the meter is optional here (no tokenizer: skip the budget)
            structure.add("budget_unchecked")
    elif t == "merge":
        involved = [str(c.get("keep", "")), *(str(x) for x in c.get("absorb") or [])]
        if any(h not in idx.sources or h in idx.context_handles for h in involved):
            flags.add("merge_unknown_item")
        ev = _evidence(c.get("evidence"), idx, flags)
        checked = [{"text": c.get("reason", ""), "evidence": ev}]
        if not ev:
            flags.add("no_evidence")
        grounded = {e["source"] for e in ev if e.get("status") == "verbatim"}
        if not set(involved) <= grounded:
            flags.add("merge_under_evidenced")
    elif t == "experience":
        lines = [*(c.get("when") or []), *(c.get("do") or []), *(c.get("avoid") or [])]
        if not lines:
            flags.add("no_evidence")
        for claim in _claims(lines):
            row = _check_claims([claim], idx, flags)[0]
            ok = [e for e in row["evidence"] if e.get("status") == "verbatim"]
            projects = {idx.project_of(e["source"]) for e in ok}
            if len({e["source"] for e in ok}) < 2 or len(projects) < 2:
                flags.add("under_evidenced")
                row["under_evidenced"] = True
            checked.append(row)
    refs, handles = invented_refs(_free_text(unit), idx)
    if refs:
        flags.add("invented_ref")
    if handles:
        flags.add("invented_handle")
    grounding = sorted(flags & GROUNDING_FLAGS)
    return {
        "uid": unit["uid"],
        "type": t,
        "hiding": unit["hiding"],
        "grounded_det": not grounding,
        "grounding_flags": grounding,
        "structure_flags": sorted(structure),
        "invented": {"refs": refs, "handles": handles},
        "claims": checked,
    }


def check_output(packet: dict[str, Any], output: dict[str, Any], *, card_tokens: int = 512) -> list[dict]:
    if packet["exp"] in C.LESSON_EXPS:
        import al_e4

        return [
            {**unit, "check": al_e4.check_lesson(unit, packet)}
            for unit in al_e4.lesson_units(packet["packet_id"], output)
        ]
    idx = PacketIndex(packet)
    return [
        {**unit, "check": check_unit(unit, idx, card_tokens=card_tokens)}
        for unit in units_of(packet["exp"], packet["packet_id"], output)
    ]


def check_run(exp: str, run_label: str, packets: list[dict[str, Any]], card_tokens: int = 512) -> dict:
    """Check every saved output of one arm run (``outputs/<exp>/<run>/<packet>.json``)."""
    out_dir = C.pdir("outputs", exp, run_label)
    result: dict[str, Any] = {"exp": exp, "run": run_label, "packets": {}}
    totals = {"packets": 0, "failed": 0, "abstained": 0, "units": 0, "grounded_det": 0}
    for pk in packets:
        f = out_dir / f"{pk['packet_id']}.json"
        totals["packets"] += 1
        if not f.is_file():
            result["packets"][pk["packet_id"]] = {"status": "missing", "units": []}
            totals["failed"] += 1
            continue
        rec = C.read_json(f)
        if rec.get("status") != "ok" or not isinstance(rec.get("output"), dict):
            result["packets"][pk["packet_id"]] = {"status": rec.get("status", "failed"), "units": []}
            totals["failed"] += 1
            continue
        units = check_output(pk, rec["output"], card_tokens=card_tokens)
        abstained = bool(rec["output"].get("abstain"))
        totals["abstained"] += int(abstained)
        totals["units"] += sum(1 for u in units if u["type"] != "card")
        totals["grounded_det"] += sum(1 for u in units if u["type"] != "card" and u["check"]["grounded_det"])
        result["packets"][pk["packet_id"]] = {"status": "ok", "abstain": abstained, "units": units}
    result["totals"] = totals
    return result


__all__ = [
    "GROUNDING_FLAGS",
    "MAX_QUOTE",
    "MIN_QUOTE",
    "PacketIndex",
    "check_output",
    "check_run",
    "check_unit",
    "invented_refs",
    "norm_ws",
    "quote_status",
    "render_card",
    "units_of",
]
