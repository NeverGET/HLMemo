"""The research librarian's LLM side (``memory.ask``; D-130, D-136): prompts, deterministic answer
validation and the provider wrapper. ``core/research_service`` runs the loop and the DB phases.

One provider TASK, ``research``, for the four JOBs of the loop (one system prompt, one schema, a
job-specific shape check): ``plan`` (map + question → queries + map sections), ``answer`` (excerpts →
answer + quoted claims + primary/related), ``check`` (the COMPLETENESS pass: question + draft + the
same excerpts → the full revised answer, with the draft's missing facts listed first) and ``refine``
(one more search round, only when the answer abstained or is unsure). The per-task fallback is
therefore ``HLM_FALLBACK_PROFILE__RESEARCH`` (D-094); a profile listing ``research`` in
``disabled_tasks`` is not used (D-071).

Guards (the W2e/W2d ones): privacy default-deny before EVERY attempt (``precheck``: the strict
``librarian.privacy`` gate over exactly the version ids whose text is in the prompt), redaction of
the prompt and of the returned free text, the spend guard (atomic reservation + ``llm_calls``
ledger), the ``latency`` attempt policy (one bounded primary attempt, then the qualified fallback),
per-profile breakers, a per-request lineage whose DB-enforced ceiling (``MAX_ATTEMPTS``) bounds the
provider requests of one question, and at most ``MAX_IN_FLIGHT`` questions per process.

Validation (``validate_answer``) is deterministic. Excerpt ids are the handles the caller can drill
(``vN.M``/``vN``). A claim's ``quote`` must occur in a cited excerpt after ``qnorm`` (NFKC, casefold,
typographic quotes, markdown markers, whitespace, and a DECIMAL comma between digits read as a point:
TR "1,6" = "1.6"; a thousands-style "1,600" is left alone); a quote found only in another SHOWN
excerpt is re-attributed to it; the quote returned is the original substring of the excerpt. Every
number/identifier/path literal of a claim must occur in its cited excerpts. quote + literals ok →
kept; literals ok but no verbatim quote → downgraded (its handles are only ``related``); literals
not ok → dropped. A sentence of the free-text answer whose literals are not in the returned sources
(primary ∪ related) is dropped. No kept claim (or no answer sentence) left → abstention.
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
JOBS = ("plan", "answer", "check", "refine")
#: logical LLM calls of one question (plan, answer, [refine, answer], check)
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
MAX_CLAIMS = 10
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


def literals_ok(text: str, hay: str) -> bool:
    return all(literal_supported(x, hay) for x in literal_claims(text))


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
    """The COMPLETENESS pass: the question, the draft (answer text + its claims) and EVERY excerpt
    the draft was written from, so each retrieved candidate fact can be checked against the draft."""
    payload = {
        "question": question,
        "draft": {"answer": draft.get("answer", ""), "claims": draft.get("claims", [])},
        "excerpts": [e.shown() for e in excerpts],
    }
    return "JOB: check\n" + _input(payload)


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

    return plan if job in ("plan", "refine") else answer


def parse_plan(obj: dict[str, Any] | None, question: str) -> tuple[list[str], list[str]]:
    """``(queries, sections)``: at most ``MAX_QUERIES`` distinct non-empty queries other than the
    question, and at most ``MAX_SECTIONS`` well-formed handles (the caller checks them against the
    map)."""
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
    quote: str  # the verbatim span of the quoting excerpt ('' when none verified)
    cite: list[str]  # handles (shown), the quoting excerpt first
    state: str  # kept | downgraded | dropped
    source: str | None = None  # the handle whose text contains ``quote``

    def draft(self) -> dict[str, Any]:
        return {"text": self.text, "quote": self.quote, "cite": self.cite}


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

    @property
    def answered(self) -> bool:
        return self.status == ANSWERED

    @property
    def kept(self) -> list[Claim]:
        return [c for c in self.claims if c.state == "kept"]

    def quote_of(self, handle: str) -> str | None:
        for c in self.claims:
            if c.state == "kept" and c.source == handle:
                return c.quote
        return None

    def draft(self) -> dict[str, Any]:
        """The draft handed to the completeness pass: the answer and the claims that survived."""
        return {"answer": self.answer, "claims": [c.draft() for c in self.claims if c.state != "dropped"]}


_SENTENCE = re.compile(r"(?<=[.!?…])\s+(?=\S)")


def _lower(conf: str) -> str:
    return {"high": "medium", "medium": "low"}.get(conf, "low")


def _hay(excerpts: list[Excerpt]) -> str:
    return _lit_norm("\n".join(f"{e.handle}\n{e.title}\n{e.path}\n{e.text}" for e in excerpts))


def validate_answer(
    obj: dict[str, Any] | None, shown: dict[str, Excerpt], redact: Callable[[str], str] = lambda s: s
) -> Validated:
    """The deterministic check of an ``answer``/``check`` output against the excerpts it was shown
    (module docstring). Never cites a handle that was not shown."""
    obj = obj or {}
    status = obj.get("status") if obj.get("status") in (ANSWERED, INSUFFICIENT) else None
    conf = obj.get("confidence") if obj.get("confidence") in _CONF else "low"
    missing = [" ".join(str(m).split())[:200] for m in (obj.get("missing") or []) if str(m).strip()][:12]
    out_claims: list[Claim] = []
    raw_claims = obj.get("claims") if isinstance(obj.get("claims"), list) else []
    for c in raw_claims[:MAX_CLAIMS]:
        if not isinstance(c, dict):
            continue
        text = " ".join(str(c.get("text") or "").split())
        quote = str(c.get("quote") or "")
        cites = list(dict.fromkeys(str(x).strip() for x in (c.get("cite") or []) if str(x).strip() in shown))
        if not text or not cites:
            out_claims.append(Claim(text, "", cites, "dropped"))
            continue
        source, span = None, None
        for h in cites:
            span = find_verbatim(quote, shown[h].text)
            if span is not None:
                source = h
                break
        if span is None:  # re-attribute to another SHOWN excerpt that holds the quote verbatim
            for h, ex in shown.items():
                if h in cites:
                    continue
                span = find_verbatim(quote, ex.text)
                if span is not None:
                    source = h
                    cites = [h, *cites]
                    break
        elif source is not None:
            cites = [source, *(h for h in cites if h != source)]
        hay = _hay([shown[h] for h in cites])
        if not literals_ok(text, hay):
            out_claims.append(Claim(text, "", cites, "dropped"))
        elif span is None:
            out_claims.append(Claim(text, "", cites, "downgraded"))
        else:
            span = " ".join(span.split())
            if len(span) > QUOTE_MAX_CHARS:
                span = span[: QUOTE_MAX_CHARS - 1] + "…"
            out_claims.append(Claim(text, redact(span), cites, "kept", source))
    kept = [c for c in out_claims if c.state == "kept"]
    answer = " ".join(str(obj.get("answer") or "").split())
    model_answered = status == ANSWERED and bool(answer)
    if not model_answered or not kept:
        closest = [h for h in (obj.get("related") or []) if isinstance(h, str) and h in shown][:3]
        return Validated(
            INSUFFICIENT,
            status,
            "",
            out_claims,
            [],
            list(dict.fromkeys(closest)),
            "low",
            guard=model_answered,
            missing=missing,
        )
    if any(c.state != "kept" for c in out_claims):
        conf = _lower(conf)
    quoted = {c.source for c in kept}
    primary: list[str] = []
    related: list[str] = []
    for h in obj.get("primary") or []:
        if isinstance(h, str) and h in shown and h not in primary:
            (primary if h in quoted and len(primary) < MAX_PRIMARY else related).append(h)
    if not primary:
        primary = [c.source for c in kept if c.source][:MAX_PRIMARY]
        primary = list(dict.fromkeys(primary))
    for h in [
        *(obj.get("related") or []),
        *(h for c in out_claims if c.state != "dropped" for h in c.cite),
    ]:
        if isinstance(h, str) and h in shown and h not in primary and h not in related:
            related.append(h)
    related = related[:MAX_RELATED]
    # the free-text answer: a sentence whose literals are not in the RETURNED sources is dropped
    hay = _hay([shown[h] for h in [*primary, *related]])
    sentences = _SENTENCE.split(answer)
    kept_sentences = [s for s in sentences if literals_ok(s, hay)]
    dropped = len(sentences) - len(kept_sentences)
    text = redact(" ".join(kept_sentences))[:ANSWER_MAX_CHARS]
    if not text.strip():
        return Validated(
            INSUFFICIENT,
            status,
            "",
            out_claims,
            [],
            related[:3],
            "low",
            guard=True,
            dropped_sentences=dropped,
        )
    if dropped:
        conf = _lower(conf)
    return Validated(
        ANSWERED, status, text, out_claims, primary, related, conf, dropped_sentences=dropped, missing=missing
    )


def merge_check(draft: Validated, checked: Validated) -> Validated:
    """The completeness pass wins when it validated as an answer; the draft's kept claims that the
    revision lost are carried over (their quotes are verified), so the pass can only add evidence."""
    if not checked.answered:
        return draft
    have = {qnorm(c.quote) for c in checked.kept}
    extra = [c for c in draft.kept if qnorm(c.quote) not in have]
    if extra:
        checked.claims = [*checked.claims, *extra]
        for c in extra:
            if c.source and c.source not in checked.primary and c.source not in checked.related:
                if len(checked.related) < MAX_RELATED:
                    checked.related.append(c.source)
    return checked


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

    async def complete(
        self,
        job: str,
        user: str,
        *,
        capabilities: dict[str, Any],
        gate_ids: list[int],
        deadline: float,
        lineage: str,
    ) -> LlmResult:
        """One logical call. The strict privacy gate over ``gate_ids`` runs before EVERY attempt
        (retries and the fallback included) and aborts before any byte is sent."""
        assert self.provider is not None
        ids = sorted(set(gate_ids))

        async def precheck() -> None:
            verdict, _ = await privacy.gate(self.connect, capabilities, ids, bodies=False)
            if not verdict.device_ok:
                raise AuthorityLost("E_AUTHORITY_LOST")
            if verdict.denied:
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
    "research_chain",
    "validate_answer",
]
