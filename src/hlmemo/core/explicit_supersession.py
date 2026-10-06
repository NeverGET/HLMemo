"""D-184 (A): EXPLICIT supersession declarations -> ``supersedes`` link proposals (no LLM).

Imported documents often say in plain text what they replace: ``PHASE0-SPEC.md`` opens with
"Status: MERGED 2026-09-22 from `docs/consults/03-claude-phase0-spec.md` ...", and a decision row
says "D-050 | ... | Supersedes the implementer role in D-036/D-048." Nothing recorded those
declarations, so the D-057 read rules (``core/supersession.py``, ``db/librarian_queries``) had no
links to act on. This module finds such declarations and resolves them to current items. It is
pure (no database); ``ops/explicit_links.py`` loads the items and writes the links through the
librarian's link path.

**Declarations** (EN/DE/TR; the marker family is the proposal's ``marker``):

* ``merged_from``: "merged [<date>] from <path>" (DE "zusammengeführt aus", TR "<path>'den
  birleştirildi"); "merged into <path>" is the reverse (``merged_into``).
* ``supersedes``: "supersedes <D-id | path>" (DE "ersetzt <...>", TR "<...>'i geçersiz kılar").
* ``replaces``: "replaces / replaced <...>" (TR "<...>'in yerini alır").
* ``superseded_by`` / ``replaced_by``: the REVERSE direction, "superseded by <...>", "replaced
  by|with <...>" (DE "ersetzt durch", "abgelöst durch", "überholt durch", TR "<...> tarafından
  geçersiz kılındı").
* ``instead_of``: "instead of / rather than / in place of <...>" (DE "statt", "anstelle von",
  TR "<...> yerine"), ONLY inside a decision row or with an explicit subject: a decision chose
  something over the target; a consult or a prompt saying "instead of" only proposes.
* ``update``: "Update [<date>] (D-xxx): supersedes <...>" names the superseding decision of a
  following marker (the explicit subject). An update WITHOUT an explicit target declares an
  in-item revision and yields no link.

**Who supersedes whom.** An explicit subject ref directly before the marker ("D-050 supersedes
D-036", "`docs/a.md` is superseded by `docs/b.md`") is used as written. Otherwise the subject is
the DECLARING UNIT: the decision row the marker sits in (a decision log is an item with >= 2 row
starts ``D-NNN |``), else the whole declaring document. A document is the subject only when the
marker is in self-reference position (clause start, "Status:", "This spec is ..."), so "the
librarian proposes `supersedes` links (D-057)" or "PHASE0-SPEC.md (authoritative, merged from
...)" inside a prompt never makes that prompt a superseder. A document never retires a decision
row by an implicit subject (only a decision, or an explicit subject, can).

**Targets** resolve only by EXACT identity: a path equal to a current item's ``source.path``
without its ``#anchor`` (repo-root relative, or relative to the declaring document's directory),
or a D-id whose row start exists in exactly ONE current item. A document split into several items
(``path#0``, ``path#1``) is linked item by item. Unresolvable targets, self-links (two rows of
one item included), a document "superseding" its own chunk items, D-id ranges,
negated/hypothetical/interrogative clauses, markers in code or inside a quotation (a text quoting
another document's declaration) and cycles are dropped. Precision over recall.

**Scope and quote** (link ``props``): ``part`` when the superseded side is a decision row (the
quote is that row's text, the D-057 rule-3 span), a section of a document ("§4", "#anchor"), or a
whole document retired by a decision row (a decision replaces part of a document; never hidden);
otherwise ``whole``, and the quote is the declaration itself. Every quote is a verbatim substring
of an item body, at most ``QUOTE_MAX`` characters. ``declaration`` is the declaring text.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

PARSER_VERSION = 1
QUOTE_MAX = 300
BY = "explicit"
LEAD_WORDS = 5  # at most this many filler words between a marker and its first target
WINDOW = 240  # a target list is looked for in at most this many characters past the marker

_DID = r"D-\d{3}[a-z]?"
_DID_RE = re.compile(rf"(?<![\w-])({_DID})(?![\w])")
_EXT = r"(?:md|markdown|mdx|txt|rst|adoc|org|ya?ml|toml|json|py|sql|sh)"
_PATH_RE = re.compile(rf"(?<![\w/.~@-])((?:\.{{1,2}}/)?(?:[\w.@-]+/)*[\w.@-]+\.{_EXT})(#[\w.-]*)?(?![\w/-])")
_ROW_RE = re.compile(rf"(?m)^({_DID})[ \t]*\|")
_RANGE_RE = re.compile(rf"^\s*(?:\.\.\.?|–|—|-|to|bis)\s*`?{_DID}", re.I)
_SECTION_RE = re.compile(r"^`?\s*(?:§\s*\d|\bsections?\s+\d|\babschnitt\b|\bbölüm\b|\bkısım\b)", re.I)
_FENCE_RE = re.compile(r"(?ms)^[ \t]*(```|~~~).*?^[ \t]*\1")
_TR_SUFFIX = r"(?:['’][A-Za-zÇĞİÖŞÜçğıöşü]{1,7})?"

# after a ref: separators that continue a target list (a parenthetical note may follow a ref)
_JOIN_RE = re.compile(
    r"^[`'\"*\s]*(?:\([^()\n]{0,100}\)[\s`]*)?(?:,|/|&|\band\b|\bund\b|\bsowie\b)[\s`'\"*]*", re.I
)
# a case suffix may sit on every ref of the list ("D-150'yi ve D-151'i"), not only on the last one
_TR_JOIN_RE = re.compile(rf"^[`'\"*\s]*{_TR_SUFFIX}[`'\"*\s]*(?:/|\bve\b|\bile\b)[\s`'\"*]*$", re.I)
# between an explicit subject ref and its marker
_SUBJ_GAP_RE = re.compile(
    r"^[`'\"*_)\]\s]*(?:['’]s\s+)?(?:(?:is|was|are|were|has|have|had|been|being|now|hereby|also|thereby|fully|"
    r"partly|partially|officially|formally|then|ist|wurde|wird|sind|wurden|hat|haben|nun|jetzt)\s+)*$",
    re.I,
)
# the text before a marker when the declaring DOCUMENT is its subject
_SELF_RE = re.compile(
    r"^[\s>*_#+•\-]*(?:(?:status|note|hinweis|durum|not)[\s*_]*:[\s*_]*)?"
    r"(?:(?:this|the\s+present|diese[rs]?|dieses|bu)\s+(?:document|doc|file|spec|specification|draft|note|"
    r"page|report|version|revision|plan|belge|doküman|dosya|taslak|not|sayfa|rapor|plan|dokument|datei|"
    r"entwurf|seite|fassung|bericht)\s+)?(?:(?:is|was|has\s+been|have\s+been|are|were|now|hereby|ist|wurde|"
    r"wird|nun)\s+)*$",
    re.I,
)
_NEGATION = frozenset(
    {"not", "no", "never", "cannot", "can't", "don't", "doesn't", "didn't", "isn't", "wasn't", "won't",
     "nicht", "kein", "keine", "keinen", "niemals", "nie", "değil", "hiçbir", "asla"}
)  # fmt: skip
_HYPOTHETICAL = frozenset(
    {"would", "could", "should", "may", "might", "can", "will", "shall", "if", "whether", "unless",
     "propose", "proposes", "proposed", "proposal", "suggest", "suggests", "suggested", "maybe", "perhaps",
     "würde", "könnte", "sollte", "soll", "falls", "wenn", "ob", "vielleicht", "eğer", "belki", "önerir"}
)  # fmt: skip
# the marker used as a noun/adjective ("a supersedes link"), never a declaration
_NOUN_AFTER = frozenset(
    {"link", "links", "edge", "edges", "relation", "relations", "rel", "rels", "pointer", "pointers",
     "column", "columns", "field", "fields", "chain", "marker", "markers", "rule", "rules", "check",
     "checks", "flag", "flags", "candidate", "candidates", "proposal", "proposals", "status", "test",
     "tests", "tag", "tags", "event", "events", "detection", "logic", "semantics", "direction"}
)  # fmt: skip
_UPDATE_RE = re.compile(
    rf"\b(?:update[ds]?|updated|güncelleme|aktualisierung|nachtrag)\b[^\n()]{{0,40}}\(\s*({_DID})\s*\)", re.I
)
_WORD_RE = re.compile(r"[\w'’-]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class _Marker:
    name: str
    regex: re.Pattern[str]
    reverse: bool  # True: the targets supersede the subject
    before: bool = False  # TR: the targets precede the marker
    row_only: bool = False  # an implicit subject must be a decision row (instead_of)


def _m(
    name: str, pattern: str, *, reverse: bool = False, before: bool = False, row_only: bool = False
) -> _Marker:
    return _Marker(name, re.compile(pattern, re.I), reverse, before, row_only)


MARKERS: tuple[_Marker, ...] = (
    # English
    _m("merged_from", r"\bmerged\b(?:[ \t]+[^\s,;|()]+){0,3}?[ \t]+(?:from|out\s+of)\b"),
    _m("merged_into", r"\bmerged\s+into\b", reverse=True),
    _m("superseded_by", r"\bsuperseded\s+(?:by|through)\b", reverse=True),
    _m("supersedes", r"\bsupersed(?:es|e|ing)\b"),
    _m("replaced_by", r"\breplaced\s+(?:by|with)\b", reverse=True),
    _m("replaces", r"\breplac(?:es|ed|e|ing)\b(?!\s+(?:by|with)\b)"),
    _m("instead_of", r"\b(?:instead\s+of|rather\s+than|in\s+place\s+of)\b", row_only=True),
    # German
    _m("merged_from", r"\bzusammengeführt\s+(?:aus|von)\b"),
    _m("superseded_by", r"\b(?:ersetzt|abgelöst|überholt)\s+(?:durch|von)\b", reverse=True),
    _m("supersedes", r"\bersetzt\b(?!\s+(?:durch|von)\b)"),
    _m("instead_of", r"\b(?:anstelle\s+von|anstatt|statt)\b", row_only=True),
    # Turkish (targets precede the marker)
    _m("merged_from", r"\bbirleştiril(?:di|miştir|erek)\b", before=True),
    _m(
        "superseded_by",
        r"\btarafından\s+(?:geçersiz\s+kılın(?:dı|mıştır)|değiştiril(?:di|miştir))",
        reverse=True,
        before=True,
    ),
    _m("supersedes", r"\bgeçersiz\s+kıl(?:ar|ıyor|dı|mıştır)\b", before=True),
    _m("replaces", r"\byerini\s+al(?:ır|dı|mıştır|ıyor)\b", before=True),
    _m("instead_of", r"(?<![\w])yerine\b", before=True, row_only=True),
)


# --------------------------------------------------------------------------- data
@dataclass(frozen=True, slots=True)
class Doc:
    """One current item: ``path`` is ``source.path`` (``#anchor`` included) or ``None``."""

    version_id: int
    logical_id: int
    path: str | None
    body: str
    valid_from: object = None

    @property
    def doc_path(self) -> str | None:
        return self.path.split("#", 1)[0] if self.path else None


@dataclass(frozen=True, slots=True)
class Ref:
    kind: str  # "path" | "did"
    value: str  # the path as written (no anchor) or the D-id
    start: int
    end: int
    section: bool = False


@dataclass(frozen=True, slots=True)
class Declaration:
    marker: str
    reverse: bool
    targets: tuple[Ref, ...]
    subject: Ref | None  # an explicit subject; None = the declaring unit
    unit_row: str | None  # the decision row the marker sits in (None: the document)
    self_position: bool  # the document itself is the grammatical subject
    row_only: bool
    start: int
    end: int
    text: str  # the declaration, verbatim (<= QUOTE_MAX)


@dataclass(frozen=True, slots=True)
class Proposal:
    source_version_id: int  # the superseding item (link src)
    target_version_id: int  # the superseded item (link dst)
    source_logical_id: int
    target_logical_id: int
    scope: str  # "whole" | "part"
    quote: str
    marker: str
    declared_in: int  # version id of the item holding the declaration
    source_ref: str  # document path or D-id of the superseding side
    target_ref: str
    declaration: str
    source_path: str | None = None  # the items' own source.path (anchor included)
    target_path: str | None = None

    def props(self) -> dict[str, str]:
        out = {"by": BY, "scope": self.scope, "quote": self.quote, "marker": self.marker}
        if self.declaration != self.quote:
            out["declaration"] = self.declaration
        return out

    def as_dict(self) -> dict[str, object]:
        return {
            "source_version_id": self.source_version_id,
            "target_version_id": self.target_version_id,
            "source_path": self.source_path,
            "target_path": self.target_path,
            "source_ref": self.source_ref,
            "target_ref": self.target_ref,
            "scope": self.scope,
            "marker": self.marker,
            "quote": self.quote,
            "declaration": self.declaration,
            "declared_in": self.declared_in,
        }


# --------------------------------------------------------------------------- text helpers
def _clip(text: str, n: int = QUOTE_MAX) -> str:
    """A verbatim prefix of ``text`` of at most ``n`` characters, cut at whitespace when possible."""
    text = text.rstrip()
    if len(text) <= n:
        return text
    cut = text[:n]
    ws = max(cut.rfind(" "), cut.rfind("\n"), cut.rfind("\t"))
    return (cut[:ws] if ws >= n // 2 else cut).rstrip()


def _fences(body: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _FENCE_RE.finditer(body)]


def _inside(spans: list[tuple[int, int]], pos: int) -> bool:
    return any(s <= pos < e for s, e in spans)


_BOUNDARY_RE = re.compile(r"\n|\||;|[.!?](?=\s|$)")


def _clause_start(body: str, pos: int) -> int:
    start = 0
    for m in _BOUNDARY_RE.finditer(body, max(0, pos - 600), pos):
        start = m.end()
    return max(start, max(0, pos - 600))


def _clause_end(body: str, pos: int) -> int:
    m = _BOUNDARY_RE.search(body, pos, min(len(body), pos + WINDOW))
    return m.start() if m else min(len(body), pos + WINDOW)


def _inline_code(body: str, start: int, end: int) -> bool:
    """The marker sits inside an inline code span (odd number of backticks before it on its line)."""
    line_start = body.rfind("\n", 0, start) + 1
    return body.count("`", line_start, start) % 2 == 1 or body[end : end + 1] == "`"


def _quoted(body: str, start: int) -> bool:
    """The marker sits inside a quotation on its line ("X declares: "MERGED … from …""): the text
    quotes someone else's declaration, it does not make one."""
    line = body[body.rfind("\n", 0, start) + 1 : start]
    if "„" in line:  # German „…“
        curly = line.count("„") > line.count("“") + line.count("”")
    else:  # English “…”
        curly = line.count("“") > line.count("”")
    return curly or line.count('"') % 2 == 1


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text)]


def _refs(text: str, offset: int) -> list[Ref]:
    """Every path and D-id ref in ``text`` (offsets relative to the body), in order."""
    out: list[Ref] = []
    for m in _PATH_RE.finditer(text):
        section = bool(m.group(2)) or bool(_SECTION_RE.match(text[m.end() :]))
        out.append(Ref("path", m.group(1), offset + m.start(), offset + m.end(), section))
    taken = [(r.start, r.end) for r in out]
    for m in _DID_RE.finditer(text):
        s, e = offset + m.start(), offset + m.end()
        if any(a <= s < b for a, b in taken):
            continue
        out.append(Ref("did", m.group(1), s, e))
    return sorted(out, key=lambda r: r.start)


# --------------------------------------------------------------------------- declarations
def decision_rows(body: str, path: str | None = None) -> list[tuple[str, int, int]]:
    """``(D-id, start, end)`` of every decision row of a decision log, else [].

    A decision log is an item with >= 2 row starts, or an imported one-row item: its body starts with
    the row and its ``source.path`` anchor is that row's D-id (``DECISIONS.md#D-006``), the key the
    markdown importer gives each decision row (2026-10-06, D-249). Any other single row start stays
    prose, so a document that quotes a row is never indexed as that decision (review 109)."""
    starts = [(m.group(1), m.start()) for m in _ROW_RE.finditer(body)]
    if len(starts) == 1:
        did, start = starts[0]
        anchor = path.rsplit("#", 1)[1] if path and "#" in path else None
        if anchor == did and not body[:start].strip():
            return [(did, start, len(body))]
        return []
    if len(starts) < 2:
        return []
    return [
        (did, s, starts[i + 1][1] if i + 1 < len(starts) else len(body)) for i, (did, s) in enumerate(starts)
    ]


def _row_at(rows: list[tuple[str, int, int]], pos: int) -> str | None:
    for did, s, e in rows:
        if s <= pos < e:
            return did
    return None


def _targets_after(body: str, end: int, limit: int, *, max_lead: int = LEAD_WORDS) -> tuple[Ref, ...]:
    """The target list right after a marker: the first ref within ``max_lead`` filler words (and
    not a parenthesized citation after other words: "instead of cursor paging (D-026)"), then the
    refs joined by ``,`` ``/`` ``&`` "and" (a parenthetical note may follow each)."""
    refs = _refs(body[end:limit], end)
    if not refs:
        return ()
    lead = body[end : refs[0].start]
    lead_words = _words(lead)
    if len(lead_words) > max_lead or (lead_words and lead_words[0] in _NOUN_AFTER):
        return ()
    if set(lead_words) & ({"nothing", "none", "nichts", "hiçbir"} | _NEGATION):
        return ()
    if lead_words and "(" in lead:
        return ()  # a citation of the ref, not the object of the marker
    out = [refs[0]]
    for nxt in refs[1:]:
        if _RANGE_RE.match(body[out[-1].end : nxt.end]):
            return ()  # "D-001..D-024": a range, never resolved row by row
        gap = body[out[-1].end : nxt.start]
        join = _JOIN_RE.match(gap)
        if join is None or join.end() != len(gap):
            break
        out.append(nxt)
    if _RANGE_RE.match(body[out[-1].end : limit]):
        return ()
    return tuple(out)


def _targets_before(body: str, start: int, begin: int) -> tuple[tuple[Ref, ...], Ref | None]:
    """TR (targets precede the marker): the refs joined by "ve"/"ile"/"/" ending right before it,
    and an explicit subject ref directly before that list (separated by a comma or whitespace)."""
    refs = _refs(body[begin:start], begin)
    if not refs or not re.fullmatch(rf"[`'\"*]*{_TR_SUFFIX}[`'\"*\s]*", body[refs[-1].end : start]):
        return (), None
    out = [refs[-1]]
    i = len(refs) - 2
    while i >= 0 and _TR_JOIN_RE.match(body[refs[i].end : out[0].start]):
        out.insert(0, refs[i])
        i -= 1
    subject = None
    if i >= 0 and re.fullmatch(r"[`'\"*\s]*,?[`'\"*\s]*", body[refs[i].end : out[0].start]):
        subject = refs[i]
    return tuple(out), subject


def _explicit_subject(body: str, clause: int, start: int) -> Ref | None:
    refs = _refs(body[clause:start], clause)
    if refs and _SUBJ_GAP_RE.match(body[refs[-1].end : start]):
        return refs[-1]
    return None


def _declaration_text(body: str, clause: int, start: int, end: int) -> str:
    """The declaring text, verbatim: from the clause start to the last target, <= QUOTE_MAX
    (trimmed from the left, keeping the marker, then from the right)."""
    lo = clause
    while lo < start and body[lo].isspace():
        lo += 1
    if end - lo > QUOTE_MAX:
        lo = max(lo, start - 40)
        nxt = body.find(" ", lo, start)
        if lo != start and nxt != -1:
            lo = nxt + 1
    return _clip(body[lo:end])


_PASSIVE = frozenset(
    {"is", "was", "are", "were", "be", "been", "being", "ist", "wurde", "wird", "sind", "wurden"}
)


def find_declarations(body: str, path: str | None = None) -> list[Declaration]:
    """Every explicit supersession declaration of one item ``body``, in text order. The item's
    ``source.path`` only tells an imported one-row decision item (``decision_rows``); the rest of
    resolution happens in ``resolve``."""
    fences = _fences(body)
    rows = decision_rows(body, path)
    out: list[Declaration] = []
    seen: set[int] = set()
    for mk in MARKERS:
        for m in mk.regex.finditer(body):
            s, e = m.start(), m.end()
            if s in seen or _inside(fences, s) or _inline_code(body, s, e) or _quoted(body, s):
                continue
            clause = _clause_start(body, s)
            prefix = body[clause:s]
            limit = _clause_end(body, e)
            if "?" in body[clause : limit + 1]:  # the clause end may be the "?" itself
                continue  # a question never declares anything
            prev = _words(prefix)[-3:]
            if set(prev) & (_NEGATION | _HYPOTHETICAL):
                continue
            if mk.before:
                targets, subject = _targets_before(body, s, clause)
                decl_lo = targets[0].start if targets else s
                self_scan = body[clause : (subject or targets[0]).start] if targets else prefix
            else:
                if prev and prev[-1] in _PASSIVE and mk.name in ("supersedes", "replaces"):
                    continue  # passive without an agent ("X is replaced"): no declaration
                targets = _targets_after(body, e, limit, max_lead=1 if mk.row_only else LEAD_WORDS)
                subject = _explicit_subject(body, clause, s)
                decl_lo = clause
                self_scan = prefix
            if not targets:
                continue
            update = None if subject is not None else _UPDATE_RE.search(prefix)
            marker = mk.name
            if update is not None:
                subject = Ref("did", update.group(1), clause + update.start(1), clause + update.end(1))
                marker = "update"
            paren = max(self_scan.rfind("("), self_scan.rfind("["))
            self_scan = (self_scan[paren + 1 :] if paren >= 0 else self_scan).rstrip(" \t`'\"*") + " "
            last = max(t.end for t in targets)
            if mk.before:
                last = e
            seen.add(s)
            out.append(
                Declaration(
                    marker=marker,
                    reverse=mk.reverse,
                    targets=targets,
                    subject=subject,
                    unit_row=_row_at(rows, s),
                    self_position=bool(_SELF_RE.match(self_scan)),
                    row_only=mk.row_only,
                    start=s,
                    end=e,
                    text=_declaration_text(body, min(decl_lo, clause), s, last),
                )
            )
    return sorted(out, key=lambda d: d.start)


# --------------------------------------------------------------------------- resolution
@dataclass(frozen=True, slots=True)
class _End:
    doc: Doc
    row: str | None  # D-id when this side is one decision row of the item
    row_text: str = ""
    section: bool = False

    @property
    def ref(self) -> str:
        return self.row or self.doc.doc_path or f"v{self.doc.version_id}"


@dataclass(slots=True)
class Index:
    """Current items of one project: documents by path, decision rows by D-id."""

    docs: list[Doc]
    by_path: dict[str, list[Doc]] = field(default_factory=dict)
    rows: dict[str, list[tuple[Doc, str]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for d in self.docs:
            if d.doc_path:
                self.by_path.setdefault(d.doc_path, []).append(d)
            for did, s, e in decision_rows(d.body, d.path):
                self.rows.setdefault(did, []).append((d, d.body[s:e]))
        for docs in self.by_path.values():
            docs.sort(key=lambda d: (d.path or "", d.version_id))

    def path(self, ref: str, declaring: Doc) -> list[Doc]:
        ref = ref.lstrip("`")
        cands = [posixpath.normpath(ref)]
        if declaring.doc_path:
            cands.append(posixpath.normpath(posixpath.join(posixpath.dirname(declaring.doc_path), ref)))
        for c in cands:
            if c in self.by_path:
                return self.by_path[c]
        return []

    def row(self, did: str) -> tuple[Doc, str] | None:
        found = self.rows.get(did) or []
        return found[0] if len(found) == 1 else None  # missing or ambiguous: unresolvable

    def ends(self, ref: Ref, declaring: Doc) -> list[_End]:
        if ref.kind == "did":
            hit = self.row(ref.value)
            return [] if hit is None else [_End(hit[0], ref.value, hit[1])]
        return [_End(d, None, section=ref.section) for d in self.path(ref.value, declaring)]

    def unit(self, decl: Declaration, doc: Doc) -> list[_End]:
        if decl.unit_row is not None:
            hit = self.row(decl.unit_row)
            if hit is None or hit[0].logical_id != doc.logical_id:
                return []
            return [_End(doc, decl.unit_row, hit[1])]
        if doc.doc_path and doc.doc_path in self.by_path:
            return [_End(d, None) for d in self.by_path[doc.doc_path]]
        return [_End(doc, None)]


def _scope(new: _End, old: _End, *, reverse: bool) -> str:
    if old.row is not None or old.section:
        return "part"
    if new.row is not None and not reverse:
        return "part"  # a decision row retiring a document: part of it, never hidden
    return "whole"


def resolve(decl: Declaration, doc: Doc, index: Index) -> list[Proposal]:
    """The link proposals of one declaration found in ``doc`` (unresolvable -> [])."""
    if decl.subject is not None:
        subject = index.ends(decl.subject, doc)
    else:
        if decl.unit_row is None and not decl.self_position:
            return []  # a document is the subject only in self-reference position
        if decl.row_only and decl.unit_row is None:
            return []
        subject = index.unit(decl, doc)
    targets = [end for ref in decl.targets for end in index.ends(ref, doc)]
    if not subject or not targets:
        return []
    if decl.subject is None and decl.unit_row is None and not decl.reverse:
        targets = [t for t in targets if t.row is None]  # a document never retires a decision row
    newer, older = (targets, subject) if decl.reverse else (subject, targets)
    out: list[Proposal] = []
    for new in newer:
        for old in older:
            if new.doc.logical_id == old.doc.logical_id:
                continue  # self-link (e.g. two rows of one decision log item)
            same_doc = new.doc.doc_path is not None and new.doc.doc_path == old.doc.doc_path
            if same_doc and new.row is None and old.row is None:
                continue  # a document never supersedes (a chunk item of) itself
            scope = _scope(new, old, reverse=decl.reverse)
            quote = _clip(old.row_text) if old.row is not None else decl.text
            out.append(
                Proposal(
                    source_version_id=new.doc.version_id,
                    target_version_id=old.doc.version_id,
                    source_logical_id=new.doc.logical_id,
                    target_logical_id=old.doc.logical_id,
                    scope=scope,
                    quote=quote,
                    marker=decl.marker,
                    declared_in=doc.version_id,
                    source_ref=new.ref,
                    target_ref=old.ref,
                    declaration=decl.text,
                    source_path=new.doc.path,
                    target_path=old.doc.path,
                )
            )
    return out


def _cyclic(edges: Iterable[tuple[int, int]]) -> set[tuple[int, int]]:
    """The edges inside a strongly connected component with a cycle (Tarjan, iterative)."""
    succ: dict[int, list[int]] = {}
    for u, v in edges:
        succ.setdefault(u, []).append(v)
        succ.setdefault(v, [])
    index: dict[int, int] = {}
    low: dict[int, int] = {}
    on: set[int] = set()
    stack: list[int] = []
    comp: dict[int, int] = {}
    counter = 0
    for root in sorted(succ):
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            node, i = work.pop()
            if i == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on.add(node)
            children = succ[node]
            if i < len(children):
                work.append((node, i + 1))
                child = children[i]
                if child not in index:
                    work.append((child, 0))
                elif child in on:
                    low[node] = min(low[node], index[child])
                continue
            if low[node] == index[node]:
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp[w] = node
                    if w == node:
                        break
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    sizes: dict[int, int] = {}
    for c in comp.values():
        sizes[c] = sizes.get(c, 0) + 1
    return {(u, v) for u, v in set(edges) if comp[u] == comp[v] and (sizes[comp[u]] > 1 or u == v)}


def propose(docs: list[Doc], existing: Iterable[tuple[int, int]] = ()) -> list[Proposal]:
    """Every link proposal over ``docs`` (current items of one project). ``existing``: live
    ``supersedes`` links ``(src_logical_id, dst_logical_id)``, which take part in the cycle check.

    One proposal per ``(src, dst)`` logical pair (the ``links`` exclusion allows one live edge):
    the first declaration in document order wins, and conflicting scopes fall back to ``part``.
    Proposals on a cycle (with each other or with existing links) are dropped."""
    index = Index(list(docs))
    by_pair: dict[tuple[int, int], Proposal] = {}
    for doc in sorted(index.docs, key=lambda d: (d.path or "", d.version_id)):
        for decl in find_declarations(doc.body, doc.path):
            for p in resolve(decl, doc, index):
                key = (p.source_logical_id, p.target_logical_id)
                if key not in by_pair or (by_pair[key].scope == "whole" and p.scope == "part"):
                    by_pair[key] = p
    cyclic = _cyclic([*by_pair, *existing])
    return [p for k, p in by_pair.items() if k not in cyclic]


__all__ = [
    "BY",
    "MARKERS",
    "PARSER_VERSION",
    "QUOTE_MAX",
    "Declaration",
    "Doc",
    "Index",
    "Proposal",
    "Ref",
    "decision_rows",
    "find_declarations",
    "propose",
    "resolve",
]
