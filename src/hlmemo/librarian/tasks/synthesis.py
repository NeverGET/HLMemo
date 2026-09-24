"""Low-confidence synthesis for ``memory.query`` (PHASE2-4-ROADMAP W2e; D-062, D-066, D-067, D-071).

``core/synthesis_service`` calls ``Synthesizer.synthesize`` when a query asked for it
(``synthesize:true``) and the fast path is weak. It runs IN THE API PROCESS, bounded by a hard
wall-clock cap (``SYNTH_TIMEOUT_S`` = 6 s), and never raises: every timeout, breaker, budget stop,
privacy denial or model failure comes back as a status and the query answers without a synthesis
(``synthesis_unavailable``). The guards are the risk judge's (``librarian/risk_judge.py``):

* **Privacy default-deny** (``librarian.privacy``): the gate runs over the excerpts' versions at
  plan time and AGAIN before every provider attempt (retries and fallback included), on its own
  short unpooled connections. Only the excerpts it allows enter the prompt: ``device:*`` items,
  projects with ``policy.librarian=off``, items co-owned by an ungranted project and non-current
  versions never leave the host. No transaction is held across the call: ``core/synthesis_service``
  detaches the API request transaction first and re-checks authority and visibility afterwards.
* **Redaction** of the prompt (provider) and of every returned sentence (CC-5 free text).
* **Spend guard**: the provider's atomic hour/day/month reservation and the ``llm_calls`` ledger.
* **D-067 guards**: schema + enum validation, status/sentences consistency (a mismatch is a schema
  failure, retried once), and the citation validator: every sentence must cite at least one excerpt
  id it was shown; ids it was not shown are dropped and counted, and a sentence left without a
  valid citation is DROPPED. A support guard (Sol 51 #3) drops every sentence whose numbers,
  identifiers, paths, commands or quoted strings do not occur verbatim in its cited excerpts; if
  that leaves nothing, the result is an abstention. An answer with no surviving (cited) sentence
  is a failure (``guard``), never a silent empty answer. **Abstention is first-class**:
  ``insufficient_evidence`` is a successful, judged outcome.
* **Qualification** (D-017, D-071): a profile whose file lists ``disabled_tasks = ["synthesis"]``
  is left out of the chain; a result from a qualified fallback profile is labelled ``fallback``.
  The fallback is ``HLM_FALLBACK_PROFILE__SYNTHESIS`` when set, else ``HLM_FALLBACK_PROFILE`` (D-094).
* The ``latency`` attempt policy (``Provider.complete(attempt_policy="latency")``): one bounded
  primary attempt (≤ 55 % of the remaining deadline), then straight to the qualified fallback
  with the rest, no backoff sleeps: a stalled or failing primary still yields a ``fallback`` answer
  inside the cap (BACKLOG, D-084 bake-off).
* Circuit breakers PER PROFILE (3 consecutive failed attempts → skip that profile for 30 s,
  doubling to 15 min; counted by the provider, ``self.breaker`` is a ``ChainBreakers`` view: a
  failing primary never suppresses the fallback) and at most ``MAX_IN_FLIGHT`` concurrent
  syntheses per process (the rest answer ``busy``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from hlmemo.librarian import privacy
from hlmemo.librarian.budget import Caps, DbBudget, NoBudget
from hlmemo.librarian.cassette import CassetteStore
from hlmemo.librarian.errors import (
    AuthorityLost,
    BudgetDeferred,
    CassetteMiss,
    DeadlineExceeded,
    LlmConfigError,
    LlmDisabled,
    PrivacyDenied,
    ProviderUnavailable,
    SchemaFail,
)
from hlmemo.librarian.ledger import DbLedger
from hlmemo.librarian.profiles import LlmProfile, profile_chain
from hlmemo.librarian.prompts import TaskSpec, load_task
from hlmemo.librarian.provider import ChainBreakers, Clock, Provider
from hlmemo.librarian.redact import Redactor
from hlmemo.librarian.risk_judge import ConnectFactory, direct_connector

log = logging.getLogger("hlmemo.librarian.synthesis")

TASK = "synthesis"
JOB_NAME = "synthesis"
#: hard wall-clock cap of one synthesis (W2e: 6 s)
SYNTH_TIMEOUT_S = 6.0
#: per-request HTTP timeout inside the cap, so a stalled attempt settles its reservation and writes
#: its ledger row instead of being cancelled mid-flight and swept later as worst case
HTTP_TIMEOUT_S = 5.5
MAX_EXCERPTS = 10
EXCERPT_CHARS = 2400
SENTENCE_CHARS = 600
MAX_SENTENCES = 6
MAX_IN_FLIGHT = 4
BREAKER_THRESHOLD = 3
BREAKER_OPEN_S = 30.0
BREAKER_MAX_OPEN_S = 900.0

# statuses
OK = "ok"
OK_FALLBACK = "ok_fallback"  # produced by the (qualified) fallback profile: labelled (D-066)
DISABLED = "disabled"
BUSY = "busy"
UNAVAILABLE = "unavailable"
TIMEOUT = "timeout"
BUDGET = "budget"
SCHEMA_FAIL = "schema_fail"
PRIVACY = "privacy"  # the device lost its authority, or the gate denied an item mid-call
WITHHELD = "withheld"  # the privacy gate allowed none of the excerpts
GUARD = "guard"  # D-067: the model answered, but no sentence cited an excerpt it was shown
ERROR = "error"
SUCCESS = frozenset({OK, OK_FALLBACK})

ANSWERED = "answered"
INSUFFICIENT = "insufficient_evidence"


@dataclass(slots=True)
class Excerpt:
    version_id: int
    clue: str  # the hit's chunk clue (v<version_id>.<ordinal>) the sentence will cite
    title: str
    date: str  # valid_from, YYYY-MM-DD
    text: str


@dataclass(slots=True)
class Sentence:
    text: str
    clues: list[str]  # cited hit clues, in the model's order, deduplicated


@dataclass(slots=True)
class SynthResult:
    status: str
    answer: str | None = None  # ANSWERED | INSUFFICIENT when status is a success
    sentences: list[Sentence] = field(default_factory=list)
    denied: set[int] = field(default_factory=set)  # version ids the privacy gate withheld
    dropped: int = 0  # sentences dropped by the citation validator
    bad_cites: int = 0  # cited ids that were never shown to the model
    profile: str | None = None
    latency_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status in SUCCESS

    @property
    def fallback(self) -> bool:
        return self.status == OK_FALLBACK


def synthesis_chain(settings: Any) -> list[LlmProfile]:
    """The primary plus the synthesis fallback (``HLM_FALLBACK_PROFILE__SYNTHESIS`` when set, else
    ``HLM_FALLBACK_PROFILE``; D-094) minus the profiles not qualified for synthesis (D-071)."""
    try:
        chain = profile_chain(settings, TASK)
    except LlmConfigError as exc:
        log.warning("synthesis disabled: %s", exc)
        return []
    return [p for p in chain if TASK not in p.disabled_tasks]


def user_message(question: str, excerpts: list[dict[str, str]]) -> str:
    payload = {"question": question, "excerpts": excerpts}
    return f"JOB: {JOB_NAME}\nINPUT: {json.dumps(payload, ensure_ascii=False)}"


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + " …"


def _consistency(obj: dict[str, Any]) -> str | None:
    """D-067: status and sentences must agree (a mismatch is a schema failure: retried once)."""
    if (obj.get("status") == ANSWERED) != bool(obj.get("sentences")):
        return "status/sentences mismatch"
    return None


_BACKTICK = re.compile(r"`([^`]+)`")
_QUOTED = re.compile(r'"([^"]{2,})"|“([^”]{2,})”|«([^»]{2,})»')
_EDGE = "()[]{}<>,;:!?'\"`“”‘’«»…*"
_SYMBOLS = "€$£¥~≈≤≥±+%°#"
_FILE_EXT = re.compile(r"\.(md|py|toml|json|jsonl|ya?ml|sh|log|txt|sql|js|ts|conf|env|lock|cfg|ini|html)$")
_NUM_UNIT = re.compile(r"^(\d[\d.,]*)[a-z%µ]{1,3}$")
_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFKC", text).casefold()).strip()


def claims(text: str) -> list[str]:
    """The checkable literals of a sentence (Sol 51 #3): backticked spans, quoted strings, and every
    token carrying a digit, ``_``, ``/``, ``::``, ``@``, ``=``, a leading ``-`` flag or a file
    extension (numbers, identifiers, paths, commands). Edge punctuation and currency/unit symbols
    are stripped. A cheap support guard, not a semantic check."""
    out = [m.group(1) for m in _BACKTICK.finditer(text)]
    out += [next(g for g in m.groups() if g) for m in _QUOTED.finditer(text)]
    for tok in text.split():
        core = tok.strip(_EDGE).rstrip(".").strip(_EDGE + _SYMBOLS + ".")
        if len(core) < 1:
            continue
        if (
            any(ch.isdigit() for ch in core)
            or any(mark in core for mark in ("_", "/", "::", "@", "="))
            or re.match(r"-{1,2}[A-Za-z]", core)
            or _FILE_EXT.search(core.casefold())
        ):
            out.append(core)
    return [c for c in out if c.strip()]


def _token_in(tok: str, hay: str) -> bool:
    """``tok`` occurs in ``hay`` as a WHOLE token (Sol 52 #2): not preceded or followed by a word
    character, and a number is not part of a longer number ("42" is not in "142", "4.2" or "42.5";
    "foo" is not in "foobar")."""
    if not tok:
        return True
    before = r"(?<!\w)" + (r"(?<!\d[.,])" if tok[0].isdigit() else "")
    after = r"(?!\w)" + (r"(?![.,]\d)" if tok[-1].isdigit() else "")
    return re.search(before + re.escape(tok) + after, hay) is not None


def supported(claim: str, hay: str) -> bool:
    """``claim`` occurs verbatim as a whole token (case-insensitive, whitespace-collapsed) in
    ``hay``; tolerated: thousands separators, a number glued to its unit (``10s`` for ``10 s``) and
    hyphen-joined parts (``top-3`` for ``top 3``), each part again a whole token."""
    c = _norm(claim)
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


def validate_sentences(
    raw: list[Any], local: dict[str, Excerpt], redact: Callable[[str], str]
) -> tuple[list[Sentence], int, int, int]:
    """The citation validator (D-067): ``(kept, dropped, bad_cites, unsupported)``. A cited id must
    be one shown to the model (``local``); others are removed and counted. A sentence with no valid
    citation, or with no text, is dropped. Support guard (Sol 51 #3): every number, identifier,
    path, command-like token and quoted string of a sentence must occur verbatim in the title or
    text of at least one of its cited excerpts, else the sentence is dropped (``unsupported``).
    Kept text is redacted, whitespace-collapsed and length-capped."""
    kept: list[Sentence] = []
    dropped = bad = unsupported = 0
    for s in raw[:MAX_SENTENCES]:
        if not isinstance(s, dict):
            dropped += 1
            continue
        clues: list[str] = []
        cited: list[Excerpt] = []
        for cid in s.get("cite") or []:
            ex = local.get(str(cid).strip())
            if ex is None:
                bad += 1
                continue
            if ex.clue not in clues:
                clues.append(ex.clue)
                cited.append(ex)
        raw_text = " ".join(str(s.get("text") or "").split())
        text = " ".join(redact(raw_text).split())[:SENTENCE_CHARS]
        if not clues or not text:
            dropped += 1
            continue
        hay = _norm("\n".join(f"{e.title}\n{e.text}" for e in cited))
        if not all(supported(c, hay) for c in claims(raw_text)):
            dropped += 1
            unsupported += 1
            continue
        kept.append(Sentence(text, clues))
    dropped += max(0, len(raw) - MAX_SENTENCES)
    return kept, dropped, bad, unsupported


class Synthesizer:
    def __init__(
        self,
        settings: Any,
        *,
        provider: Provider | None = None,
        chain: list[LlmProfile] | None = None,
        connect: ConnectFactory | None = None,
        clock: Clock | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = SYNTH_TIMEOUT_S,
        cassette_dir: Path | None = None,
    ) -> None:
        self.settings = settings
        self.timeout_s = timeout_s
        self.in_flight = 0
        self.clock = clock or Clock()
        self.provider: Provider | None = None
        #: the provider's per-profile breakers (a failing primary never suppresses the fallback)
        self.breaker = ChainBreakers(lambda: self.provider, task=TASK)
        default_connect, conn_ctx = direct_connector(settings.db_dsn, settings)
        self.connect = connect or default_connect
        if provider is not None:
            self.chain = list(provider.chain)
        else:
            self.chain = list(chain) if chain is not None else synthesis_chain(settings)
        self.spec: TaskSpec = load_task(TASK)
        self._enabled = bool(settings.librarian_enabled) and settings.llm_mode != "off" and bool(self.chain)
        if provider is None and self._enabled:
            provider = self._build_provider(conn_ctx, transport, cassette_dir)
        self.provider = provider

    def _build_provider(
        self,
        conn_ctx: Callable[[], Any],
        transport: httpx.AsyncBaseTransport | None,
        cassette_dir: Path | None,
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
            else DbBudget(conn_ctx, Caps.from_settings(s), ttl_s=s.llm_reservation_ttl_s)
        )
        return Provider(
            self.chain,
            mode=s.llm_mode,
            budget=budget,
            ledger=DbLedger(conn_ctx),
            cassettes=cassettes,
            transport=transport,
            clock=self.clock,
            redactor=Redactor.from_settings(s),
            timeout_s=min(float(s.llm_timeout_s), HTTP_TIMEOUT_S),
            breaker_threshold=BREAKER_THRESHOLD,  # the API synthesizer's own per-profile breakers
            breaker_open_s=BREAKER_OPEN_S,
            breaker_max_open_s=BREAKER_MAX_OPEN_S,
            job_call_cap=s.llm_job_call_cap,
            budget_disabled=s.llm_budget_disabled,
        )

    @property
    def enabled(self) -> bool:
        return self._enabled and self.provider is not None

    async def aclose(self) -> None:
        if self.provider is not None:
            await self.provider.aclose()

    def unavailable(self) -> str | None:
        """Why a synthesis would not reach the provider right now (None: it would try); lets the
        caller keep its request transaction for a call that cannot happen."""
        if not self.enabled:
            return DISABLED
        if self.in_flight >= MAX_IN_FLIGHT:
            return BUSY
        if self.breaker.remaining_s() > 0:
            return UNAVAILABLE
        return None

    # ------------------------------------------------------------------ synthesis
    async def synthesize(
        self,
        question: str,
        excerpts: list[Excerpt],
        capabilities: dict[str, Any],
        *,
        timeout_s: float | None = None,
    ) -> SynthResult:
        """``timeout_s`` (the caller's remaining request deadline) can only shorten the cap."""
        t0 = time.perf_counter()
        if not self.enabled:
            return SynthResult(DISABLED)
        excerpts = excerpts[:MAX_EXCERPTS]
        if not excerpts:
            return SynthResult(WITHHELD)
        if self.in_flight >= MAX_IN_FLIGHT:
            return SynthResult(BUSY)
        if not self.breaker.allow():
            return SynthResult(UNAVAILABLE)
        self.in_flight += 1  # no await since the check: atomic on the event loop
        try:
            cap = self.timeout_s if timeout_s is None else max(0.05, min(self.timeout_s, timeout_s))
            # One deadline for the whole call (R2 rehearsal finding, same as risk_judge): the
            # provider budgets every HTTP timeout to end before it, so the cap never cuts a request
            # mid-flight and every attempt settles its reservation and writes its ledger row.
            deadline = asyncio.get_running_loop().time() + cap
            async with asyncio.timeout_at(deadline):
                result = await self._synthesize(question, excerpts, capabilities, deadline)
        except (TimeoutError, DeadlineExceeded):  # the provider counted per-profile failures
            result = SynthResult(TIMEOUT)
        except (ProviderUnavailable, httpx.HTTPError, OSError) as exc:
            log.warning("synthesis unavailable: %s", type(exc).__name__)
            result = SynthResult(UNAVAILABLE)
        except BudgetDeferred:
            result = SynthResult(BUDGET)
        except SchemaFail:  # the provider answered (its breaker closed); the model output was wrong
            result = SynthResult(SCHEMA_FAIL)
        except (PrivacyDenied, AuthorityLost):
            result = SynthResult(PRIVACY)
        except (LlmDisabled, LlmConfigError) as exc:
            log.warning("synthesis disabled: %s", exc)
            result = SynthResult(DISABLED)
        except CassetteMiss as exc:  # strict replay (CI): an error status, never a crash
            log.error("synthesis cassette miss: %s", exc)
            result = SynthResult(ERROR)
        except Exception:  # noqa: BLE001 - synthesis is advisory: any failure degrades, never raises
            log.exception("synthesis failed")
            result = SynthResult(ERROR)
        finally:
            self.in_flight -= 1
        result.latency_ms = int((time.perf_counter() - t0) * 1000)
        return result

    async def _synthesize(
        self,
        question: str,
        excerpts: list[Excerpt],
        capabilities: dict[str, Any],
        deadline: float | None = None,
    ) -> SynthResult:
        assert self.provider is not None
        ids = list(dict.fromkeys(e.version_id for e in excerpts))
        verdict, _loaded = await privacy.gate(self.connect, capabilities, ids)
        if not verdict.device_ok:
            return SynthResult(PRIVACY, denied=set(ids))
        allowed = [e for e in excerpts if verdict.allowed(e.version_id)]
        denied = {e.version_id for e in excerpts if not verdict.allowed(e.version_id)}
        if not allowed:
            return SynthResult(WITHHELD, denied=denied)
        local = {f"C{i + 1}": e for i, e in enumerate(allowed)}
        shown = [
            {"id": lid, "title": e.title, "date": e.date, "text": _clip(e.text, EXCERPT_CHARS)}
            for lid, e in local.items()
        ]
        sent = list(dict.fromkeys(e.version_id for e in allowed))

        async def precheck() -> None:  # before EVERY provider attempt (D-062)
            again, _ = await privacy.gate(self.connect, capabilities, sent)
            if not again.device_ok:
                raise AuthorityLost("E_AUTHORITY_LOST")
            if not all(again.allowed(v) for v in sent):
                raise PrivacyDenied("E_PRIVACY_DENIED")

        res = await self.provider.complete(
            self.spec,
            user_message(question, shown),
            validate=_consistency,
            precheck=precheck,
            deadline=deadline,
            attempt_policy="latency",  # one bounded primary attempt, then the fallback in the cap
        )
        status = OK if res.profile == self.chain[0].name else OK_FALLBACK
        if res.output.get("status") == INSUFFICIENT:
            return SynthResult(status, answer=INSUFFICIENT, denied=denied, profile=res.profile)
        redactor = self.provider.redactor
        kept, dropped, bad, unsupported = validate_sentences(
            res.output.get("sentences") or [], local, redactor.text
        )
        if not kept and unsupported:  # Sol 51 #3: nothing the excerpts support survived: abstain
            return SynthResult(
                status,
                answer=INSUFFICIENT,
                denied=denied,
                dropped=dropped,
                bad_cites=bad,
                profile=res.profile,
            )
        if not kept:  # D-067: an answer whose every sentence is uncited is a failure, not an answer
            return SynthResult(GUARD, denied=denied, dropped=dropped, bad_cites=bad, profile=res.profile)
        return SynthResult(
            status,
            answer=ANSWERED,
            sentences=kept,
            denied=denied,
            dropped=dropped,
            bad_cites=bad,
            profile=res.profile,
        )


def app_synthesizer(app: Any) -> Synthesizer:
    """The app's synthesizer, built on first use (the provider keeps breakers and HTTP clients)."""
    synth = getattr(app.state, "synthesizer", None)
    if synth is None:
        synth = Synthesizer(app.state.settings)
        app.state.synthesizer = synth
    return synth


async def close_app_synthesizer(app: Any) -> None:
    synth = getattr(app.state, "synthesizer", None)
    if synth is not None:
        app.state.synthesizer = None
        with contextlib.suppress(Exception):
            await synth.aclose()


__all__ = [
    "ANSWERED",
    "INSUFFICIENT",
    "MAX_EXCERPTS",
    "SUCCESS",
    "SYNTH_TIMEOUT_S",
    "TASK",
    "Excerpt",
    "Sentence",
    "SynthResult",
    "Synthesizer",
    "app_synthesizer",
    "close_app_synthesizer",
    "claims",
    "supported",
    "synthesis_chain",
    "user_message",
    "validate_sentences",
]
