"""Text normalisation and term extraction (PHASE0-SPEC §4 steps 2-3).

``normalize`` must be *identical* at index time and query time; bump
``hlmemo.core.NORMALIZER_VERSION`` whenever its behaviour changes.
"""

from __future__ import annotations

import re
import unicodedata

TERM_MAX = 24
TERM_MIN_LEN = 2

# §4.3: ``[\w][\w./-]*`` on the normalised query. ``\w`` is Unicode-aware in Python.
_TERM_RE = re.compile(r"[\w][\w./-]*")
_IDENT_RE = re.compile(r"[_/.0-9]")
_TRAILING_PUNCT = "./-"
_MARK_CATEGORIES = frozenset({"Mn", "Mc", "Me"})


def _drop_combining_marks(s: str) -> str:
    return "".join(ch for ch in s if unicodedata.category(ch) not in _MARK_CATEGORIES)


def normalize(s: str) -> str:
    """``drop_combining_marks(NFD(NFKC(s).casefold())).replace('ı', 'i')``.

    Effects (deterministic, idempotent): ß→ss, İ→i, ı→i, ü→u, ş→s, ç→c, Ä→a,
    compatibility forms folded (ﬁ→fi, full-width ASCII → ASCII).
    """
    if not isinstance(s, str):
        raise TypeError(f"normalize() expects str, got {type(s).__name__}")
    folded = unicodedata.normalize("NFKC", s).casefold()
    decomposed = unicodedata.normalize("NFD", folded)
    return _drop_combining_marks(decomposed).replace("ı", "i")


def extract_terms(s: str, *, max_terms: int = TERM_MAX) -> list[str]:
    """Terms of the *normalised* text: regex ``[\\w][\\w./-]*``, drop ``len < 2``,
    dedupe preserving first occurrence, keep the first ``max_terms``.

    Trailing ``.``/``/``/``-`` are stripped from each match (sentence-final
    punctuation glued to a word would otherwise become part of the term).
    """
    out: list[str] = []
    seen: set[str] = set()
    for m in _TERM_RE.finditer(normalize(s)):
        term = m.group(0).rstrip(_TRAILING_PUNCT)
        if len(term) < TERM_MIN_LEN or term in seen:
            continue
        seen.add(term)
        out.append(term)
        if len(out) >= max_terms:
            break
    return out


def is_identifier(term: str) -> bool:
    """§4.3: identifier terms contain ``_``, ``/``, ``.`` or a digit."""
    return bool(_IDENT_RE.search(term))


def identifier_terms(terms: list[str]) -> list[str]:
    return [t for t in terms if is_identifier(t)]


__all__ = ["normalize", "extract_terms", "is_identifier", "identifier_terms", "TERM_MAX"]
