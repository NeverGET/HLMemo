"""D-067 deterministic guards around every librarian LLM decision (W2b).

Even the ceiling model is wrong a few percent of the time, so no model output is trusted as-is:

1. **Schema + enum validation** — the provider already rejects schema-invalid output (retry once,
   then ``SchemaFail``); here the task-level rules follow.
2. **Reference (clue) checks** — every result must cite an id that was in THIS call's input (the
   candidate set). Unknown ids are dropped (``uncited``), repeated ids keep the first answer
   (``duplicate_id``), missing ids are an abstention (``missing``). A proposal can therefore
   only ever name versions the model was shown.
3. **Evidence checks** — a non-``none`` relation must quote 2+ consecutive words of EACH text
   (``new_quote``/``old_quote``, normalized substring match). An unverifiable quote never blocks
   a question but caps the decision below the action tier (``quote_unverified``).
4. **Temporal consistency** — a supersession must point to the item with the later ``t_valid``
   (or equal: then the newly written one). Otherwise the supersession is dropped
   (``supersedes_against_time``); the contradiction itself stays a question.
5. **Calibrated confidence** — ``TIERS`` maps (relation, model confidence) to ``action`` (may be
   applied by the role ladder's auto rule), ``question`` (owner decides) or ``abstain``. Low
   confidence is NEVER an action. The table is the calibration: it is tuned on the G-LIVE-B
   results (per-confidence precision in ``eval/live/<date>-w2b-<profile>/results.json``).
6. **Self-consistency / verifier** for high-impact proposals (contradicts + supersedes, and
   cross-project widening): an independent second call (``relate_verify``: decomposed questions,
   a different framing, by default the OTHER profile of the chain) must agree before the
   proposal is even raised; ``verifier_agrees`` is the agreement rule. Disagreement downgrades
   (a verified conflict without an agreed direction stays a plain contradiction question) or
   drops the proposal (``verifier_rejected``). Both calls are in the audit record.
7. **First-class abstention** — ``none`` is the prompts' default; a missing, uncited or
   low-confidence link-type answer is an abstention, and abstentions are counted and scored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from hlmemo.core.normalize import normalize

RELATIONS = ("none", "duplicate", "refines", "contradicts")
CONFIDENCES = ("low", "med", "high")
#: (relation, model confidence) -> decision tier. Contradictions are never silently dropped
#: (a real conflict is always worth a question); weak link-type guesses are.
TIERS: dict[tuple[str, str], str] = {
    ("contradicts", "high"): "action",
    ("contradicts", "med"): "question",
    ("contradicts", "low"): "question",
    ("duplicate", "high"): "action",
    ("duplicate", "med"): "question",
    ("duplicate", "low"): "abstain",
    ("refines", "high"): "action",
    ("refines", "med"): "question",
    ("refines", "low"): "abstain",
}
QUOTE_MIN_WORDS = 2
MAX_REASON = 200
_WS = re.compile(r"\s+")
_EDGE_PUNCT = " \t\n\"'`“”„‚‘’«».,;:!?()[]{}…-–—"
_TAG = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,31}$")


def _norm(text: str) -> str:
    return _WS.sub(" ", normalize(text)).strip()


def quote_in(quote: str, *texts: str) -> bool:
    """``quote`` (≥ 2 words after trimming edge punctuation) occurs in one of ``texts`` after
    ``normalize`` + whitespace collapsing (case, accents and line breaks do not matter)."""
    q = _norm(quote).strip(_EDGE_PUNCT)
    if len(q.split(" ")) < QUOTE_MIN_WORDS:
        return False
    return any(q in _norm(t) for t in texts if t)


@dataclass(slots=True)
class PairText:
    """The two sides of one relation judgement as the model saw them."""

    cid: str  # the existing item's id (a clue, e.g. "v12")
    new_text: str
    old_text: str
    new_valid: datetime
    old_valid: datetime


@dataclass(slots=True)
class Judgement:
    cid: str
    relation: str = "none"
    supersedes: str = "none"
    confidence: str = "high"
    tier: str = "abstain"
    reason: str = ""
    new_quote: str = ""
    old_quote: str = ""
    flags: list[str] = field(default_factory=list)
    verification: dict[str, Any] | None = None

    @property
    def raised(self) -> bool:
        return self.relation != "none" and self.tier != "abstain"

    def audit(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.cid,
            "relation": self.relation,
            "supersedes": self.supersedes,
            "confidence": self.confidence,
            "tier": self.tier,
        }
        if self.flags:
            out["flags"] = list(self.flags)
        if self.verification is not None:
            out["verification"] = self.verification
        return out


def _cap(tier: str, at_most: str) -> str:
    order = ("abstain", "question", "action")
    return order[min(order.index(tier), order.index(at_most))]


def check_relations(
    output: dict[str, Any], pairs: list[PairText], *, redact: Any = None
) -> tuple[list[Judgement], dict[str, int]]:
    """Guard one ``relate`` answer. Returns one ``Judgement`` per input pair (input order) and the
    counters ``{uncited, duplicate_id, missing, quote_unverified, supersedes_against_time,
    supersedes_without_contradiction, abstained_low}``."""
    by_id = {p.cid: p for p in pairs}
    counts = dict.fromkeys(
        (
            "uncited",
            "duplicate_id",
            "missing",
            "quote_unverified",
            "supersedes_against_time",
            "supersedes_without_contradiction",
            "abstained_low",
        ),
        0,
    )
    seen: dict[str, Judgement] = {}
    for r in output.get("results") or []:
        if not isinstance(r, dict):
            continue
        cid = str(r.get("id", ""))
        if cid not in by_id:
            counts["uncited"] += 1
            continue
        if cid in seen:
            counts["duplicate_id"] += 1
            continue
        p = by_id[cid]
        rel = r.get("relation") if r.get("relation") in RELATIONS else "none"
        conf = r.get("confidence") if r.get("confidence") in CONFIDENCES else "low"
        sup = r.get("supersedes") if r.get("supersedes") in ("new", "old", "none") else "none"
        reason = str(r.get("reason") or "")
        if redact is not None:
            reason = redact(reason)
        j = Judgement(
            cid,
            relation=rel,
            supersedes=sup,
            confidence=conf,
            reason=reason[:MAX_REASON],
            new_quote=str(r.get("new_quote") or "")[:200],
            old_quote=str(r.get("old_quote") or "")[:200],
        )
        if rel == "none":
            j.supersedes, j.tier = "none", "abstain"
        else:
            j.tier = TIERS[(rel, conf)]
            if j.tier == "abstain":
                counts["abstained_low"] += 1
                j.flags.append("abstained_low")
            if rel != "contradicts" and sup != "none":
                j.supersedes = "none"
                counts["supersedes_without_contradiction"] += 1
                j.flags.append("supersedes_without_contradiction")
            if (j.supersedes == "new" and p.new_valid < p.old_valid) or (
                j.supersedes == "old" and p.old_valid < p.new_valid
            ):
                j.supersedes = "none"
                counts["supersedes_against_time"] += 1
                j.flags.append("supersedes_against_time")
            if not (quote_in(j.new_quote, p.new_text) and quote_in(j.old_quote, p.old_text)):
                counts["quote_unverified"] += 1
                j.flags.append("quote_unverified")
                j.tier = _cap(j.tier, "question")
        seen[cid] = j
    out: list[Judgement] = []
    for p in pairs:
        j = seen.get(p.cid)
        if j is None:
            counts["missing"] += 1
            j = Judgement(p.cid, flags=["missing"])
        out.append(j)
    return out, counts


# --------------------------------------------------------------------------- verifier (D-067 #4)
def high_impact(j: Judgement, *, cross_project: bool) -> str | None:
    """``supersede`` / ``widen`` when the judgement needs the second opinion before it is raised."""
    if not j.raised:
        return None
    if j.relation == "contradicts" and j.supersedes != "none":
        return "supersede"
    if cross_project and j.relation in ("duplicate", "refines"):
        return "widen"
    return None


def verifier_agrees(kind: str, v: dict[str, Any], *, new_is_b: bool, direction: str = "new") -> bool:
    """The agreement rule. ``supersede``: same subject, not both true, and the verifier's current
    item is the one the primary said replaces the other. ``widen``: same subject and both true."""
    same, both, current = bool(v.get("same_subject")), bool(v.get("both_true")), v.get("current")
    if kind == "widen":
        return same and both
    if not same or both:
        return False
    subject_letter = "B" if new_is_b else "A"  # where the NEW item sits in the verifier's pair
    existing_letter = "A" if new_is_b else "B"
    return current == (subject_letter if direction == "new" else existing_letter)


def apply_verification(j: Judgement, kind: str, v: dict[str, Any] | None, *, new_is_b: bool) -> None:
    """Downgrade ``j`` in place per the verifier (``v`` None = the verifier gave no answer)."""
    if v is None:  # no usable second opinion: never an action, never a supersession
        j.verification = {"kind": kind, "agreed": False, "answer": None}
        agreed, conflict = False, kind == "supersede"
    else:
        agreed = verifier_agrees(kind, v, new_is_b=new_is_b, direction=j.supersedes)
        conflict = bool(v.get("same_subject")) and not bool(v.get("both_true"))
        j.verification = {
            "kind": kind,
            "agreed": agreed,
            "answer": {k: v.get(k) for k in ("same_subject", "both_true", "current")},
        }
    if agreed:
        return
    if kind == "supersede" and conflict:
        # a verified conflict without an agreed direction: a plain contradiction question
        j.supersedes = "none"
        j.tier = _cap(j.tier, "question")
        j.flags.append("verifier_direction_disputed")
        return
    j.flags.append("verifier_rejected")
    j.relation, j.supersedes, j.tier = "none", "none", "abstain"


def check_verifications(output: dict[str, Any], ids: list[str]) -> dict[str, dict[str, Any]]:
    """Guard one ``relate_verify`` answer: only ids that were asked, first answer wins."""
    wanted = set(ids)
    out: dict[str, dict[str, Any]] = {}
    for r in output.get("results") or []:
        if isinstance(r, dict) and str(r.get("id")) in wanted and str(r["id"]) not in out:
            out[str(r["id"])] = r
    return out


# --------------------------------------------------------------------------- placement
def clean_tags(tags: Any, existing: list[str]) -> list[str]:
    have = {t.casefold() for t in existing}
    out: list[str] = []
    for t in tags if isinstance(tags, list) else []:
        tag = _norm(str(t)).replace(" ", "-")
        if _TAG.match(tag) and tag not in have and tag not in out:
            out.append(tag)
        if len(out) == 5:
            break
    return out


def check_placement(
    output: dict[str, Any], ids: list[str], *, redact: Any = None
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Guard one ``place`` answer: ``{id: {importance, stability, topic_hint, tags_add}}`` for the
    ids that were asked (first answer wins); counters ``{uncited, duplicate_id, missing}``."""
    wanted = set(ids)
    counts = {"uncited": 0, "duplicate_id": 0, "missing": 0}
    out: dict[str, dict[str, Any]] = {}
    for r in output.get("items") or []:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id", ""))
        if rid not in wanted:
            counts["uncited"] += 1
            continue
        if rid in out:
            counts["duplicate_id"] += 1
            continue
        imp = r.get("importance")
        if isinstance(imp, bool) or not isinstance(imp, int) or not 1 <= imp <= 10:
            continue
        stab = r.get("stability") if r.get("stability") in ("stable", "volatile") else None
        hint = str(r.get("topic_hint") or "").strip()
        if redact is not None:
            hint = redact(hint)
        out[rid] = {
            "importance": imp,
            "stability": stab,
            "topic_hint": hint[:80] or None,
            "tags_add": r.get("tags_add") or [],
        }
    counts["missing"] = len(wanted - set(out))
    return out, counts


__all__ = [
    "CONFIDENCES",
    "RELATIONS",
    "TIERS",
    "Judgement",
    "PairText",
    "apply_verification",
    "check_placement",
    "check_relations",
    "check_verifications",
    "clean_tags",
    "high_impact",
    "quote_in",
    "verifier_agrees",
]
