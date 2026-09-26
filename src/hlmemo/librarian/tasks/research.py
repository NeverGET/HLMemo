"""The research librarian's LLM side (``memory.ask``; D-130, D-136): prompts, deterministic answer
validation and the provider wrapper. ``core/research_service`` runs the loop and the DB phases.

One provider TASK, ``research``, for the four JOBs of the loop (one system prompt, one schema, a
job-specific shape check): ``plan`` (map + question → queries + map sections), ``answer`` (excerpts →
answer + claims, each with 1-3 verbatim supporting quotes), ``check`` (the COMPLETENESS + REPAIR pass:
the question's sub-asks against the draft and the same excerpts → the full revised answer; a missing
fact is added only as a new quoted claim, and each claim flagged by the deterministic copy-through /
attribution checks is repaired in the same call) and ``refine`` (one more search round, only when the
answer abstained). At most 4 sequential steps (addendum 7). The per-task fallback is therefore
``HLM_FALLBACK_PROFILE__RESEARCH`` (D-094); a profile listing ``research`` in ``disabled_tasks`` is
not used (D-071).

Guards (the W2e/W2d ones): privacy default-deny before EVERY attempt (``precheck``: the strict
``librarian.privacy`` gate over exactly the version ids whose text is in the prompt), redaction of
the prompt and of the returned free text, the spend guard (atomic reservation + ``llm_calls``
ledger), the ``latency`` attempt policy (one bounded primary attempt, then the qualified fallback),
per-profile breakers, a per-request lineage whose DB-enforced ceiling (``MAX_ATTEMPTS``) bounds the
provider requests of one question, and at most ``MAX_IN_FLIGHT`` questions per process.

Validation (``validate_answer``, ``enforce_attribution``) is deterministic. Excerpt ids are the
handles the
caller can drill (``vN.M``/``vN``). Each quote must occur CONTIGUOUSLY (no skipped word, review 79
T6) in the excerpt it names after ``qnorm``
(NFKC, casefold, typographic quotes, markdown markers, whitespace, and a DECIMAL comma between digits
read as a point: TR "1,6" = "1.6"; a thousands-style "1,600" is left alone); a quote found only in
another SHOWN excerpt is re-attributed to it; the quote returned is the original substring of the
excerpt. A claim is kept only when its quotes TOGETHER contain every number/identifier/path literal of
the claim (a literal its quotes lack is re-quoted deterministically from the sentence of a cited
excerpt that states it, ``requote``); a claim without a verified quote is downgraded (its handles
only ``related``) or dropped. A claim that drops or inserts a negation or a modal its quotes state
about the same words (``polarity_ok``) is dropped.
The free-text answer keeps only the sentences whose literals are in the kept claims' quotes and whose
polarity agrees with them.
primary = the handles supporting the most kept claims (≤ 3). No kept claim left → abstention.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field, replace
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
from hlmemo.librarian.provider import AttemptGuard, ChainBreakers, Clock, LlmResult, Provider
from hlmemo.librarian.redact import Redactor
from hlmemo.librarian.risk_judge import ConnectFactory, direct_connector
from hlmemo.librarian.tasks.synthesis import claims as literal_claims

log = logging.getLogger("hlmemo.librarian.research")

TASK = "research"
JOBS = ("plan", "answer", "check", "refine")
#: sequential LLM steps of one question (addendum 7): plan, answer, completeness+repair — or, when
#: the answer abstained, plan, answer, refine, answer
MAX_CALLS = 4
#: provider requests of one question (schema retries and fallbacks included), DB-enforced per lineage
MAX_ATTEMPTS = 9
MAX_IN_FLIGHT = 4
#: addendum 5: an explicit max_tokens on EVERY call, per JOB (reasoning tokens included); a
#: runaway output is cut there, and the per-question budget reserves exactly this worst case
JOB_MAX_TOKENS = {"plan": 800, "refine": 800, "answer": 3000, "check": 3000}
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
ANSWER_MAX_CHARS = 3200
MAX_PRIMARY = 3
MAX_RELATED = 5
MAX_QUERIES = 4
MAX_SECTIONS = 6

ANSWERED = "answered"
INSUFFICIENT = "insufficient_evidence"
_CONF = ("high", "medium", "low")
_HANDLE = re.compile(r"^v[1-9][0-9]*(\.(0|[1-9][0-9]*))?$")


# --------------------------------------------------------------------------- normalisation
_DROP = frozenset("*`_#>|•◦▪")
_QUOTES = {"“": '"', "”": '"', "„": '"', "«": '"', "»": '"', "’": "'", "‘": "'", "‚": "'", "‛": "'"}
_DASHES = frozenset("‐‑‒–—―−")
#: a list marker at the start of a line ("- item", "* item", "+ item", "• item"): markup, not text
_LIST_MARKER = re.compile(r"(?m)^[ \t]*([-*+•◦▪])[ \t]+")
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
    """``qnorm`` of ``text`` (which must already be NFC) plus, for every character of the result,
    the index of the character of ``text`` it came from (so a match maps back to the raw span).
    Addendum 2: whitespace and line breaks collapse, markdown markup (table pipes, ``**``, backticks,
    headings, quotes, list markers) is dropped, quote and dash variants are unified, casefolded."""
    markers = {m.start(1) for m in _LIST_MARKER.finditer(text)}
    out: list[str] = []
    idx: list[int] = []
    space = True
    for i, ch in enumerate(text):
        if i in markers:
            piece = " "
        elif _decimal_comma_at(text, i):
            piece = "."
        else:
            piece = unicodedata.normalize("NFKC", ch).casefold()
            piece = "".join("-" if c in _DASHES else _QUOTES.get(c, c) for c in piece)
        for c in piece:
            if c in _DROP:
                continue  # markup is not text, and not a word break either ("**X**:" = "X:")
            if c.isspace():
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
    return _norm_with_map(unicodedata.normalize("NFC", text))[0]


def find_verbatim(quote: str, text: str) -> str | None:
    """The raw span of ``text`` that ``quote`` matches under ``qnorm``, or ``None``. Quotes shorter
    than ``QUOTE_MIN_CHARS`` normalised characters never match."""
    q = qnorm(quote)
    if len(q) < QUOTE_MIN_CHARS:
        return None
    raw = unicodedata.normalize("NFC", text)
    t, idx = _norm_with_map(raw)
    at = t.find(q)
    if at < 0:
        return None
    start, end = idx[at], idx[at + len(q) - 1]
    # the raw span keeps the emphasis/code markers around its edges ("**X**: …", not "X**: …")
    while start > 0 and raw[start - 1] in "*`":
        start -= 1
    while end + 1 < len(raw) and raw[end + 1] in "*`":
        end += 1
    return raw[start : end + 1]


_WORD = re.compile(r"\w+(?:[.,]\d+)*")


def _words(text: str) -> list[tuple[str, int, int]]:
    """``(normalised word, start, end)`` of the NFC ``text`` (a decimal comma reads as a point)."""
    out = []
    for m in _WORD.finditer(text):
        w = _DECIMAL_COMMA.sub(".", unicodedata.normalize("NFKC", m.group(0)).casefold()).replace("_", "")
        if w:
            out.append((w, m.start(), m.end()))
    return out


def locate_quote(quote: str, text: str) -> str | None:
    """The raw span of ``text`` supporting ``quote``: a CONTIGUOUS match under ``qnorm`` only
    (whitespace, markup, NFC, dashes and quote variants are normalised; no word is ever skipped).
    Review 79 T6: the addendum 2 in-order fallback let a quote drop a word ("is **not** enabled" →
    "is enabled") and still verify; it is gone."""
    return find_verbatim(quote, text)


#: review 79/80 T6: polarity words. A claim may not drop a negation or an (epistemic) modal that its
#: quotes state about the same words, nor insert a negation (``polarity_ok``). ``n't`` reads as ``not``.
_NEGATION = frozenset(
    "not no never none nor neither cannot without unable instead rather unlike rejected "
    "değil yok hiç asla hiçbir yerine reddedildi "
    "nicht kein keine keinen keinem keiner keines nie niemals ohne statt anstatt abgelehnt".split()
)
#: epistemic hedges only: "can"/"could"/"kann" state an ability ("could not read"), not a doubt
_MODAL = frozenset(
    "may might possibly maybe perhaps probably likely unlikely "
    "belki olabilir olabilirler muhtemelen "
    "vielleicht wahrscheinlich eventuell".split()
)
_NT = re.compile(r"(?i)(\w)n['’]t\b")
#: "LLM-free", "lock-free" read as "no LLM", "no lock"
_FREE = re.compile(r"(?i)\b([\w.]+)-free\b")
#: a TR negative verb ("çağırmaz", "çalışmıyor", "yapılmadı", "kullanılmamalı"): stem + negation suffix
_TR_NEG_VERB = re.compile(
    r"^(\w{3,}?)(m[ae]z(?:l[ae]r)?|m[ıiuü]yor\w*|m[ae]d[ıi]\w*|m[ae]m[ıi]ş\w*|m[ae]y[ae]n\w*|m[ae]m[ae]\w*)$"
)
#: polarity words that FOLLOW what they negate/hedge ("etkin değil", "etkin olabilir"); every other
#: one precedes it ("not enabled", "nicht aktiviert", "may be enabled")
#: the negation a TR negative verb suffix stands for (``_polarity_words``)
_TR_NEG = "\x02"
#: derived negations (contrast/rejection words, TR verb suffixes): evidence that a text DOES negate
#: something, never proof that it inserted a negation (they meet "uncommitted", "skip", a table column)
_SOFT_NEG = frozenset(
    [
        "unable",
        "instead",
        "rather",
        "unlike",
        "rejected",
        "yerine",
        "reddedildi",
        "statt",
        "anstatt",
        "abgelehnt",
        _TR_NEG,
    ]
)
_NEGATION = _NEGATION | {_TR_NEG}
_POSTPOSITIVE = frozenset(["değil", "yok", "olabilir", "olabilirler", "yerine", "reddedildi", _TR_NEG])
#: negations about the words on BOTH sides ("X was rejected" / "D-006 rejected X")
_BOTH_SIDES = frozenset("rejected abgelehnt".split())
#: content words within this many words of a polarity word are what it is about; a TR postpositive
#: one ("… etkin değil", a negative verb) looks further back: its arguments precede the verb (SOV)
POLARITY_WINDOW, POLARITY_WINDOW_SOV = 3, 6
#: a sentence or clause end ("( ) [ ] | ; :" or a dash between spaces): a window never crosses it
_CLAUSE_END = re.compile(r"(?<=[.!?;:])\s+|\s*[()\[\]|—]\s*|\s+[–-]\s+|(?<=[;:])(?=\S)")
#: ", " also ends a window, but "X, not Y" reads as a contrast (``_about``)
_COMMA_END = re.compile(r",\s+")
_BOUNDARY, _COMMA = "\x00", "\x01"


def _polarity_words(text: str) -> list[str]:
    """The normalised words of ``text``, with ``_BOUNDARY`` between its clauses and ``_COMMA`` at a
    ", "; "X-free" reads as "no X" and a TR negative verb as its stem plus ``_TR_NEG``."""
    text = _FREE.sub(r"no \1", _NT.sub(r"\1 not", unicodedata.normalize("NFC", text)))
    out: list[str] = []
    for clause in _CLAUSE_END.split(text):
        if out and out[-1] != _BOUNDARY:
            out.append(_BOUNDARY)
        for i, part in enumerate(_COMMA_END.split(clause)):
            if i:
                out.append(_COMMA)
            for w, _s, _e in _words(part):
                m = _TR_NEG_VERB.match(w) if len(w) >= 6 else None
                out.extend([m.group(1), _TR_NEG] if m else [w])
    return out


def _is_content(w: str) -> bool:
    return len(w) >= 3 and w not in _STOP and w not in _NEGATION | _MODAL


def _side(words: list[str], i: int, step: int, limit: int = POLARITY_WINDOW) -> list[str]:
    """Up to ``limit`` words from ``i`` in direction ``step``, stopping at any boundary."""
    out, j = [], i + step
    while 0 <= j < len(words) and len(out) < limit and words[j] not in (_BOUNDARY, _COMMA):
        out.append(words[j])
        j += step
    return out


def _nearest(words: list[str]) -> list[str]:
    return [w for w in words if _is_content(w)][:1]


class _Scope:
    """What one polarity word is about: ``core`` (the content words of its governed side, or of the
    other side when that has none), ``words`` (``core``; a single word widened with the nearest
    content word of the other side, so "is not enabled" is about the gate that is enabled) and, for
    a contrast ("X, not Y" / "0.9 not 0.1"), ``pair`` = {X, Y}. ``words`` decides whether a text
    states the proposition; two scopes are about the same thing when their ``core`` words meet."""

    __slots__ = ("core", "pair", "soft", "words")

    def __init__(self, words: set[str], core: set[str], pair: set[str] | None, soft: bool) -> None:
        self.words, self.core, self.pair, self.soft = words, core, pair, soft


def _about(words: list[str], cls: frozenset[str]) -> list[_Scope]:
    out = []
    for i, w in enumerate(words):
        if w not in cls:
            continue
        sov = w in _POSTPOSITIVE
        left, right = (
            _side(words, i, -1, POLARITY_WINDOW_SOV if sov else POLARITY_WINDOW),
            _side(words, i, +1),
        )
        gov, other = (left, right) if sov else (right, left)
        core = {x for x in gov if _is_content(x)} or {x for x in other if _is_content(x)}
        if w in _BOTH_SIDES:
            core = {x for x in gov + other if _is_content(x)}
        scope = set(core)
        if len(scope) == 1:
            scope |= set(_nearest(other if scope & set(gov) else gov))
        pair = None
        after = _nearest(right)
        if after and not sov:
            if i >= 2 and words[i - 1] == _COMMA and _is_content(words[i - 2]):
                pair = {words[i - 2], after[0]}  # "weekly, not daily"
            elif i >= 1 and _is_content(words[i - 1]) and any(c.isdigit() for c in words[i - 1] + after[0]):
                pair = {words[i - 1], after[0]}  # "0.9 not 0.1"
        if scope:
            out.append(_Scope(scope, core, pair, w in _SOFT_NEG))
    return out


def _hits(scope: set[str], words: set[str]) -> int:
    """How many of ``scope`` are in ``words``; a word also matches its inflections (one is a prefix of
    the other: run/runs, enable/enabled, etkin/etkinleştirildi)."""
    return sum(1 for w in scope if w in words or any(x.startswith(w) or w.startswith(x) for x in words))


def _shares(scope: set[str], words: set[str]) -> bool:
    """At least two (or all, when fewer) of ``scope`` are in ``words``."""
    return bool(scope) and _hits(scope, words) >= min(2, len(scope))


def polarity_ok(text: str, quotes: list[str]) -> bool:
    """Review 79/80 T6: ``text`` (a claim, or an answer sentence) keeps the polarity of ``quotes``, per
    proposition. It fails when
    - a quote negates (or hedges) words that ``text`` states, and no negation (modal) in ``text`` is
      about any of those words — a DROPPED "not" (an unrelated "not" elsewhere never excuses it), or
    - ``text`` negates words that the quotes state, and no quote negation is about them — an
      INSERTED "not".
    A contrast ("X, not Y") is kept when ``text`` names both X and Y (it states the pair). A modal
    ``text`` adds is a hedge, not a contradiction. Scopes stay inside a clause. EN/TR/DE words, "X-free",
    TR negative verb suffixes. D-153: tuned on 111 real claims (0 accepted-true claims rejected by
    design target <= 2)."""
    tw = _polarity_words(text)
    qw = [w for q in quotes for w in [*_polarity_words(q), _BOUNDARY]]
    t_content = {w for w in tw if _is_content(w)}
    q_content = {w for w in qw if _is_content(w)}
    for cls in (_NEGATION, _MODAL):
        t_scopes, q_scopes = _about(tw, cls), _about(qw, cls)
        for sc in q_scopes:
            if not _shares(sc.words, t_content):
                continue
            if sc.pair and _hits(sc.pair, t_content) == len(sc.pair):
                continue  # the claim names both sides of the contrast
            if not any(_hits(sc.words, ts.core) for ts in t_scopes):
                return False  # the quotes say "not X"; the claim says "X"
        if cls is _MODAL:
            continue  # an added hedge weakens a claim; it does not contradict its quote
        for sc in (t for t in t_scopes if not t.soft):
            if _shares(sc.words, q_content) and not any(_hits(qs.words, sc.core) for qs in q_scopes):
                if not (sc.pair and _hits(sc.pair, q_content) == len(sc.pair)):
                    return False  # the claim says "not X"; the quotes say "X"
    return True


def _lit_norm(text: str) -> str:
    """The literal-check form of a text: NFKC, casefold, unified dashes/quotes, a decimal comma read
    as a point, whitespace collapsed."""
    text = unicodedata.normalize("NFKC", unicodedata.normalize("NFC", text)).casefold()
    text = "".join("-" if c in _DASHES else _QUOTES.get(c, c) for c in text)
    return _WS.sub(" ", _DECIMAL_COMMA.sub(".", text)).strip()


def _token_in(tok: str, hay: str) -> bool:
    if not tok:
        return True
    before = r"(?<!\w)" + (r"(?<!\d[.,])" if tok[0].isdigit() else "")
    after = r"(?!\w)" + (r"(?![.,]\d)" if tok[-1].isdigit() else "")
    return re.search(before + re.escape(tok) + after, hay) is not None


_NUM_UNIT = re.compile(r"^(\d[\d.,]*)[a-z%µ]{1,3}$")
#: a TR/EN suffix glued to a number or an identifier ending in a digit: 2026da, D-116da, R3te, 1600ler
_GLUED_SUFFIX = re.compile(r"^(.*\d)([a-zçğıöşü]{1,8})$")
_THOUSANDS = re.compile(r"(?<=\d)[.,](?=\d{3}(?!\d))")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DOT_DATE = re.compile(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$")


def _date_variants(c: str) -> list[str]:
    """2026-09-26 <-> 26.09.2026 / 26/09/2026 (and a non-padded day or month)."""
    m = _ISO_DATE.match(c)
    if m:
        y, mo, d = m.groups()
        return [f"{d}.{mo}.{y}", f"{d}/{mo}/{y}", f"{int(d)}.{int(mo)}.{y}"]
    m = _DOT_DATE.match(c)
    if m:
        d, mo, y = m.groups()
        return [f"{y}-{int(mo):02d}-{int(d):02d}"]
    return []


def literal_supported(literal: str, hay: str) -> bool:
    """``literal`` occurs in ``hay`` (``_lit_norm``ed) as a whole token, tolerating (addendum 3 #2):
    thousands separators (1,600 / 1.600 / 1600), a decimal comma (1,6 = 1.6), a unit glued to a
    number (10s), a currency symbol or code around it ($10 = 10 USD: the symbol is stripped from the
    literal and a code is its own word), a TR/EN suffix glued to a number or an identifier (2026da,
    D-116da), a date written ISO or dotted (2026-09-26 = 26.09.2026) and hyphen-joined parts."""
    c = _lit_norm(literal)
    if not c or _token_in(c, hay):
        return True
    if any(_token_in(v, hay) for v in _date_variants(c)):
        return True
    if _token_in(_THOUSANDS.sub("", c), _THOUSANDS.sub("", hay)):
        return True
    m = _NUM_UNIT.match(c)
    if m and _token_in(m.group(1), hay):
        return True
    m = _GLUED_SUFFIX.match(c)
    if m and _token_in(m.group(1), hay):
        return True
    if "-" in c and re.fullmatch(r"[\w.-]+", c):
        return all(_token_in(p, hay) for p in c.split("-") if p)
    return False


#: a TR/EN suffix after an apostrophe (``%40’ını``, ``D-130'da``, ``v3's``, ``1,6'dır``)
_APOS_SUFFIX = re.compile(r"^(.*?[\w%])['’][^\W\d_]{1,8}$")
#: two plain words joined by a slash ("read/write", "TR/EN", "and/or"): prose, not an identifier
_WORD_PAIR = re.compile(r"^[^\W\d_]+/[^\W\d_]+$")


def literals(text: str) -> list[str]:
    """The checkable literals of ``text`` (``synthesis.claims``): an apostrophe suffix is removed
    (``%40’ını`` is checked as ``40``, ``D-130'da`` as ``D-130``) and a plain ``a/b`` word pair is
    not a literal (addendum 3 #2)."""
    out = []
    for lit in literal_claims(text):
        m = _APOS_SUFFIX.match(lit)
        lit = m.group(1).lstrip("%") if m else lit
        if lit and not _WORD_PAIR.match(lit):
            out.append(lit)
    return out


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


Redact = Callable[[str], str]
#: the rule set every prompt builder applies when the caller passes none (the service passes the
#: provider's own redactor, with the configured e-mail/phone options)
_DEFAULT_REDACTOR = Redactor()


def redact_values(obj: Any, redact: Redact) -> Any:
    """Every string inside a JSON-like value, redacted (dict keys are the builders' constants)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, list):
        return [redact_values(v, redact) for v in obj]
    if isinstance(obj, dict):
        return {k: redact_values(v, redact) for k, v in obj.items()}
    return obj


def _input(payload: dict[str, Any], redact: Redact | None = None) -> str:
    """``INPUT: <json>``. Review 79 T2: every text value is redacted BEFORE the JSON serialisation
    (JSON escapes the quotes of ``password = "..."`` and the escaped form hides the assignment from
    the redactor); the provider still redacts the whole message right before the send."""
    fn = redact or _DEFAULT_REDACTOR.text
    return "INPUT: " + json.dumps(redact_values(payload, fn), ensure_ascii=False)


def _text(text: str, redact: Redact | None) -> str:
    return (redact or _DEFAULT_REDACTOR.text)(text)


def plan_user(question: str, context: str, map_text: str, redact: Redact | None = None) -> str:
    payload = {"project_context": context, "question": question}
    return "JOB: plan\n" + _input(payload, redact) + "\n\n" + _text(map_text, redact)


def answer_user(question: str, excerpts: list[Excerpt], redact: Redact | None = None) -> str:
    payload = {"question": question, "excerpts": [e.shown() for e in excerpts]}
    return "JOB: answer\n" + _input(payload, redact)


def check_user(
    question: str,
    draft: dict[str, Any],
    excerpts: list[Excerpt],
    fixes: dict[int, list[str]] | None = None,
    redact: Redact | None = None,
) -> str:
    """The COMPLETENESS + REPAIR pass (addendum 7): the question, the draft (answer text + its claims
    with their quotes, a claim carrying ``fix`` notes where the deterministic copy-through and
    attribution checks flagged it) and EVERY excerpt the draft was written from, so each sub-ask and
    each retrieved candidate fact is checked against the draft and each flagged claim is repaired in
    the same call."""
    fixes = fixes or {}
    claims = []
    for i, c in enumerate(draft.get("claims", [])):
        claims.append({**c, "fix": fixes[i]} if fixes.get(i) else c)
    payload = {
        "question": question,
        "draft": {"answer": draft.get("answer", ""), "claims": claims},
        "excerpts": [e.shown() for e in excerpts],
    }
    return "JOB: check\n" + _input(payload, redact)


def refine_user(
    question: str, tried: list[str], read: list[Excerpt], map_text: str, redact: Redact | None = None
) -> str:
    payload = {
        "question": question,
        "queries_tried": tried,
        "read_so_far": [{"id": e.handle, "title": e.title} for e in read],
    }
    return "JOB: refine\n" + _input(payload, redact) + "\n\n" + _text(map_text, redact)


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
    # addendum 2 flags (meta.flags): claims saved by a deterministic re-quote, claims whose quotes
    # were completed with the sentence of a missing literal, claims not kept, the FIRST claim (the
    # main fact) not kept
    requoted: int = 0
    completed: int = 0
    dropped_claims: int = 0
    main_dropped: bool = False

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


def support_hay(support: list[tuple[str, str]], shown: dict[str, Excerpt]) -> str:
    """Where a claim's values are checked (addendum 3 #2c): its quotes AND the whole cited excerpts
    (the chunk windows) the quotes come from."""
    return _hay(
        [
            *(q for _h, q in support),
            *(shown[h].text for h in dict.fromkeys(h for h, _q in support) if h in shown),
        ]
    )


def _supports(raw: Any, cited: list[str], shown: dict[str, Excerpt]) -> list[tuple[str, str]]:
    """The verified support of one claim: each quote located (a contiguous normalised substring,
    ``locate_quote``) in the excerpt it names, else re-attributed to another SHOWN excerpt
    holding it; the RAW span of the excerpt is kept for display; deduplicated, ≤ MAX_SUPPORT."""
    out: list[tuple[str, str]] = []
    for s in (raw if isinstance(raw, list) else [])[: MAX_SUPPORT * 2]:
        if not isinstance(s, dict):
            continue
        quote = str(s.get("quote") or "")
        h = str(s.get("id") or "").strip()
        span, where = None, None
        if h in shown:
            span, where = locate_quote(quote, shown[h].text), h
        if span is None:
            for other in [*cited, *shown]:
                if other in shown and other != h:
                    span = locate_quote(quote, shown[other].text)
                    if span is not None:
                        where = other
                        break
        if span is None or where is None:
            continue
        span = _cut(" ".join(span.split()))
        if all(qnorm(span) != qnorm(q) for _h, q in out):
            out.append((where, span))
        if len(out) >= MAX_SUPPORT:
            break
    return out


def _cut(span: str, limit: int = QUOTE_MAX_CHARS) -> str:
    """A long span cut at a word boundary (no ellipsis: a quote stays a verbatim substring)."""
    if len(span) <= limit:
        return span
    head = span[:limit]
    return head.rsplit(" ", 1)[0] if " " in head else head


_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")


def _window(sentence: str, literal: str) -> str:
    """``sentence`` itself, or (when longer than a quote may be) a word-bounded window of it around
    the first occurrence of ``literal``."""
    if len(sentence) <= QUOTE_MAX_CHARS:
        return sentence
    at = sentence.casefold().find(literal.casefold())
    if at < 0:
        return _cut(sentence)
    lo = max(0, at - QUOTE_MAX_CHARS // 2)
    hi = min(len(sentence), at + len(literal) + QUOTE_MAX_CHARS // 2)
    lo = sentence.rfind(" ", 0, lo) + 1 if lo > 0 else 0
    hi_space = sentence.find(" ", hi)
    hi = hi_space if hi_space >= 0 else len(sentence)
    return _cut(sentence[lo:hi].strip())


def requote(
    text: str, support: list[tuple[str, str]], cited: list[str], shown: dict[str, Excerpt]
) -> list[tuple[str, str]]:
    """Deterministic re-quoting (gate v1 finding b): for each literal of the claim that its verified
    quotes lack, add the verbatim sentence (or a window of it) of a cited excerpt that states it, up
    to ``MAX_SUPPORT`` quotes. The model often quotes "It defaults to true" for a claim that names
    the flag: the flag's own sentence is added, so the quotes TOGETHER carry the claim."""
    out = list(support)
    for lit in literals(text):
        if literal_supported(lit, _hay([q for _h, q in out])):
            continue
        if len(out) >= MAX_SUPPORT:
            break
        found = None
        for h in dict.fromkeys([*(h for h, _q in out), *cited]):
            ex = shown.get(h)
            if ex is None:
                continue
            for sentence in _SENTENCES.split(ex.text):
                sentence = " ".join(sentence.split())
                if len(qnorm(sentence)) >= QUOTE_MIN_CHARS and literal_supported(lit, _lit_norm(sentence)):
                    found = (h, _window(sentence, lit))
                    break
            if found is not None:
                break
        if found is None or any(qnorm(found[1]) == qnorm(q) for _h, q in out):
            break
        out.append(found)
    return out


_STOP = frozenset(
    "the a an and or of to in on at by for from with as is are was were be been it its this that these "
    "those not no but if then than so into over under about which who what when where how why there their "
    "has have had do does did can could will would should may might must also only "
    "ve bir bu şu ile için da de ki mi mu mü mı ne gibi daha çok en olan olarak veya ya ama fakat her hem "
    "sonra önce kadar göre".split()
)
#: a line/sentence re-quotes a claim when it holds at least this share of the claim's content words
REQUOTE_MIN_OVERLAP = 0.6


def content_words(text: str) -> set[str]:
    """Content words of ``text`` (normalised, ≥ 3 characters, no EN/TR stop word)."""
    return _content(text)


def lit_hay(text: str) -> str:
    return _lit_norm(text)


def _content(text: str) -> set[str]:
    return {w for w, _s, _e in _words(unicodedata.normalize("NFC", text)) if len(w) >= 3 and w not in _STOP}


def best_line(text: str, quote: str, handles: list[str], shown: dict[str, Excerpt]) -> tuple[str, str] | None:
    """Addendum 2 re-quote: when none of a claim's quotes is found, quote the table row / line (or
    sentence) of a cited excerpt that holds every literal of the claim and the largest share (≥
    ``REQUOTE_MIN_OVERLAP``) of its content words; None when no line qualifies."""
    want = _content(text)
    if not want:
        return None
    lits = literals(text)
    best: tuple[float, int, str, str] | None = None
    for h in handles:
        ex = shown.get(h)
        if ex is None:
            continue
        candidates = [ln for ln in ex.text.splitlines()] + _SENTENCES.split(ex.text)
        for cand in candidates:
            line = " ".join(cand.split())
            if len(qnorm(line)) < QUOTE_MIN_CHARS:
                continue
            words = _content(line)
            overlap = len(want & words) / len(want)
            if overlap < REQUOTE_MIN_OVERLAP:
                continue
            if lits and not all(literal_supported(x, _lit_norm(line)) for x in lits):
                continue
            key = (overlap, -len(line))
            if best is None or key > (best[0], best[1]):
                best = (overlap, -len(line), h, line)
    if best is None:
        return None
    anchor = next(iter(lits), next(iter(want)))
    return best[2], _window(best[3], anchor)


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
    hay = _hay([c.text for c in kept]) + "\n" + "\n".join(support_hay(c.support, shown) for c in kept)
    sentences = _SENTENCE.split(answer)
    kept_quotes = [q for c in kept for _h, q in c.support]
    kept_sentences = [s for s in sentences if literals_ok(s, hay) and polarity_ok(s, kept_quotes)]
    dropped = len(sentences) - len(kept_sentences)
    text = redact(" ".join(kept_sentences))[:ANSWER_MAX_CHARS]
    if text.strip():  # D-154: a kept claim the summary dropped is appended (never to an empty answer)
        text = redact(complete_with_claims(text, [c.text for c in kept]))[:ANSWER_MAX_CHARS]
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


#: an answer covers a claim when it states all of the claim's literals and this share of its words
COVER_MIN_SHARE = 0.6


def covers(answer: str, claim: str) -> bool:
    """D-154: ``answer`` already states ``claim``: every literal (number, identifier, path) of the
    claim is in the answer, and at least ``COVER_MIN_SHARE`` of its content words are (a word also
    matches its inflections: one is a prefix of the other)."""
    if not literals_ok(claim, answer):
        return False
    words, have = _content(claim), _content(answer)
    if not words:
        return True
    hits = sum(1 for w in words if w in have or any(x.startswith(w) or w.startswith(x) for x in have))
    return hits / len(words) >= COVER_MIN_SHARE


def complete_with_claims(answer: str, claims: list[str], max_chars: int = ANSWER_MAX_CHARS) -> str:
    """D-154 (compression): the answer text is the model's summary; a KEPT claim it does not state is
    appended, in claim order, so every verified fact reaches the caller. The miss taxonomy on the dev
    set: 6 of 13 misses had the key fact in a kept claim that the summary dropped."""
    out = answer.strip()
    for c in claims:
        c = c.strip()
        if not c or covers(out, c):
            continue
        add = c if c[-1] in ".!?" else c + "."
        if len(out) + 1 + len(add) > max_chars:
            break
        out = f"{out} {add}" if out else add
    return out


def support_ok(text: str, support: list[tuple[str, str]]) -> bool:
    """A claim that is only downgraded (no verified quote) still may not contradict the polarity of
    the quotes it was re-quoted with."""
    return not support or polarity_ok(text, [q for _h, q in support])


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
    flags: dict[str, Any] = {"requoted": 0, "completed": 0}
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
        requoted = False
        if text and not support:
            # addendum 2: never drop silently — one re-quote attempt from the cited items' own lines
            first_quote = next((str(x.get("quote") or "") for x in raw_support if isinstance(x, dict)), "")
            found = best_line(text, first_quote, cited, shown)
            if found is not None:
                support, requoted = [found], True
                if found[0] not in cited:
                    cited.append(found[0])
        n_before = len(support)
        if support:
            support = requote(text, support, cited, shown)
        if not text:
            claims.append(Claim(text, [], "dropped", cited))
        elif (
            support
            and literals_ok(text, support_hay(support, shown))
            and polarity_ok(text, [q for _h, q in support])
        ):
            claims.append(Claim(text, support, "kept", cited))
            flags["requoted"] += int(requoted)
            flags["completed"] += int(len(support) > n_before)
        elif cited and literals_ok(text, _hay([shown[h].text for h in cited])) and support_ok(text, support):
            claims.append(Claim(text, [], "downgraded", cited))  # true to its sources, not to a quote
        else:
            claims.append(Claim(text, [], "dropped", cited))
    flags["dropped_claims"] = sum(1 for c in claims if c.state != "kept")
    flags["main_dropped"] = bool(claims) and claims[0].state != "kept"
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
        **flags,
    )


def merge_check(draft: Validated, checked: Validated, shown: dict[str, Excerpt] | None = None) -> Validated:
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
        shown if shown is not None else _shown_of(checked, draft),
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


# --------------------------------------------------------------------------- addendum 6
_ID_LIKE = re.compile(
    r"^(?=[^\s]*\d)(?=[^\s]*[A-Z])[A-Za-z0-9][A-Za-z0-9._-]+$|^[A-Z]{1,3}-[A-Z0-9][A-Za-z0-9-]*$"
)
_PROPER = re.compile(r"^[A-ZÇĞİÖŞÜ][a-zçğıöşü]{2,}$")
_ACRONYM = re.compile(r"^[A-Z]{2,6}$")
_EDGE_CHARS = "()[]{}<>,;:!?\"`“”‘’«»…*.'"
_COMMON_CAPS = frozenset(
    "The This That These Those It Its A An In On At For And But Or If When Where While Then Also Only "
    "Both Each Every All No Not Yes After Before Since Until With Without From Into Over Under Bu Şu Bir "
    "Ve Ama Her Hem Daha Sonra Önce Ancak Çünkü Için İçin Yani".split()
)


def named_subjects(text: str) -> list[str]:
    """The named subjects of a claim (addendum 6 #2): identifiers of documents, sections, decisions,
    gates and workstreams (a token with a capital and a digit, or ``X-…``), file names, acronyms and
    proper names (capitalised words that do not start a sentence)."""
    out: list[str] = []
    words = text.split()
    for i, raw in enumerate(words):
        tok = raw.strip(_EDGE_CHARS)
        m = _APOS_SUFFIX.match(tok)
        tok = m.group(1) if m else tok
        if len(tok) < 2:
            continue
        start = i == 0 or words[i - 1].endswith((".", "!", "?", ":", ";"))
        if (
            _FILE_EXT_RE.search(tok.casefold())
            or _ID_LIKE.match(tok)
            or _ACRONYM.match(tok)
            or (_PROPER.match(tok) and not start and tok not in _COMMON_CAPS)
        ):
            if tok not in out:
                out.append(tok)
    return out


_FILE_EXT_RE = re.compile(r"\.(md|py|toml|json|jsonl|ya?ml|sh|log|txt|sql|js|ts|conf|env|lock|cfg|ini|html)$")


def unattributed(c: Claim) -> list[str]:
    """The claim's named subjects that its quotes do not state (provenance lives in the handle)."""
    hay = _hay([q for _h, q in c.support])
    return [n for n in named_subjects(c.text) if not literal_supported(n, hay)]


def _near(quote: str, lit: str, words: set[str], radius: int) -> bool:
    at = quote.casefold().find(lit.casefold())
    if at < 0:
        return False
    window = quote[max(0, at - radius) : at + len(lit) + radius]
    return bool(_content(window) & words)


def uncopied(c: Claim, question: str, main: bool) -> list[str]:
    """Addendum 6 #1 (copy-through): identifiers and values of the claim's quotes that bear on the
    question (their context shares a word with it, or the question names them) but that the claim
    does not state. The main claim is held to a wider context."""
    qwords = _content(question)
    qhay = _lit_norm(question)
    chay = _lit_norm(c.text)
    out: list[str] = []
    for _h, q in c.support:
        for lit in literals(q):
            if literal_supported(lit, chay) or lit in out:
                continue
            if literal_supported(lit, qhay) or _near(q, lit, qwords, 160 if main else 60):
                out.append(lit)
    return out[:6]


def claim_fixes(v: Validated, question: str) -> dict[int, list[str]]:
    """Per kept claim (by index), the targeted rewrites the self-check is asked for."""
    fixes: dict[int, list[str]] = {}
    for i, c in enumerate(v.kept):
        notes = []
        miss = uncopied(c, question, main=i == 0)
        if miss:
            notes.append("copy into the claim, exactly as the quotes write them: " + ", ".join(miss))
        names = unattributed(c)
        if names:
            notes.append(
                "the quotes do not name: " + ", ".join(names) + " — remove them (state the fact only)"
            )
        if notes:
            fixes[i] = notes
    return fixes


def enforce_attribution(
    v: Validated, shown: dict[str, Excerpt], redact: Callable[[str], str] = lambda s: s
) -> Validated:
    """Without a self-check (no call left, no time): a claim naming a subject that neither its
    quotes nor its cited excerpts state is dropped; the answer is grounded again."""
    if not v.answered:
        return v
    claims = []
    changed = False
    for c in v.kept:
        names = unattributed(c)
        hay = support_hay(c.support, shown)
        if names and not all(literal_supported(n, hay) for n in names):
            claims.append(Claim(c.text, [], "dropped", c.cited))
            changed = True
        else:
            claims.append(c)
    if not changed:
        return v
    return assemble(ANSWERED, v.answer, claims, v.related, _lower(v.confidence), shown, redact,
                    missing=v.missing, sub_asks=v.sub_asks)  # fmt: skip


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
            acc = self.tally.setdefault(row.lineage, [0, Decimal(0), 0])
            if row.outcome in NETWORK_OUTCOMES:
                acc[0] += 1
            acc[1] += Decimal(row.cost_usd or 0)
            acc[2] += int(row.input_tokens or 0) + int(row.output_tokens or 0)
        await self.inner.record(row)

    async def job_calls(self, job_id: int) -> int:
        return await self.inner.job_calls(job_id)

    async def lineage_calls(self, lineage: str) -> int:
        return await self.inner.lineage_calls(lineage)

    async def claim(self, lineage: str, cap: int) -> bool:
        return await self.inner.claim(lineage, cap)

    def take(self, lineage: str) -> tuple[int, Decimal]:
        acc = self.tally.pop(lineage, [0, Decimal(0), 0])
        return int(acc[0]), Decimal(acc[1])

    def peek(self, lineage: str) -> tuple[Decimal, int]:
        """``(USD, tokens)`` spent under ``lineage`` so far (the per-question budget, addendum 5)."""
        acc = self.tally.get(lineage, [0, Decimal(0), 0])
        return Decimal(acc[1]), int(acc[2])


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
            bool(getattr(settings, "research_enabled", False))
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

    def job_spec(self, job: str) -> TaskSpec:
        """The ``research`` spec with the JOB's own ``max_tokens`` (the task name, and so the
        per-task fallback and the ledger, stay ``research``)."""
        return replace(self.spec, max_tokens=JOB_MAX_TOKENS.get(job, self.spec.max_tokens))

    def worst_case(self, job: str, user: str) -> tuple[Decimal, int]:
        """``(USD, tokens)`` the next call may cost at most on the expected path: its padded input
        and its max_tokens, priced by the PRIMARY. The call-level check only; every ATTEMPT (schema
        retry, fallback) is checked again with its own profile's worst case (``attempt_guard``,
        review 79 T4)."""
        spec = self.job_spec(job)
        tokens_in = (
            self.provider.estimate_input_tokens(
                [{"role": "system", "content": spec.system}, {"role": "user", "content": user}]
            )
            if self.provider is not None
            else 0
        )
        head = self.chain[0] if self.chain else None
        usd = head.worst_usd(tokens_in, spec.max_tokens) if head is not None and head.priced else Decimal(0)
        return usd, -(-tokens_in * 11 // 10) + spec.max_tokens

    def spent(self, lineage: str) -> tuple[Decimal, int]:
        ledger = self.provider.ledger if self.provider is not None else None
        if isinstance(ledger, _TeeLedger):
            return ledger.peek(lineage)
        return Decimal(0), 0

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
        attempt_guard: AttemptGuard | None = None,
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
            self.job_spec(job),
            user,
            validate=job_validator(job),
            precheck=precheck,
            deadline=deadline,
            lineage=lineage,
            attempt_policy="latency",
            attempt_guard=attempt_guard,
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
    "assemble",
    "check_user",
    "clip",
    "close_app_researcher",
    "best_line",
    "find_verbatim",
    "locate_quote",
    "polarity_ok",
    "redact_values",
    "job_validator",
    "literal_supported",
    "literals_ok",
    "merge_check",
    "parse_plan",
    "plan_user",
    "qnorm",
    "refine_user",
    "rank_sources",
    "requote",
    "claim_fixes",
    "content_words",
    "lit_hay",
    "enforce_attribution",
    "named_subjects",
    "uncopied",
    "unattributed",
    "support_hay",
    "research_chain",
    "validate_answer",
]
