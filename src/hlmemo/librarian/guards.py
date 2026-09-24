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
   drops the proposal (``verifier_rejected``); no usable answer drops it (``verifier_no_answer``).
   The verifier sees each side's project (same/other) to judge the scope. Both calls are audited.
7. **First-class abstention** — ``none`` is the prompts' default; a missing, uncited or
   low-confidence link-type answer is an abstention, and abstentions are counted and scored.

Judgement v2 (D-076 hold-out failures; prompts ``relate/v2`` + ``relate_verify/v2``), all
deterministic, applied by ``check_relations`` (pre-verification) and ``finalize`` (after it):

8. **Fact-level supersession** — a supersession carries a ``scope``: ``whole`` only when the model
   asserts that EVERY statement of the replaced item is outdated and proves it with verbatim quotes
   covering every body statement (``statements``/``covers_all``), the replaced body was shown
   untruncated, and the verifier agrees (``replaces_all``). Anything else is ``part``: no
   ``version_close`` is ever proposed, only a scoped ``supersedes`` link quoting the outdated span
   (which never hides the item on the read side). A v1 answer (no scope) is always ``part``.
9. **Strict duplicates** — ``duplicate`` needs near-identical bodies (token Jaccard ≥
   ``NEAR_IDENTICAL``); otherwise it becomes ``refines`` (one side decisively more specific) or
   ``relates`` (a restated claim), or is dropped when each side carries concrete details the other
   lacks.
10. **Refine direction** — the more specific item refines the more general one (``specificity``:
    concrete tokens nested, else content-term coverage); undecided → the newer one (valid_from,
    then recorded_at); conflicting details drop it. Wrong directions are flipped and counted
    (``refine_flipped``). A judgement the guard corrected (a flipped direction, a duplicate that is
    not near-identical) is capped at ``question``: never an automatic action.
11. **doc_chunk pairs** raise contradictions only (a duplicate/refinement involving a document
    chunk is dropped, ``doc_chunk_link_skipped``).
12. **Strict action tier** (``finalize``) — ``action`` additionally needs the verifier's agreement
    (``confirm`` for every action-tier judgement not already verified; for duplicate/refines the
    verifier's ``adds_detail`` must match), both-side quotes that pin the claim (``quotes_pin``)
    and a same-kind candidate. Everything else is a ``question``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from hlmemo.core.normalize import normalize

RELATIONS = ("none", "duplicate", "refines", "contradicts")
#: the relations a FINAL judgement can carry: ``relates`` = a restated claim that is not
#: near-identical (a downgraded duplicate); it becomes a plain ``relates_to`` link
FINAL_RELATIONS = ("none", "duplicate", "relates", "refines", "contradicts")
#: token Jaccard of the normalized bodies for a ``duplicate`` (D-076: 0/6 semantic duplicates)
NEAR_IDENTICAL = 0.90
#: specificity: the more specific side covers this share of the other's content terms ...
SPEC_COVER_MIN = 0.60
#: ... and has at least this many more of them
SPEC_MORE_TERMS = 2
#: a body statement (line / sentence) needs this many words to count as a claim
STATEMENT_MIN_WORDS = 3
MAX_REPLACED_QUOTES = 20
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
    """The two sides of one relation judgement as the model saw them (``new_text``/``old_text``:
    title + the shown body, the quote guard's evidence) plus the deterministic context the v2
    rules need (kinds, full bodies, whether the body was shown whole, recorded_at)."""

    cid: str  # the existing item's id (a clue, e.g. "v12")
    new_text: str
    old_text: str
    new_valid: datetime
    old_valid: datetime
    new_kind: str = "fact"
    old_kind: str = "fact"
    new_body: str | None = None  # the full body (None: ``new_text``)
    old_body: str | None = None
    new_shown_all: bool = True  # the body reached the prompt untruncated
    old_shown_all: bool = True
    new_recorded: datetime | None = None
    old_recorded: datetime | None = None

    def body(self, side: str) -> str:
        if side == "new":
            return self.new_body if self.new_body is not None else self.new_text
        return self.old_body if self.old_body is not None else self.old_text


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
    #: refines: the side that adds the detail (the link goes refiner -> refined)
    refiner: str = "new"
    #: supersession scope: "whole" (every statement of the replaced item is outdated) or "part"
    scope: str = "part"
    replaced_quotes: list[str] = field(default_factory=list)
    #: set by ``finalize``: a ``version_close`` of the replaced item may be proposed
    close_ok: bool = False
    #: set by ``finalize``: the replaced item is ONE statement (only then may a close be automatic)
    single_statement: bool = False

    @property
    def raised(self) -> bool:
        return self.relation != "none" and self.tier != "abstain"

    @property
    def replaced_quote(self) -> str:
        """The verbatim span of the replaced (outdated) item."""
        return self.old_quote if self.supersedes == "new" else self.new_quote

    def audit(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.cid,
            "relation": self.relation,
            "supersedes": self.supersedes,
            "confidence": self.confidence,
            "tier": self.tier,
        }
        if self.relation == "refines":
            out["refiner"] = self.refiner
        if self.supersedes != "none":
            out["scope"] = self.scope
            out["close_ok"] = self.close_ok
        if self.flags:
            out["flags"] = list(self.flags)
        if self.verification is not None:
            out["verification"] = self.verification
        return out


# --------------------------------------------------------------------------- v2 text helpers
_WORD = re.compile(r"\w+")
_LINE_MARK = re.compile(r"^\s*(?:[-*+•>]+|\d+[.)]|#+)\s+")
_SENT_SPLIT = re.compile(r"(?<=[.!?;])\s+")


def tokens(text: str) -> list[str]:
    """Word tokens of the normalized text (``16.4`` -> ``16``, ``4``; ``hlm_test`` stays one)."""
    return _WORD.findall(normalize(text or ""))


def _concrete(toks: list[str] | set[str]) -> set[str]:
    """Concrete details: numbers and identifier-like tokens."""
    return {t for t in toks if "_" in t or any(ch.isdigit() for ch in t)}


def _content(toks: list[str] | set[str]) -> set[str]:
    return {t for t in toks if len(t) >= 4} | _concrete(toks)


def jaccard(a: str, b: str) -> float:
    ta, tb = set(tokens(a)), set(tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def near_identical(a: str, b: str) -> bool:
    return jaccard(a, b) >= NEAR_IDENTICAL


def specificity(new_body: str, old_body: str) -> tuple[str | None, bool, str]:
    """``(more specific side | None, conflicting details, basis)``. Concrete tokens (numbers,
    identifiers) strictly nested decide (basis ``concrete``); else content terms (basis
    ``content``): the larger side covers ``SPEC_COVER_MIN`` of the other's terms and has
    ``SPEC_MORE_TERMS`` (and 30 %) more, so a paraphrase's filler words do not count. Each side
    carrying concrete tokens the other lacks = two different values/attributes (``conflicting``)."""
    tn, to = tokens(new_body), tokens(old_body)
    cn, co = _concrete(tn), _concrete(to)
    if cn - co and co - cn:
        return None, True, "concrete"
    if cn > co:
        return "new", False, "concrete"
    if co > cn:
        return "old", False, "concrete"
    wn, wo = _content(tn), _content(to)

    def more(big: set[str], small: set[str]) -> bool:
        need = max(SPEC_MORE_TERMS, math.ceil(0.3 * len(small)))
        return (
            bool(small) and len(big) >= len(small) + need and len(small & big) / len(small) >= SPEC_COVER_MIN
        )

    if more(wn, wo):
        return "new", False, "content"
    if more(wo, wn):
        return "old", False, "content"
    return None, False, ""


def newer_side(p: PairText) -> str | None:
    """The later item by ``valid_from``, then ``recorded_at``; ``None`` on a full tie."""
    if p.new_valid != p.old_valid:
        return "new" if p.new_valid > p.old_valid else "old"
    if p.new_recorded is not None and p.old_recorded is not None and p.new_recorded != p.old_recorded:
        return "new" if p.new_recorded > p.old_recorded else "old"
    return None


def statements(body: str) -> list[str]:
    """The claims of a body: its lines (list/heading markers stripped), split at sentence ends;
    fragments under ``STATEMENT_MIN_WORDS`` words are not claims. A body with none is one claim."""
    out: list[str] = []
    for line in (body or "").splitlines():
        line = _LINE_MARK.sub("", line).strip()
        if not line:
            continue
        for part in _SENT_SPLIT.split(line):
            if len(_norm(part).strip(_EDGE_PUNCT).split()) >= STATEMENT_MIN_WORDS:
                out.append(part.strip())
    if not out and (body or "").strip():
        out.append(body.strip())
    return out


def covers_all(body: str, quotes: list[str]) -> bool:
    """Every statement of ``body`` is covered by a verbatim quote: the quote occurs in it and has
    ≥ 3 words or ≥ half of its words (the whole statement when shorter), or the statement occurs
    in the quote. Quotes that are not verbatim in ``body`` are ignored."""
    qs = [q for q in (_norm(x).strip(_EDGE_PUNCT) for x in quotes) if len(q.split()) >= QUOTE_MIN_WORDS]
    qs = [q for q in qs if q in _norm(body)]
    if not qs:
        return False
    for st in statements(body):
        s = _norm(st).strip(_EDGE_PUNCT)
        n = len(s.split())
        need = min(n, max(3, math.ceil(0.5 * n)))
        if not any((q in s and len(q.split()) >= need) or s in q for q in qs):
            return False
    return True


def quotes_pin(j: Judgement) -> bool:
    """Both-side quotes that pin the judged claim: verified verbatim (no ``quote_unverified``),
    about the same thing (a shared content term) and, for a contradiction, differing where the
    conflict is (a content/concrete term on one side only); for duplicate/refines/relates they
    share ≥ 2 content terms."""
    if "quote_unverified" in j.flags:
        return False
    qn, qo = _content(tokens(j.new_quote)), _content(tokens(j.old_quote))
    shared = qn & qo
    if j.relation == "contradicts":
        return bool(shared) and bool(qn ^ qo)
    return len(shared) >= 2


def _drop(j: Judgement, flag: str, counts: dict[str, int], counter: str | None = None) -> None:
    j.relation, j.supersedes, j.tier = "none", "none", "abstain"
    j.flags.append(flag)
    key = counter or flag
    counts[key] = counts.get(key, 0) + 1


def _count(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def pre_verify(j: Judgement, p: PairText, counts: dict[str, int]) -> None:
    """The v2 rules that need no second opinion (doc_chunk pairs, strict duplicates, refine
    direction, whole-scope evidence), applied to a raised judgement before the verifier."""
    if not j.raised:
        return
    if "doc_chunk" in (p.new_kind, p.old_kind) and j.relation != "contradicts":
        _drop(j, "doc_chunk_link_skipped", counts)
        return
    nb, ob = p.body("new"), p.body("old")
    if j.relation == "duplicate" and not near_identical(nb, ob):
        spec, conflicting, basis = specificity(nb, ob)
        if conflicting:
            _drop(j, "duplicate_conflicting_details", counts, "dup_dropped")
            return
        if spec is not None and basis == "concrete":  # an added number/identifier: a refinement
            j.relation, j.refiner = "refines", spec
            _count(counts, "dup_to_refines")
        else:
            j.relation = "relates"
            _count(counts, "dup_to_relates")
        # a judgement the guard had to correct is never an automatic action: the owner decides
        j.flags.append("duplicate_not_identical")
        j.tier = _cap(j.tier, "question")
    if j.relation == "refines":
        spec, conflicting, _basis = specificity(nb, ob)
        if conflicting:
            _drop(j, "refine_conflicting_details", counts, "refine_dropped")
            return
        expected = spec or newer_side(p)
        if expected is not None and expected != j.refiner:
            j.refiner = expected
            j.flags.append("refine_direction_flipped")
            _count(counts, "refine_flipped")
            j.tier = _cap(j.tier, "question")  # a corrected judgement is never an automatic action
            if spec is None:  # time alone decided the direction (weak evidence)
                j.flags.append("refine_direction_by_time")
    if j.relation == "contradicts" and j.supersedes != "none":
        side = "old" if j.supersedes == "new" else "new"  # the replaced item
        if j.scope == "whole":
            shown_all = p.old_shown_all if side == "old" else p.new_shown_all
            quotes = [*j.replaced_quotes, j.replaced_quote]
            if not shown_all:
                j.scope = "part"
                j.flags.append("close_truncated")
                _count(counts, "close_truncated")
            elif not covers_all(p.body(side), quotes):
                j.scope = "part"
                j.flags.append("close_not_covered")
                _count(counts, "close_not_covered")
        j.single_statement = len(statements(p.body(side))) == 1


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
        replaced = r.get("replaced_statements")
        j = Judgement(
            cid,
            relation=rel,
            supersedes=sup,
            confidence=conf,
            reason=reason[:MAX_REASON],
            new_quote=str(r.get("new_quote") or "")[:200],
            old_quote=str(r.get("old_quote") or "")[:200],
            # v2 fields; a v1 answer has none: the new item refines, a supersession is partial
            refiner=r.get("refiner") if r.get("refiner") in ("new", "old") else "new",
            scope="whole" if r.get("scope") == "whole" else "part",
            replaced_quotes=[str(x)[:400] for x in replaced[:MAX_REPLACED_QUOTES]]
            if isinstance(replaced, list)
            else [],
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
            # the replacing item must be the later one; on a tie only the NEW item may replace
            # (an existing item never hides the item just written, Sol 44 #5)
            if (j.supersedes == "new" and p.new_valid < p.old_valid) or (
                j.supersedes == "old" and p.old_valid <= p.new_valid
            ):
                j.supersedes = "none"
                counts["supersedes_against_time"] += 1
                j.flags.append("supersedes_against_time")
            if not (quote_in(j.new_quote, p.new_text) and quote_in(j.old_quote, p.old_text)):
                counts["quote_unverified"] += 1
                j.flags.append("quote_unverified")
                j.tier = _cap(j.tier, "question")
            if j.supersedes == "none":
                j.scope = "part"
            pre_verify(j, p, counts)
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
    if cross_project and j.relation in ("duplicate", "refines", "relates"):
        return "widen"
    return None


def verification_kind(j: Judgement, *, cross_project: bool, pair: PairText | None = None) -> str | None:
    """Which second opinion ``j`` needs: ``supersede``/``widen`` (D-067 high impact: without
    agreement not raised) or ``confirm`` (v2: an ``action``-tier judgement that already passes the
    other action rules — quotes pin the claim, same kind — needs the verifier to stay an action;
    without agreement it becomes a question). ``None``: no verifier call."""
    kind = high_impact(j, cross_project=cross_project)
    if kind is not None:
        return kind
    if j.raised and j.tier == "action" and quotes_pin(j) and (pair is None or pair.new_kind == pair.old_kind):
        return "confirm"
    return None


def verifier_agrees(
    kind: str,
    v: dict[str, Any],
    *,
    new_is_b: bool,
    direction: str = "new",
    relation: str = "",
    refiner: str = "new",
) -> bool:
    """The agreement rule. ``supersede``: same subject, a conflict, and the verifier's current item
    is the one the primary said replaces the other. ``widen``: same subject and no conflict.
    ``confirm``: a contradiction = same subject and a conflict; duplicate/relates = same subject,
    no conflict and (v2) neither adds a detail; refines = same subject, no conflict and (v2) the
    detail is added by the primary's ``refiner`` (a v1 answer without ``adds_detail`` agrees on
    subject and conflict only)."""
    same, conflict, current = bool(v.get("same_subject")), bool(v.get("conflict")), v.get("current")
    subject_letter = "B" if new_is_b else "A"  # where the NEW item sits in the verifier's pair
    existing_letter = "A" if new_is_b else "B"
    if kind == "widen":
        return same and not conflict
    if kind == "confirm":
        if relation == "contradicts":
            return same and conflict
        if not same or conflict:
            return False
        adds = v.get("adds_detail")
        if adds not in ("A", "B", "none"):
            return True  # v1 verifier: no detail question asked
        if relation == "refines":
            return adds == (subject_letter if refiner == "new" else existing_letter)
        return adds == "none"
    if not same or not conflict:
        return False
    return current == (subject_letter if direction == "new" else existing_letter)


def apply_verification(j: Judgement, kind: str, v: dict[str, Any] | None, *, new_is_b: bool) -> None:
    """Downgrade ``j`` in place per the verifier (``v`` None = the verifier gave no answer)."""
    if kind == "confirm":  # v2 strict action tier: never drops, caps at question
        agreed = v is not None and verifier_agrees(
            kind, v, new_is_b=new_is_b, relation=j.relation, refiner=j.refiner
        )
        j.verification = {
            "kind": kind,
            "agreed": agreed,
            "answer": None
            if v is None
            else {k: v.get(k) for k in ("same_subject", "conflict", "current", "adds_detail")},
        }
        if not agreed:
            j.tier = _cap(j.tier, "question")
            j.flags.append("verifier_not_confirmed")
        return
    if v is None:  # no usable second opinion: the high-impact judgement is not raised (Sol 44 #6)
        j.verification = {"kind": kind, "agreed": False, "answer": None}
        j.flags.append("verifier_no_answer")
        j.relation, j.supersedes, j.tier = "none", "none", "abstain"
        return
    agreed = verifier_agrees(kind, v, new_is_b=new_is_b, direction=j.supersedes)
    conflict = bool(v.get("same_subject")) and bool(v.get("conflict"))
    keys = (
        ("same_subject", "conflict", "current", "replaces_all")
        if kind == "supersede"
        else (
            "same_subject",
            "conflict",
            "current",
        )
    )
    j.verification = {"kind": kind, "agreed": agreed, "answer": {k: v.get(k) for k in keys}}
    if agreed:
        if kind == "supersede" and j.scope == "whole" and v.get("replaces_all") is not True:
            j.scope = "part"  # the second opinion does not confirm that EVERY statement is outdated
            j.flags.append("verifier_not_all")
        return
    if kind == "supersede" and conflict:
        # a verified conflict without an agreed direction: a plain contradiction question
        j.supersedes = "none"
        j.tier = _cap(j.tier, "question")
        j.flags.append("verifier_direction_disputed")
        return
    j.flags.append("verifier_rejected")
    j.relation, j.supersedes, j.tier = "none", "none", "abstain"


def finalize(j: Judgement, p: PairText, counts: dict[str, int] | None = None) -> None:
    """After the verifier: the v2 strict action tier and the close decision (in place).

    ``action`` stays only with the verifier's agreement, quotes that pin the claim and a same-kind
    candidate (else ``question``, flagged ``action_*``). ``close_ok``: a supersession with scope
    ``whole`` (every statement covered by quotes, shown untruncated) that the verifier agreed to and
    confirmed as replacing ALL of the item."""
    counts = counts if counts is not None else {}
    if not j.raised:
        j.close_ok = False
        return
    if j.tier == "action":
        reasons = []
        if not (j.verification and j.verification.get("agreed")):
            reasons.append("action_unverified")
        if not quotes_pin(j):
            reasons.append("action_quotes")
        if p.new_kind != p.old_kind:
            reasons.append("action_kind")
        if reasons:
            j.tier = "question"
            j.flags.extend(reasons)
            _count(counts, "action_to_question")
    answer = (j.verification or {}).get("answer") or {}
    j.close_ok = (
        j.relation == "contradicts"
        and j.supersedes != "none"
        and j.scope == "whole"
        and bool((j.verification or {}).get("agreed"))
        and answer.get("replaces_all") is True
    )


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
    "FINAL_RELATIONS",
    "NEAR_IDENTICAL",
    "RELATIONS",
    "TIERS",
    "Judgement",
    "PairText",
    "apply_verification",
    "check_placement",
    "check_relations",
    "check_verifications",
    "clean_tags",
    "covers_all",
    "finalize",
    "high_impact",
    "jaccard",
    "near_identical",
    "newer_side",
    "pre_verify",
    "quote_in",
    "quotes_pin",
    "specificity",
    "statements",
    "tokens",
    "verification_kind",
    "verifier_agrees",
]
