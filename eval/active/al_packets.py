"""Packet builders of the active-librarian ceiling experiment (PLAN §3), one per experiment.

Every builder reads the restored database ``hlm_al_ceiling`` through READ-ONLY transactions
(``al_common.connect_ro``) and writes JSON packets to ``<private>/packets/<exp>/``. A packet holds
its sources (handle, kind, project, dates, text), the context shown to the model, private metadata
the model never sees, and ``user``: the exact message both arms receive (rendered once, hashed).
Every text is redacted with the product ``Redactor`` BEFORE it is cut and rendered, so the provider's
own redaction is a no-op and both arms see byte-identical input.

* E0: a stratified sample of the OPEN observer proposals, selected through the ops audit code path
  (``hlmemo.ops.librarian.audit``), enriched with the subjects' texts (no LLM arm: blind labels only).
* E1 (AL1 session distillation): current session notes with a Decisions section, their Decisions /
  Uncertain lines, and the product candidate selector's output (``WriteReview._candidates``: same
  project top-8 + cross-project top-5, the triggering device's capabilities; the reserved document
  list is dropped).
* E2 (AL3 project card): per project with at least ``min_items`` current items, the previous card
  (context, stale flag), the product Memory Map (context), the newest session notes and the current
  facts/lessons/experiences/episodes (sources).
* E3 (AL4 cross-project lessons): every current lesson and experience of every non-reserved project.

Pure assembly (``split_note``, ``*_packet``, ``render_user``, ``stratified_sample``, …) is separate
from the SQL so it is unit-tested on synthetic rows.
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import al_common as C

PACKET_SCHEMA = "al-packet/1"
CURRENT = "mv.superseded_at = 'infinity' AND mv.valid_to = 'infinity' AND mv.status = 'active'"
NOT_RESERVED = "COALESCE(p.policy->>'reserved', 'false') <> 'true' AND p.archived_at IS NULL"
SHARED_SCOPE = "left(mv.device_scope, 7) <> 'device:'"  # device-scoped items never reach a provider
#: items written by a device of an excluded class (``selection.exclude_device_classes``: the gate and
#: test devices, class ``ci``) are smoke fixtures, never sources
WRITER_OK = (
    "NOT EXISTS (SELECT 1 FROM events xe JOIN devices xd ON xd.device_id = xe.device_id"
    " WHERE xe.event_id = mv.source_event_id AND xd.class = ANY(%(excl)s))"
)
E2_KINDS = ("fact", "lesson", "experience", "episode")
E3_KINDS = ("lesson", "experience")
DOC_KINDS = frozenset({"doc_chunk"})
HIDING_ACTIONS = frozenset({"version_close"})
#: E1 readers see this many newer session notes of the subject's project (staleness judgement)
LATER_NOTES = 5


# --------------------------------------------------------------------------- pure helpers
def cut(text: str, n: int) -> str:
    """The product's prompt cut (``write_review._cut``)."""
    return text if len(text) <= n else text[:n] + " …"


def day(value: Any) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()[:10]
    return str(value or "")[:10]


def handle(version_id: int) -> str:
    return f"v{int(version_id)}"


_SECTION = re.compile(r"^#{2,3}\s+(.+?)\s*$")


@dataclass(slots=True)
class NoteSections:
    notes: str
    decisions: list[str]
    uncertain: list[str]


def split_note(body: str) -> NoteSections:
    """A session note's ``## Decisions`` and ``## Uncertain …`` bullet lines (``write_service.
    _close_items`` and ``capture.write.compose`` layouts); every other line stays in ``notes``. A
    wrapped bullet continues on the next non-empty line."""
    notes: list[str] = []
    out: dict[str, list[str]] = {"decisions": [], "uncertain": []}
    current: str | None = None
    for line in body.splitlines():
        m = _SECTION.match(line)
        if m:
            name = m.group(1).strip().lower()
            current = (
                "decisions"
                if name.startswith("decision")
                else "uncertain"
                if name.startswith("uncertain")
                else None
            )
            if current is None:
                notes.append(line)
            continue
        if current is None:
            notes.append(line)
            continue
        s = line.strip()
        if not s:
            continue
        if s[:2] in ("- ", "* "):
            out[current].append(s[2:].strip())
        elif out[current]:
            out[current][-1] = f"{out[current][-1]} {s}"
        else:
            out[current].append(s)
    return NoteSections("\n".join(notes).strip(), out["decisions"], out["uncertain"])


def make_source(
    hdl: str,
    *,
    kind: str,
    project: str,
    title: str,
    text: str,
    valid_from: Any,
    role: str,
    quotable: bool = True,
    cross: bool | None = None,
    recorded_at: Any = None,
) -> dict[str, Any]:
    src: dict[str, Any] = {
        "handle": hdl,
        "kind": kind,
        "project": project,
        "title": title,
        "valid_from": day(valid_from),
        "role": role,
        "quotable": quotable,
        "text": text,
    }
    if cross is not None:
        src["cross_project"] = cross
    if recorded_at is not None:
        src["recorded_at"] = day(recorded_at)
    return src


def _redactor() -> Any:
    from hlmemo.librarian.redact import Redactor

    return Redactor()


def redact_sources(sources: list[dict[str, Any]], limit: int | None = None, redactor: Any = None) -> None:
    """Redact title and text in place, then cut the text to ``limit`` characters (the model sees
    exactly this text; the grounding checker compares quotes against it)."""
    red = redactor or _redactor()
    for s in sources:
        s["title"] = red.text(str(s["title"]))
        text = red.text(str(s["text"]))
        s["text"] = cut(text, limit) if limit else text


def render_source(s: dict[str, Any]) -> str:
    bits = [f"[{s['handle']}] {s['kind']}", f"project {s['project']}", f"valid from {s['valid_from']}"]
    if s.get("cross_project") is not None:
        bits.append("other project" if s["cross_project"] else "same project")
    if not s.get("quotable", True):
        bits.append("CONTEXT ONLY, never quote")
    return f"{' · '.join(bits)}\ntitle: {s['title']}\n<<<\n{s['text']}\n>>>"


def _bullets(lines: list[str]) -> str:
    return "\n".join(f"- {x}" for x in lines) if lines else "(none)"


def render_user(packet: dict[str, Any]) -> str:
    """The exact user message of a packet (deterministic in the packet's content)."""
    exp, pid = packet["exp"], packet["packet_id"]
    ctx = packet.get("context") or {}
    srcs = packet["sources"]
    quotable = [s for s in srcs if s.get("quotable", True)]
    if exp == "E1":
        subj = ctx["subject"]
        head = [
            f"TASK: session distillation (experiment E1, packet {pid})",
            f"PROJECT: {packet['project']}",
            f"SUBJECT SESSION NOTE: {subj} (valid from {ctx['subject_valid_from']})",
            "",
            f"DECISION LINES of {subj} (the only lines facts may come from):",
            _bullets(ctx["decisions"]),
            "",
            f"UNCERTAIN LINES of {subj} (never promote these):",
            _bullets(ctx["uncertain"]),
            "",
            f"EXISTING ITEMS found by retrieval: {len(srcs) - 1}",
        ]
    elif exp == "E2":
        card = ctx.get("previous_card") or "(no card yet)"
        stale = (
            " (FLAGGED STALE: some items it was written from have changed)" if ctx.get("card_stale") else ""
        )
        head = [
            f"TASK: project card refresh (experiment E2, packet {pid})",
            f"PROJECT: {packet['project']}",
            f"NEWEST SOURCE DATE: {ctx.get('newest_source') or 'unknown'}",
            "",
            f"PREVIOUS PROJECT CARD (context only; never quote it){stale}:",
            "<<<",
            card,
            ">>>",
            "",
            "MEMORY MAP of the project (context only; never quote it):",
            "<<<",
            ctx.get("memory_map") or "(no map)",
            ">>>",
        ]
    elif exp == "E3":
        projects = sorted({s["project"] for s in quotable})
        head = [
            f"TASK: cross-project lesson synthesis (experiment E3, packet {pid})",
            f"PROJECTS ({len(projects)}): {', '.join(projects)}",
        ]
    else:
        raise C.HarnessError(f"no user message for experiment {exp}")
    body = "\n\n".join(render_source(s) for s in srcs)
    return "\n".join([*head, "", f"SOURCES ({len(srcs)}):", "", body, "", "Produce the JSON object now."])


def finalize(packet: dict[str, Any]) -> dict[str, Any]:
    packet["schema"] = PACKET_SCHEMA
    packet["user"] = render_user(packet)
    packet["user_sha256"] = C.sha256_text(packet["user"])
    return packet


def new_packet(exp: str, packet_id: str, project: str | None) -> dict[str, Any]:
    return {"schema": PACKET_SCHEMA, "exp": exp, "packet_id": packet_id, "project": project, "sources": []}


# --------------------------------------------------------------------------- E1 assembly
def e1_packet(
    packet_id: str,
    note: dict[str, Any],
    candidates: list[tuple[dict[str, Any], bool]],
    *,
    slugs: dict[int, str],
    candidate_chars: int,
    meta: dict[str, Any] | None = None,
    later_notes: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """``note``: a session note row (handle, project_id, title, body, valid_from); ``candidates``:
    ``[(candidate row, cross_project)]`` in selector order. None when the note has no decision line.
    ``later_notes``: newer session notes of the same project, kept for the READERS only (to judge
    whether a promoted decision was reversed later); never part of the model's message."""
    red = _redactor()
    body = red.text(note["body"])
    sections = split_note(body)
    if not sections.decisions:
        return None
    p = new_packet("E1", packet_id, slugs.get(int(note["project_id"]), str(note["project_id"])))
    subject = make_source(
        note["handle"],
        kind="session_note",
        project=p["project"],
        title=note["title"],
        text=body,
        valid_from=note["valid_from"],
        role="subject",
    )
    cands = [
        make_source(
            handle(c["version_id"]),
            kind=c["kind"],
            project=slugs.get(int(c["project_id"]), str(c["project_id"])),
            title=c["title"],
            text=c["body"],
            valid_from=c["valid_from"],
            role="candidate",
            cross=cross,
        )
        for c, cross in candidates
        if c["kind"] not in DOC_KINDS
    ]
    redact_sources([subject], None, red)
    redact_sources(cands, candidate_chars, red)
    p["sources"] = [subject, *cands]
    p["context"] = {
        "subject": subject["handle"],
        "subject_valid_from": subject["valid_from"],
        "decisions": sections.decisions,
        "uncertain": sections.uncertain,
    }
    later = []
    for n in later_notes or []:
        sec = split_note(red.text(n["body"]))
        later.append(
            {
                "handle": n["handle"],
                "valid_from": day(n["valid_from"]),
                "decisions": sec.decisions,
                "uncertain": sec.uncertain,
            }
        )
    p["grader_context"] = {"later_notes": later}
    p["meta"] = dict(meta or {})
    return finalize(p)


def load_dry_runs(directory: Path | str) -> list[tuple[str, dict[str, Any]]]:
    """``(file name, payload)`` of every dry-run capture payload in ``directory``, by name."""
    return [
        (f.name, json.loads(f.read_text(encoding="utf-8"))) for f in sorted(Path(directory).glob("*.json"))
    ]


def dry_run_note(payload: dict[str, Any], k: int) -> dict[str, Any]:
    """A dry-run capture payload (``capture.write.write_dry``) as a session-note row: the body the
    server would store (``write_service._close_items``)."""
    body = str(payload.get("notes") or "")
    decisions = [str(d) for d in payload.get("decisions") or []]
    if decisions:
        body += "\n\n## Decisions\n" + "\n".join(f"- {d}" for d in decisions)
    return {
        "handle": f"x{k}",
        "version_id": 0,
        "logical_id": 0,
        "slug": str(payload.get("project") or ""),
        "title": f"Session {payload.get('session_id', '')}",
        "body": body,
        "valid_from": str(payload.get("occurred_at") or ""),
    }


# --------------------------------------------------------------------------- E2 assembly
def select_e2_projects(counts: Iterable[tuple[int, str, int]], min_items: int) -> list[tuple[int, str]]:
    """``(project_id, slug, current items)`` -> the projects with at least ``min_items``, by slug."""
    return sorted(((pid, slug) for pid, slug, n in counts if n >= min_items), key=lambda x: x[1])


def e2_packet(
    packet_id: str,
    slug: str,
    *,
    card: dict[str, Any] | None,
    card_stale: bool,
    memory_map: str,
    notes: list[dict[str, Any]],
    items: list[dict[str, Any]],
    note_chars: int,
    item_chars: int,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    red = _redactor()
    p = new_packet("E2", packet_id, slug)
    note_srcs = [
        make_source(
            handle(n["version_id"]),
            kind="session_note",
            project=slug,
            title=n["title"],
            text=n["body"],
            valid_from=n["valid_from"],
            role="note",
        )
        for n in notes
    ]
    item_srcs = [
        make_source(
            handle(i["version_id"]),
            kind=i["kind"],
            project=slug,
            title=i["title"],
            text=i["body"],
            valid_from=i["valid_from"],
            role="item",
        )
        for i in items
    ]
    redact_sources(note_srcs, note_chars, red)
    redact_sources(item_srcs, item_chars, red)
    srcs = [*note_srcs, *item_srcs]
    p["sources"] = srcs
    newest = max((s["valid_from"] for s in srcs), default="")
    # the previous card is context only: cards are never sources of a derivation (PLAN §1)
    p["context"] = {
        "previous_card": None if card is None else red.text(card["body"]),
        "previous_card_handle": None if card is None else handle(card["version_id"]),
        "card_skeleton": bool(card and card.get("skeleton")),
        "card_stale": card_stale,
        "memory_map": red.text(memory_map),
        "newest_source": newest,
    }
    p["meta"] = dict(meta or {})
    return finalize(p)


# --------------------------------------------------------------------------- E3 assembly
def e3_packet(packet_id: str, lessons: list[dict[str, Any]], *, lesson_chars: int) -> dict[str, Any]:
    p = new_packet("E3", packet_id, None)
    srcs = [
        make_source(
            handle(r["version_id"]),
            kind=r["kind"],
            project=r["slug"],
            title=r["title"],
            text=r["body"],
            valid_from=r["valid_from"],
            role="lesson",
        )
        for r in lessons
    ]
    redact_sources(srcs, lesson_chars)
    p["sources"] = srcs
    p["context"] = {"projects": sorted({s["project"] for s in srcs})}
    p["meta"] = {"lessons_per_project": _count_by(s["project"] for s in srcs)}
    return finalize(p)


def _count_by(values: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))


# --------------------------------------------------------------------------- E0 assembly
def e0_stratum(proposal: dict[str, Any], imported_project: str) -> str:
    """``imported`` for proposals of the project whose memory is mostly imported long documents,
    ``curated`` for every other (migrated, curated) project."""
    return "imported" if proposal.get("project") == imported_project else "curated"


def _round_robin(
    group: list[dict[str, Any]], sub_key: str | None, rng: random.Random
) -> list[dict[str, Any]]:
    """``group`` in a seeded order that alternates between its ``sub_key`` values (so one large
    project cannot fill a stratum), each sub-group shuffled."""
    if not sub_key:
        out = list(group)
        rng.shuffle(out)
        return out
    subs: dict[str, list[dict[str, Any]]] = {}
    for r in group:
        subs.setdefault(str(r.get(sub_key)), []).append(r)
    order = sorted(subs)
    rng.shuffle(order)
    for name in order:
        rng.shuffle(subs[name])
    out: list[dict[str, Any]] = []
    while any(subs[n] for n in order):
        for name in order:
            if subs[name]:
                out.append(subs[name].pop(0))
    return out


def stratified_sample(
    rows: list[dict[str, Any]],
    *,
    key: str,
    per_stratum: int,
    total: int,
    seed: int,
    sub_key: str | None = None,
) -> list[dict[str, Any]]:
    """``per_stratum`` rows per stratum (seeded; round-robin over ``sub_key`` inside a stratum), then
    the remaining slots from the leftovers of every stratum, never more than ``total``.
    Deterministic in (rows order, seed)."""
    rng = random.Random(seed)
    strata: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        strata.setdefault(str(r[key]), []).append(r)
    names = sorted(strata)
    quota = {n: min(per_stratum, len(strata[n])) for n in names}
    while sum(quota.values()) > total:  # quotas over the total: trim the largest quota first
        quota[max(names, key=lambda n: (quota[n], n))] -= 1
    chosen: list[dict[str, Any]] = []
    left: list[dict[str, Any]] = []
    for name in names:
        group = _round_robin(strata[name], sub_key, rng)
        chosen += group[: quota[name]]
        left += group[quota[name] :]
    rng.shuffle(left)
    chosen += left[: max(0, total - len(chosen))]
    return chosen


def e0_hiding(proposal: dict[str, Any]) -> bool:
    """A proposal whose application hides or closes an item: a version close, or a whole-item
    ``supersedes`` link."""
    for a in proposal.get("actions") or []:
        if a.get("op") in HIDING_ACTIONS:
            return True
        if a.get("rel") == "supersedes" and (a.get("scope") in (None, "whole")):
            return True
    return False


def e0_unit(
    proposal: dict[str, Any],
    raw: dict[str, Any],
    subjects: dict[int, dict[str, Any]],
    *,
    stratum: str,
    text_chars: int = 3000,
) -> dict[str, Any]:
    """One E0 unit: what a blind reader sees (``view``) and what only the key keeps (``hidden``):
    the model's confidence, tier, verifier result and guard flags are hidden (no anchoring)."""
    red = _redactor()
    subj_view = []
    for s in proposal["subjects"]:
        vid = int(s["clue"][1:].split(".")[0])
        row = subjects.get(vid, {})
        text = cut(red.text(str(row.get("body", ""))), text_chars)
        subj_view.append(
            {
                "handle": s["clue"],
                "kind": s.get("kind") or row.get("kind"),
                "projects": s.get("projects"),
                "valid_from": day(row.get("valid_from")),
                "still_current_in_db": bool(row.get("current")),
                "title": red.text(str(row.get("title") or s.get("title") or "")),
                "text": text,
            }
        )
    actions = []
    for a in raw.get("actions") or ([raw["mutation"]] if raw.get("mutation") else []):
        props = a.get("props") or {}
        actions.append(
            {
                "op": a.get("op"),
                "rel": a.get("rel"),
                "scope": props.get("scope"),
                "valid_to": a.get("valid_to"),
            }
        )
    view = {
        "kind": proposal["kind"],
        "relation": raw.get("relation"),
        "supersedes": raw.get("supersedes"),
        "scope": raw.get("scope"),
        "reason": red.text(str(raw.get("reason") or "")),
        "quotes": red.value(raw.get("quotes") or {}),
        "proposed_actions": actions,
        "subjects": subj_view,
    }
    hidden = {
        "question_id": proposal["question_id"],
        "project": proposal.get("project"),
        "stratum": stratum,
        "confidence": raw.get("confidence"),
        "tier": raw.get("tier"),
        "verification": raw.get("verification"),
        "flags": raw.get("flags") or [],
        "auto_class": proposal.get("auto_class"),
        "hiding": e0_hiding({"actions": actions}),
    }
    return {"view": view, "hidden": hidden}


# --------------------------------------------------------------------------- SQL (read-only)
async def _rows(conn: Any, sql: str, params: Any = None) -> list[dict[str, Any]]:
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params, prepare=False)
        return list(await cur.fetchall())


async def project_slugs(conn: Any) -> dict[int, str]:
    rows = await _rows(conn, "SELECT project_id, slug FROM projects")
    return {int(r["project_id"]): str(r["slug"]) for r in rows}


async def user_projects(conn: Any) -> list[tuple[int, str]]:
    rows = await _rows(
        conn, f"SELECT p.project_id, p.slug FROM projects p WHERE {NOT_RESERVED} ORDER BY p.slug"
    )
    return [(int(r["project_id"]), str(r["slug"])) for r in rows]


async def session_notes(conn: Any) -> list[dict[str, Any]]:
    return await _rows(
        conn,
        f"""
        SELECT mv.version_id, mv.logical_id, mv.project_id, mv.project_ids, mv.device_scope, mv.kind,
               mv.title, mv.body, mv.tags, mv.pinned, mv.importance, mv.stability, mv.valid_from,
               mv.recorded_at, e.device_id, e.kind AS event_kind, e.client, d.class AS device_class, p.slug
          FROM memory_versions mv
          JOIN events e ON e.event_id = mv.source_event_id
          JOIN devices d ON d.device_id = e.device_id
          JOIN projects p ON p.project_id = mv.project_id
         WHERE mv.kind = 'session_note' AND {CURRENT} AND {NOT_RESERVED} AND {SHARED_SCOPE}
         ORDER BY mv.valid_from DESC, mv.version_id DESC
        """,
    )


async def e1_candidates(conn: Any, note: dict[str, Any]) -> tuple[list[tuple[dict[str, Any], bool]], dict]:
    """The product candidate selector for a session note treated as a ``fact`` subject (AL1 derives
    facts from it): the triggering device's capabilities NOW (``jobs.compute_capabilities``), its
    readable projects and scopes, the D-083 isolation, then ``WriteReview._candidates`` (vector +
    lexical lists, RRF, drop rule, same-project top-8 + cross-project top-5). The reserved document
    list (``DOC_TOP``) is dropped: no derivation from document chunks (PLAN §5 risk 1)."""
    from hlmemo.db import librarian_queries as lq
    from hlmemo.librarian import jobs
    from hlmemo.librarian.tasks.write_review import WriteReview

    subject = lq.SubjectRow(
        version_id=int(note["version_id"]),
        logical_id=int(note["logical_id"]),
        project_id=int(note["project_id"]),
        project_ids=[int(x) for x in note["project_ids"]],
        device_scope=str(note.get("device_scope") or "all"),
        kind="fact",
        title=str(note["title"]),
        body=str(note["body"]),
        tags=list(note.get("tags") or []),
        pinned=bool(note.get("pinned")),
        importance=note.get("importance"),
        stability=str(note.get("stability") or "volatile"),
        valid_from=note["valid_from"],
        current=True,
        recorded_at=note.get("recorded_at") or note["valid_from"],
    )
    device = int(note["device_id"])
    meta: dict[str, Any] = {"device_id": device}
    caps = await jobs.compute_capabilities(conn, device, list(subject.project_ids))
    trusted, device_class, allowed = await lq.readable_projects(
        conn, device, [int(x) for x in caps.get("question") or []]
    )
    if not trusted:
        # the writing device was revoked or rotated since: the product would not review the note
        # (authority_lost). The ceiling asks what the selector gives the note TODAY, so the project's
        # current trusted device with a grant on it stands in (recorded in the packet meta).
        stand_in = await current_device(conn, subject.project_id)
        meta["device_fallback"] = {"from": device, "to": stand_in}
        if stand_in is not None:
            device = stand_in
            caps = await jobs.compute_capabilities(conn, device, list(subject.project_ids))
            trusted, device_class, allowed = await lq.readable_projects(
                conn, device, [int(x) for x in caps.get("question") or []]
            )
    meta.update(trusted=trusted, allowed_projects=len(allowed))
    if not trusted:
        meta["note"] = "no trusted device with a grant on the project: no candidates"
        return [], meta
    scopes = ["all", f"class:{device_class}"]
    excluded = await lq.cross_project_excluded(conn, [*allowed, *subject.project_ids])
    pairs, audit, dropped = await WriteReview()._candidates(conn, [subject], allowed, scopes, excluded)
    meta.update(selector_audit=audit, dropped_by_rule=dropped, excluded=sorted(excluded))
    out = [
        (
            {
                "version_id": pr.cand.version_id,
                "kind": pr.cand.kind,
                "project_id": pr.cand.project_id,
                "title": pr.cand.title,
                "body": pr.cand.body,
                "valid_from": pr.cand.valid_from,
            },
            pr.cross,
        )
        for pr in pairs
    ]
    meta["doc_candidates_dropped"] = sum(1 for c, _ in out if c["kind"] in DOC_KINDS)
    return out, meta


async def current_device(conn: Any, project_id: int) -> int | None:
    """The project's current device: trusted, not expired, not a system or admin device, with a live
    grant on the project; the most recently seen first."""
    rows = await _rows(
        conn,
        """
        SELECT d.device_id FROM devices d
          JOIN device_project_grants g ON g.device_id = d.device_id AND g.revoked_at IS NULL
         WHERE g.project_id = %s AND d.status = 'trusted' AND NOT d.is_admin
           AND NOT COALESCE(d.is_system, false) AND NOT COALESCE(d.expires_at <= now(), false)
         ORDER BY d.last_seen_at DESC NULLS LAST, d.device_id DESC LIMIT 1
        """,
        (project_id,),
    )
    return int(rows[0]["device_id"]) if rows else None


async def project_counts(conn: Any, excl: list[str]) -> list[tuple[int, str, int]]:
    rows = await _rows(
        conn,
        f"""
        SELECT p.project_id, p.slug, count(mv.version_id) AS n
          FROM projects p JOIN memory_versions mv ON mv.project_id = p.project_id
         WHERE {NOT_RESERVED} AND {CURRENT} AND mv.kind <> 'project_card' AND {WRITER_OK}
         GROUP BY p.project_id, p.slug ORDER BY p.slug
        """,
        {"excl": excl},
    )
    return [(int(r["project_id"]), str(r["slug"]), int(r["n"])) for r in rows]


async def current_card(conn: Any, project_id: int) -> tuple[dict[str, Any] | None, bool]:
    from hlmemo.core.skeleton_card import SKELETON_TAG

    rows = await _rows(
        conn,
        f"""
        SELECT mv.version_id, mv.logical_id, mv.body, mv.tags, mv.valid_from
          FROM projects p JOIN memory_versions mv ON mv.logical_id = p.card_logical_id
         WHERE p.project_id = %s AND {CURRENT}
         ORDER BY mv.valid_from DESC, mv.version_id DESC LIMIT 1
        """,
        (project_id,),
    )
    if not rows:
        return None, False
    card = dict(rows[0])
    card["skeleton"] = SKELETON_TAG in (card.get("tags") or [])
    links = await _rows(
        conn,
        """
        SELECT (d.superseded_at <> 'infinity' OR d.valid_to <> 'infinity' OR d.status <> 'active') AS stale
          FROM links l JOIN memory_versions d ON d.version_id = l.dst_version_id
         WHERE l.src_logical_id = %s AND l.rel = 'derived_from'
           AND l.superseded_at = 'infinity' AND l.valid_to = 'infinity'
        """,
        (card["logical_id"],),
    )
    return card, any(bool(r["stale"]) for r in links)


async def project_items(
    conn: Any, project_id: int, kinds: tuple[str, ...], limit: int, excl: list[str]
) -> list[dict[str, Any]]:
    return await _rows(
        conn,
        f"""
        SELECT mv.version_id, mv.kind, mv.title, mv.body, mv.valid_from, mv.recorded_at, mv.pinned,
               mv.importance
          FROM memory_versions mv
         WHERE mv.project_id = %(pid)s AND {CURRENT} AND {SHARED_SCOPE} AND mv.kind = ANY(%(kinds)s)
           AND {WRITER_OK}
         ORDER BY mv.pinned DESC, mv.importance DESC NULLS LAST, mv.valid_from DESC, mv.version_id DESC
         LIMIT %(limit)s
        """,
        {"pid": project_id, "kinds": list(kinds), "limit": limit, "excl": excl},
    )


async def newest_notes(conn: Any, project_id: int, limit: int, excl: list[str]) -> list[dict[str, Any]]:
    return await _rows(
        conn,
        f"""
        SELECT mv.version_id, mv.kind, mv.title, mv.body, mv.valid_from
          FROM memory_versions mv
         WHERE mv.project_id = %(pid)s AND mv.kind = 'session_note' AND {CURRENT} AND {SHARED_SCOPE}
           AND {WRITER_OK}
         ORDER BY mv.valid_from DESC, mv.version_id DESC LIMIT %(limit)s
        """,
        {"pid": project_id, "limit": limit, "excl": excl},
    )


async def memory_map_text(conn: Any, project_id: int, slug: str, budget_tokens: int) -> str:
    """The product Memory Map of the project (``core/memory_map``), rendered for the operator's view."""
    from hlmemo.core import memory_map as mm
    from hlmemo.core.skeleton_card import operator_context

    ctx = operator_context("hlm-al-ceiling")
    items = await mm.load_view(conn, ctx, project_id)
    entries = await mm.load_entries(conn, items)
    summaries = await mm.load_summaries(conn, project_id, {it.version_id for it in items})
    return mm.build_map(items, entries, summaries, budget_tokens=budget_tokens, project=slug).text


async def all_lessons(conn: Any, excl: list[str]) -> list[dict[str, Any]]:
    return await _rows(
        conn,
        f"""
        SELECT mv.version_id, mv.kind, mv.title, mv.body, mv.valid_from, mv.project_id, p.slug
          FROM memory_versions mv JOIN projects p ON p.project_id = mv.project_id
         WHERE mv.kind = ANY(%(kinds)s) AND {CURRENT} AND {NOT_RESERVED} AND {SHARED_SCOPE}
           AND {WRITER_OK}
         ORDER BY p.slug, mv.valid_from, mv.version_id
        """,
        {"kinds": list(E3_KINDS), "excl": excl},
    )


# --------------------------------------------------------------------------- builders
def excluded_writers(cfg: dict[str, Any]) -> list[str]:
    return [str(x) for x in cfg["selection"].get("exclude_device_classes") or []]


def _write_packets(exp: str, packets: list[dict[str, Any]], extra: dict[str, Any]) -> Path:
    from hlmemo.core.budget import Meter

    meter = Meter()
    out = C.packets_dir(exp)
    for old in out.glob("*.json"):
        C.ensure_private(old).unlink()
    system = ""
    if exp in C.PROMPTS:
        system = C.prompt_files(exp)[0].read_text(encoding="utf-8")
    rows = []
    for pk in packets:
        f = C.write_json(out / f"{pk['packet_id']}.json", pk)
        rows.append(
            {
                "packet_id": pk["packet_id"],
                "file": f.name,
                "sha256": C.sha256_file(f),
                "project": pk.get("project"),
                "sources": len(pk.get("sources") or []),
                "input_tokens": meter.count_text(system + "\n" + pk["user"]) if pk.get("user") else None,
            }
        )
    manifest = {"exp": exp, "packets": rows, "built_at": datetime.now().astimezone().isoformat(), **extra}
    C.write_json(out / "manifest.json", manifest)
    return out


async def build_e1(conn: Any, cfg: dict[str, Any], dry_run_dir: Path | None = None) -> list[dict[str, Any]]:
    sel = cfg["selection"]["E1"]
    slugs = await project_slugs(conn)
    by_slug = {v: k for k, v in slugs.items()}
    excluded_classes = set(excluded_writers(cfg))
    all_notes = await session_notes(conn)
    # notes written by gate/test devices (class ci) are smoke fixtures, not sessions
    notes = [n for n in all_notes if n.get("device_class") not in excluded_classes]
    packets: list[dict[str, Any]] = []
    skipped = 0
    for row in notes:
        if len(packets) >= sel["max_notes"]:
            break
        if not split_note(row["body"]).decisions:
            skipped += 1
            continue
        cands, meta = await e1_candidates(conn, row)
        note = {**row, "handle": handle(row["version_id"])}
        later = [
            {**n, "handle": handle(n["version_id"])}
            for n in reversed(notes)  # oldest first: the notes right after the subject
            if n["project_id"] == row["project_id"] and n["valid_from"] > row["valid_from"]
        ][:LATER_NOTES]
        pk = e1_packet(
            f"E1-{len(packets) + 1:03d}",
            note,
            cands,
            slugs=slugs,
            candidate_chars=sel["candidate_chars"],
            meta={**meta, "source": "db", "event_kind": row.get("event_kind")},
            later_notes=later,
        )
        if pk is not None:
            packets.append(pk)
    dry = 0
    if dry_run_dir is not None:
        for k, (name, payload) in enumerate(load_dry_runs(dry_run_dir), start=1):
            note = dry_run_note(payload, k)
            pid = by_slug.get(note["slug"])
            if pid is None:
                continue
            device = next((int(r["device_id"]) for r in notes if int(r["project_id"]) == pid), 1)
            row = {**note, "project_id": pid, "project_ids": [pid], "device_id": device}
            cands, meta = await e1_candidates(conn, row)
            pk = e1_packet(
                f"E1-{len(packets) + 1:03d}",
                row,
                cands,
                slugs=slugs,
                candidate_chars=sel["candidate_chars"],
                meta={**meta, "source": "dry_run", "file": name},
            )
            if pk is not None:
                packets.append(pk)
                dry += 1
    _write_packets(
        "E1",
        packets,
        {
            "notes_without_decisions": skipped,
            "notes_excluded_by_device_class": len(all_notes) - len(notes),
            "dry_run_notes": dry,
        },
    )
    if len(packets) < sel["min_notes"]:
        print(f"WARNING: E1 has {len(packets)} packets, below the plan's {sel['min_notes']}")
    return packets


async def build_e2(conn: Any, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    sel = cfg["selection"]["E2"]
    excl = excluded_writers(cfg)
    projects = select_e2_projects(await project_counts(conn, excl), sel["min_items"])
    packets = []
    for k, (pid, slug) in enumerate(projects, start=1):
        card, stale = await current_card(conn, pid)
        mm_text = await memory_map_text(conn, pid, slug, sel["map_tokens"])
        notes = await newest_notes(conn, pid, sel["session_notes"], excl)
        items = await project_items(conn, pid, E2_KINDS, sel["max_items"], excl)
        packets.append(
            e2_packet(
                f"E2-{k:03d}",
                slug,
                card=card,
                card_stale=stale,
                memory_map=mm_text,
                notes=notes,
                items=items,
                note_chars=sel["note_chars"],
                item_chars=sel["item_chars"],
                meta={"items_listed": len(items), "notes_listed": len(notes)},
            )
        )
    _write_packets("E2", packets, {"min_items": sel["min_items"]})
    return packets


async def build_e3(conn: Any, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    lessons = await all_lessons(conn, excluded_writers(cfg))
    packets = (
        [e3_packet("E3-001", lessons, lesson_chars=cfg["selection"]["E3"]["lesson_chars"])] if lessons else []
    )
    _write_packets("E3", packets, {})
    return packets


async def build_e0(conn: Any, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    from hlmemo.ops.librarian import audit

    sel = cfg["selection"]["E0"]
    imported = sel.get("imported_project", "hlmemo")
    proposals: list[dict[str, Any]] = []
    for _pid, slug in await user_projects(conn):
        out = await audit(conn, slug, status="open")
        for p in out["proposals"]:
            proposals.append({**p, "project": slug, "stratum": e0_stratum({"project": slug}, imported)})
    chosen = stratified_sample(
        proposals,
        key="stratum",
        per_stratum=sel["per_stratum"],
        total=sel["sample"],
        seed=sel["seed"],
        sub_key="project",
    )
    qids = [p["question_id"] for p in chosen]
    raws = {
        str(r["question_id"]): r["proposal"]
        for r in await _rows(
            conn,
            "SELECT question_id::text AS question_id, proposal FROM librarian_questions"
            " WHERE question_id = ANY(%s::uuid[])",
            (qids,),
        )
    }
    vids = sorted({int(s["clue"][1:].split(".")[0]) for p in chosen for s in p["subjects"]})
    subj = {
        int(r["version_id"]): r
        for r in await _rows(
            conn,
            """
            SELECT mv.version_id, mv.kind, mv.title, mv.body, mv.valid_from,
                   (mv.superseded_at = 'infinity' AND mv.valid_to = 'infinity' AND mv.status = 'active')
                     AS current
              FROM memory_versions mv WHERE mv.version_id = ANY(%s)
            """,
            (vids,),
        )
    }
    units = [e0_unit(p, raws.get(p["question_id"], {}), subj, stratum=p["stratum"]) for p in chosen]
    out_dir = C.packets_dir("E0")
    C.write_json(out_dir / "units.json", {"exp": "E0", "units": units})
    strata = _count_by(p["stratum"] for p in proposals)
    C.write_json(
        out_dir / "manifest.json",
        {
            "exp": "E0",
            "open_proposals_by_stratum": strata,
            "sampled_by_stratum": _count_by(u["hidden"]["stratum"] for u in units),
            "seed": sel["seed"],
            "file": "units.json",
            "sha256": C.sha256_file(out_dir / "units.json"),
            "built_at": datetime.now().astimezone().isoformat(),
        },
    )
    return units


BUILDERS = {"E0": build_e0, "E1": build_e1, "E2": build_e2, "E3": build_e3}


def load_packets(exp: str) -> list[dict[str, Any]]:
    d = C.packets_dir(exp)
    manifest = C.read_json(d / "manifest.json")
    return [C.read_json(d / row["file"]) for row in manifest["packets"]]


__all__ = [
    "BUILDERS",
    "NoteSections",
    "cut",
    "dry_run_note",
    "e0_hiding",
    "e0_stratum",
    "e0_unit",
    "e1_packet",
    "e2_packet",
    "e3_packet",
    "finalize",
    "load_packets",
    "make_source",
    "redact_sources",
    "render_source",
    "render_user",
    "select_e2_projects",
    "split_note",
    "stratified_sample",
]
