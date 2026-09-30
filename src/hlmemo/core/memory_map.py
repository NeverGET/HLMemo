"""The Memory Map of a project: "what is where" for the research librarian (D-130, D-136).

A deterministic, rebuildable projection of a project's CURRENT items, rendered per ``memory.ask``
request from the CALLER's view and never stored as a whole:

* the path tree: ``[group/]`` lines (a directory, ``git log``, or a kind cluster for items without a
  source path), one line per source file with its item handle ``vN(k)`` (k = chunks) or its item
  count, and
* section entries: markdown headings outside fenced code, decision-log rows (``ABC-123`` + their first
  descriptive cell) and git commit subjects, each with the chunk handle ``vN.M`` it lives in, and for
  a file split into several items one entry per item (``label vN(k)``);
* the L2 layer (report §D, "summarising"): a 1–3 sentence LLM summary per source file or topic
  cluster (``librarian/tasks/map_summary.py``, cached in ``memory_map_summaries``), shown after its
  file line as ``~ …`` — only when every version it was written from is in the caller's view.

Budget (about 6k o200k tokens, ``HLM_RESEARCH_MAP_TOKENS``): header, group and file lines first
(spread-truncated if even they overflow); then (D-191) the NEWEST ``NEWEST_K`` entries of every
source with more entries than that (a multi-item file: its newest items by valid_from, then
recorded_at; a single-item document: its last level-1/2 entries), largest sources first; then the
section entries round-robin by size (priority = chunks / (1 + entries taken), each file's remaining
candidates in a bit-reversal "spread" order so any prefix covers the whole document); summaries
only with the budget left after the entries (largest files first). Truncation is by spreading,
never by cutting the head. A large item (more than 2 chunks) is drillable only by a chunk handle.

The VIEW (``load_view``) is what the caller may see AND what may be sent to a provider: current,
active, non-card items of the project whose ``device_scope`` is ``all`` or the caller's class (never
``device:*``) and whose every project is readable by the caller with ``policy.librarian`` not
``off`` — the privacy gate's rules (``librarian/privacy.py``), which ``memory.ask`` re-runs over the
exact ids before every provider call — and that the D-083 isolation allows: a project whose
``policy.librarian_cross_project`` is ``exclude`` never takes part in librarian work with another
project (``candidates.relation_allowed`` over the item's projects plus the asked project; review 79
T1), so an item co-owned with an excluded project, or lying in one while another is asked, is not in
the view. Structural entries are cached per version id in-process
(versions are immutable); nothing here writes to the database.

The structural rules are ported from the measured W-B prototype (``eval/research/research_loop.py``
@ be3956b, V3/V5: map +.17 correct answers on B-dev).
"""

from __future__ import annotations

import hashlib
import heapq
import re
import threading
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from psycopg import AsyncConnection

from hlmemo.auth.context import AuthContext, Role
from hlmemo.core.budget import Meter
from hlmemo.db.librarian_queries import cross_project_excluded
from hlmemo.librarian.candidates import relation_allowed

#: D-191: a source with more entries than this ALWAYS lists its newest NEWEST_K entries first
NEWEST_K = 10
_EPOCH = datetime.min.replace(tzinfo=UTC)
#: summaries may take at most this share of the map budget (before D-191; summaries now only use
#: the budget the entries leave)
SUMMARY_SHARE = 0.40
#: a summary is clipped to this many characters in the map
SUMMARY_CHARS = 320
#: whole-item handles are drillable only for items of at most this many chunks
WHOLE_ITEM_MAX_CHUNKS = 2
ENTRY_CACHE_MAX = 50_000

_HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$")
_ROW_ID = re.compile(r"^\|?\s*([A-Z]{1,5}-\d{2,5})\s*\|")
_GIT_HEAD = re.compile(r"^\d{1,2}:\d{2}\s+[0-9a-f]{7,40}\s+")
# emphasis/code markers; an underscore only when it is emphasis (identifiers keep theirs)
_MD = re.compile(r"\*{1,3}|`{1,3}|(?<!\w)_{1,2}|_{1,2}(?!\w)")
_GIT_TITLE = re.compile(r"^git log (\d{4}-\d{2}-\d{2})\b")
_ROW_LEAD = re.compile(r"^([A-Z]{1,5}-\d{2,5}) · ([A-Za-z]+)[^:]{0,60}: ")
_PATHISH = re.compile(r"^[\w.@~+-]+(?:/[\w.@~+-]+)*\.[A-Za-z0-9]{1,8}$|^[\w.@~+-]+(?:/[\w.@~+-]+)+$")


@dataclass(frozen=True, slots=True)
class Entry:
    level: int  # 1-4 = heading level; decision rows are level 2
    ordinal: int  # the chunk the entry starts in
    label: str


@dataclass(slots=True)
class ViewItem:
    version_id: int
    title: str
    kind: str
    source_system: str | None
    source_path: str | None  # the item's source path, anchor included (``docs/x.md#d-130``)
    n_chunks: int
    project_ids: list[int] = field(default_factory=list)
    #: D-191: when the item became valid / was recorded (the map's "newest" order)
    valid_from: datetime | None = None
    recorded_at: datetime | None = None

    @property
    def path(self) -> str:
        """The human-readable location of the item (the ``path`` of a ``memory.ask`` source)."""
        return self.source_path or self.title


@dataclass(slots=True)
class Located:
    group: str  # "docs/decisions/", "git log/", "(no source)/"
    key: str  # the source file / cluster key (``memory_map_summaries.source_key``)
    name: str  # the file line's name
    part: str | None  # the item's own label inside a multi-item file


@dataclass(slots=True)
class MemoryMap:
    text: str
    tokens: int
    handles: dict[str, tuple[int, int | None]]  # every rendered handle -> (version_id, ordinal)
    sizes: dict[int, int]  # version_id -> chunks, every item of the view
    version_ids: list[int]  # every item whose name/labels the text carries (rendered files)
    summary_members: list[int]  # the member versions of the summaries shown
    files: int = 0
    files_omitted: int = 0
    items: int = 0
    sections: int = 0
    summaries: int = 0
    budget: int = 0

    def drillable(self, handle: str) -> bool:
        """A handle the planner may drill: any chunk handle of the map, or a whole-item handle of an
        item with at most ``WHOLE_ITEM_MAX_CHUNKS`` chunks (a large item would swamp the drill)."""
        hit = self.handles.get(handle)
        if hit is None:
            return False
        vid, ordinal = hit
        return ordinal is not None or self.sizes.get(vid, 99) <= WHOLE_ITEM_MAX_CHUNKS

    def gate_ids(self) -> list[int]:
        """The version ids whose content (titles, labels, summaries) the map text carries."""
        return sorted(set(self.version_ids) | set(self.summary_members))


# --------------------------------------------------------------------------- structural entries
def clean(s: str, words: int = 8, chars: int = 56) -> str:
    s = _MD.sub("", s).replace("|", " ").strip()
    s = " ".join(s.split()[:words])
    return s if len(s) <= chars else s[: chars - 1] + "…"


def row_label(line: str, rid: str) -> str:
    """The row id plus its first descriptive cell (skips date/status-like cells: < 3 words or no
    letters)."""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    text = next((c for c in cells[1:] if len(c.split()) >= 3 and re.search(r"[^\W\d_]", c)), "")
    return f"{rid} {clean(text, 7, 52)}".strip()


def structural_entries(title: str, body: str, starts: list[tuple[int, int]]) -> list[Entry]:
    """Structural entries of one item in document order: markdown headings outside fenced code
    (levels 1-4; a git-day heading ``HH:MM <sha> subject`` keeps only the subject) and rows of a
    table whose first cell is an id like ``ABC-123`` (decision/ADR log), as level-2 entries.
    ``starts`` = ``[(char_start, ordinal)]`` of the item's chunks; an entry belongs to the last chunk
    that starts at or before it (chunks overlap slightly)."""
    out: list[Entry] = []
    seen: set[str] = set()
    fence = False
    pos = 0
    ordered = sorted(starts)
    for line in body.splitlines(keepends=True):
        off, pos = pos, pos + len(line)
        s = line.rstrip("\n")
        if s.lstrip().startswith(("```", "~~~")):
            fence = not fence
            continue
        if fence:
            continue
        m = _HEADING.match(s)
        if m:
            level, label = len(m.group(1)), _GIT_HEAD.sub("", m.group(2))
            if level == 1 and clean(label, 30, 200)[:24] in title:
                continue  # the H1 repeats the item's title
            label = clean(label, 7, 44)
        else:
            r = _ROW_ID.match(s)
            if not r:
                continue
            level, label = 2, row_label(s, r.group(1))
        ordinal = 0
        for st, o in ordered:
            if st <= off:
                ordinal = o
            else:
                break
        key = f"{level}:{label}"
        if label and key not in seen:
            seen.add(key)
            out.append(Entry(level, ordinal, label))
    return out


def spread(entries: list[Any]) -> list[Any]:
    """Reorder so that any prefix is spread over the whole list (bit-reversal order)."""
    n = len(entries)
    if n <= 2:
        return list(entries)
    bits = (n - 1).bit_length()
    order = sorted(range(1 << bits), key=lambda i: int(format(i, f"0{bits}b")[::-1], 2))
    return [entries[i] for i in order if i < n]


# --------------------------------------------------------------------------- locating an item
def _dir_name(path: str) -> tuple[str, str]:
    d, _, base = path.rpartition("/")
    return (d + "/") if d else "./", base or path


def lead_of(title: str) -> str:
    """A title without its trailing `` · <path>`` (``hlm import`` titles) and without a leading
    ``<doc title> › `` (a section's parent)."""
    title = " ".join(title.split())
    if " · " in title:
        lead, _, tail = title.rpartition(" · ")
        if _PATHISH.match(tail):
            title = lead
    if " › " in title:
        title = title.rsplit(" › ", 1)[1]
    m = _ROW_LEAD.match(title)
    if m:  # a decision row's lead "D-130 · ACCEPTED (owner): text" -> "D-130 text" (a rarer status stays)
        status = m.group(2)
        title = f"{m.group(1)} " + ("" if status.upper() == "ACCEPTED" else f"({status}) ") + title[m.end() :]
    return title


def locate(item: ViewItem) -> Located:
    """Where an item sits in the tree: its source file (``source.path`` without the ``#anchor``),
    else a path parsed from the title (``path § section`` of the eval importer, ``… · path`` of
    ``hlm import``, ``git log <day>``), else a cluster of its kind."""
    title = " ".join(item.title.split())
    if item.source_path:
        path, _, _anchor = item.source_path.partition("#")
        group, name = _dir_name(path)
        if "/" not in path and item.source_system:
            group = f"{item.source_system}/"
        key = f"{item.source_system or 'src'}:{path}"
        return Located(group, key, name, clean(lead_of(title), 9, 64) if _anchor else None)
    m = _GIT_TITLE.match(title)
    if m:
        part = title.split(" § ", 1)[1] if " § " in title else None
        return Located("git log/", f"git:{m.group(1)}", m.group(1), part)
    if " § " in title:
        path, _, section = title.partition(" § ")
        if _PATHISH.match(path):
            group, name = _dir_name(path)
            return Located(group, f"title:{path}", name, clean(section, 9, 64))
    if " · " in title:
        lead, _, tail = title.rpartition(" · ")
        if _PATHISH.match(tail):
            group, name = _dir_name(tail)
            return Located(group, f"title:{tail}", name, clean(lead, 9, 64))
    if _PATHISH.match(title):
        group, name = _dir_name(title)
        return Located(group, f"title:{title}", name, None)
    return Located("(no source)/", f"kind:{item.kind}", item.kind, clean(title, 9, 64))


def members_digest(version_ids: Iterable[int]) -> str:
    """The source digest of a summary: sha256 of its sorted member version ids."""
    return hashlib.sha256(",".join(str(v) for v in sorted(set(version_ids))).encode()).hexdigest()


# --------------------------------------------------------------------------- the map
@dataclass(slots=True)
class _File:
    group: str
    key: str
    name: str
    items: list[tuple[ViewItem, Located]]
    chunks: int
    cands: list[tuple[int, int, str, str, int | None]] = field(default_factory=list)
    take: list[tuple[int, int, str, str, int | None]] = field(default_factory=list)
    summary: str | None = None
    summary_members: list[int] = field(default_factory=list)

    def line(self) -> str:
        if len(self.items) == 1 and self.items[0][1].part is None:
            it = self.items[0][0]
            return f"- {self.name} " + item_handle(it.version_id, it.n_chunks)
        if len(self.items) == 1:
            it, loc = self.items[0]
            return f"- {self.name}: {loc.part} " + item_handle(it.version_id, it.n_chunks)
        return f"- {self.name} ({len(self.items)} items)"


def item_handle(version_id: int, n_chunks: int) -> str:
    return f"v{version_id}" + (f"({n_chunks})" if n_chunks > 1 else "")


def _candidates(f: _File, entries: dict[int, list[Entry]]) -> list[tuple[int, int, str, str, int | None]]:
    """``(item index, ordinal, label, handle, ordinal|None)`` in spread order. A single-item file:
    its H1s, then level-2 entries spread, then deeper ones spread. A multi-item file: one entry per
    item (spread over the items), then the items' own level-1/2 entries spread."""
    out: list[tuple[int, int, str, str, int | None]] = []
    single = len(f.items) == 1 and f.items[0][1].part is None
    if single or len(f.items) == 1:
        it = f.items[0][0]
        ents = entries.get(it.version_id, [])
        ordered = (
            [e for e in ents if e.level == 1]
            + spread([e for e in ents if e.level == 2])
            + spread([e for e in ents if e.level > 2])
        )
        for e in ordered:
            out.append((0, e.ordinal, e.label, f"v{it.version_id}.{e.ordinal}", e.ordinal))
        return out
    heads = []
    subs = []
    for idx, (it, loc) in enumerate(f.items):
        label = loc.part or clean(lead_of(it.title), 9, 64)
        heads.append((idx, -1, label, item_handle(it.version_id, it.n_chunks), None))
        for e in entries.get(it.version_id, []):
            if e.level <= 2 and it.n_chunks > 1:
                subs.append((idx, e.ordinal, e.label, f"v{it.version_id}.{e.ordinal}", e.ordinal))
    return spread(heads) + spread(subs)


def _newest_key(it: ViewItem) -> tuple[datetime, datetime, int]:
    """D-191: newest first = latest valid_from, then latest recorded_at, then the higher version."""
    return (it.valid_from or _EPOCH, it.recorded_at or _EPOCH, it.version_id)


def _newest_first(f: _File, entries: dict[int, list[Entry]], k: int) -> None:
    """D-191: ``f.cands`` reordered: its newest ``k`` entries first (a multi-item file: its items'
    head entries by ``_newest_key``; a single-item document: its last ``k`` level-1/2 entries, the
    latest first), then the rest in their spread order."""
    if len(f.items) > 1:
        heads = [c for c in f.cands if c[1] == -1]
        heads.sort(key=lambda c: _newest_key(f.items[c[0]][0]), reverse=True)
        newest = heads[:k]
    else:
        ents = entries.get(f.items[0][0].version_id, [])
        tail = [e for e in ents if e.level <= 2][-k:][::-1]
        newest = [c for e in tail for c in f.cands if c[1] == e.ordinal and c[2] == e.label][:k]
    f.cands = newest + [c for c in f.cands if c not in newest]


def _doc_pos(f: _File, c: tuple[int, int, str, str, int | None], entries: dict[int, list[Entry]]) -> int:
    """An entry's position in its item's document (entries sharing one chunk render in document
    order whatever order the budget took them in; an item head first)."""
    if c[1] == -1:
        return -1
    ents = entries.get(f.items[c[0]][0].version_id, [])
    return next((i for i, e in enumerate(ents) if e.ordinal == c[1] and e.label == c[2]), len(ents))


def build_map(
    items: list[ViewItem],
    entries: dict[int, list[Entry]],
    summaries: dict[str, tuple[list[int], str]],
    *,
    budget_tokens: int,
    project: str,
    meter: Meter | None = None,
) -> MemoryMap:
    """Render the map of ``items`` (the caller's view) within ``budget_tokens`` (module docstring).
    ``summaries``: ``{source_key: (member version ids, summary)}`` already filtered for the caller."""
    meter = meter or Meter()
    ntok = meter.count_text
    files: dict[str, _File] = {}
    for it in sorted(items, key=lambda i: (i.source_path or i.title, i.version_id)):
        loc = locate(it)
        f = files.get(loc.key)
        if f is None:
            f = files[loc.key] = _File(loc.group, loc.key, loc.name, [], 0)
        f.items.append((it, loc))
        f.chunks += max(1, it.n_chunks)
    for f in files.values():
        f.cands = _candidates(f, entries)
        _newest_first(f, entries, NEWEST_K)
        s = summaries.get(f.key)
        if s is not None and s[1].strip():
            text = " ".join(s[1].split())
            f.summary = text if len(text) <= SUMMARY_CHARS else text[: SUMMARY_CHARS - 1] + "…"
            f.summary_members = list(s[0])
    n_items = len(items)
    header = (
        f"MEMORY MAP of project {project}: what is where ({n_items} items in {len(files)} sources). "
        "vN = a whole item, vN.M = chunk M of item N (drilling it returns chunk M with its neighbours), "
        "(k) = the item has k chunks: drill an item with more than 2 chunks only by a chunk handle vN.M. "
        "A line after ~ is a short summary of that source.\n"
    )
    used = ntok(header)
    ordered = sorted(files.values(), key=lambda f: (f.group, f.name, f.key))
    # 1. mandatory: group + file lines; spread-truncated when even they overflow (keep 25% for the rest)
    cost = {f.key: ntok(f.line() + "\n") for f in ordered}
    groups = sorted({f.group for f in ordered})
    gcost = {g: ntok(f"[{g}]\n") for g in groups}
    mandatory = used + sum(cost.values()) + sum(gcost.values())
    kept: set[str] = {f.key for f in ordered}
    if mandatory > budget_tokens * 0.75:
        kept = set()
        room = budget_tokens * 0.75 - used
        seen_groups: set[str] = set()
        for f in spread(ordered):
            extra = cost[f.key] + (0 if f.group in seen_groups else gcost[f.group] + 6)
            if extra > room:
                continue
            kept.add(f.key)
            seen_groups.add(f.group)
            room -= extra
    rendered = [f for f in ordered if f.key in kept]
    used += sum(cost[f.key] for f in rendered) + sum(gcost[g] for g in {f.group for f in rendered})
    omitted = len(ordered) - len(rendered)
    # 2. D-191: the newest NEWEST_K entries of every source with more entries (largest sources first)
    for f in sorted(rendered, key=lambda f: (-f.chunks, f.key)):
        if len(f.cands) <= NEWEST_K:
            continue
        for cand in f.cands[:NEWEST_K]:
            c = ntok(f" · {cand[2]} {cand[3]}") + (2 if not f.take else 0)
            if used + c > budget_tokens:
                break
            f.take.append(cand)
            used += c
    # 3. section entries: round-robin by size, each file's remaining candidates in spread order
    heap = [
        (-float(f.chunks) / (1 + len(f.take)), i)
        for i, f in enumerate(rendered)
        if len(f.take) < len(f.cands)
    ]
    heapq.heapify(heap)
    while heap and used < budget_tokens:
        _, i = heapq.heappop(heap)
        f = rendered[i]
        cand = f.cands[len(f.take)]
        c = ntok(f" · {cand[2]} {cand[3]}") + (2 if not f.take else 0)
        if used + c <= budget_tokens:
            f.take.append(cand)
            used += c
        else:
            f.cands = f.cands[: len(f.take)]  # this file's next entry does not fit: stop it
        if len(f.take) < len(f.cands):
            heapq.heappush(heap, (-f.chunks / (1 + len(f.take)), i))
    # 4. D-191: summaries only with the budget the entries left (largest sources first)
    shown_summaries = 0
    for f in sorted(rendered, key=lambda f: (-f.chunks, f.key)):
        if f.summary is None:
            continue
        c = ntok(f" ~ {f.summary}")
        if used + c > budget_tokens:
            f.summary, f.summary_members = None, []
            continue
        used += c
        shown_summaries += 1
    for f in rendered:
        if f.summary is None:
            f.summary_members = []
    # render
    lines = [header.rstrip("\n")]
    handles: dict[str, tuple[int, int | None]] = {}
    version_ids: list[int] = []
    summary_members: list[int] = []
    sections = 0
    group = None
    omitted_in: dict[str, int] = {}
    for f in ordered:
        if f.key not in kept:
            omitted_in[f.group] = omitted_in.get(f.group, 0) + 1
    for f in rendered:
        if f.group != group:
            if group is not None and omitted_in.get(group):
                lines.append(f"- … {omitted_in[group]} more sources")
            group = f.group
            lines.append(f"[{group}]")
        line = f.line() + (f" ~ {f.summary}" if f.summary else "")
        lines.append(line)
        for it, _loc in f.items:
            version_ids.append(it.version_id)
        if len(f.items) == 1:
            it = f.items[0][0]
            handles[f"v{it.version_id}"] = (it.version_id, None)
        summary_members.extend(f.summary_members)
        if f.take:
            ents = sorted(f.take, key=lambda c: (c[0], c[1], _doc_pos(f, c, entries)))
            lines.append("  " + " · ".join(f"{c[2]} {c[3]}" for c in ents))
            for c in ents:
                vid = f.items[c[0]][0].version_id
                handles[f"v{vid}" if c[4] is None else f"v{vid}.{c[4]}"] = (vid, c[4])
            sections += len(ents)
    if group is not None and omitted_in.get(group):
        lines.append(f"- … {omitted_in[group]} more sources")
    text = "\n".join(lines)
    return MemoryMap(
        text=text,
        tokens=ntok(text),
        handles=handles,
        sizes={it.version_id: it.n_chunks for it in items},
        version_ids=sorted(set(version_ids)),
        summary_members=sorted(set(summary_members)),
        files=len(rendered),
        files_omitted=omitted,
        items=n_items,
        sections=sections,
        summaries=shown_summaries,
        budget=budget_tokens,
    )


# --------------------------------------------------------------------------- the caller's view (DB)
_VIEW_SQL = """
SELECT mv.version_id, mv.title, mv.kind, mv.source->>'system', mv.source->>'path', mv.project_ids,
       (SELECT count(*) FROM chunks c WHERE c.version_id = mv.version_id)::int, mv.valid_from, mv.recorded_at
  FROM memory_versions mv
 WHERE %(pid)s = ANY(mv.project_ids) AND mv.device_scope = ANY(%(scopes)s)
   AND mv.status = 'active' AND mv.superseded_at = 'infinity' AND mv.valid_to = 'infinity'
   AND mv.valid_from <= now() AND mv.kind <> 'project_card'
 ORDER BY mv.version_id
"""


def view_scopes(ctx: AuthContext) -> list[str]:
    """The scopes a provider prompt may carry for this caller: ``all`` and its class, never
    ``device:*`` (privacy: device-scoped content never leaves the host)."""
    return ["all", f"class:{ctx.device_class}"]


async def project_policies(conn: AsyncConnection, project_ids: Iterable[int]) -> dict[int, str | None]:
    ids = sorted(set(project_ids))
    if not ids:
        return {}
    cur = await conn.execute(
        "SELECT project_id, policy->>'librarian' FROM projects WHERE project_id = ANY(%s)", (ids,)
    )
    return {int(pid): pol for pid, pol in await cur.fetchall()}


async def load_view(conn: AsyncConnection, ctx: AuthContext, project_id: int) -> list[ViewItem]:
    """The caller's VIEW of ``project_id`` (module docstring): what it may read AND what may reach a
    provider. Run inside the caller's (request or fresh) transaction."""
    cur = await conn.execute(_VIEW_SQL, {"pid": project_id, "scopes": view_scopes(ctx)}, prepare=False)
    rows = await cur.fetchall()
    touched = {int(p) for r in rows for p in r[5]} | {project_id}
    policies = await project_policies(conn, touched)
    excluded = await cross_project_excluded(conn, touched)
    out: list[ViewItem] = []
    for vid, title, kind, system, path, pids, n, valid_from, recorded_at in rows:
        pids = [int(p) for p in pids]
        if not all(p in policies and policies[p] != "off" and ctx.has(p, Role.READ) for p in pids):
            continue
        if not isolation_ok(pids, project_id, excluded):
            continue
        out.append(
            ViewItem(
                int(vid), str(title), str(kind), system, path, int(n or 0), pids, valid_from, recorded_at
            )
        )
    return out


def isolation_ok(project_ids: Iterable[int], home: int, excluded: set[int]) -> bool:
    """D-083 isolation for librarian work ABOUT ``home`` (a question asked in it, a summary of its
    source): the item's projects plus ``home`` touch no ``exclude`` project, or exactly one project."""
    return relation_allowed({*project_ids, home}, excluded)


class EntryCache:
    """Structural entries per version id (immutable versions), LRU-bounded, thread-safe."""

    def __init__(self, maxsize: int = ENTRY_CACHE_MAX) -> None:
        self.maxsize = maxsize
        self._d: OrderedDict[int, list[Entry]] = OrderedDict()
        self._lock = threading.Lock()

    def get_many(self, version_ids: Iterable[int]) -> tuple[dict[int, list[Entry]], list[int]]:
        found: dict[int, list[Entry]] = {}
        missing: list[int] = []
        with self._lock:
            for v in version_ids:
                e = self._d.get(v)
                if e is None:
                    missing.append(v)
                else:
                    self._d.move_to_end(v)
                    found[v] = e
        return found, missing

    def put(self, version_id: int, entries: list[Entry]) -> None:
        with self._lock:
            self._d[version_id] = entries
            self._d.move_to_end(version_id)
            while len(self._d) > self.maxsize:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


ENTRIES = EntryCache()


async def load_entries(
    conn: AsyncConnection, items: list[ViewItem], cache: EntryCache | None = None
) -> dict[int, list[Entry]]:
    """Structural entries of every item (bodies are read only for versions not yet cached)."""
    cache = cache or ENTRIES
    found, missing = cache.get_many(it.version_id for it in items)
    if missing:
        titles = {it.version_id: it.title for it in items}
        cur = await conn.execute(
            "SELECT version_id, body FROM memory_versions WHERE version_id = ANY(%s)",
            (missing,),
            prepare=False,
        )
        bodies = {int(v): b for v, b in await cur.fetchall()}
        cur = await conn.execute(
            "SELECT version_id, char_start, ordinal FROM chunks WHERE version_id = ANY(%s)",
            (missing,),
            prepare=False,
        )
        starts: dict[int, list[tuple[int, int]]] = {}
        for v, st, o in await cur.fetchall():
            starts.setdefault(int(v), []).append((int(st), int(o)))
        for v in missing:
            ents = structural_entries(titles.get(v, ""), bodies.get(v, ""), starts.get(v, []))
            cache.put(v, ents)
            found[v] = ents
    return found


async def load_summaries(
    conn: AsyncConnection, project_id: int, view_ids: set[int]
) -> dict[str, tuple[list[int], str]]:
    """The cached L2 summaries of the project that the caller may see: every member version is in
    the caller's view (so a summary never carries content of an item the caller cannot read, nor of
    a version that is no longer current)."""
    cur = await conn.execute(
        # a failed refresh keeps the last good summary (and its members): shown while it still fits
        "SELECT source_key, member_ids, summary FROM memory_map_summaries"
        " WHERE project_id = %s AND summary IS NOT NULL",
        (project_id,),
        prepare=False,
    )
    out: dict[str, tuple[list[int], str]] = {}
    for key, members, summary in await cur.fetchall():
        ids = [int(m) for m in members or []]
        if ids and set(ids) <= view_ids:
            out[str(key)] = (ids, str(summary))
    return out


__all__ = [
    "ENTRIES",
    "SUMMARY_SHARE",
    "WHOLE_ITEM_MAX_CHUNKS",
    "Entry",
    "EntryCache",
    "Located",
    "MemoryMap",
    "ViewItem",
    "build_map",
    "clean",
    "item_handle",
    "load_entries",
    "load_summaries",
    "load_view",
    "locate",
    "members_digest",
    "project_policies",
    "row_label",
    "spread",
    "structural_entries",
    "view_scopes",
]
