"""The research librarian's LLM side (``memory.ask``; D-130, D-136): prompts, deterministic answer
validation and the provider wrapper. ``core/research_service`` runs the loop and the DB phases.

One provider TASK, ``research``, for the five JOBs of the loop (one system prompt, one schema, a
job-specific shape check): ``plan`` (map + question → queries + map sections), ``answer`` (excerpts →
answer + claims, each with 1-3 verbatim supporting quotes), ``check`` (the COMPLETENESS pass: the
question's sub-asks against the draft and the same excerpts → the full revised answer; a missing fact
is added only as a new quoted claim), ``verify`` (the cheap self-check: each claim against ONLY its
quotes → full / partial (narrowed) / none, and the answer rewritten to the entailed claims) and
``refine`` (one more search round, only when the answer abstained or is unsure; it takes the
self-check's call slot). The per-task fallback is therefore ``HLM_FALLBACK_PROFILE__RESEARCH``
(D-094); a profile listing ``research`` in ``disabled_tasks`` is not used (D-071).

Guards (the W2e/W2d ones): privacy default-deny before EVERY attempt (``precheck``: the strict
``librarian.privacy`` gate over exactly the version ids whose text is in the prompt), redaction of
the prompt and of the returned free text, the spend guard (atomic reservation + ``llm_calls``
ledger), the ``latency`` attempt policy (one bounded primary attempt, then the qualified fallback),
per-profile breakers, a per-request lineage whose DB-enforced ceiling (``MAX_ATTEMPTS``) bounds the
provider requests of one question, and at most ``MAX_IN_FLIGHT`` questions per process.

Validation (``validate_answer``, ``apply_verify``) is deterministic. Excerpt ids are the handles the
caller can drill (``vN.M``/``vN``). Each quote must occur in the excerpt it names after ``qnorm``
(NFKC, casefold, typographic quotes, markdown markers, whitespace, and a DECIMAL comma between digits
read as a point: TR "1,6" = "1.6"; a thousands-style "1,600" is left alone); a quote found only in
another SHOWN excerpt is re-attributed to it; the quote returned is the original substring of the
excerpt. A claim is kept only when its quotes TOGETHER contain every number/identifier/path literal of
the claim; a claim without a verified quote is downgraded (its handles only ``related``) or dropped.
The free-text answer keeps only the sentences whose literals are in the kept claims' quotes.
primary = the handles supporting the most kept claims (≤ 3). No kept claim left → abstention.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from hlmemo.librarian import privacy
from hlmemo.librarian.budget import Caps, DbBudget, NoBudget
from hlmemo.librarian.cassette import CassetteStore
from hlmemo.librarian.errors import AuthorityLost, LlmConfigError, PrivacyDenied
from hlmemo.librarian.ledger import NETWORK_OUTCOMES, DbLedger, Ledger, LedgerRow
from hlmemo.librarian.profiles import LlmProfile, profile_chain
from hlmemo.librarian.prompts import TaskSpec, load_task
from hlmemo.librarian.provider import ChainBreakers, Clock, LlmResult, Provider
from hlmemo.librarian.redact import Redactor
from hlmemo.librarian.risk_judge import ConnectFactory, direct_connector
from hlmemo.librarian.tasks.synthesis import claims as literal_claims

log = logging.getLogger("hlmemo.librarian.research")

TASK = "research"
JOBS = ("plan", "answer", "check", "verify", "refine")
#: logical LLM calls of one question: plan, answer, check, verify — or, when the answer abstained or
#: is unsure, plan, answer, refine, answer, check (the refinement takes the self-check's slot)
MAX_CALLS = 5
#: provider requests of one question (schema retries and fallbacks included), DB-enforced per lineage
MAX_ATTEMPTS = 9
MAX_IN_FLIGHT = 4
BREAKER_THRESHOLD = 3
BREAKER_OPEN_S = 30.0
BREAKER_MAX_OPEN_S = 900.0
#: per-request HTTP timeout ceiling (each attempt is also budgeted to the call's deadline)
HTTP_TIMEOUT_S = 20.0
EXCERPT_CHARS = 3200
MAX_CLAIMS = 12
#: verbatim quotes per claim (together they must state the whole claim)
MAX_SUPPORT = 3
QUOTE_MIN_CHARS = 12
QUOTE_MAX_CHARS = 600
ANSWER_MAX_CHARS = 2400
MAX_PRIMARY = 3
MAX_RELATED = 5
MAX_QUERIES = 4
MAX_SECTIONS = 6

ANSWERED = "answered"
INSUFFICIENT = "insufficient_evidence"
_CONF = ("high", "medium", "low")
_HANDLE = re.compile(r"^v[1-9][0-9]*(\.(0|[1-9][0-9]*))?$")


# --------------------------------------------------------------------------- normalisation
_DROP = frozenset("*`_#>|")
_QUOTES = {"“": '"', "”": '"', "„": '"', "«": '"', "»": '"', "’": "'", "‘": "'", "‚": "'"}
_DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d{1,2}(?!\d))")
_WS = re.compile(r"\s+")


def _decimal_comma_at(text: str, i: int) -> bool:
    """``text[i]`` is a decimal comma: digit before, then 1-2 digits and no further digit."""
    if text[i] != "," or i == 0 or not text[i - 1].isdigit():
        return False
    j = i + 1
    while j < len(text) and text[j].isdigit():
        j += 1
    return 1 <= j - i - 1 <= 2


def _norm_with_map(text: str) -> tuple[str, list[int]]:
    """``qnorm`` of ``text`` plus, for every character of the result, the index of the original
    character it came from (so a match can be mapped back to the verbatim span)."""
    out: list[str] = []
    idx: list[int] = []
    space = True
    for i, ch in enumerate(text):
        if _decimal_comma_at(text, i):
            piece = "."
        else:
            piece = unicodedata.normalize("NFKC", ch).casefold()
            piece = "".join(_QUOTES.get(c, c) for c in piece)
        for c in piece:
            if c in _DROP or c.isspace():
                if not space:
                    out.append(" ")
                    idx.append(i)
                    space = True
                continue
            out.append(c)
            idx.append(i)
            space = False
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def qnorm(text: str) -> str:
    """The quote-matching normal form (module docstring)."""
    return _norm_with_map(text)[0]


def find_verbatim(quote: str, text: str) -> str | None:
    """The span of ``text`` that ``quote`` matches under ``qnorm`` (the ORIGINAL characters), or
    ``None``. Quotes shorter than ``QUOTE_MIN_CHARS`` normalised characters never match."""
    q = qnorm(quote)
    if len(q) < QUOTE_MIN_CHARS:
        return None
    t, idx = _norm_with_map(text)
    at = t.find(q)
    if at < 0:
        return None
    start, end = idx[at], idx[at + len(q) - 1]
    return text[start : end + 1]


def _lit_norm(text: str) -> str:
    return _WS.sub(" ", _DECIMAL_COMMA.sub(".", unicodedata.normalize("NFKC", text).casefold())).strip()


def _token_in(tok: str, hay: str) -> bool:
    if not tok:
        return True
    before = r"(?<!\w)" + (r"(?<!\d[.,])" if tok[0].isdigit() else "")
    after = r"(?!\w)" + (r"(?![.,]\d)" if tok[-1].isdigit() else "")
    return re.search(before + re.escape(tok) + after, hay) is not None


_NUM_UNIT = re.compile(r"^(\d[\d.,]*)[a-z%µ]{1,3}$")


def literal_supported(literal: str, hay: str) -> bool:
    """``synthesis.supported`` with the decimal-comma rule on both sides (``hay`` is ``_lit_norm``ed):
    the literal occurs as a whole token; tolerated: thousands separators, a number glued to its unit
    and hyphen-joined parts."""
    c = _lit_norm(literal)
    if not c or _token_in(c, hay):
        return True
    if "," in c and _token_in(c.replace(",", ""), hay.replace(",", "")):
        return True
    m = _NUM_UNIT.match(c)
    if m and _token_in(m.group(1), hay):
        return True
    if "-" in c and re.fullmatch(r"[\w.-]+", c):
        return all(_token_in(p, hay) for p in c.split("-") if p)
    return False


#: a Turkish case suffix after an apostrophe (``%40’ını``, ``D-130'da``): not part of the literal
_APOS_SUFFIX = re.compile(r"^(.*?[\w%])['’][^\W\d_]{1,8}$")


def literals(text: str) -> list[str]:
    """The checkable literals of ``text`` (``synthesis.claims``), with a Turkish apostrophe suffix
    removed so ``%40’ını`` is checked as ``40`` and ``D-130'da`` as ``D-130``."""
    out = []
    for lit in literal_claims(text):
        m = _APOS_SUFFIX.match(lit)
        out.append(m.group(1).lstrip("%") if m else lit)
    return [x for x in out if x]


def literals_ok(text: str, hay: str) -> bool:
    return all(literal_supported(x, hay) for x in literals(text))


# --------------------------------------------------------------------------- excerpts and prompts
@dataclass(slots=True)
class Excerpt:
    handle: str  # the drillable handle, also the excerpt id the model cites (vN.M / vN)
    version_id: int
    title: str
    path: str  # source path (anchor included) or the title
    date: str  # valid_from, YYYY-MM-DD
    text: str  # redacted, clipped to EXCERPT_CHARS: exactly what the model is shown

    def shown(self) -> dict[str, str]:
        return {"id": self.handle, "title": self.title, "date": self.date, "text": self.text}


def clip(text: str, limit: int = EXCERPT_CHARS) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + " …"


def _input(payload: dict[str, Any]) -> str:
    return "INPUT: " + json.dumps(payload, ensure_ascii=False)


def plan_user(question: str, context: str, map_text: str) -> str:
    return "JOB: plan\n" + _input({"project_context": context, "question": question}) + "\n\n" + map_text


def answer_user(question: str, excerpts: list[Excerpt]) -> str:
    return "JOB: answer\n" + _input({"question": question, "excerpts": [e.shown() for e in excerpts]})


def check_user(question: str, draft: dict[str, Any], excerpts: list[Excerpt]) -> str:
    """The COMPLETENESS pass: the question, the draft (answer text + its claims with their quotes)
    and EVERY excerpt the draft was written from, so each sub-ask and each retrieved candidate fact
    can be checked against the draft."""
    payload = {
        "question": question,
        "draft": {"answer": draft.get("answer", ""), "claims": draft.get("claims", [])},
        "excerpts": [e.shown() for e in excerpts],
    }
    return "JOB: check\n" + _input(payload)


def verify_user(question: str, answer: str, claims: list[Claim]) -> str:
    """The self-check: the answer and each claim with ONLY its verified quotes (cheap: no
    documents), so entailment is judged on exactly what the caller will be shown."""
    payload = {
        "question": question,
        "answer": answer,
        "claims": [
            {"i": i, "text": c.text, "quotes": [q for _h, q in c.support]} for i, c in enumerate(claims)
        ],
    }
    return "JOB: verify\n" + _input(payload)


def refine_user(question: str, tried: list[str], read: list[Excerpt], map_text: str) -> str:
    payload = {
        "question": question,
        "queries_tried": tried,
        "read_so_far": [{"id": e.handle, "title": e.title} for e in read],
    }
    return "JOB: refine\n" + _input(payload) + "\n\n" + map_text


# --------------------------------------------------------------------------- job shape checks
def job_validator(job: str) -> Callable[[dict[str, Any]], str | None]:
    """The JOB's own shape (the schema is shared): a violation is a schema failure, retried once."""

    def plan(obj: dict[str, Any]) -> str | None:
        if not isinstance(obj.get("queries"), list):
            return "queries missing"
        return None

    def answer(obj: dict[str, Any]) -> str | None:
        if obj.get("status") not in (ANSWERED, INSUFFICIENT):
            return "status missing"
        if not isinstance(obj.get("answer"), str) or not isinstance(obj.get("claims"), list):
            return "answer/claims missing"
        if obj["status"] == ANSWERED and not obj["claims"]:
            return "answered without claims"
        return None

    def verify(obj: dict[str, Any]) -> str | None:
        if not isinstance(obj.get("verdicts"), list) or not isinstance(obj.get("answer"), str):
            return "verdicts/answer missing"
        return None

    return {"plan": plan, "refine": plan, "verify": verify}.get(job, answer)


def parse_plan(obj: dict[str, Any] | None, question: str) -> tuple[list[str], list[str]]:
    """``(queries, sections)``: at most ``MAX_QUERIES`` distinct non-empty queries other than the
    question, and well-formed handles (the caller checks them against the map)."""
    if not obj:
        return [], []
    seen = {" ".join(question.split()).casefold()}
    queries: list[str] = []
    for q in obj.get("queries") or []:
        q = " ".join(str(q).split())[:300]
        if q and q.casefold() not in seen:
            seen.add(q.casefold())
            queries.append(q)
    sections = [str(s).strip() for s in obj.get("sections") or [] if _HANDLE.match(str(s).strip())]
    return queries[:MAX_QUERIES], list(dict.fromkeys(sections))[: MAX_SECTIONS * 2]


# --------------------------------------------------------------------------- answer validation
@dataclass(slots=True)
class Claim:
    text: str
    support: list[tuple[str, str]]  # (handle, verbatim span of that excerpt), 1..MAX_SUPPORT
    state: str  # kept | downgraded | dropped
    cited: list[str] = field(default_factory=list)  # every shown handle the model named for it

    def draft(self) -> dict[str, Any]:
        return {"text": self.text, "support": [{"id": h, "quote": q} for h, q in self.support]}

    def out(self) -> dict[str, Any]:
        return {"text": self.text, "support": [{"handle": h, "quote": q} for h, q in self.support]}


@dataclass(slots=True)
class Validated:
    status: str  # answered | insufficient_evidence
    model_status: str | None
    answer: str
    claims: list[Claim]
    primary: list[str]
    related: list[str]
    confidence: str
    guard: bool = False  # the model answered, but nothing survived validation
    dropped_sentences: int = 0
    missing: list[str] = field(default_factory=list)  # check: the facts the draft lacked
    sub_asks: list[dict[str, Any]] = field(default_factory=list)  # check: the question's parts

    @property
    def answered(self) -> bool:
        return self.status == ANSWERED

    @property
    def kept(self) -> list[Claim]:
        return [c for c in self.claims if c.state == "kept"]

    def quote_of(self, handle: str) -> str | None:
        for c in self.kept:
            for h, q in c.support:
                if h == handle:
                    return q
        return None

    def draft(self) -> dict[str, Any]:
        """The draft handed to the completeness pass: the answer and its verified claims."""
        return {"answer": self.answer, "claims": [c.draft() for c in self.kept]}


_SENTENCE = re.compile(r"(?<=[.!?…])\s+(?=\S)")


def _lower(conf: str) -> str:
    return {"high": "medium", "medium": "low"}.get(conf, "low")


def _hay(parts: list[str]) -> str:
    return _lit_norm("\n".join(parts))


def _supports(raw: Any, cited: list[str], shown: dict[str, Excerpt]) -> list[tuple[str, str]]:
    """The verified support of one claim: each quote located verbatim (``qnorm``) in the excerpt it
    names, else re-attributed to another SHOWN excerpt holding it; deduplicated, ≤ MAX_SUPPORT."""
    out: list[tuple[str, str]] = []
    for s in (raw if isinstance(raw, list) else [])[: MAX_SUPPORT * 2]:
        if not isinstance(s, dict):
            continue
        quote = str(s.get("quote") or "")
        h = str(s.get("id") or "").strip()
        span, where = None, None
        if h in shown:
            span, where = find_verbatim(quote, shown[h].text), h
        if span is None:
            for other in [*cited, *shown]:
                if other in shown and other != h:
                    span = find_verbatim(quote, shown[other].text)
                    if span is not None:
                        where = other
                        break
        if span is None or where is None:
            continue
        span = " ".join(span.split())
        if len(span) > QUOTE_MAX_CHARS:
            span = span[: QUOTE_MAX_CHARS - 1] + "…"
        if all(qnorm(span) != qnorm(q) for _h, q in out):
            out.append((where, span))
        if len(out) >= MAX_SUPPORT:
            break
    return out


def rank_sources(claims: list[Claim]) -> list[str]:
    """Handles by the number of kept claims they support (ties: first appearance)."""
    count: dict[str, int] = {}
    order: dict[str, int] = {}
    for c in claims:
        if c.state != "kept":
            continue
        for h in dict.fromkeys(h for h, _q in c.support):
            count[h] = count.get(h, 0) + 1
            order.setdefault(h, len(order))
    return sorted(count, key=lambda h: (-count[h], order[h]))


def assemble(
    status: str | None,
    answer: str,
    claims: list[Claim],
    related_hint: list[str],
    conf: str,
    shown: dict[str, Excerpt],
    redact: Callable[[str], str],
    **extra: Any,
) -> Validated:
    """The deterministic answer from validated claims: primary = the handles that support the most
    kept claims (≤ 3); related = the other supporting / named handles and the model's suggestions
    (≤ 5); a sentence of the free-text answer whose literals are not in the KEPT claims' quotes is
    dropped (the answer states only what the claims state); nothing kept → an abstention."""
    kept = [c for c in claims if c.state == "kept"]
    answer = " ".join(answer.split())
    if status != ANSWERED or not answer or not kept:
        closest = [h for h in related_hint if h in shown][:3]
        return Validated(
            INSUFFICIENT, status, "", claims, [], list(dict.fromkeys(closest)), "low",
            guard=status == ANSWERED and bool(answer), **extra,
        )  # fmt: skip
    if any(c.state != "kept" for c in claims):
        conf = _lower(conf)
    ranked = rank_sources(claims)
    primary = ranked[:MAX_PRIMARY]
    related: list[str] = []
    for h in [
        *ranked[MAX_PRIMARY:],
        *(h for c in claims if c.state != "dropped" for h in c.cited),
        *related_hint,
    ]:
        if h in shown and h not in primary and h not in related:
            related.append(h)
    related = related[:MAX_RELATED]
    hay = _hay([t for c in kept for t in (c.text, *(q for _h, q in c.support))])
    sentences = _SENTENCE.split(answer)
    kept_sentences = [s for s in sentences if literals_ok(s, hay)]
    dropped = len(sentences) - len(kept_sentences)
    text = redact(" ".join(kept_sentences))[:ANSWER_MAX_CHARS]
    if not text.strip():
        return Validated(
            INSUFFICIENT,
            status,
            "",
            claims,
            [],
            related[:3],
            "low",
            guard=True,
            dropped_sentences=dropped,
            **extra,
        )
    if dropped:
        conf = _lower(conf)
    return Validated(
        ANSWERED, status, text, claims, primary, related, conf, dropped_sentences=dropped, **extra
    )


def validate_answer(
    obj: dict[str, Any] | None, shown: dict[str, Excerpt], redact: Callable[[str], str] = lambda s: s
) -> Validated:
    """The deterministic check of an ``answer``/``check`` output against the excerpts it was shown
    (module docstring). A claim is KEPT when at least one of its quotes is verbatim in a shown
    excerpt and every literal (number, identifier, path) of the claim is in its quotes TOGETHER;
    DOWNGRADED (its handles only ``related``) when no quote verifies but its literals are in the
    excerpts it names; DROPPED otherwise. Never cites a handle that was not shown."""
    obj = obj or {}
    status = obj.get("status") if obj.get("status") in (ANSWERED, INSUFFICIENT) else None
    conf = obj.get("confidence") if obj.get("confidence") in _CONF else "low"
    missing = [" ".join(str(m).split())[:200] for m in (obj.get("missing") or []) if str(m).strip()][:12]
    sub_asks = [
        {"ask": " ".join(str(a.get("ask") or "").split())[:200], "covered": bool(a.get("covered"))}
        for a in (obj.get("sub_asks") or [])
        if isinstance(a, dict) and str(a.get("ask") or "").strip()
    ][:16]
    claims: list[Claim] = []
    raw_claims = obj.get("claims") if isinstance(obj.get("claims"), list) else []
    for c in raw_claims[:MAX_CLAIMS]:
        if not isinstance(c, dict):
            continue
        text = " ".join(str(c.get("text") or "").split())
        raw_support = c.get("support") if isinstance(c.get("support"), list) else []
        cited = list(
            dict.fromkeys(
                str(s.get("id") or "").strip()
                for s in raw_support
                if isinstance(s, dict) and str(s.get("id") or "").strip() in shown
            )
        )
        support = _supports(raw_support, cited, shown)
        for h, _q in support:
            if h not in cited:
                cited.append(h)
        if not text:
            claims.append(Claim(text, [], "dropped", cited))
        elif support and literals_ok(text, _hay([q for _h, q in support])):
            claims.append(Claim(text, support, "kept", cited))
        elif cited and literals_ok(text, _hay([shown[h].text for h in cited])):
            claims.append(Claim(text, [], "downgraded", cited))  # true to its sources, not to a quote
        else:
            claims.append(Claim(text, [], "dropped", cited))
    related_hint = [h for h in (obj.get("related") or []) if isinstance(h, str)]
    return assemble(
        status,
        str(obj.get("answer") or ""),
        claims,
        related_hint,
        conf,
        shown,
        redact,
        missing=missing,
        sub_asks=sub_asks,
    )


def merge_check(draft: Validated, checked: Validated) -> Validated:
    """The completeness pass wins when it validated as an answer; the draft's kept claims that the
    revision lost are carried over (their quotes are verified), so the pass can only add evidence."""
    if not checked.answered:
        return draft
    have = {frozenset(qnorm(q) for _h, q in c.support) for c in checked.kept}
    extra = [c for c in draft.kept if frozenset(qnorm(q) for _h, q in c.support) not in have]
    if not extra:
        return checked
    claims = [*checked.claims, *extra]
    merged = assemble(
        ANSWERED,
        checked.answer,
        claims,
        checked.related,
        checked.confidence,
        {h: e for h, e in _shown_of(checked, draft).items()},
        lambda s: s,
        missing=checked.missing,
        sub_asks=checked.sub_asks,
    )
    return merged if merged.answered else checked


def _shown_of(*vs: Validated) -> dict[str, Excerpt]:
    """Placeholder excerpts for the handles the validated answers already verified (``assemble``
    only needs membership for the ranking; the texts were checked before)."""
    out: dict[str, Excerpt] = {}
    for v in vs:
        for c in v.claims:
            for h in [*c.cited, *(h for h, _q in c.support)]:
                out.setdefault(h, Excerpt(h, 0, "", "", "", ""))
        for h in [*v.primary, *v.related]:
            out.setdefault(h, Excerpt(h, 0, "", "", "", ""))
    return out


def apply_verify(
    v: Validated, obj: dict[str, Any] | None, redact: Callable[[str], str] = lambda s: s
) -> Validated:
    """The self-check: a claim judged ``none`` is dropped; ``partial`` is narrowed to the model's
    rewrite when that rewrite's literals are all in the claim's quotes, else dropped; ``full`` (or no
    verdict) is kept. The answer becomes the verify rewrite (then grounded on the remaining claims
    like any answer); with no usable output the answer is returned unchanged."""
    if not v.answered or not obj or not isinstance(obj.get("verdicts"), list):
        return v
    kept = v.kept
    verdicts = {
        int(x["i"]): x
        for x in obj["verdicts"]
        if isinstance(x, dict) and isinstance(x.get("i"), int) and 0 <= x["i"] < len(kept)
    }
    claims: list[Claim] = []
    changed = False
    for i, c in enumerate(kept):
        vd = verdicts.get(i)
        verdict = (vd or {}).get("entailed", "full")
        if verdict == "full":
            claims.append(c)
            continue
        changed = True
        narrowed = " ".join(str((vd or {}).get("text") or "").split())
        if verdict == "partial" and narrowed and literals_ok(narrowed, _hay([q for _h, q in c.support])):
            claims.append(Claim(narrowed, c.support, "kept", c.cited))
        else:
            claims.append(Claim(c.text, [], "dropped", c.cited))
    answer = str(obj.get("answer") or "").strip() or v.answer
    out = assemble(
        ANSWERED,
        answer,
        claims,
        v.related,
        v.confidence if not changed else _lower(v.confidence),
        _shown_of(v),
        redact,
        missing=v.missing,
        sub_asks=v.sub_asks,
    )
    return out


# --------------------------------------------------------------------------- the provider wrapper
class ResearchUnavailable(Exception):
    """A call could not produce an output: ``reason`` is a synthesis-style status."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _TeeLedger:
    """The provider's ledger, plus an in-process tally of every attempt per lineage (cost and network
    attempts), so ``meta`` reports what one question really cost (fallbacks and retries included)."""

    def __init__(self, inner: Ledger) -> None:
        self.inner = inner
        self.tally: dict[str, list[Any]] = {}

    async def record(self, row: LedgerRow) -> None:
        if row.lineage is not None:
            acc = self.tally.setdefault(row.lineage, [0, Decimal(0)])
            if row.outcome in NETWORK_OUTCOMES:
                acc[0] += 1
            acc[1] += Decimal(row.cost_usd or 0)
        await self.inner.record(row)

    async def job_calls(self, job_id: int) -> int:
        return await self.inner.job_calls(job_id)

    async def lineage_calls(self, lineage: str) -> int:
        return await self.inner.lineage_calls(lineage)

    async def claim(self, lineage: str, cap: int) -> bool:
        return await self.inner.claim(lineage, cap)

    def take(self, lineage: str) -> tuple[int, Decimal]:
        acc = self.tally.pop(lineage, [0, Decimal(0)])
        return int(acc[0]), Decimal(acc[1])


def research_chain(settings: Any) -> list[LlmProfile]:
    """The primary plus the research fallback (``HLM_FALLBACK_PROFILE__RESEARCH`` when set, else
    ``HLM_FALLBACK_PROFILE``; D-094) minus profiles not qualified for ``research`` (D-071)."""
    try:
        chain = profile_chain(settings, TASK)
    except LlmConfigError as exc:
        log.warning("research disabled: %s", exc)
        return []
    return [p for p in chain if TASK not in p.disabled_tasks]


class Researcher:
    """The app's ``research`` provider (built on first use; keeps breakers and HTTP clients)."""

    def __init__(
        self,
        settings: Any,
        *,
        provider: Provider | None = None,
        chain: list[LlmProfile] | None = None,
        connect: ConnectFactory | None = None,
        clock: Clock | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        cassette_dir: Path | None = None,
        ledger: Ledger | None = None,
    ) -> None:
        self.settings = settings
        self.in_flight = 0
        self.clock = clock or Clock()
        self.provider: Provider | None = None
        self.breaker = ChainBreakers(lambda: self.provider, task=TASK)
        default_connect, conn_ctx = direct_connector(settings.db_dsn, settings)
        self.connect = connect or default_connect
        self._conn_ctx = conn_ctx
        if provider is not None:
            self.chain = list(provider.chain)
        else:
            self.chain = list(chain) if chain is not None else research_chain(settings)
        self.spec: TaskSpec = load_task(TASK)
        self._enabled = (
            bool(getattr(settings, "research_enabled", True))
            and bool(settings.librarian_enabled)
            and settings.llm_mode != "off"
            and bool(self.chain)
        )
        if provider is None and self._enabled:
            provider = self._build_provider(transport, cassette_dir, ledger)
        if provider is not None and not isinstance(provider.ledger, _TeeLedger):
            provider.ledger = _TeeLedger(provider.ledger)  # type: ignore[assignment]
        self.provider = provider

    def _build_provider(
        self,
        transport: httpx.AsyncBaseTransport | None,
        cassette_dir: Path | None,
        ledger: Ledger | None,
    ) -> Provider:
        s = self.settings
        cassettes = None
        if s.llm_mode in ("record", "replay"):
            directory = cassette_dir or s.llm_cassette_dir
            if directory is None:
                raise LlmConfigError(f"HLM_LLM_MODE={s.llm_mode} needs HLM_LLM_CASSETTE_DIR")
            cassettes = CassetteStore(Path(directory), record_name=TASK, redactor=Redactor.from_settings(s))
        budget = (
            NoBudget()
            if s.llm_budget_disabled
            else DbBudget(self._conn_ctx, Caps.from_settings(s), ttl_s=s.llm_reservation_ttl_s)
        )
        return Provider(
            self.chain,
            mode=s.llm_mode,
            budget=budget,
            ledger=ledger if ledger is not None else DbLedger(self._conn_ctx),
            cassettes=cassettes,
            transport=transport,
            clock=self.clock,
            redactor=Redactor.from_settings(s),
            timeout_s=min(float(s.llm_timeout_s), HTTP_TIMEOUT_S),
            breaker_threshold=BREAKER_THRESHOLD,
            breaker_open_s=BREAKER_OPEN_S,
            breaker_max_open_s=BREAKER_MAX_OPEN_S,
            job_call_cap=MAX_ATTEMPTS,  # per question (the request's lineage), DB-enforced
            budget_disabled=s.llm_budget_disabled,
        )

    @property
    def enabled(self) -> bool:
        return self._enabled and self.provider is not None

    @property
    def redactor(self) -> Redactor:
        return self.provider.redactor if self.provider is not None else Redactor()

    async def aclose(self) -> None:
        if self.provider is not None:
            await self.provider.aclose()

    def unavailable(self) -> str | None:
        """Why a question would not reach the provider right now (None: it would try)."""
        if not self.enabled:
            return "disabled"
        if self.in_flight >= MAX_IN_FLIGHT:
            return "busy"
        if self.breaker.remaining_s() > 0:
            return "unavailable"
        return None

    def tally(self, lineage: str) -> tuple[int, Decimal]:
        """``(provider attempts, USD)`` of every call made under ``lineage`` (popped)."""
        ledger = self.provider.ledger if self.provider is not None else None
        if isinstance(ledger, _TeeLedger):
            return ledger.take(lineage)
        return 0, Decimal(0)

    async def gate(self, capabilities: dict[str, Any], version_ids: list[int]) -> privacy.Verdict:
        """The strict privacy gate over ``version_ids`` in a fresh short transaction (no bodies)."""
        verdict, _ = await privacy.gate(self.connect, capabilities, sorted(set(version_ids)), bodies=False)
        return verdict

    async def gate_carried(self, capabilities: dict[str, Any], version_ids: list[int]) -> privacy.Verdict:
        """The privacy rules alone (currency ignored) over text ALREADY sent (review 80 #1): a
        superseded item is still judged on device scope, policy and grants."""
        verdict, _ = await privacy.gate(
            self.connect, capabilities, sorted(set(version_ids)), bodies=False, ignore_currency=True
        )
        return verdict

    async def complete(
        self,
        job: str,
        user: str,
        *,
        capabilities: dict[str, Any],
        gate_ids: list[int],
        deadline: float,
        lineage: str,
        carried_ids: list[int] | None = None,
    ) -> LlmResult:
        """One logical call. Before EVERY attempt (retries and the fallback included), and before any
        byte is sent: the strict privacy gate over ``gate_ids`` (the text of this prompt) and the
        privacy rules over ``carried_ids`` (the sources of text sent earlier that this prompt may carry
        in derived form: plan queries, a draft, an answer)."""
        assert self.provider is not None
        ids = sorted(set(gate_ids))
        carried = sorted(set(carried_ids or []) - set(ids))

        async def precheck() -> None:
            verdict = await self.gate(capabilities, ids)
            if not verdict.device_ok:
                raise AuthorityLost("E_AUTHORITY_LOST")
            if verdict.denied:
                raise PrivacyDenied("E_PRIVACY_DENIED")
            if carried:
                prior = await self.gate_carried(capabilities, carried)
                if not prior.device_ok:
                    raise AuthorityLost("E_AUTHORITY_LOST")
                if prior.denied:
                    raise PrivacyDenied("E_PRIVACY_DENIED")

        return await self.provider.complete(
            self.spec,
            user,
            validate=job_validator(job),
            precheck=precheck,
            deadline=deadline,
            lineage=lineage,
            attempt_policy="latency",
        )

    @contextlib.contextmanager
    def slot(self) -> Any:
        """One of ``MAX_IN_FLIGHT`` question slots (no await between check and increment)."""
        if self.in_flight >= MAX_IN_FLIGHT:
            raise ResearchUnavailable("busy")
        self.in_flight += 1
        try:
            yield
        finally:
            self.in_flight -= 1


def app_researcher(app: Any) -> Researcher:
    r = getattr(app.state, "researcher", None)
    if r is None:
        r = Researcher(app.state.settings)
        app.state.researcher = r
    return r


async def close_app_researcher(app: Any) -> None:
    r = getattr(app.state, "researcher", None)
    if r is not None:
        app.state.researcher = None
        with contextlib.suppress(Exception):
            await r.aclose()


__all__ = [
    "ANSWERED",
    "INSUFFICIENT",
    "JOBS",
    "MAX_ATTEMPTS",
    "MAX_CALLS",
    "TASK",
    "Claim",
    "Excerpt",
    "ResearchUnavailable",
    "Researcher",
    "Validated",
    "answer_user",
    "app_researcher",
    "apply_verify",
    "assemble",
    "check_user",
    "clip",
    "close_app_researcher",
    "find_verbatim",
    "job_validator",
    "literal_supported",
    "literals_ok",
    "merge_check",
    "parse_plan",
    "plan_user",
    "qnorm",
    "refine_user",
    "rank_sources",
    "research_chain",
    "validate_answer",
    "verify_user",
]
