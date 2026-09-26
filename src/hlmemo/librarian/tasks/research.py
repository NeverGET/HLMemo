"""The research librarian's LLM side (``memory.ask``; D-130, D-136): prompts, deterministic answer
validation and the provider wrapper. ``core/research_service`` runs the loop and the DB phases.

One provider TASK, ``research``, for the four JOBs of the loop (one system prompt, one schema, a
job-specific shape check): ``plan`` (map + question → queries + map sections), ``answer`` (excerpts →
answer + claims, each with 1-3 verbatim supporting quotes), ``check`` (the COMPLETENESS + REPAIR pass:
the question's sub-asks against the draft and the same excerpts → the full revised answer; a missing
fact is added only as a new quoted claim, and each claim flagged by the deterministic copy-through /
attribution checks is repaired in the same call) and ``refine`` (one more search round, only when the
answer abstained). At most 4 sequential steps (addendum 7; 6 with the D-159 select). The per-task
fallback is therefore ``HLM_FALLBACK_PROFILE__RESEARCH`` (D-094); a profile listing ``research`` in
``disabled_tasks`` is not used (D-071).

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

D-156 ``HLM_RESEARCH_ANSWER_MODE=cite`` ("V14: write, then cite"; the default ``claims`` is all of the
above, unchanged): the prompt is research/v2 (an opt-in version) and the answer step is the JOB
``write``: complete prose sentences, each citing the EXCERPT handles it rests on, no quotes, and no
``check`` call. ``validate_cited`` checks every sentence deterministically against the FULL text of
its cited excerpts (a sentence citing no shown excerpt is checked against all of them, ``uncited``):
its literals (``literals_ok`` rules) and its polarity against the excerpt lines/sentences that share
a literal or ≥ 2 content words with it; a failing sentence is dropped, the answer is the kept
sentences, and each cited handle is displayed with its best-matching line.

D-159 ``HLM_RESEARCH_SELECT`` (cite mode only; "select, then write"): before each ``write``, the JOB
``select`` sees the same excerpts and returns the ids of the ≤ ``SELECT_MAX`` that state the answer
(``parse_select``: shown ids only, in its order); the ``write`` sees and may cite only those, and the
others stay drillable as ``related``. An empty or failed select writes over every excerpt.

D-162 ``HLM_RESEARCH_ANSWER_MODE=prose`` ("V16: write freely, keep all but fabricated values"): the
prompt is research/v3 (opt-in) and the answer step is the JOB ``prose``: free prose plus the
``sources`` it draws on, no per-sentence citation duty and no ``check`` call. ``validate_prose``
splits the answer into sentences (``split_sentences``: never inside a code span) and drops a sentence
ONLY when one of its HARD literals (``hard_literals``: a digit, or a backticked identifier) is in no
shown excerpt (texts, titles and dates together): the fabricated-value guard. Every other sentence is
kept and attributed deterministically (display and measurement) to ≤ 2 excerpts and their best
line; a polarity mismatch against those lines FLAGS the claim (``flags: ["polarity"]``), it never
drops it.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import time
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from hlmemo.librarian import privacy
from hlmemo.librarian.budget import Caps, DbBudget, NoBudget
from hlmemo.librarian.cassette import CassetteStore
from hlmemo.librarian.errors import AuthorityLost, LlmConfigError, PrivacyDenied
from hlmemo.librarian.ledger import NETWORK_OUTCOMES, DbLedger, Ledger, LedgerRow
from hlmemo.librarian.profiles import LlmProfile, named_profile, profile_chain
from hlmemo.librarian.prompts import TaskSpec, load_task
from hlmemo.librarian.provider import AttemptGuard, ChainBreakers, Clock, LlmResult, Provider
from hlmemo.librarian.redact import Redactor
from hlmemo.librarian.risk_judge import ConnectFactory, direct_connector
from hlmemo.librarian.tasks.synthesis import claims as literal_claims

log = logging.getLogger("hlmemo.librarian.research")

TASK = "research"
JOBS = ("plan", "answer", "check", "refine", "write", "select", "prose", "attribute", "expand")
#: D-156: ``claims`` (the default: quoted claims + the completeness pass, research/v1) or ``cite``
#: (V14 "write, then cite": sentences citing excerpt handles, verified deterministically, research/v2);
#: D-162: ``prose`` (V16: free prose, only fabricated values dropped, research/v3)
ANSWER_MODES = ("claims", "cite", "prose")
#: D-165: how a prose answer's sentences are attributed to excerpts (``HLM_RESEARCH_ATTRIBUTION``,
#: ``attribute``): the model's sources (V16, default), every shown excerpt with the embedding
#: (``wide``), or one extra JOB attribute (``llm``, falls back to ``sources``)
ATTRIBUTIONS = ("sources", "wide", "llm")
#: D-165 ``llm``: the excerpt ids one sentence may be attributed to (the prompt says "1 to 3")
ATTRIBUTE_MAX = 3
#: D-170 expand (``HLM_RESEARCH_EXPAND``): the new sentences one expand call may add ("up to 6")
EXPAND_MAX = 6
#: D-171: the JOBs that write the prose answer: they use ``HLM_RESEARCH_WRITER_PROFILE`` when set
WRITER_JOBS = frozenset({"prose", "expand"})
WRITER_ENV = "HLM_RESEARCH_WRITER_PROFILE"
#: D-172: the default attempt timeout of the writer profile (``HLM_RESEARCH_WRITER_TIMEOUT_S``)
WRITER_TIMEOUT_S = 12.0
#: the opt-in prompt versions of the cite and prose modes (``prompts.OPT_IN_VERSIONS``: never the
#: default)
CITE_PROMPT_VERSION = 2
PROSE_PROMPT_VERSION = 3
#: sequential LLM steps of one question (addendum 7): plan, answer, completeness+repair — or, when
#: the answer abstained, plan, answer, refine, answer (``MAX_CALLS_NO_SELECT``; the prose mode: plan,
#: prose — or plan, prose, refine, prose). D-159 select-then-
#: write (cite mode + ``HLM_RESEARCH_SELECT``) adds one select before each write: plan, select,
#: write — or plan, select, write, refine, select, write (``MAX_CALLS``). D-165 ``llm`` attribution
#: (prose mode) adds one attribute after an answered prose: plan, prose, attribute — or plan, prose,
#: refine, prose, attribute (``MAX_CALLS_ATTRIBUTE``). D-170 expand (prose mode) adds one expand after
#: an answered prose (before the attribute): the prose mode's cap is ``MAX_CALLS_NO_SELECT`` + 1 per
#: extra JOB on (expand, llm attribution), at most ``MAX_CALLS`` (plan, prose, refine, prose, expand,
#: attribute)
MAX_CALLS = 6
MAX_CALLS_NO_SELECT = 4
MAX_CALLS_ATTRIBUTE = 5
#: provider requests of one question (schema retries and fallbacks included), DB-enforced per lineage
MAX_ATTEMPTS = 9
MAX_IN_FLIGHT = 4
#: addendum 5: an explicit max_tokens on EVERY call, per JOB (reasoning tokens included); a
#: runaway output is cut there, and the per-question budget reserves exactly this worst case
JOB_MAX_TOKENS = {
    "plan": 800,
    "refine": 800,
    "answer": 3000,
    "check": 3000,
    "write": 3000,
    "select": 800,
    "prose": 3000,
    "attribute": 1500,
    "expand": 1500,
}
BREAKER_THRESHOLD = 3
BREAKER_OPEN_S = 30.0
BREAKER_MAX_OPEN_S = 900.0
#: per-request HTTP timeout ceiling (each attempt is also budgeted to the call's deadline)
HTTP_TIMEOUT_S = 20.0
EXCERPT_CHARS = 3200
MAX_CLAIMS = 12
#: cite mode: the sentences of one ``write`` answer that are checked (the rest is ignored) and the
#: cited excerpts one sentence is checked against
MAX_SENTENCES = 24
MAX_CITES = 6
#: D-159: the excerpts one ``select`` may pick for the ``write`` (the prompt says "at most 6")
SELECT_MAX = 6
#: D-162 prose mode: the model's ``sources`` kept (the prompt says "at most 6") and the excerpts one
#: sentence is attributed to
PROSE_MAX_SOURCES = 6
PROSE_ATTRIBUTE = 2
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
    if isinstance(literal, _CodeSpan):
        # D-156 (c): `pg_trgm.word_similarity_threshold = 0.9` vs "SET pg_trgm.… TO 0.9" or a hay
        # whose own markup splits the span: every whitespace-free token of it is enough
        toks = [t.strip(_TOKEN_EDGE) for t in literal.split()]
        toks = [t for t in toks if len(t) >= 2 or t.isdigit()]
        return bool(toks) and all(literal_supported(t, hay) for t in toks)
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
_CODE_SPAN = re.compile(r"`([^`]+)`")
#: the edge punctuation of one token of a code span (``_CodeSpan``)
_TOKEN_EDGE = "()[]{}<>,;:!?\"'`“”‘’«»*"


class _CodeSpan(str):
    """A backticked span with inner whitespace, as ``literals`` returns it: supported when the whole
    span is in the hay OR every whitespace-free token of it (≥ 2 characters, or a digit) is."""

    __slots__ = ()


def _spaced(span: str) -> bool:
    return _WS.search(span.strip()) is not None


def _unglue(lit: str) -> list[str]:
    """D-156 (a)(b): a token glued to a code span is not one literal. A suffix after the closing
    backtick (```profile-v2.3`dır``, ```127.0.0.1:8765/mcp`’dir``) is dropped; code spans joined by a
    slash (```CLAUDE.md`/`AGENTS.md```) are two literals. Each part is a literal again only when it is
    literal-like and holds a letter or a digit. D-162: the slash joining a code span and a plain word
    (```prose`/cite``) belongs to neither (the word alone is a literal only when it is literal-like)."""
    if "`" not in lit or isinstance(lit, _CodeSpan) or _WS.search(lit):
        return [lit]
    return [
        x for part in lit.split("`") for x in literal_claims(part.strip("/")) if any(ch.isalnum() for ch in x)
    ]


#: D-162 (D-161 false positives): a TR/EN suffix glued after CLOSING bold markup (``**1.2.8**’dir``,
#: ``__D-130__'da``): the literal is what the bold wraps. ``__x__`` without a suffix is unwrapped
#: only around a digit (``__init__`` is an identifier)
_STAR_SUFFIX = re.compile(r"^([^*\s]+?)\*\*['’]?[^\W\d_]{1,8}$")
_UNDER_SUFFIX = re.compile(r"^__([^_\s].*?)__(['’]?[^\W\d_]{1,8})?$")


def _unbold(lit: str, codes: set[str]) -> str:
    if lit in codes:  # a code span's own text is never markup
        return lit
    m = _STAR_SUFFIX.match(lit)
    if m:
        return type(lit)(m.group(1))
    m = _UNDER_SUFFIX.match(lit)
    if m and (m.group(2) or any(ch.isdigit() for ch in m.group(1))):
        return type(lit)(m.group(1))
    return lit


def literals(text: str) -> list[str]:
    """The checkable literals of ``text`` (``synthesis.claims``): an apostrophe suffix is removed
    (``%40’ını`` is checked as ``40``, ``D-130'da`` as ``D-130``) and a plain ``a/b`` word pair is
    not a literal (addendum 3 #2). D-156: a suffix glued after a code span and slash-joined code
    spans are split off (``_unglue``); a code span with inner whitespace is ONE literal
    (``_CodeSpan``), its tokens are not literals of their own. D-162: a suffix glued after closing
    bold markup is removed too (``**1.2.8**’dir`` is checked as ``1.2.8``)."""
    codes = {m.group(1) for m in _CODE_SPAN.finditer(text)}
    spaced = [m.group(1) for m in _CODE_SPAN.finditer(text) if _spaced(m.group(1))]
    if spaced:
        text = _CODE_SPAN.sub(lambda m: " " if _spaced(m.group(1)) else m.group(0), text)
    out: list[str] = []
    for raw in [*(_CodeSpan(x) for x in spaced), *literal_claims(text)]:
        for lit in _unglue(raw):
            lit = _unbold(lit, codes)
            m = _APOS_SUFFIX.match(lit)
            lit = type(lit)(m.group(1).lstrip("%")) if m else lit
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
    #: D-184 (prose mode): where the chunk sits (``context_label``), and its supersession status
    #: (``status_label``: set only when superseded; ``status_vid`` = the superseding version)
    context: str = ""
    status: str = ""
    status_vid: int | None = None

    def shown(self, temporal: bool = False) -> dict[str, str]:
        """What the model is shown; ``temporal`` (research/v3 prose, expand) adds the excerpt's
        ``context`` and ``status`` when it has them (a current excerpt carries no status)."""
        out = {"id": self.handle, "title": self.title, "date": self.date}
        if temporal and self.context:
            out["context"] = self.context
        if temporal and self.status:
            out["status"] = self.status
        out["text"] = self.text
        return out


# --------------------------------------------------------------------------- D-184 temporal layer
#: a markdown heading line, and a decision-log / table row start ("D-026 | 2026-09-22 | ...", with
#: or without a leading pipe): the row key and its date
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$", re.M)
_ROW = re.compile(
    r"^\|?[ \t]*([A-Z][A-Za-z]{0,11}-\d{1,5}[a-z]?)[ \t]*\|[ \t]*(\d{4}-\d{2}-\d{2})[ \t]*\|", re.M
)
#: the heading levels a context label names, the characters of one heading, of a status quote
CONTEXT_LEVELS = 2
CONTEXT_HEADING_CHARS = 80
STATUS_QUOTE_CHARS = 200
#: part-scope supersession: the quote overlaps an excerpt when this many consecutive words of it
#: (all of a shorter quote) occur in the excerpt's text
QUOTE_OVERLAP_WORDS = 6


def doc_name(path: str) -> str:
    """The file name of an item path (``docs/decisions/DECISIONS.md#D-004`` -> ``DECISIONS.md``)."""
    base = path.split("#", 1)[0].rstrip("/")
    return base.rsplit("/", 1)[-1] or path


def _heading_text(raw: str) -> str:
    return _cut(
        " ".join(raw.replace("**", "").replace("__", "").replace("`", "").split()), CONTEXT_HEADING_CHARS
    )


def context_label(path: str, body: str, start: int) -> str:
    """D-184: where the text at ``start`` of an item ``body`` sits, computed at READ time: the row
    (key and its date) of a decision-log/table document that contains ``start`` (also when the
    chunk starts mid-row), else the nearest preceding markdown heading path (≤ ``CONTEXT_LEVELS``
    levels), whichever starts later. ``"<file> › row D-026 (2026-09-22)"`` or ``"<file> › §4
    Retrieval algorithm"``; ``""`` when neither precedes ``start``."""
    start = max(0, min(start, len(body)))
    eol = body.find("\n", start)
    end = len(body) if eol < 0 else eol  # the line holding ``start`` is searched whole
    row = None
    for m in _ROW.finditer(body, 0, end):
        if m.start() > start:
            break
        row = m
    stack: list[tuple[int, str]] = []
    last_heading = -1
    for m in _HEADING.finditer(body, 0, end):
        if m.start() > start:
            break
        level = len(m.group(1))
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, _heading_text(m.group(2))))
        last_heading = m.start()
    name = doc_name(path)
    if row is not None and row.start() >= last_heading:
        return f"{name} › row {row.group(1)} ({row.group(2)})"
    if stack:
        return " › ".join([name, *(t for _lvl, t in stack[-CONTEXT_LEVELS:] if t)])
    return ""


def quote_overlaps(quote: str, text: str, n: int = QUOTE_OVERLAP_WORDS) -> bool:
    """D-184: a part-scope link's quote overlaps an excerpt: ``n`` consecutive words of it (all of a
    shorter quote) occur in the excerpt's text (normalised: a chunk boundary may cut the quote)."""
    words = qnorm(quote).split()
    if not words:
        return False
    hay = " " + " ".join(qnorm(text).split()) + " "
    k = min(n, len(words))
    return any(" " + " ".join(words[i : i + k]) + " " in hay for i in range(len(words) - k + 1))


def status_label(handle: str, path: str, quote: str, part: bool) -> str:
    """D-184: the status of a superseded excerpt, as the writer sees it: ``superseded by v67
    (docs/decisions/PHASE0-SPEC.md): «MERGED … from …»`` (``superseded in part by`` for a
    part-scope link; the quote is the link's, cut to ``STATUS_QUOTE_CHARS``)."""
    head = f"superseded {'in part ' if part else ''}by {handle} ({path})"
    q = _cut(" ".join(quote.split()), STATUS_QUOTE_CHARS)
    return f"{head}: «{q}»" if q else head


#: D-188: the sentence/line units of a text an excerpt window may be centred on
_CLIP_UNIT = re.compile(r"[^\n.!?]+[.!?]*")


def clip(text: str, limit: int = EXCERPT_CHARS, focus: set[str] | None = None) -> str:
    """``text`` cut to ``limit`` characters (" …" marks a cut): its head, or (D-188, with ``focus``:
    the question's content words) a window centred on the sentence or line that shares the most
    ``focus`` words, starting at a line (else word) boundary; the head when nothing shares."""
    text = text.strip()
    if len(text) <= limit:
        return text
    best, at = 0, 0
    for m in _CLIP_UNIT.finditer(text) if focus else ():
        score = len(focus & _content(m.group(0)))  # type: ignore[operator]
        if score > best:
            best, at = score, (m.start() + m.end()) // 2
    if best == 0:
        return text[:limit] + " …"
    start = max(0, min(at - limit // 2, len(text) - limit))
    if start > 0:
        nl = text.find("\n", start, start + 200)
        sp = text.find(" ", start, start + 40)
        start = nl + 1 if nl >= 0 else (sp + 1 if sp >= 0 else start)
    end = min(len(text), start + limit)
    return ("… " if start > 0 else "") + text[start:end].strip() + (" …" if end < len(text) else "")


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


def write_user(question: str, excerpts: list[Excerpt], redact: Redact | None = None) -> str:
    """D-156 cite mode: the JOB ``write`` (the question and the excerpts, as ``answer_user``)."""
    payload = {"question": question, "excerpts": [e.shown() for e in excerpts]}
    return "JOB: write\n" + _input(payload, redact)


def select_user(question: str, excerpts: list[Excerpt], redact: Redact | None = None) -> str:
    """D-159: the JOB ``select`` (the question and the excerpts exactly as ``write_user`` shows them)."""
    payload = {"question": question, "excerpts": [e.shown() for e in excerpts]}
    return "JOB: select\n" + _input(payload, redact)


def prose_user(question: str, excerpts: list[Excerpt], redact: Redact | None = None) -> str:
    """D-162 prose mode: the JOB ``prose`` (the question and the excerpts, as ``write_user``; D-184:
    with their context and supersession status)."""
    payload = {"question": question, "excerpts": [e.shown(temporal=True) for e in excerpts]}
    return "JOB: prose\n" + _input(payload, redact)


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

    def write(obj: dict[str, Any]) -> str | None:
        if obj.get("status") not in (ANSWERED, INSUFFICIENT):
            return "status missing"
        if not isinstance(obj.get("sentences"), list):
            return "sentences missing"
        if obj["status"] == ANSWERED and not any(
            isinstance(x, dict) and str(x.get("text") or "").strip() for x in obj["sentences"]
        ):
            return "answered without sentences"
        return None

    def select(obj: dict[str, Any]) -> str | None:
        if not isinstance(obj.get("ids"), list):
            return "ids missing"
        return None

    def prose(obj: dict[str, Any]) -> str | None:
        if obj.get("status") not in (ANSWERED, INSUFFICIENT):
            return "status missing"
        if obj["status"] == ANSWERED and not (isinstance(obj.get("answer"), str) and obj["answer"].strip()):
            return "answered without an answer"
        return None

    if job in ("plan", "refine"):
        return plan
    if job == "select":
        return select

    def attribute(obj: dict[str, Any]) -> str | None:
        if not isinstance(obj.get("cites"), list):
            return "cites missing"
        return None

    def expand(obj: dict[str, Any]) -> str | None:
        if not isinstance(obj.get("add"), list):
            return "add missing"
        return None

    if job == "prose":
        return prose
    if job == "attribute":
        return attribute
    if job == "expand":
        return expand
    return write if job == "write" else answer


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


def parse_select(obj: dict[str, Any] | None, shown: Iterable[str]) -> list[str]:
    """D-159: the excerpt ids a ``select`` picked, in its order: ids of ``shown`` only (an unknown id
    is ignored), each once, at most ``SELECT_MAX``; ``[]`` for no or a malformed output."""
    raw = obj.get("ids") if isinstance(obj, dict) else None
    if not isinstance(raw, list):
        return []
    known = set(shown)
    out: list[str] = []
    for x in raw:
        h = x.strip() if isinstance(x, str) else ""
        if h in known and h not in out:
            out.append(h)
            if len(out) >= SELECT_MAX:
                break
    return out


# --------------------------------------------------------------------------- answer validation
@dataclass(slots=True)
class Claim:
    text: str
    support: list[tuple[str, str]]  # (handle, verbatim span of that excerpt), 1..MAX_SUPPORT
    state: str  # kept | downgraded | dropped
    cited: list[str] = field(default_factory=list)  # every shown handle the model named for it
    #: D-162 prose mode: whether a line break followed it in the answer (kept when re-joined);
    #: D-170: whether it was added by the expand pass
    line_end: bool = False
    added: bool = False

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
    # D-156 cite mode: sentences that cited no shown excerpt (checked against all of them), and the
    # dropped sentences by reason (literal / polarity / unsupported)
    uncited: int = 0
    drop_reasons: dict[str, int] = field(default_factory=dict)
    # D-159: the handles the answer was written over when a select narrowed them (None: all shown);
    # the re-check verifies its sentences against these only
    written_over: list[str] | None = None
    # D-162 prose mode: the model's valid ``sources`` (an attribution tie-break, D-165; the re-check
    # attributes the sentences again over the excerpts still citable)
    sources: list[str] = field(default_factory=list)
    # D-170 expand: the added sentences kept, and those dropped (a hard literal no excerpt states, or
    # past the answer's length cap)
    expand_added: int = 0
    expand_dropped: int = 0

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


# --------------------------------------------------------------------------- D-156 cite mode
#: a handle list the model wrote INTO a sentence ("… [v12.3]", "[v4, v7.1]"; "(v12.3)" only when
#: every handle in it was shown: "(v2)" may be prose): moved to its cites (a handle left in the text
#: would fail the literal check, excerpts never state their own handle)
_H = r"v[1-9][0-9]*(?:\.[0-9]+)?"
_INLINE_CITE = re.compile(rf"\s*([\[(])\s*({_H}(?:\s*[,;]?\s*{_H})*)\s*[\])]")
_INLINE_HANDLE = re.compile(_H)
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,;:!?…])")
#: the reasons a sentence is dropped (``Validated.drop_reasons``)
DROP_REASONS = ("literal", "polarity", "unsupported")
_Unit = tuple[str, set[str], str]  # (a line/sentence of an excerpt, its content words, its lit form)


def split_inline_cites(text: str, shown: dict[str, Excerpt] | None = None) -> tuple[str, list[str]]:
    """``(sentence without inline handle lists, the handles they named)``."""
    handles: list[str] = []

    def cut(m: re.Match[str]) -> str:
        found = _INLINE_HANDLE.findall(m.group(2))
        if m.group(1) == "(" and not (shown is not None and all(h in shown for h in found)):
            return m.group(0)
        handles.extend(found)
        return ""

    out = _INLINE_CITE.sub(cut, text)
    if handles:
        text = _SPACE_BEFORE_PUNCT.sub(r"\1", out)
    return " ".join(text.split()), handles


def _units(ex: Excerpt, cache: dict[str, list[_Unit]]) -> list[_Unit]:
    """The lines of an excerpt, each split into its sentences (a table row stays whole)."""
    if ex.handle not in cache:
        units = (" ".join(x.split()) for x in _SENTENCES.split(ex.text))
        cache[ex.handle] = [(u, _content(u), _lit_norm(u)) for u in units if u]
    return cache[ex.handle]


def _first_line(ex: Excerpt) -> str:
    return next((" ".join(ln.split()) for ln in ex.text.splitlines() if ln.strip()), "")


def _display(unit: str, lits: list[str]) -> str:
    """A unit as a displayed quote (≤ QUOTE_MAX_CHARS, a window around its first literal)."""
    anchor = next((x for x in lits if literal_supported(x, _lit_norm(unit))), None)
    return _window(unit, anchor) if anchor is not None else _cut(unit)


def cite_check(
    text: str,
    cites: list[str],
    shown: dict[str, Excerpt],
    cache: dict[str, list[_Unit]] | None = None,
) -> tuple[str | None, list[tuple[str, str]]]:
    """D-156: the deterministic check of ONE written sentence against the FULL text of the excerpts
    it cites (all ``shown`` ones when it cites none): ``(reason it fails or None, support)``.

    - literal: every literal of the sentence (``literals``/``literal_supported``) is in the cited
      excerpts' texts (and titles) together;
    - polarity: ``polarity_ok`` against the best-matching line/sentence (most shared literals, then
      content words; ≥ 1 literal or ≥ 2 words) of each of the top ``MAX_SUPPORT`` cited excerpts
      (D-157); skipped when no line shares;
    - support: per cited handle (the best-matching ones first, ≤ MAX_SUPPORT) its line with the most
      shared literals, then content words (else its first non-empty line), as the displayed quote.
      A sentence citing nothing is attributed to the excerpts that share the most with it (until
      their texts hold all its literals); none shares anything → ``unsupported``."""
    cache = {} if cache is None else cache
    cited = [h for h in dict.fromkeys(cites) if h in shown]
    pool = cited or list(shown)
    if not pool:
        return "unsupported", []
    lits = literals(text)
    hay = _hay([x for h in pool for x in (shown[h].title, shown[h].text)])
    if not all(literal_supported(x, hay) for x in lits):
        return "literal", []
    words = _content(text)
    best: dict[str, tuple[tuple[int, int], str]] = {}
    for h in pool:
        for unit, cw, uhay in _units(shown[h], cache):
            score = (sum(1 for x in lits if literal_supported(x, uhay)), _hits(words, cw))
            if (score[0] >= 1 or score[1] >= 2) and (h not in best or score > best[h][0]):
                best[h] = (score, unit)
    order = {h: i for i, h in enumerate(pool)}

    def rank(h: str) -> tuple[int, int, int]:
        return (-best[h][0][0], -best[h][0][1], order[h]) if h in best else (1, 0, order[h])

    ranked = sorted(pool, key=rank)
    # D-157: polarity against each top source's BEST line only (what the caller is shown), as a
    # claim is checked against its own quotes; every sharing line was too noisy (8/25 questions
    # lost sentences to a "not" about other words elsewhere in the excerpt).
    lines = [best[h][1] for h in ranked[:MAX_SUPPORT] if h in best]
    if lines and not polarity_ok(text, lines):
        return "polarity", []
    if cited:
        chosen = ranked[:MAX_SUPPORT]
    else:
        chosen = []
        for h in (h for h in ranked if h in best):
            chosen.append(h)
            have = _hay([x for c in chosen for x in (shown[c].title, shown[c].text)])
            if len(chosen) >= MAX_SUPPORT or all(literal_supported(x, have) for x in lits):
                break
        if not chosen:
            return "unsupported", []
    support = [(h, _display(best[h][1], lits) if h in best else _cut(_first_line(shown[h]))) for h in chosen]
    return None, support


def _join(kept: list[Claim], redact: Callable[[str], str]) -> str:
    """The kept sentences, in order, whole sentences up to ``ANSWER_MAX_CHARS``."""
    parts: list[str] = []
    size = 0
    for c in kept:
        add = len(c.text) + (1 if parts else 0)
        if parts and size + add > ANSWER_MAX_CHARS:
            break
        parts.append(c.text)
        size += add
    return redact(" ".join(parts))[:ANSWER_MAX_CHARS]


def assemble_cited(
    status: str | None,
    claims: list[Claim],
    related_hint: list[str],
    conf: str,
    shown: dict[str, Excerpt],
    redact: Callable[[str], str],
    **extra: Any,
) -> Validated:
    """The cite-mode answer from checked sentences (``Claim``s): the answer is the kept sentences;
    primary = the handles supporting the most kept sentences (≤ 3); related = the other supporting /
    cited handles and the model's suggestions (≤ 5); nothing kept → an abstention (guard when the
    model answered)."""
    kept = [c for c in claims if c.state == "kept"]
    dropped = len(claims) - len(kept)
    text = _join(kept, redact) if status == ANSWERED else ""
    if status != ANSWERED or not text.strip():
        closest = [h for h in related_hint if h in shown][:3]
        return Validated(
            INSUFFICIENT, status, "", claims, [], list(dict.fromkeys(closest)), "low",
            guard=status == ANSWERED, dropped_sentences=dropped, **extra,
        )  # fmt: skip
    ranked = rank_sources(claims)
    primary = ranked[:MAX_PRIMARY]
    related: list[str] = []
    for h in [*ranked[MAX_PRIMARY:], *(h for c in kept for h in c.cited), *related_hint]:
        if h in shown and h not in primary and h not in related:
            related.append(h)
    return Validated(
        ANSWERED, status, text, claims, primary, related[:MAX_RELATED], _lower(conf) if dropped else conf,
        dropped_sentences=dropped, **extra,
    )  # fmt: skip


def validate_cited(
    obj: dict[str, Any] | None, shown: dict[str, Excerpt], redact: Callable[[str], str] = lambda s: s
) -> Validated:
    """D-156: the deterministic check of a ``write`` output against the excerpts it was shown. Each
    sentence (≤ MAX_SENTENCES; handles written inside its text count as cites) is checked by
    ``cite_check`` against its valid cites (≤ MAX_CITES; none valid → all shown excerpts, counted as
    ``uncited``); a failing sentence is dropped (counted by reason), the rest is the answer (the
    first dropped → ``main_dropped``; nothing kept → an abstention, guard). Never cites a handle
    that was not shown."""
    obj = obj or {}
    status = obj.get("status") if obj.get("status") in (ANSWERED, INSUFFICIENT) else None
    conf = obj.get("confidence") if obj.get("confidence") in _CONF else "low"
    raw = obj.get("sentences") if isinstance(obj.get("sentences"), list) and status == ANSWERED else []
    claims: list[Claim] = []
    reasons = dict.fromkeys(DROP_REASONS, 0)
    uncited = 0
    cache: dict[str, list[_Unit]] = {}
    for x in raw[:MAX_SENTENCES]:
        if not isinstance(x, dict):
            continue
        text, inline = split_inline_cites(str(x.get("text") or ""), shown)
        if not text:
            continue
        cite = x.get("cite")
        cite = [cite] if isinstance(cite, str) else cite if isinstance(cite, list) else []
        named = [str(h).strip() for h in cite]
        cites = [h for h in dict.fromkeys([*named, *inline]) if h in shown][:MAX_CITES]
        uncited += int(not cites)
        why, support = cite_check(text, cites, shown, cache)
        if why is None:
            claims.append(Claim(text, support, "kept", cites))
        else:
            reasons[why] += 1
            claims.append(Claim(text, [], "dropped", cites))
    related_hint = [h for h in (obj.get("related") or []) if isinstance(h, str)]
    return assemble_cited(
        status,
        claims,
        related_hint,
        conf,
        shown,
        redact,
        dropped_claims=sum(1 for c in claims if c.state != "kept"),
        main_dropped=bool(claims) and claims[0].state != "kept",
        uncited=uncited,
        drop_reasons=reasons,
    )


# --------------------------------------------------------------------------- D-162 prose mode
#: a sentence or line boundary of a prose answer: after . ! ? … and whitespace, or a line break
_PROSE_BREAK = re.compile(r"(?<=[.!?…])\s+(?=\S)|\s*\n\s*")
#: a list or heading number that is all of a sentence so far ("1.", "- 2)", "### 3.", "**4.**"):
#: the boundary after it is not a sentence end
_ITEM_HEAD, _ITEM_NUM = r"\s*(?:#{1,6}\s+)?(?:[-*+•◦▪]\s+)?", r"(?:\*\*|__)?\d{1,3}[.)](?:\*\*|__)?"
_ITEM_ONLY = re.compile(_ITEM_HEAD + _ITEM_NUM)
#: a list/heading marker at the start of a sentence: layout, never a literal or a checked word
_ITEM_MARK = re.compile(rf"{_ITEM_HEAD}(?:{_ITEM_NUM}(?=\s|$))?\s*")
#: abbreviations a sentence does not end at
_ABBREV_END = re.compile(r"(?:^|[\s(])(?:e\.g|i\.e|vs|cf|z\.b|bzw|vb|approx|ca)\.$")
#: an excerpt handle written bare in a sentence ("see v12.3"): a reference, not a value
_BARE_HANDLE = re.compile(rf"(?<![\w.-]){_H}(?!\w)")


def split_sentences(text: str) -> list[tuple[str, bool]]:
    """D-162: a prose answer as ``(sentence, a line break follows it)``. It is split after . ! ? …
    followed by whitespace (the ``_SENTENCE`` rule) and at line breaks, but NEVER inside a backtick
    code span (D-161: "…" or "." inside `…`), after a bare list or heading number ("1. ") or after a
    common abbreviation ("e.g."). Whitespace is collapsed inside a sentence; a piece without a letter
    or digit (a table rule, "---") is layout and is left out. D-187: a FENCED code block (a line
    starting with ``` or ~~~, to its closing fence or the end) is ONE unit of its own, its lines kept
    as written (``is_block``; checked line by line)."""
    out: list[tuple[str, bool]] = []
    at = 0
    for m in _FENCED.finditer(text):
        out += _split_prose(text[at : m.start()])
        block = "\n".join(line.rstrip() for line in m.group(0).strip("\n").split("\n"))
        if any(ch.isalnum() for ch in block):
            out.append((block, True))
        at = m.end()
    return out + _split_prose(text[at:])


#: D-187: a fenced code block (its fence at a line start) to its closing fence, or to the end
_FENCED = re.compile(r"(?ms)^[ \t]*(```|~~~)[^\n]*\n.*?^[ \t]*\1[ \t]*$|^[ \t]*(?:```|~~~)[^\n]*(?:\n.*)?\Z")
_FENCE_LINE = re.compile(r"^[ \t]*(```|~~~)")


def is_block(text: str) -> bool:
    """D-187: a unit of ``split_sentences`` that is a fenced code block."""
    return _FENCE_LINE.match(text) is not None


def _split_prose(text: str) -> list[tuple[str, bool]]:
    code = [(m.start(), m.end()) for m in _CODE_SPAN.finditer(text)]
    out: list[tuple[str, bool]] = []
    start = 0
    for m in _PROSE_BREAK.finditer(text):
        at = m.start()
        if any(a <= at < b for a, b in code):
            continue
        head, brk = text[start:at], "\n" in m.group(0)
        if not brk and (_ITEM_ONLY.fullmatch(head) or _ABBREV_END.search(head.casefold())):
            continue
        out.append((head, brk))
        start = m.end()
    out.append((text[start:], False))
    return [(" ".join(s.split()), brk) for s, brk in out if any(ch.isalnum() for ch in s)]


def _body(sentence: str) -> str:
    """A sentence without its list/heading marker: what is checked and attributed."""
    m = _ITEM_MARK.match(sentence)
    return sentence[m.end() :] if m else sentence


#: D-187: placeholders a writer fills in or leaves for the reader, never a fabricated value:
#: ``<name>``, ``${VAR}``, ``$VAR``, ``{name}``, ``…`` / ``...``; and an ALL-CAPS single word
_PLACEHOLDER = re.compile(
    r"<[^<>\n]{1,60}>|\$\{[^}\n]{0,60}\}|\$[A-Za-z_][A-Za-z0-9_]*|\{[^{}\n]{1,60}\}|…|\.\.\."
)
_CAPS_WORD = re.compile(r"^[A-Z]{2,}$")
#: D-187: a bare integer up to this is never a hard literal (counts, steps, small settings)
SMALL_INT_MAX = 20


def _placeholder_word(tok: str) -> bool:
    """An ALL-CAPS single word (``STATE``, ``HOST``): a placeholder, never guarded."""
    return bool(_CAPS_WORD.match(tok.strip(_TOKEN_EDGE + ".")))


def _small_int(core: str) -> bool:
    """A bare integer ≤ ``SMALL_INT_MAX`` (a count, a step): not a hard literal on its own (inside a
    code span it is part of the span's value, e.g. ``Postgres 18``, and stays guarded)."""
    return core.isdigit() and int(core) <= SMALL_INT_MAX


#: D-190: a placeholder's stand-in inside a literal: one token, matched as a wildcard
#: (``_wildcard_ok``), so ``v<vid>[.<chunk>]`` is checked as ``v…[.…]``, never as fragments
PH = "qqph"
#: D-190: an ALL-CAPS name (``REF``, ``SERVER_IP``, ``DEVICE_NAME``) inside inline code: a
#: placeholder when no shown excerpt states it
_CAPS_NAME = re.compile(r"(?<![\w$])[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*(?![\w])")
_PH_EDGE = PH + " .-/:=\"'`()[]{}<>|"


def _placeholders_in(text: str, hay: str | None) -> str:
    """D-190 (a): placeholders -> ``PH``: ``<…>``, ``${…}``, ``$VAR``, ``{…}``, ``…``/``...``, and
    (with ``hay``) an ALL-CAPS name inside inline code that no shown excerpt states."""
    text = _PLACEHOLDER.sub(PH, text)
    if hay is None:
        return text

    def caps(m: re.Match[str]) -> str:
        w = m.group(0)
        return w if len(w) < 2 or _token_in(_lit_norm(w), hay) else PH

    return _CODE_SPAN.sub(lambda m: "`" + _CAPS_NAME.sub(caps, m.group(1)) + "`", text)


def hard_literals(
    text: str, handles: Iterable[str] = (), hay: str | None = None, *, wildcard: bool = True
) -> list[str]:
    """D-162: the literals of a sentence whose absence from every shown excerpt proves a fabricated
    value: the ``literals`` that hold a digit or come from a backtick code span (edge punctuation
    stripped: ```foo()``` is ``foo``). A quoted prose phrase and a § reference are not hard (D-161:
    wording, not values; a digit token inside a quote is a literal of its own), and neither is one of
    ``handles`` (an excerpt id the model was shown: a reference, not a value). D-187: ALL-CAPS single
    words are never hard, nor is a bare integer ≤ ``SMALL_INT_MAX``. D-190 (a): a placeholder is a
    WILDCARD token ``PH`` inside its literal (``_placeholders_in``; with ``hay``, also an unstated
    ALL-CAPS name in inline code), and a literal that is only placeholders and punctuation is none.
    ``wildcard=False``: the D-187 form (placeholders removed), for a block line's first check."""
    text = _placeholders_in(text, hay) if wildcard else _PLACEHOLDER.sub(" ", text)
    codes = {_lit_norm(m.group(1).strip(_TOKEN_EDGE)) for m in _CODE_SPAN.finditer(text)}
    skip = set(handles)
    out: list[str] = []
    for lit in literals(text):
        if "§" in lit or (not isinstance(lit, _CodeSpan) and _WS.search(lit)):
            continue
        core = type(lit)(lit.strip(_TOKEN_EDGE))
        if isinstance(core, _CodeSpan):
            toks = [t for t in core.split() if not _placeholder_word(t)]
            core = _CodeSpan(" ".join(toks)) if len(toks) > 1 else (toks[0] if toks else "")
            code = True
        else:
            code = _lit_norm(core) in codes
        if not core.strip() or core in skip or core in out or _placeholder_word(core):
            continue
        if not code and _small_int(core):
            continue
        if isinstance(core, _CodeSpan) or code or any(ch.isdigit() for ch in core):
            out.append(core)
    return [x for x in out if _lit_norm(x).strip(_PH_EDGE)]


def _wildcard_ok(literal: str, hay: str) -> bool:
    """D-190 (a): a literal holding the placeholder token ``PH`` is supported when the hay states it
    with any short run of non-space characters in each placeholder's place (a code span: each of its
    tokens, the placeholder-only ones skipped)."""
    c = _lit_norm(literal)
    if isinstance(literal, _CodeSpan):
        toks = [t.strip(_TOKEN_EDGE) for t in literal.split()]
        toks = [t for t in toks if (len(t) >= 2 or t.isdigit()) and _lit_norm(t).strip(PH + ".-/:=\"'")]
        return all(_wildcard_ok(t, hay) if PH in _lit_norm(t) else literal_supported(t, hay) for t in toks)
    parts = c.split(PH)
    if not any(re.search(r"\w", x) for x in parts):
        return True
    return re.search(r"\S{1,60}?".join(re.escape(x) for x in parts), hay) is not None


_NOTATION_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")


def _notation_variants(c: str) -> list[str | re.Pattern[str]]:
    """D-190 (c): other spellings of a normalised literal: ``74/200`` = 74 of 200 (or both numbers
    stated close together), ``3,000-token`` = 3000 tokens, ``→`` = ``->`` = "to", a range ``x-y``
    (an optional ``$`` on each end: both ends stated close together), ``$N`` = N USD, ``$N/unit`` = N
    per unit. Never a bare number on its own."""
    out: list[str | re.Pattern[str]] = []
    c = _NOTATION_THOUSANDS.sub("", c)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)", c)
    if m:
        a, b = m.groups()
        out += [f"{a} of {b}", f"{a} out of {b}", f"{a} / {b}"]
        out.append(
            re.compile(rf"(?<![\d.]){re.escape(a)}(?![\d])[^\n]{{0,30}}?(?<![\d.]){re.escape(b)}(?!\d)")
        )
    m = re.fullmatch(r"(\d+(?:\.\d+)?)-([^\W\d_]{2,})", c)
    if m:
        n, unit = m.groups()
        out.append(re.compile(rf"(?<![\d.]){re.escape(n)}(?!\d)\s*-?\s*{re.escape(unit[:4])}"))
    if "→" in c or "->" in c or "=>" in c:
        core = [x for x in re.split(r"\s*(?:→|->|=>)\s*", c) if x]
        out += [" → ".join(core), " -> ".join(core), "->".join(core), "→".join(core), " to ".join(core)]
    m = re.fullmatch(r"\$?(\d[\d.,]*)\s*-\s*\$?(\d[\d.,]*)(/[^\W\d_]+)?", c)
    if m:
        a, b = (re.escape(x) for x in m.groups()[:2])
        out.append(re.compile(rf"(?<![\d.]){a}(?!\d)[^\d\n]{{1,12}}(?<![\d.]){b}(?!\d)"))
    m = re.fullmatch(r"\$(\d[\d.,]*)", c)
    if m:
        n = m.group(1)
        out += [f"{n} usd", f"usd {n}", f"{n}$"]
    m = re.fullmatch(r"\$?(\d[\d.,]*)/([^\W\d_]+)", c)
    if m:
        n, unit = m.groups()
        out += [
            f"${n} per {unit}",
            f"{n} per {unit}",
            f"{n} usd/{unit}",
            f"{n} usd per {unit}",
            f"${n} / {unit}",
        ]
    return out


def _notation_ok(c: str, hay: str) -> bool:
    """D-190 (c): a normalised literal ``c`` is stated in another notation (``_notation_variants``),
    as the same number with other trailing zeros (``3.50`` = ``3.5``), or as a URL/path that is a
    prefix of one the excerpts state (up to a ``/``, ``?`` or ``#``)."""
    hay_n = _NOTATION_THOUSANDS.sub("", hay)
    for v in _notation_variants(c):
        if isinstance(v, re.Pattern):
            if v.search(hay_n):
                return True
        elif _token_in(v, hay_n) or v in hay_n:
            return True
    if re.fullmatch(r"\d+\.\d+", c):  # the same number, other trailing zeros
        want = Decimal(c)
        if any(Decimal(n) == want for n in _NUMBER.findall(hay_n)):
            return True
    if ("://" in c or "/" in c) and len(c.rstrip("/")) >= 6:  # a URL/path prefix of a stated one
        return re.search(r"(?<![\w/])" + re.escape(c.rstrip("/")) + r"(?=[/?#])", hay) is not None
    return False


def _prose_supported(literal: str, hay: str, derived: frozenset[Decimal], qhay: str) -> str | None:
    """How a hard literal of a prose answer is supported, or None: ``stated`` (an excerpt states it),
    ``derived`` (D-187: a sum/difference of stated numbers), D-190: ``wildcard`` (a placeholder's
    literal, ``_wildcard_ok``), ``notation`` (``_notation_ok``), ``question`` (the question states it:
    the caller's own words, not a fabrication)."""
    c = _lit_norm(literal)
    if PH in c:
        return "wildcard" if _wildcard_ok(literal, hay) else None
    if literal_supported(literal, hay):
        return "stated"
    if _is_derived(literal, derived):
        return "derived"
    if _notation_ok(c, hay):
        return "notation"
    if qhay and literal_supported(literal, qhay):
        return "question"
    return None


#: D-190 (d): shell variables and placeholders of a command line: ``$(…)``, ``${…}``, ``$VAR``,
#: ``<…>`` and an unstated ALL-CAPS name become one slot before two lines are compared
_CMD_VARS = re.compile(r"\$\{[^}\n]{0,60}\}|\$[A-Za-z_][A-Za-z0-9_]*|<[^<>\n]{1,60}>")
CMD_SLOT = "§v"
#: D-190 (d): a command line that relies on variables/placeholders is supported only when it is at
#: least this similar to a command an excerpt states (so a composed command is still caught)
CMD_SIMILARITY_MIN = 0.8


def _cmd_subst(s: str) -> str:
    """Every balanced ``$( … )`` command substitution replaced by the slot."""
    out, i = [], 0
    while i < len(s):
        if s.startswith("$(", i):
            depth, j = 0, i + 1
            while j < len(s):
                if s[j] == "(":
                    depth += 1
                elif s[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            out.append(CMD_SLOT)
            i = j + 1
            continue
        out.append(s[i])
        i += 1
    return "".join(out)


def _norm_cmd(line: str, hay: str | None = None) -> str:
    """D-190 (d): a command line with its variables and placeholders normalised to one slot, its
    leading prompt and trailing shell comment removed, ``_lit_norm``."""
    s = re.sub(r"^\s*(?:\$|>)\s+", "", line.strip())
    s = _SHELL_COMMENT.sub("", s)
    s = _CMD_VARS.sub(CMD_SLOT, _cmd_subst(s))
    if hay is not None:
        s = _CAPS_NAME.sub(lambda m: m.group(0) if _token_in(_lit_norm(m.group(0)), hay) else CMD_SLOT, s)
    return _lit_norm(s)


def _excerpt_commands(shown: dict[str, Excerpt], hay: str) -> list[str]:
    """D-190 (d): the commands the shown excerpts state, normalised (``_norm_cmd``): each text line
    and each inline code span of at least 8 characters."""
    out: list[str] = []
    for e in shown.values():
        for raw in [*(e.text or "").split("\n"), *(m.group(1) for m in _CODE_SPAN.finditer(e.text or ""))]:
            n = _norm_cmd(raw.strip("`"), hay)
            if len(n) >= 8:
                out.append(n)
    return list(dict.fromkeys(out))


def _cmd_similarity(n: str, commands: list[str]) -> float:
    """The best ``difflib`` ratio of a normalised command line to a stated command (sharing a word)."""
    import difflib

    toks = set(re.findall(r"\w{3,}", n))
    best = 0.0
    for ex in commands:
        if toks and not (toks & set(re.findall(r"\w{3,}", ex))):
            continue
        best = max(best, difflib.SequenceMatcher(None, n, ex).ratio())
    return round(best, 3)


# --------------------------------------------------------------------------- D-165 attribution
#: D-165: the weights of one (sentence, excerpt line) match. A hard literal of the sentence that the
#: EXCERPT states (its text, title or date) counts ``ATTR_DOC``, and ``ATTR_LIT`` more when the line
#: itself states it; a content word of the sentence in the line (prefix-tolerant, ``_hits``) counts
#: ``ATTR_WORD``; the multilingual similarity of the sentence and the line counts at most
#: ``ATTR_EMB`` (relative: 1 for the sentence's most similar embedded line, 0 at or below the mean of
#: its embedded lines). Literals dominate (one stated value outweighs four shared words), then words,
#: then the embedding (at most two words' worth).
ATTR_DOC, ATTR_LIT, ATTR_WORD, ATTR_EMB = 4.0, 3.0, 1.0, 2.0
#: tie-break bonuses (never part of the minimum): an excerpt the sentence names inline ("[v12.3]"),
#: one the model listed in its ``sources``
ATTR_CITED, ATTR_SOURCE = 0.6, 0.3
#: an excerpt supports a sentence from this score: a literal it states, two words, a word and a close
#: line; the excerpt of the sentence's most similar line always qualifies
ATTR_MIN = 2.0
#: the embedding's cost caps: lines per excerpt, texts per request (the sentences first), characters
#: per text, texts per embedder call, and the time budget (s) of all calls of one request
ATTR_LINES, ATTR_TEXTS, ATTR_TEXT_CHARS, ATTR_BATCH, ATTR_EMBED_S = 60, 400, 300, 8, 1.5

Embed = Callable[[list[str]], Any]
_Key = tuple[str, int]  # (excerpt handle, index of its line in ``_units``)


class LineSim:
    """D-165: the multilingual similarity of answer sentences and excerpt lines, from the server's own
    embedder (``embed``: texts -> L2-normalised rows, one per text; whatever model the server is
    configured with, D-017). The texts of one request are embedded ONCE (the first ``prepare`` with
    texts), in their order: deduplicated, each cut to ``ATTR_TEXT_CHARS``, ≤ ``ATTR_TEXTS``, in calls
    of ≤ ``ATTR_BATCH`` texts (sorted by length: less padding) while the next call is expected to end
    within ``budget_s``. A text left out (the cap, the budget, a failing embedder) has no similarity:
    its lines are scored by literals and words alone. Later calls (the re-check) only read the cache.
    ``state``: ``full`` (every wanted text embedded), ``partial`` or ``off``."""

    def __init__(
        self, embed: Embed, budget_s: float = ATTR_EMBED_S, clock: Callable[[], float] = time.perf_counter
    ) -> None:
        self._embed = embed
        self._budget = budget_s
        self._clock = clock
        self._vec: dict[str, np.ndarray] = {}
        self.prepared = False
        self.state = "off"
        self.wanted = 0
        self.seconds = 0.0

    @property
    def embedded(self) -> int:
        return len(self._vec)

    @staticmethod
    def key(text: str) -> str:
        return " ".join(text.split())[:ATTR_TEXT_CHARS]

    def get(self, text: str) -> np.ndarray | None:
        return self._vec.get(self.key(text))

    def prepare(self, texts: list[str]) -> None:
        if self.prepared:
            return
        todo = list(dict.fromkeys(k for k in map(self.key, texts) if k))[:ATTR_TEXTS]
        if not todo:
            return
        self.prepared, self.wanted = True, len(todo)
        t0 = last = self._clock()
        step = 0.0  # the longest call so far: the next one is expected to take as long
        try:
            for i in range(0, len(todo), ATTR_BATCH):
                if i and last + step - t0 > self._budget:
                    break
                part = sorted(todo[i : i + ATTR_BATCH], key=len)
                rows = np.asarray(self._embed(part), dtype=np.float32)
                if rows.ndim != 2 or rows.shape[0] != len(part):
                    raise ValueError(f"embedder returned shape {rows.shape} for {len(part)} texts")
                self._vec.update(zip(part, rows, strict=True))
                now = self._clock()
                step, last = max(step, now - last), now
        except Exception:  # noqa: BLE001 - an unavailable embedder: literal + word scoring (D-165)
            log.warning("research attribution: the embedder failed; literal+word scoring", exc_info=True)
        self.seconds = self._clock() - t0
        self.state = "full" if self.embedded == len(todo) else "partial" if self.embedded else "off"


def _embed_order(
    bodies: list[str], order: list[str], units: dict[str, list[_Unit]], lexes: list[_Lexical]
) -> list[str]:
    """What one request embeds, most useful first (a time budget that runs out cuts the tail): the
    sentences; then, for each sentence, each excerpt's best literal/word line (its excerpts by that
    score, interleaved over the sentences: where the similarity decides between excerpts); then the
    other lines sharing anything with a sentence; then the rest. Lines: ≤ ``ATTR_LINES`` per excerpt,
    ≥ ``QUOTE_MIN_CHARS``; the later tiers round-robin over ``order`` (the model's sources first)."""

    def ok(k: _Key) -> bool:
        return k[1] < ATTR_LINES and len(units[k[0]][k[1]][0]) >= QUOTE_MIN_CHARS

    rank = {h: i for i, h in enumerate(order)}
    per: list[list[_Key]] = []
    for lex in lexes:
        best: dict[str, tuple[float, _Key]] = {}
        for k, sc in lex.line.items():
            if ok(k) and (k[0] not in best or sc > best[k[0]][0]):
                best[k[0]] = (sc, k)
        total = {h: sc + ATTR_DOC * len(lex.doc[h]) for h, (sc, _k) in best.items()}
        per.append([best[h][1] for h in sorted(best, key=lambda h: (-total[h], rank.get(h, len(rank))))])
    keys: list[_Key] = [
        k for i in range(max(map(len, per), default=0)) for p in per if i < len(p) for k in p[i : i + 1]
    ]
    hot = {k for lex in lexes for k in lex.line if ok(k)}
    for tier in (True, False):
        lines = {
            h: [(h, j) for j in range(min(len(units[h]), ATTR_LINES)) if ((h, j) in hot) is tier]
            for h in order
        }
        for i in range(max((len(x) for x in lines.values()), default=0)):
            keys.extend(lines[h][i] for h in order if i < len(lines[h]) and ok(lines[h][i]))
    return [*bodies, *(units[h][j][0] for h, j in dict.fromkeys(keys))]


class _Lines:
    """The embedded lines of the shown excerpts (``_Key``) with their vectors."""

    __slots__ = ("keys", "mat")

    def __init__(self, sim: LineSim | None, units: dict[str, list[_Unit]]) -> None:
        self.keys: list[_Key] = []
        rows: list[np.ndarray] = []
        if sim is not None:
            for h, us in units.items():
                for j, (u, _cw, _uh) in enumerate(us[:ATTR_LINES]):
                    vec = sim.get(u)
                    if vec is not None:
                        self.keys.append((h, j))
                        rows.append(vec)
        self.mat = np.stack(rows) if rows else None

    def relative(self, vec: np.ndarray | None) -> tuple[dict[_Key, float], str | None]:
        """``(the relative similarity of each embedded line to a sentence, the excerpt of its most
        similar line)``: 1 for that line, (cos - mean) / (top - mean) clipped to [0, 1] for the
        others (the cosine range is the model's own; only the order within one sentence counts)."""
        if vec is None or self.mat is None:
            return {}, None
        cos = self.mat @ vec
        top = int(np.argmax(cos))
        mean, span = float(cos.mean()), float(cos[top] - cos.mean())
        rel = np.clip((cos - mean) / span, 0.0, 1.0) if span > 1e-6 else np.zeros_like(cos)
        out = {k: float(x) for k, x in zip(self.keys, rel, strict=True)}
        out[self.keys[top]] = 1.0
        return out, self.keys[top][0]


@dataclass(slots=True)
class _Lexical:
    """One kept sentence's literal and word evidence: ``doc`` = its hard literals each excerpt states
    (text, title or date); ``line`` = ``ATTR_LIT`` × the literals a line states + ``ATTR_WORD`` × its
    content words in the line (``_hits``), for every line that shares anything."""

    doc: dict[str, list[str]]
    line: dict[_Key, float]


def _lexical(body: str, lits: list[str], units: dict[str, list[_Unit]], hay_of: dict[str, str]) -> _Lexical:
    words = _content(body)
    doc = {h: [x for x in lits if literal_supported(x, hay_of[h])] for h in units}
    line: dict[_Key, float] = {}
    for h, us in units.items():
        for j, (_u, cw, uhay) in enumerate(us):
            s = ATTR_LIT * sum(1 for x in doc[h] if literal_supported(x, uhay)) + ATTR_WORD * _hits(words, cw)
            if s:
                line[(h, j)] = s
    return _Lexical(doc, line)


def _attribute_wide(
    lits: list[str],
    lex: _Lexical,
    rel: dict[_Key, float],
    nearest: str | None,
    cited: list[str],
    sources: list[str],
    shown: dict[str, Excerpt],
    units: dict[str, list[_Unit]],
) -> list[tuple[str, str]]:
    """D-165 ``wide``: the support of one sentence. EVERY shown excerpt is a candidate: ``ATTR_DOC`` ×
    the hard literals it states plus its best line's ``lex.line`` + ``ATTR_EMB`` × relative
    similarity ``rel``. The ≤ ``PROSE_ATTRIBUTE`` best (score plus the tie-break bonus of an excerpt
    the sentence names in ``cited`` or the model named in ``sources``) with at least ``ATTR_MIN``, or
    the excerpt of the sentence's most similar line (``nearest``), are its support, each with its best
    line (``_display``; the excerpt's first line when no line shares anything). None qualifies (no
    similarity) → the single best, which is the first source when nothing is shared at all."""
    bonus = {h: ATTR_SOURCE for h in sources} | {h: ATTR_CITED for h in cited}
    ranked: list[tuple[float, int, str, float, str]] = []
    for i, h in enumerate(shown):
        best, line = 0.0, ""
        for j, (unit, _cw, _uh) in enumerate(units[h]):
            s = lex.line.get((h, j), 0.0) + ATTR_EMB * rel.get((h, j), 0.0)
            if s > best:
                best, line = s, unit
        score = ATTR_DOC * len(lex.doc[h]) + best
        ranked.append((score + bonus.get(h, 0.0), i, h, score, line))
    ranked.sort(key=lambda r: (-r[0], r[1]))
    chosen = [r for r in ranked if r[3] >= ATTR_MIN or r[2] == nearest][:PROSE_ATTRIBUTE] or ranked[:1]
    return [
        (h, _display(line, lits) if line else _cut(_first_line(shown[h]))) for _t, _i, h, _s, line in chosen
    ]


def _best_line(body: str, lits: list[str], ex: Excerpt, units: list[_Unit]) -> tuple[tuple[int, int], str]:
    """``((its hard literals in the line, its content words in the line), line)`` of the line of ``ex``
    that shares the most with a sentence (literals first); ``((0, 0), "")`` when none shares."""
    words = _content(body)
    score, line = (0, 0), ""
    for unit, cw, uhay in units:
        s = (sum(1 for x in lits if literal_supported(x, uhay)), _hits(words, cw))
        if s > score:
            score, line = s, unit
    return score, line


def _shown_line(
    h: str, score: tuple[int, int], line: str, lits: list[str], shown: dict[str, Excerpt]
) -> tuple[str, str]:
    """The displayed quote of excerpt ``h`` for a sentence: its best line when that shares a literal
    or two words (``_display``), else the excerpt's first line."""
    if line and (score[0] >= 1 or score[1] >= 2):
        return h, _display(line, lits)
    return h, _cut(_first_line(shown[h]))


def _attribute_sources(
    body: str,
    lits: list[str],
    preferred: list[str],
    shown: dict[str, Excerpt],
    hay_of: dict[str, str],
    units: dict[str, list[_Unit]],
) -> list[tuple[str, str]]:
    """D-162 (V16, the D-165 ``sources`` strategy): the support of one sentence. The candidates are
    ``preferred`` (the handles it names, then the model's sources; none valid → every shown excerpt),
    plus any other shown excerpt that states one of its hard literals the candidates lack. The ≤
    ``PROSE_ATTRIBUTE`` candidates sharing the most hard literals with it, then content words (≥ 1
    literal or ≥ 2 words), are its support, each with its best line (``_shown_line``). Sharing
    nothing, it is attributed to the first candidate's first line."""
    pool = [h for h in dict.fromkeys(preferred) if h in shown] or list(shown)
    if not pool:
        return []
    have = "\n".join(hay_of[h] for h in pool)
    missing = [x for x in lits if not literal_supported(x, have)]
    if missing:
        pool += [h for h in shown if h not in pool and any(literal_supported(x, hay_of[h]) for x in missing)]
    ranked: list[tuple[tuple[int, int, int], int, str, str]] = []
    for i, h in enumerate(pool):
        here = [x for x in lits if literal_supported(x, hay_of[h])]
        score, line = _best_line(body, here, shown[h], units[h])
        ranked.append(((len(here), *score), i, h, line))
    ranked.sort(key=lambda r: (-r[0][0], -r[0][1], -r[0][2], r[1]))
    chosen = [r for r in ranked if r[0][0] >= 1 or r[0][2] >= 2][:PROSE_ATTRIBUTE]
    if not chosen:
        return [(pool[0], _cut(_first_line(shown[pool[0]])))]
    return [_shown_line(h, (n_line, hits), line, lits, shown) for (_n, n_line, hits), _i, h, line in chosen]


def _attribute_ids(
    body: str, lits: list[str], ids: list[str], shown: dict[str, Excerpt], units: dict[str, list[_Unit]]
) -> list[tuple[str, str]]:
    """D-165 ``llm``: the excerpts the attribute JOB named for one sentence, in its order, each with
    its best line (``_shown_line``)."""
    return [_shown_line(h, *_best_line(body, lits, shown[h], units[h]), lits, shown) for h in ids]


def attribute(
    sentences: list[str],
    shown: dict[str, Excerpt],
    strategy: str,
    *,
    model_sources: list[str],
    llm_cites: list[list[str]] | None = None,
    embed: Embed | LineSim | None = None,
) -> list[list[tuple[str, str]]]:
    """D-165: the support ``[(handle, displayed line), ...]`` of each of ``sentences`` (answer
    sentences as written: an inline "[v12.3]" or a bare shown handle names an excerpt, a list marker
    is layout) over the ``shown`` excerpts. PURE (no I/O; ``embed`` is only computed with), so the
    strategies can be replayed offline on saved answers (``HLM_RESEARCH_ATTRIBUTION``):

    - ``sources`` (default; V16, D-162): candidates are the handles the sentence names and the
      model's ``model_sources`` (none → every shown excerpt) plus an excerpt stating a hard literal
      they lack; literal + word scoring (``_attribute_sources``); no embedding.
    - ``wide`` (cbc4297): every shown excerpt is a candidate, scored by literals, words and the
      multilingual similarity of ``embed`` (a ``LineSim``, or texts -> L2-normalised rows; None →
      literals and words only; ``_attribute_wide``).
    - ``llm``: ``llm_cites[i]`` are the excerpt ids the JOB attribute named for sentence ``i`` (ids
      not shown are ignored, ≤ ``ATTRIBUTE_MAX``); each is shown with its best line. A sentence with
      no valid id (or no ``llm_cites``) is attributed as ``sources``.
    """
    if not shown:
        return [[] for _ in sentences]
    cache: dict[str, list[_Unit]] = {}
    units = {h: _units(ex, cache) for h, ex in shown.items()}
    hay_of = {h: _hay([e.title, e.date, e.text]) for h, e in shown.items()}
    sources = [h for h in dict.fromkeys(model_sources) if h in shown]
    parsed: list[tuple[str, list[str], list[str]]] = []  # (body, hard literals, handles it names)
    for s in sentences:
        text, inline = split_inline_cites(s, shown)
        body = _body(text)
        refs = [h for h in _BARE_HANDLE.findall(body) if h in shown]
        parsed.append(
            (body, hard_literals(body, shown), [h for h in dict.fromkeys([*inline, *refs]) if h in shown])
        )
    if strategy == "wide":
        sim = embed if isinstance(embed, LineSim) or embed is None else LineSim(embed)
        lexes = [_lexical(body, lits, units, hay_of) for body, lits, _c in parsed]
        if sim is not None:
            order = [*sources, *(h for h in shown if h not in sources)]
            sim.prepare(_embed_order([body for body, _l, _c in parsed], order, units, lexes))
        lines = _Lines(sim, units)
        out = []
        for (body, lits, cited), lex in zip(parsed, lexes, strict=True):
            rel, nearest = lines.relative(sim.get(body) if sim is not None else None)
            out.append(_attribute_wide(lits, lex, rel, nearest, cited, sources, shown, units))
        return out
    out = []
    for i, (body, lits, cited) in enumerate(parsed):
        ids: list[str] = []
        if strategy == "llm" and llm_cites is not None and i < len(llm_cites):
            ids = [h for h in dict.fromkeys(llm_cites[i]) if h in shown][:ATTRIBUTE_MAX]
        if ids:
            out.append(_attribute_ids(body, lits, ids, shown, units))
        else:
            out.append(_attribute_sources(body, lits, [*cited, *sources], shown, hay_of, units))
    return out


def _prose_keep(
    sentences: list[tuple[str, bool]],
    shown: dict[str, Excerpt],
    added_from: int | None = None,
    explain: list[dict[str, Any]] | None = None,
    question: str = "",
) -> tuple[list[Claim], dict[str, int], list[str]]:
    """D-162: the literal check of prose sentences: ``(claims, drop counts by reason, the kept
    sentences as written)``. A sentence is DROPPED only when one of its hard literals is in no shown
    excerpt (texts, titles and dates together: what the model was shown), or (``unsupported``) when
    nothing was shown; every other one is KEPT (its support is ``attribute``'s). Sentences past
    ``ANSWER_MAX_CHARS`` of kept text are not checked. D-170: the sentences from index
    ``added_from`` on are the expand pass's (``Claim.added``). D-189: with ``explain`` (the trace),
    one entry per checked unit is appended: its literals, where each was found, its verdict. D-190:
    a literal is also supported as a placeholder wildcard, in another notation, or when the
    ``question`` states it (``_prose_supported``); block lines follow ``_check_block``."""
    hay = "\n".join(_hay([e.title, e.date, e.text]) for e in shown.values())
    derived = _derived_numbers(hay)
    qhay = _lit_norm(question) if question else ""

    def ok(x: str) -> bool:  # stated, derived (D-187), a wildcard, another notation, the question's (D-190)
        return _prose_supported(x, hay, derived, qhay) is not None

    claims: list[Claim] = []
    raw_of: list[str] = []
    reasons = {"literal": 0, "unsupported": 0, "block_lines": 0, "dangling": 0}
    size = 0

    def carry(brk: bool, before: int | None = None) -> None:  # a dropped unit's line break stays
        pool = claims if before is None else claims[:before]
        last = next((c for c in reversed(pool) if c.state == "kept"), None)
        if last is not None and brk:
            last.line_end = True

    for i, (raw, brk) in enumerate(sentences):
        added = added_from is not None and i >= added_from
        if is_block(raw):  # D-187: a fenced block, checked line by line
            lines_explain: list[dict[str, Any]] | None = [] if explain is not None else None
            text, lines_dropped = _check_block(raw, ok, shown, lines_explain)
            if explain is not None:
                explain.append(
                    {"unit": "block", "text": raw, "added": added, "lines": lines_explain, "kept_text": text}
                )
            reasons["block_lines"] += lines_dropped
            if text is not None and size and size + 1 + len(text) > ANSWER_MAX_CHARS:
                break
            if text is None or not shown:
                reasons["literal" if shown else "unsupported"] += 1
                claims.append(Claim(raw, [], "dropped", line_end=True, added=added))
                raw_of.append(raw)
                carry(True)
                continue
            size += len(text) + (1 if size else 0)
            claims.append(Claim(text, [], "kept", line_end=True, added=added))
            raw_of.append(text)
            continue
        text, _inline = split_inline_cites(raw, shown)
        body = _body(text)
        if not any(ch.isalnum() for ch in body):
            continue
        if size and size + 1 + len(text) > ANSWER_MAX_CHARS:
            break
        why = None
        lits = hard_literals(body, shown, hay=hay)
        if not all(ok(x) for x in lits):
            why = "literal"
        elif not shown:
            why = "unsupported"
        if explain is not None:
            explain.append(
                {
                    "unit": "sentence",
                    "text": text,
                    "added": added,
                    "literals": [_explain_literal(x, hay, derived, shown, qhay) for x in lits],
                    "verdict": "dropped" if why else "kept",
                    "reason": why,
                }
            )
        if why is not None:
            reasons[why] += 1
            claims.append(Claim(text, [], "dropped", line_end=brk, added=added))
            raw_of.append(raw)
            carry(brk)
            continue
        size += len(text) + (1 if size else 0)
        claims.append(Claim(text, [], "kept", line_end=brk, added=added))
        raw_of.append(raw)
    # D-187: a lead-in ("…, run:") whose block or list was dropped, or that ends the answer, goes too
    for i, c in enumerate(claims):
        if c.state != "kept" or not c.text.rstrip().endswith(":"):
            continue
        j = i + 1
        while j < len(claims) and (is_block(claims[j].text) or _LIST_ITEM.match(claims[j].text)):
            j += 1
        follow = claims[i + 1 : j]
        if (follow and all(f.state != "kept" for f in follow)) or (
            not follow and not any(f.state == "kept" for f in claims[i + 1 :])
        ):
            c.state = "dropped"
            reasons["dangling"] += 1
            carry(c.line_end, before=i)
            if explain is not None:
                explain.append(
                    {"unit": "dangling", "text": c.text, "verdict": "dropped", "reason": "dangling"}
                )
    raws = [r for c, r in zip(claims, raw_of, strict=True) if c.state == "kept"]
    return claims, reasons, raws


#: D-187: a list item unit (its marker): what a lead-in ending with ":" introduces
_LIST_ITEM = re.compile(r"^[ \t]*(?:[-*+•◦▪]|\d{1,3}[.)])[ \t]+")
_SHELL_COMMENT = re.compile(r"[ \t]+#[ \t].*$")
#: D-187: the numbers of the shown excerpts whose pairwise sums and differences are "derived"
#: (a count or total the writer computed), at most this many distinct ones (bounded work)
DERIVED_NUMBERS_MAX = 150
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])")


def _derived_numbers(hay: str) -> frozenset[Decimal]:
    """D-187: the sums and (absolute) differences of two numbers stated in ``hay`` (the first
    ``DERIVED_NUMBERS_MAX`` distinct ones)."""
    nums: list[Decimal] = []
    for m in _NUMBER.finditer(hay):
        d = Decimal(m.group(0))
        if d not in nums:
            nums.append(d)
            if len(nums) >= DERIVED_NUMBERS_MAX:
                break
    out: set[Decimal] = set()
    for i, a in enumerate(nums):
        for b in nums[i + 1 :]:
            out.add((a + b).normalize())
            out.add(abs(a - b).normalize())
    return frozenset(out)


def _is_derived(literal: str, derived: frozenset[Decimal]) -> bool:
    c = _THOUSANDS.sub("", _lit_norm(literal))
    if not re.fullmatch(r"\d+(?:\.\d+)?", c):
        return False
    return Decimal(c).normalize() in derived


def _explain_literal(
    x: str, hay: str, derived: frozenset[Decimal], shown: dict[str, Excerpt], qhay: str = ""
) -> dict[str, Any]:
    """D-189 (trace): one hard literal: how it is supported (``_prose_supported``) and, when stated,
    in which shown excerpts."""
    how = _prose_supported(x, hay, derived, qhay)
    found = (
        [h for h, e in shown.items() if literal_supported(x, _hay([e.title, e.date, e.text]))]
        if how == "stated"
        else []
    )
    return {
        "literal": str(x),
        "supported": how is not None,
        "how": how,
        "found_in": found,
        "derived": how == "derived",
    }


def _check_block(
    block: str,
    ok: Callable[[str], bool],
    shown: dict[str, Excerpt],
    explain: list[dict[str, Any]] | None = None,
) -> tuple[str | None, int]:
    """D-187: ``(the block with only its supported lines, lines dropped)``, or ``(None, ...)`` when no
    command line survives. A comment line (# or //) is checked for its hard literals; an empty line
    stays. An unclosed block gets its closing fence. D-190 (d), a command line (checked as a code
    span, its trailing shell comment left out) is supported when
    - it is equal to a command an excerpt states once its variables and placeholders are one slot
      (``_norm_cmd``, ``_excerpt_commands``), or
    - its literals pass with the placeholders removed (the D-187 check), or
    - they pass only with the placeholders and variables as WILDCARDS, and the line is at least
      ``CMD_SIMILARITY_MIN`` similar to a stated command (a composed command stays caught).
    A literal that fails even with the wildcards drops it (a changed flag, an invented value)."""
    lines = block.split("\n")
    fence = _FENCE_LINE.match(lines[0])
    assert fence is not None
    head, inner = lines[0], lines[1:]
    tail = fence.group(1)
    if inner and _FENCE_LINE.match(inner[-1]) and inner[-1].strip().startswith(fence.group(1)):
        tail, inner = inner[-1], inner[:-1]
    kept: list[str] = []
    dropped = commands = 0
    hay = "\n".join(_hay([e.title, e.date, e.text]) for e in shown.values())
    stated: list[str] | None = None  # the excerpts' commands, normalised (built when first needed)
    for line in inner:
        bare = line.strip()
        if not bare:
            kept.append(line)
            continue
        comment = bare.startswith(("#", "//"))
        code = "" if comment else _SHELL_COMMENT.sub("", bare).replace("`", "").strip()
        if comment or not code:
            lits = hard_literals(bare, shown, hay=hay) if comment else []
            verdict, how, sim = all(ok(x) for x in lits), None, None
        else:
            n = _norm_cmd(code, hay)
            if stated is None:
                stated = _excerpt_commands(shown, hay)
            how, sim = None, None
            lits = hard_literals(f"`{code}`", shown, hay=hay, wildcard=False)
            if len(n) >= 8 and n in stated:
                verdict, how = True, "equal"
            elif all(ok(x) for x in lits):
                verdict, how = True, "plain"
            else:  # D-190 (d): only as wildcards, and only close to a stated command
                lits = hard_literals(f"`{code}`", shown, hay=hay)
                wild = CMD_SLOT in n or any(PH in _lit_norm(x) for x in lits)
                if wild and all(ok(x) for x in lits):
                    sim = _cmd_similarity(n, stated)
                    verdict, how = sim >= CMD_SIMILARITY_MIN, "similar"
                else:
                    verdict = False
        if explain is not None:
            explain.append(
                {
                    "line": line,
                    "literals": [str(x) for x in lits],
                    "kept": verdict,
                    "how": how,
                    "similarity": sim,
                }
            )
        if verdict:
            kept.append(line)
            commands += not comment
        else:
            dropped += 1
    if not commands:
        return None, dropped
    return "\n".join([head, *kept, tail]), dropped


def _prose_sentences(answer: str, added: list[str] | None) -> tuple[list[tuple[str, bool]], int]:
    """``(the sentences of a prose answer and then of its added sentences, the index of the first
    added one)`` (D-170): the added sentences follow the answer, after a line break when the answer
    is laid out in lines (a list), else after a space."""
    sentences = split_sentences(answer)
    start = len(sentences)
    if added:
        if sentences and "\n" in answer.strip():
            sentences[-1] = (sentences[-1][0], True)
        for a in added:
            sentences += split_sentences(a)
    return sentences, start


def prose_check(
    sentences: list[tuple[str, bool]],
    sources: list[str],
    shown: dict[str, Excerpt],
    strategy: str = "sources",
    *,
    embed: Embed | LineSim | None = None,
    llm_cites: dict[str, list[str]] | None = None,
    added_from: int | None = None,
    explain: list[dict[str, Any]] | None = None,
    question: str = "",
) -> tuple[list[Claim], dict[str, int]]:
    """D-162: the deterministic check of prose sentences (``split_sentences``) against the excerpts
    the answer was written from: ``(claims, drop counts by reason)``. A sentence is dropped only for
    a hard literal no shown excerpt states (``_prose_keep``; ``added_from``: D-170's added ones);
    every other one is kept and attributed by ``attribute`` with ``strategy`` (D-165; ``llm_cites``:
    the JOB attribute's ids per kept sentence TEXT). There is no polarity check (D-165: its flags were
    false positives)."""
    claims, reasons, raws = _prose_keep(sentences, shown, added_from, explain, question)
    kept = [c for c in claims if c.state == "kept"]
    cites = [llm_cites.get(c.text, []) for c in kept] if llm_cites is not None else None
    supports = attribute(raws, shown, strategy, model_sources=sources, llm_cites=cites, embed=embed)
    for c, support in zip(kept, supports, strict=True):
        c.support, c.cited = support, [h for h, _q in support]
    return claims, reasons


def _join_prose(kept: list[Claim], redact: Callable[[str], str]) -> str:
    """The kept sentences in order, a line break where the answer had one."""
    parts: list[str] = []
    for i, c in enumerate(kept):
        if i:
            parts.append("\n" if kept[i - 1].line_end else " ")
        parts.append(c.text)
    return redact("".join(parts))[:ANSWER_MAX_CHARS]


def assemble_prose(
    status: str | None,
    claims: list[Claim],
    sources: list[str],
    related_hint: list[str],
    conf: str,
    shown: dict[str, Excerpt],
    redact: Callable[[str], str],
    **extra: Any,
) -> Validated:
    """The prose-mode answer: the kept sentences; primary = the excerpts attributed to the most kept
    sentences (≤ ``MAX_PRIMARY``); related = the model's related, its other sources, the other
    attributed excerpts, then the remaining retrieved ones (≤ ``MAX_RELATED``). A dropped sentence
    lowers the confidence. Nothing kept → an abstention (guard when the model answered; related = the
    closest ≤ 3: its sources, then its related)."""
    kept = [c for c in claims if c.state == "kept"]
    dropped = len(claims) - len(kept)
    text = _join_prose(kept, redact) if status == ANSWERED else ""
    if status != ANSWERED or not text.strip():
        hint = [*sources, *related_hint] if status == ANSWERED else related_hint
        closest = [h for h in dict.fromkeys(hint) if h in shown][:3]
        return Validated(
            INSUFFICIENT, status, "", claims, [], closest, "low", guard=status == ANSWERED,
            dropped_sentences=dropped, sources=list(sources), **extra,
        )  # fmt: skip
    ranked = rank_sources(claims)
    primary = ranked[:MAX_PRIMARY]
    related = [
        h
        for h in dict.fromkeys([*related_hint, *sources, *ranked[MAX_PRIMARY:], *shown])
        if h in shown and h not in primary
    ][:MAX_RELATED]
    return Validated(
        ANSWERED, status, text, claims, primary, related, _lower(conf) if dropped else conf,
        dropped_sentences=dropped, sources=list(sources), **extra,
    )  # fmt: skip


def _prose_fields(
    obj: dict[str, Any] | None, shown: dict[str, Excerpt]
) -> tuple[str | None, str, list[str], list[str], str]:
    """``(status, confidence, sources, related hint, answer)`` of a ``prose`` output: the model's
    ``sources`` are shown ids only (≤ ``PROSE_MAX_SOURCES``); the answer is empty unless answered."""
    obj = obj or {}
    status = obj.get("status") if obj.get("status") in (ANSWERED, INSUFFICIENT) else None
    conf = obj.get("confidence") if obj.get("confidence") in _CONF else "low"
    raw = obj.get("sources")
    raw = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    sources = [h for h in dict.fromkeys(x.strip() for x in raw if isinstance(x, str)) if h in shown]
    rel = obj.get("related") if isinstance(obj.get("related"), list) else []
    related_hint = [h.strip() for h in rel if isinstance(h, str)]
    answer = obj.get("answer") if status == ANSWERED and isinstance(obj.get("answer"), str) else ""
    return status, conf, sources[:PROSE_MAX_SOURCES], related_hint, answer


def prose_kept(
    obj: dict[str, Any] | None, shown: dict[str, Excerpt], added: list[str] | None = None, question: str = ""
) -> list[str]:
    """D-165: the sentences of a ``prose`` output (and D-170 its ``added`` sentences) that survive the
    literal check, as they are kept in the answer (the numbered sentences of the JOBs expand and
    attribute; their order is ``validate_prose``'s)."""
    _s, _c, _src, _rel, answer = _prose_fields(obj, shown)
    sentences, start = _prose_sentences(answer, added)
    claims, _reasons, _raws = _prose_keep(sentences, shown, start, question=question)
    return [c.text for c in claims if c.state == "kept"]


def validate_prose(
    obj: dict[str, Any] | None,
    shown: dict[str, Excerpt],
    redact: Callable[[str], str] = lambda s: s,
    strategy: str = "sources",
    *,
    embed: Embed | LineSim | None = None,
    llm_cites: dict[str, list[str]] | None = None,
    added: list[str] | None = None,
    explain: list[dict[str, Any]] | None = None,
    question: str = "",
) -> Validated:
    """D-162: the deterministic check of a ``prose`` output against the excerpts it was shown
    (``prose_check``): sentences with a fabricated hard literal are dropped (``drop_reasons``), the
    rest is the answer, attributed by ``strategy`` (D-165 ``attribute``). D-170: the expand pass's
    ``added`` sentences follow the answer's and pass the same checks (``expand_added`` kept,
    ``expand_dropped`` not). Never cites a handle that was not shown."""
    status, conf, sources, related_hint, answer = _prose_fields(obj, shown)
    sentences, start = _prose_sentences(answer, added if status == ANSWERED else None)
    claims, reasons = prose_check(
        sentences,
        sources,
        shown,
        strategy,
        embed=embed,
        llm_cites=llm_cites,
        added_from=start,
        explain=explain,
        question=question,
    )
    kept_added = sum(1 for c in claims if c.added and c.state == "kept")
    return assemble_prose(
        status,
        claims,
        sources,
        related_hint,
        conf,
        shown,
        redact,
        dropped_claims=sum(1 for c in claims if c.state != "kept"),
        main_dropped=bool(claims) and claims[0].state != "kept",
        drop_reasons=reasons,
        expand_added=kept_added,
        expand_dropped=len(sentences) - start - kept_added,
    )


def expand_user(
    question: str, sentences: list[str], excerpts: list[Excerpt], redact: Redact | None = None
) -> str:
    """D-170: the JOB ``expand`` (the question, the current answer's kept sentences numbered from 1,
    and the excerpts exactly as the prose job saw them)."""
    payload = {
        "question": question,
        "answer": [{"n": i, "text": s} for i, s in enumerate(sentences, start=1)],
        "excerpts": [e.shown(temporal=True) for e in excerpts],
    }
    return "JOB: expand\n" + _input(payload, redact)


def parse_expand(obj: dict[str, Any] | None, answer: list[str]) -> list[str]:
    """D-170: the new sentences of an expand output (``add``): strings with a letter or digit,
    whitespace collapsed, none repeating a sentence of ``answer`` or an earlier one, ≤ ``EXPAND_MAX``;
    ``[]`` for none or a malformed output."""
    raw = obj.get("add") if isinstance(obj, dict) else None
    seen = {qnorm(x) for x in answer}
    out: list[str] = []
    for x in raw if isinstance(raw, list) else []:
        text = " ".join(x.split()) if isinstance(x, str) else ""
        if not any(ch.isalnum() for ch in text) or qnorm(text) in seen:
            continue
        seen.add(qnorm(text))
        out.append(text)
        if len(out) >= EXPAND_MAX:
            break
    return out


# --------------------------------------------------------------------------- D-178 text protocol
#: the JOBs with a plain-text variant, for a profile without JSON mode (``LlmProfile.json_mode``)
TEXT_JOBS = {"prose": "prose_text", "expand": "expand_text"}
_TEXT_FENCE = re.compile(r"^\s*```[\w-]*[ \t]*\n?|\n?[ \t]*```\s*$")
#: a header line: optional markup, the key (any case), optional markup, ":" and its value
_TEXT_KEY = re.compile(
    r"^[ \t]*[*_#>`-]*[ \t]*(status|confidence|sources|related|answer)[ \t]*[*_`]*[ \t]*:[ \t]*(.*)$", re.I
)
_ADD_KEY = re.compile(r"^[ \t]*[*_#>`-]*[ \t]*add[ \t]*[*_`]*[ \t]*:[ \t]*(.*)$", re.I)
_TEXT_ITEM = re.compile(r"^[ \t]*(?:[-*+•][ \t]+|\d{1,3}[.)][ \t]+)")
_TEXT_NONE = frozenset(["", "[]", "none", "-", "n/a", "(none)", "empty"])
_TEXT_MAX_ANSWER = 20000  # the JSON schema's own answer bound (research/v3.schema.json)
_TEXT_MAX_IDS = 16


def _text_body(text: str) -> list[str]:
    return _TEXT_FENCE.sub("", text.replace("\r\n", "\n").replace("\r", "\n").strip()).split("\n")


def _text_ids(value: str, shown: set[str] | None) -> list[str]:
    ids = [x.strip("[](){}'\"`*. ") for x in re.split(r"[,;\s]+", value)]
    ok = [x for x in ids if x and (x in shown if shown is not None else _HANDLE.match(x))]
    return list(dict.fromkeys(ok))[:_TEXT_MAX_IDS]


def parse_prose_text(
    text: str | None, shown: Iterable[str] | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    """D-178: the plain-text layout of the JOB ``prose_text`` -> the object the JSON JOB ``prose``
    returns, or ``(None, why)`` (a schema failure, retried once). Tolerant: an outer code fence,
    CR/LF, header keys in any case/order with markup or extra spaces around them, stray lines
    before ``ANSWER:``. ``STATUS`` (answered / insufficient_evidence) and the ``ANSWER:`` line are
    required; the answer is everything after it (text on its own line included) to the end.
    ``SOURCES``/``RELATED`` ids are kept only when shown (``shown``; without it, well-formed
    handles); an unknown ``CONFIDENCE`` is left out (the answer's reads "low")."""
    if text is None or not text.strip():
        return None, "empty response"
    lines = _text_body(text)
    head: dict[str, str] = {}
    at = None
    for i, line in enumerate(lines):
        m = _TEXT_KEY.match(line)
        if m is None:
            continue
        key, value = m.group(1).lower(), m.group(2).strip()
        if key == "answer":
            at = i
            break
        head.setdefault(key, value)
    if at is None:
        return None, "text layout: no ANSWER line"
    status = re.sub(r"[\s-]+", "_", head.get("status", "").strip(" *_`.").lower())
    if status not in (ANSWERED, INSUFFICIENT):
        return None, f"text layout: STATUS missing or invalid ({head.get('status', '')[:40]!r})"
    first = _TEXT_KEY.match(lines[at])
    rest = "\n".join([first.group(2) if first else "", *lines[at + 1 :]]).strip()
    valid = set(shown) if shown is not None else None
    obj: dict[str, Any] = {
        "status": status,
        "answer": rest[:_TEXT_MAX_ANSWER],
        "sources": _text_ids(head.get("sources", ""), valid),
        "related": _text_ids(head.get("related", ""), valid),
    }
    conf = head.get("confidence", "").strip(" *_`.").lower()
    if conf in _CONF:
        obj["confidence"] = conf
    return obj, None


def parse_expand_text(text: str | None) -> tuple[dict[str, Any] | None, str | None]:
    """D-178: the plain-text layout of the JOB ``expand_text`` (an ``ADD:`` line, then one sentence
    per line; list markers are layout) -> ``{"add": [...]}``, or ``(None, why)``. Nothing after
    ``ADD:`` (or "none" / "[]") is ``{"add": []}``."""
    if text is None or not text.strip():
        return None, "empty response"
    lines = _text_body(text)
    at = next((i for i, line in enumerate(lines) if _ADD_KEY.match(line)), None)
    if at is None:
        return None, "text layout: no ADD line"
    m = _ADD_KEY.match(lines[at])
    items = [m.group(1) if m else "", *lines[at + 1 :]]
    add = [s for s in (_TEXT_ITEM.sub("", x).strip() for x in items) if s.lower() not in _TEXT_NONE]
    return {"add": add[:12]}, None


def _payload_ids(user: str) -> list[str] | None:
    """The excerpt ids of a JOB's INPUT (``_input``'s one-line JSON), or None."""
    try:
        payload = json.loads(user.split("INPUT: ", 1)[1].split("\n", 1)[0])
    except (IndexError, ValueError):
        return None
    ex = payload.get("excerpts") if isinstance(payload, dict) else None
    return [str(e.get("id")) for e in ex if isinstance(e, dict)] if isinstance(ex, list) else None


def text_variant(job: str, user: str) -> tuple[str, Callable[[str | None], Any]] | None:
    """D-178: the plain-text protocol of ``job`` for a profile without JSON mode: ``(the same
    message under the JOB's text name, its parser)``; None for a JOB without one."""
    name = TEXT_JOBS.get(job)
    if name is None or not user.startswith(f"JOB: {job}\n"):
        return None
    message = f"JOB: {name}\n" + user.split("\n", 1)[1]
    if job == "expand":
        return message, parse_expand_text
    shown = _payload_ids(user)
    return message, lambda content: parse_prose_text(content, shown)


def attribute_user(
    question: str, sentences: list[str], excerpts: list[Excerpt], redact: Redact | None = None
) -> str:
    """D-165 ``llm`` attribution: the JOB ``attribute`` (the question, the kept answer sentences
    numbered from 1, the excerpts the prose job saw: id, title, text)."""
    payload = {
        "question": question,
        "sentences": [{"n": i, "text": s} for i, s in enumerate(sentences, start=1)],
        "excerpts": [{"id": e.handle, "title": e.title, "text": e.text} for e in excerpts],
    }
    return "JOB: attribute\n" + _input(payload, redact)


def parse_attribute(
    obj: dict[str, Any] | None, sentences: list[str], shown: Iterable[str]
) -> dict[str, list[str]]:
    """D-165: the JOB attribute's ``cites`` as ``{sentence text: [excerpt id, ...]}``: numbers 1..n
    of ``sentences`` only (the first entry of a number wins), shown ids only, each once, ≤
    ``ATTRIBUTE_MAX``; a sentence it left out (or ``[]``) maps to ``[]`` (attributed as ``sources``)."""
    ok = set(shown)
    got: dict[int, list[str]] = {}
    raw = obj.get("cites") if isinstance(obj, dict) else None
    for x in raw if isinstance(raw, list) else []:
        if not isinstance(x, dict):
            continue
        try:
            n = int(x.get("s"))
        except (TypeError, ValueError):
            continue
        ids = x.get("ids")
        ids = [ids] if isinstance(ids, str) else ids if isinstance(ids, list) else []
        if 1 <= n <= len(sentences) and n not in got:
            got[n] = [h for h in dict.fromkeys(str(i).strip() for i in ids) if h in ok][:ATTRIBUTE_MAX]
    out: dict[str, list[str]] = {}
    for n, s in enumerate(sentences, start=1):
        out.setdefault(s, got.get(n, []))
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
        #: D-172: ``(profile, outcome)`` of every attempt per lineage, in order
        self.outcomes: dict[str, list[tuple[str, str]]] = {}
        #: D-189: every attempt's row facts per lineage, in order (the trace recorder reads them)
        self.rows: dict[str, list[dict[str, Any]]] = {}

    async def record(self, row: LedgerRow) -> None:
        if row.lineage is not None:
            self.outcomes.setdefault(row.lineage, []).append((row.profile, row.outcome))
            self.rows.setdefault(row.lineage, []).append(
                {
                    "profile": row.profile,
                    "model": row.model_id,
                    "outcome": row.outcome,
                    "input_tokens": row.input_tokens,
                    "output_tokens": row.output_tokens,
                    "reserved_usd": str(row.reserved_usd) if row.reserved_usd is not None else None,
                    "cost_usd": str(row.cost_usd) if row.cost_usd is not None else None,
                    "latency_ms": row.latency_ms,
                }
            )
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
        self.outcomes.pop(lineage, None)
        self.rows.pop(lineage, None)
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


def writer_chain(settings: Any, task_chain: list[LlmProfile]) -> list[LlmProfile]:
    """D-171: the chain of the jobs that write the prose answer (``WRITER_JOBS``): the named profile
    ``HLM_RESEARCH_WRITER_PROFILE`` (resolved like ``HLM_FALLBACK_PROFILE__<TASK>``: its own file,
    never the env's ``HLM_LLM_*``; D-172: its attempts capped at ``HLM_RESEARCH_WRITER_TIMEOUT_S``,
    ``LlmProfile.attempt_timeout_s``), then the research task's own profile as its fallback. ``[]`` (the
    task chain writes) when unset, naming the task profile, without a task chain, unknown or broken
    (logged), or not qualified for ``research`` (its ``disabled_tasks``, D-071; logged)."""
    name = str(getattr(settings, "research_writer_profile", None) or "").strip()
    if not name or not task_chain or name == task_chain[0].name:
        return []
    try:
        writer = named_profile(name)
    except LlmConfigError as exc:
        log.warning("%s=%r ignored (the research profile writes): %s", WRITER_ENV, name, exc)
        return []
    if TASK in writer.disabled_tasks:
        log.warning("%s=%r lists %r in disabled_tasks: the research profile writes", WRITER_ENV, name, TASK)
        return []
    # D-172: the writer's attempts end after HLM_RESEARCH_WRITER_TIMEOUT_S (then the task profile)
    timeout = float(getattr(settings, "research_writer_timeout_s", WRITER_TIMEOUT_S))
    return [replace(writer, attempt_timeout_s=timeout), task_chain[0]]


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
        mode = getattr(settings, "research_answer_mode", "claims")
        self.answer_mode: str = mode if mode in ANSWER_MODES else "claims"
        # D-159: select-then-write, only in the cite mode (its prompt has the JOB select)
        self.select: bool = self.answer_mode == "cite" and bool(getattr(settings, "research_select", False))
        self.max_calls: int = MAX_CALLS if self.select else MAX_CALLS_NO_SELECT
        # D-165: the prose mode's attribution strategy (the others have their own checks)
        attribution = getattr(settings, "research_attribution", "sources")
        self.attribution: str = attribution if attribution in ATTRIBUTIONS else "sources"
        # D-170: the prose mode's expand (completeness) pass
        self.expand: bool = self.answer_mode == "prose" and bool(getattr(settings, "research_expand", False))
        if self.answer_mode == "prose":
            self.max_calls = MAX_CALLS_NO_SELECT + int(self.expand) + int(self.attribution == "llm")
        # D-171: the prose writer's own chain (prose mode only: the other modes write with the task)
        self.writer_chain: list[LlmProfile] = (
            writer_chain(settings, self.chain) if self.answer_mode == "prose" else []
        )
        # D-156/D-162: the cite and prose modes' prompts are opt-in versions; the claims mode keeps the
        # default (v1, or a pin) unless that prompt has no JOB "answer" (another mode's prompt pinned
        # by mistake)
        if self.answer_mode == "cite":
            self.spec: TaskSpec = load_task(TASK, CITE_PROMPT_VERSION)
        elif self.answer_mode == "prose":
            self.spec = load_task(TASK, PROSE_PROMPT_VERSION)
        else:
            self.spec = load_task(TASK)
            if 'JOB "answer"' not in self.spec.system:
                log.warning("research prompt %s has no JOB answer: using v1", self.spec.prompt_version)
                self.spec = load_task(TASK, 1)
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

    def chain_for_job(self, job: str) -> list[LlmProfile] | None:
        """D-171: the explicit chain of a writer job when a writer profile is set, else None (the
        task's chain)."""
        return self.writer_chain if job in WRITER_JOBS and self.writer_chain else None

    @property
    def writer_profile(self) -> str | None:
        """The profile that writes the answer (D-171 ``meta.writer_profile``): the writer profile when
        routed, else the task profile."""
        head = self.writer_chain or self.chain
        return head[0].name if head else None

    def worst_case(self, job: str, user: str) -> tuple[Decimal, int]:
        """``(USD, tokens)`` the next call may cost at most on the expected path: its padded input
        and its max_tokens, priced by the head of the JOB's chain (the task primary; D-171: the
        writer profile for a writer job). The call-level check only; every ATTEMPT (schema retry,
        fallback) is checked again with its own profile's worst case (``attempt_guard``, review 79
        T4)."""
        spec = self.job_spec(job)
        tokens_in = (
            self.provider.estimate_input_tokens(
                [{"role": "system", "content": spec.system}, {"role": "user", "content": user}]
            )
            if self.provider is not None
            else 0
        )
        chain = self.chain_for_job(job) or self.chain
        head = chain[0] if chain else None
        usd = head.worst_usd(tokens_in, spec.max_tokens) if head is not None and head.priced else Decimal(0)
        return usd, -(-tokens_in * 11 // 10) + spec.max_tokens

    def attempts(self, lineage: str) -> list[tuple[str, str]]:
        """D-172: ``(profile, outcome)`` of the attempts of ``lineage`` so far, in order."""
        ledger = self.provider.ledger if self.provider is not None else None
        return list(ledger.outcomes.get(lineage, [])) if isinstance(ledger, _TeeLedger) else []

    def attempt_rows(self, lineage: str) -> list[dict[str, Any]]:
        """D-189: copies of the ledger facts of the attempts of ``lineage`` so far, in order."""
        ledger = self.provider.ledger if self.provider is not None else None
        rows = ledger.rows.get(lineage, []) if isinstance(ledger, _TeeLedger) else []
        return [dict(r) for r in rows]

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
        observe: Callable[[dict[str, Any]], None] | None = None,
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

        def variant(profile: LlmProfile) -> Any:
            """D-178: the JOB's plain-text protocol on a profile without JSON mode (never by name)."""
            return None if profile.json_mode else text_variant(job, user)

        return await self.provider.complete(
            self.job_spec(job),
            user,
            validate=job_validator(job),
            precheck=precheck,
            chain=self.chain_for_job(job),  # D-171: a writer job's own chain, else the task's
            variant=variant if job in TEXT_JOBS else None,
            observe=observe,  # D-189: the trace recorder (read-only)
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
    "ANSWER_MODES",
    "ATTRIBUTE_MAX",
    "ATTRIBUTIONS",
    "EXPAND_MAX",
    "CITE_PROMPT_VERSION",
    "DROP_REASONS",
    "INSUFFICIENT",
    "JOBS",
    "MAX_ATTEMPTS",
    "MAX_CALLS",
    "MAX_CALLS_NO_SELECT",
    "MAX_CALLS_ATTRIBUTE",
    "PROSE_PROMPT_VERSION",
    "SELECT_MAX",
    "TASK",
    "TEXT_JOBS",
    "WRITER_JOBS",
    "WRITER_TIMEOUT_S",
    "Claim",
    "Excerpt",
    "LineSim",
    "ResearchUnavailable",
    "Researcher",
    "Validated",
    "answer_user",
    "app_researcher",
    "attribute",
    "attribute_user",
    "expand_user",
    "assemble",
    "assemble_cited",
    "assemble_prose",
    "cite_check",
    "check_user",
    "clip",
    "context_label",
    "doc_name",
    "quote_overlaps",
    "status_label",
    "close_app_researcher",
    "best_line",
    "find_verbatim",
    "hard_literals",
    "is_block",
    "locate_quote",
    "polarity_ok",
    "redact_values",
    "job_validator",
    "literal_supported",
    "literals_ok",
    "merge_check",
    "parse_attribute",
    "parse_expand",
    "parse_expand_text",
    "parse_prose_text",
    "parse_plan",
    "parse_select",
    "plan_user",
    "prose_check",
    "prose_kept",
    "prose_user",
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
    "select_user",
    "split_inline_cites",
    "split_sentences",
    "text_variant",
    "validate_answer",
    "validate_cited",
    "validate_prose",
    "writer_chain",
    "write_user",
]
