"""``hlm review``: LLM-free batch review of the librarian's open questions ("assist now").

The librarian proposes; the owner accepts or rejects in short batches, and that acceptance rate is
the real-usage precision. Nothing here calls a model.

* **Listing** (read-only): the OWNER-ONLY client tool ``hlm.questions`` (oldest first, with the
  subjects' titles, body heads, the proposal's verbatim quotes, reason and pending counts per kind).
  The server dispatches it only with the owner client token (``HLM_OWNER_TOKEN``, sent in the
  ``X-HLM-Owner-Token`` header next to the device bearer). Paging is a keyset cursor: ``--cursor``
  continues strictly after a question, so answering between pages skips or repeats nothing. A server
  without the tool (``unknown tool``) or refusing it (no/wrong owner token) leaves the ``memory.query``
  notices (the newest ≤ 3, no reason or age, no paging: ``--cursor`` is refused), titled through
  ``hlm.export``. Both reads record no access event.
* **Decisions**: ``a`` accept / ``r`` reject call ``memory.answer`` with exactly
  ``{project, request_id, question_id, decision}``. The request id is derived from
  (project, question, decision), so a retry or a re-run is replayed by the server, never applied
  twice. ``s`` skip leaves the question open. ``memory.answer`` has no defer (accept | reject |
  custom). ``o`` opens the full items (``memory.drilldown``; ``hlm.export`` under ``--dry-run``,
  which records nothing). ``q`` quits.
* ``--dry-run`` prints the exact ``memory.answer`` arguments it would send and sends nothing.
* ``--decisions FILE`` (JSON ``{question_id: accept|reject|skip}``) applies a batch after one
  confirmation (``--yes`` skips it).
* The summary prints counts per decision and the acceptance rate per kind; every sent decision
  (and every skip) is appended to a private log (``~/.local/state/hlm/review.log``, 0600) for the
  precision measurement. The log holds ids, kinds and decisions only, never item text.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import textwrap
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hlmemo.cli.mcp_client import ToolCallError

Call = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
Echo = Callable[[str], None]
KeyReader = Callable[[str], str]
Confirm = Callable[[str], bool]

TOOL_LIST = "hlm.questions"
TOOL_ANSWER = "memory.answer"
TOOL_QUERY = "memory.query"
TOOL_DRILLDOWN = "memory.drilldown"
TOOL_EXPORT = "hlm.export"
#: the owner client capability: env on the owner's machine → header on every review request
OWNER_TOKEN_ENV = "HLM_OWNER_TOKEN"
OWNER_HEADER = "X-HLM-Owner-Token"
#: request ids are uuid5(NS_REVIEW, "hlm-review/1:<project>:<question_id>:<decision>")
NS_REVIEW = uuid.UUID("6d3c0f7e-2b8a-5c41-9e57-1f0a4b6c8d21")
SENT = ("accept", "reject")
KEYS = {"a": "accept", "r": "reject", "s": "skip", "o": "open", "q": "quit"}
ALIASES = {"a": "accept", "accept": "accept", "r": "reject", "reject": "reject", "s": "skip", "skip": "skip"}
BATCH_MAX = 50
LIST_BUDGET = 32000
OPEN_BUDGET = 8000
NOTICE_BUDGET = 4000
PROMPT = "   [a]ccept [r]eject [s]kip [o]pen [q]uit > "
KEY_HELP = "   keys: a accept · r reject · s skip (stays open) · o open the full items · q quit"
NO_DEFER = "   memory.answer has no defer (accept | reject | custom); s skips and the question stays open"
LOG_ENV = "HLM_REVIEW_LOG"
#: C0/C1 controls (ESC included), zero-width and bidi overrides: never sent to the terminal
_UNSAFE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")


# --------------------------------------------------------------------------- small helpers
def clean(text: Any) -> str:
    """One terminal-safe line: control/bidi characters dropped, whitespace collapsed."""
    return " ".join(_UNSAFE.sub(" ", str(text if text is not None else "")).split())


def request_id_for(project: str, question_id: str, decision: str) -> str:
    """Deterministic per (project, question, decision): a re-sent decision is a server replay."""
    return str(uuid.uuid5(NS_REVIEW, f"hlm-review/1:{project}:{question_id}:{decision}"))


def answer_args(project: str, question_id: str, decision: str) -> dict[str, Any]:
    """The exact ``memory.answer`` arguments (no note, no budget: the payload hash stays stable)."""
    return {
        "project": project,
        "request_id": request_id_for(project, question_id, decision),
        "question_id": question_id,
        "decision": decision,
    }


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def span(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def age_text(q: dict[str, Any], now: datetime) -> str | None:
    created, expires = _parse_ts(q.get("created_at")), _parse_ts(q.get("expires_at"))
    parts = []
    if created is not None:
        parts.append(f"{span((now - created).total_seconds())} old")
    if expires is not None:
        parts.append(f"expires in {span((expires - now).total_seconds())}")
    return " · ".join(parts) or None


def _unknown_tool(exc: ToolCallError, tool: str) -> bool:
    return exc.code == "E_INVALID_ARG" and (
        exc.details.get("tool") == tool or f"unknown tool {tool!r}" in exc.message
    )


def _fallback_reason(exc: ToolCallError) -> str | None:
    """Why ``hlm.questions`` is not usable here (None: a real error to raise)."""
    if _unknown_tool(exc, TOOL_LIST):
        return "this server has no hlm.questions"
    if exc.code == "E_FORBIDDEN":  # owner-only: E_FORBIDDEN_PROJECT (no read grant) still raises
        return f"the server refused hlm.questions (owner-only: set {OWNER_TOKEN_ENV} to its owner token)"
    return None


def default_log_path() -> Path:
    if os.environ.get(LOG_ENV):
        return Path(os.environ[LOG_ENV]).expanduser()
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base).expanduser() / "hlm" / "review.log"


def append_log(path: Path, entries: list[dict[str, Any]]) -> None:
    """Append JSON lines to the private log: directory 0700, file 0600 (also when it existed)."""
    if not entries:
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        data = "".join(json.dumps(e, sort_keys=True, ensure_ascii=False) + "\n" for e in entries)
        os.write(fd, data.encode("utf-8"))
    finally:
        os.close(fd)


def read_log(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def acceptance(rows: list[tuple[str, str]]) -> dict[str, tuple[int, int]]:
    """``[(kind, decision)]`` → ``{kind: (accepted, accepted + rejected)}`` (skips excluded)."""
    out: dict[str, tuple[int, int]] = {}
    for kind, decision in rows:
        if decision not in SENT:
            continue
        a, n = out.get(kind, (0, 0))
        out[kind] = (a + (decision == "accept"), n + 1)
    return dict(sorted(out.items()))


def rate_line(rates: dict[str, tuple[int, int]]) -> str:
    return " · ".join(f"{k} {a}/{n} ({round(100 * a / n)}%)" for k, (a, n) in rates.items()) or "-"


def logged_acceptance(entries: list[dict[str, Any]]) -> dict[str, tuple[int, int]]:
    """All logged sessions: the LAST accept/reject per (project, question) counts once."""
    last: dict[tuple[str, str], tuple[str, str]] = {}
    for e in entries:
        if e.get("decision") in SENT and e.get("question_id"):
            last[(str(e.get("project")), str(e["question_id"]))] = (str(e.get("kind")), str(e["decision"]))
    return acceptance(list(last.values()))


def load_decisions(path: Path) -> list[tuple[str, str]]:
    """``{question_id: accept|reject|skip}`` (a/r/s accepted) in file order; ValueError otherwise."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"--decisions: cannot read {path}: {exc}") from None
    if not isinstance(data, dict) or not data:
        raise ValueError("--decisions expects a non-empty JSON object {question_id: accept|reject|skip}")
    out: list[tuple[str, str]] = []
    for key, value in data.items():
        try:
            qid = str(uuid.UUID(str(key)))
        except ValueError:
            raise ValueError(f"--decisions: {key!r} is not a full question id (uuid)") from None
        decision = ALIASES.get(str(value).strip().lower()) if isinstance(value, str) else None
        if decision is None:
            raise ValueError(f"--decisions: {key}: decision must be accept|reject|skip, got {value!r}")
        out.append((qid, decision))
    return out


# --------------------------------------------------------------------------- listing
@dataclass
class Listing:
    questions: list[dict[str, Any]]
    pending_total: int | None = None
    by_kind: dict[str, int] | None = None
    omitted: int = 0
    #: keyset cursor after the last returned question while more remain (``hlm.questions``)
    next_cursor: str | None = None
    #: ``hlm.questions`` unusable (old server / no owner token): the memory.query notices (newest
    #: ≤ 3, no reason/age, no paging); ``degraded_reason`` says why
    degraded: bool = False
    degraded_reason: str | None = None


async def fetch_listing(
    call: Call,
    project: str,
    *,
    limit: int,
    kind: str | None = None,
    question_ids: list[str] | None = None,
    cursor: str | None = None,
) -> Listing:
    if question_ids:  # chunks of BATCH_MAX ids; the pending counts are the project's either way
        merged = Listing([])
        for i in range(0, len(question_ids), BATCH_MAX):
            chunk = question_ids[i : i + BATCH_MAX]
            args = {
                "project": project,
                "limit": BATCH_MAX,
                "question_ids": chunk,
                "token_budget": LIST_BUDGET,
            }
            part = await _list_call(call, project, args, limit=BATCH_MAX, kind=None)
            part.questions = merged.questions + part.questions
            merged = part
        return merged
    args: dict[str, Any] = {"project": project, "limit": limit, "token_budget": LIST_BUDGET}
    if kind:
        args["kind"] = kind
    if cursor:
        args["cursor"] = cursor
    return await _list_call(call, project, args, limit=limit, kind=kind)


async def _list_call(
    call: Call, project: str, args: dict[str, Any], *, limit: int, kind: str | None
) -> Listing:
    try:
        res = await call(TOOL_LIST, args)
    except ToolCallError as exc:
        reason = _fallback_reason(exc)
        if reason is None:
            raise
        if args.get("cursor"):  # the notices cannot page: never silently restart at the newest
            raise ToolCallError(
                "E_INVALID_ARG", f"--cursor needs hlm.questions, but {reason}; the notices cannot page"
            ) from None
        listing = await notices_listing(
            call, project, limit=limit, kind=kind, question_ids=args.get("question_ids")
        )
        listing.degraded_reason = reason
        return listing
    pending = res.get("pending") or {}
    return Listing(
        questions=list(res.get("questions") or []),
        pending_total=pending.get("total"),
        by_kind=pending.get("by_kind"),
        omitted=int(res.get("omitted") or 0),
        next_cursor=res.get("next_cursor") or None,
    )


async def notices_listing(
    call: Call, project: str, *, limit: int, kind: str | None = None, question_ids: list[str] | None = None
) -> Listing:
    """Fallback for a server without ``hlm.questions``: the ``librarian`` block of memory.query
    (a 1970 ``valid_at`` leaves no hits, so the notices get the budget), titled and headed through
    ``hlm.export`` (manifest + full view of the subjects; export records no access event)."""
    from hlmemo.importers.runner import fetch_full, fetch_items

    res = await call(
        TOOL_QUERY,
        {
            "project": project,
            "query": "librarian review",
            "token_budget": NOTICE_BUDGET,
            "valid_at": "1970-01-01T00:00:00Z",
        },
    )
    block = res.get("librarian") or {}
    notices = [
        n
        for n in block.get("notices") or []
        if (kind is None or n.get("kind") == kind)
        and (not question_ids or n.get("question_id") in question_ids)
    ][:limit]
    by_vid: dict[int, dict[str, Any]] = {}
    if notices:
        manifest, _ = await fetch_items(call, project)
        by_vid = {int(it["version_id"]): it for it in manifest}
        wanted = sorted(
            {
                by_vid[v]["logical_id"]
                for n in notices
                for c in n.get("clues") or []
                if (v := _vid(c)) is not None and v in by_vid
            }
        )
        if wanted:
            for it in await fetch_full(call, project, wanted):
                if int(it["version_id"]) in by_vid:
                    by_vid[int(it["version_id"])]["body"] = it.get("body") or ""
    questions = []
    for n in notices:
        subjects = []
        for c in n.get("clues") or []:
            it = by_vid.get(_vid(c) or -1)
            subjects.append(
                {"clue": c}
                if it is None
                else {
                    "clue": c,
                    "logical_id": it["logical_id"],
                    "kind": it.get("kind"),
                    "title": it.get("title"),
                    "valid_from": it.get("valid_from"),
                    "head": clean(it.get("body", ""))[:240],
                }
            )
        questions.append(
            {
                "question_id": n["question_id"],
                "kind": n.get("kind"),
                "subjects": subjects,
                "notice": n.get("text"),
            }
        )
    return Listing(questions=questions, pending_total=block.get("pending_questions", 0), degraded=True)


def _vid(clue: Any) -> int | None:
    m = re.fullmatch(r"v(\d+)(?:\.\d+)?", str(clue))
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- card rendering
def _wrap(label: str, text: str, width: int, *, indent: int = 3, col: int = 10) -> list[str]:
    head = " " * indent + label.ljust(col)
    return textwrap.wrap(text, width=width, initial_indent=head, subsequent_indent=" " * (indent + col)) or [
        head.rstrip()
    ]


def _subject_line(s: dict[str, Any]) -> str:
    bits = [clean(s.get("clue", "?"))]
    if s.get("kind"):
        bits.append(f"[{clean(s['kind'])}]")
    bits.append(clean(s.get("title")) or "(title not visible)")
    meta = []
    if s.get("projects"):
        meta.append(", ".join(clean(p) for p in s["projects"]))
    if s.get("valid_from"):
        meta.append(f"valid from {str(s['valid_from'])[:10]}")
    if s.get("current") is False:
        meta.append("no longer current")
    return " ".join(bits) + ("  · " + " · ".join(meta) if meta else "")


def proposed_text(q: dict[str, Any]) -> str:
    parts = []
    for a in q.get("actions") or []:
        op = a.get("op")
        if op == "link_insert":
            src, dst, rel = a.get("src") or "?", a.get("dst") or "?", a.get("rel") or "link"
            if rel == "supersedes":
                parts.append(f"{src} supersedes{' part of' if a.get('scope') == 'part' else ''} {dst}")
            elif rel == "relates_to":
                what = "duplicate" if a.get("dup") else a.get("relation")
                parts.append(f"{src} relates_to {dst}" + (f" ({what})" if what else ""))
            else:
                parts.append(f"{src} {rel} {dst}")
        elif op == "version_close":
            until = f" (valid until {str(a.get('valid_to'))[:10]})" if a.get("valid_to") else ""
            parts.append(f"close {a.get('target') or '?'}{until}")
        elif op == "widen_scope":
            parts.append(f"also show {a.get('target') or '?'} in {', '.join(a.get('add_projects') or ['?'])}")
        elif op:
            parts.append(str(op))
    if q.get("resolution"):
        parts.append(f"({q['resolution']}: no close)")
    return clean(" · ".join(parts))


def render_card(q: dict[str, Any], index: int, total: int, *, now: datetime, width: int = 100) -> str:
    kind = clean(q.get("kind") or "?")
    head = [f"━━ {index}/{total} · {kind}"]
    if q.get("relation") and (kind, q["relation"]) != ("contradiction", "contradicts"):
        head.append(clean(q["relation"]))
    for key in ("confidence", "tier"):
        if q.get(key):
            head.append(f"{'conf' if key == 'confidence' else key} {clean(q[key])}")
    if age := age_text(q, now):
        head.append(age)
    lines = [" · ".join(head), f"   id {clean(q.get('question_id'))}"]
    subjects = list(q.get("subjects") or [])
    quotes = q.get("quotes") or {}
    side_quote = {0: quotes.get("new"), 1: quotes.get("old")}
    sup = q.get("supersedes")
    replaced = next(
        (a.get("quote") for a in q.get("actions") or [] if a.get("rel") == "supersedes" and a.get("quote")),
        None,
    )
    if sup in ("new", "old") and len(subjects) == 2:
        newer = 0 if sup == "new" else 1
        rows = [
            (newer, "newer", side_quote[newer], ""),
            (1 - newer, "older", replaced or side_quote[1 - newer], "outdated span: " if replaced else ""),
        ]
    else:
        rows = [
            (i, chr(ord("A") + i) if i < 26 else str(i), side_quote.get(i), "") for i in range(len(subjects))
        ]
    unverified = "quote_unverified" in (q.get("flags") or [])
    for i, label, quote, tag in rows:
        s = subjects[i]
        lines += _wrap(label, _subject_line(s), width)
        if quote:
            note = " (quote not verified)" if unverified else ""
            lines += _wrap("", f'» {tag}"{clean(quote)}"{note}', width)
        elif s.get("head"):
            lines += _wrap("", f'» starts: "{clean(s["head"])}"', width)
    if proposed := proposed_text(q):
        lines += _wrap("proposed", proposed, width)
    if q.get("notice"):
        lines += _wrap("notice", clean(q["notice"]), width)
    if q.get("reason"):
        lines += _wrap("reason", clean(q["reason"]), width)
    flags = [clean(f) for f in q.get("flags") or []]
    if q.get("verified") is not None:
        flags.append("verifier agreed" if q["verified"] else "verifier disagreed")
    if q.get("cross_project"):
        flags.append("cross-project")
    if flags:
        lines += _wrap("flags", " · ".join(flags), width)
    return "\n".join(lines)


def header_text(project: str, listing: Listing, opts: ReviewOptions) -> str:
    total = listing.pending_total
    if listing.degraded:
        line = (
            f"{project}: {total if total is not None else '?'} open question(s); "
            f"{listing.degraded_reason or 'this server has no hlm.questions'}, so only the newest ≤3 "
            "notices are shown (no reason, quotes, age or paging)"
        )
    else:
        kinds = " · ".join(f"{k} {n}" for k, n in sorted((listing.by_kind or {}).items()))
        line = f"{project}: {total if total is not None else '?'} open question(s)" + (
            f" ({kinds})" if kinds else ""
        )
        line += f"; this batch: {len(listing.questions)}, oldest first"
        if opts.kind:
            line += f", kind={opts.kind}"
        if opts.cursor:
            line += ", continuing after the given cursor"
        if listing.omitted:
            line += f"; {listing.omitted} more did not fit one listing (use a smaller --batch)"
    if opts.dry_run:
        line += "\nDRY-RUN: nothing is sent; the memory.answer arguments are printed instead"
    return line


def describe_ack(ack: dict[str, Any]) -> str:
    status = ack.get("status")
    text = {
        "accepted_pending": "recorded as accepted_pending (librarian is observer: nothing applied yet)",
        "applied": "applied",
        "rejected": "rejected",
        "superseded": "superseded: a subject changed since the proposal; nothing applied",
        "authority_lost": "authority_lost: the cross-project policy now forbids it; nothing applied",
        "answered": "answered",
    }.get(str(status), str(status))
    applied = ack.get("applied") or {}
    if status == "applied" and applied:
        text += (
            f" (links {applied.get('links', 0)}, closed {applied.get('closed') or []},"
            f" widened {applied.get('widened') or []})"
        )
    if ack.get("rule"):
        text += f"; rule {ack['rule']}"
    if ack.get("replayed"):
        text += " [replayed: this exact answer was already recorded]"
    return text


# --------------------------------------------------------------------------- session
@dataclass
class ReviewOptions:
    project: str
    batch: int = 10
    kind: str | None = None
    #: ``--cursor``: continue strictly after that question (keyset; same project and kind)
    cursor: str | None = None
    dry_run: bool = False
    #: ``--decisions``: [(question_id, accept|reject|skip)] in file order
    decisions: list[tuple[str, str]] | None = None
    yes: bool = False
    width: int | None = None
    log_path: Path | None = None


@dataclass
class Session:
    project: str
    dry_run: bool = False
    rows: list[dict[str, Any]] = field(default_factory=list)
    log: list[dict[str, Any]] = field(default_factory=list)
    aborted: bool = False
    pending_before: int | None = None
    kind: str | None = None
    #: the cursor of the last reviewed question, when a plain re-run would show questions this
    #: session left open (skipped / dry-run / refused) before the ones after it
    resume: str | None = None

    def counts(self) -> Counter[str]:
        c: Counter[str] = Counter()
        for r in self.rows:
            c["error" if r.get("error") else r["decision"]] += 1
            if r.get("replayed"):
                c["replayed"] += 1
        return c

    def rates(self) -> dict[str, tuple[int, int]]:
        return acceptance([(r["kind"], r["decision"]) for r in self.rows if not r.get("error")])

    @property
    def errors(self) -> int:
        return sum(1 for r in self.rows if r.get("error"))


async def send_answer(call: Call, args: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """``memory.answer``; one retry with the SAME arguments on a retryable transport failure (the
    request id makes it a replay if the first attempt committed). Returns ``(ack, retried)``."""
    try:
        return await call(TOOL_ANSWER, args), False
    except ToolCallError as exc:
        if not (exc.retryable and exc.code == "E_UNAVAILABLE"):
            raise
    return await call(TOOL_ANSWER, args), True


def _log_entry(
    project: str, q: dict[str, Any], decision: str, status: str, request_id: str | None, now: datetime
) -> dict[str, Any]:
    created = _parse_ts(q.get("created_at"))
    entry: dict[str, Any] = {
        "ts": now.isoformat(timespec="seconds"),
        "project": project,
        "question_id": q.get("question_id"),
        "kind": q.get("kind") or "?",
        "decision": decision,
        "status": status,
    }
    for key in ("relation", "supersedes", "scope", "confidence", "tier"):
        if q.get(key) is not None:
            entry[key] = q[key]
    if request_id:
        entry["request_id"] = request_id
    if created is not None:
        entry["age_days"] = round((now - created).total_seconds() / 86400, 1)
    return entry


async def decide(
    call: Call, session: Session, q: dict[str, Any], decision: str, *, echo: Echo, now: datetime
) -> None:
    qid, kind = str(q.get("question_id")), q.get("kind") or "?"
    row: dict[str, Any] = {"question_id": qid, "kind": kind, "decision": decision}
    session.rows.append(row)
    if decision == "skip":
        row["status"] = "skipped"
        echo("   → skipped (stays open)")
        if not session.dry_run:
            session.log.append(_log_entry(session.project, q, "skip", "skipped", None, now))
        return
    args = answer_args(session.project, qid, decision)
    if session.dry_run:
        row["status"] = "dry_run"
        echo(f"   → DRY-RUN would send {TOOL_ANSWER} {json.dumps(args, sort_keys=True)}")
        return
    try:
        ack, retried = await send_answer(call, args)
    except ToolCallError as exc:
        row.update(status="error", error=exc.code)
        echo(f"   → error {exc.code}: {clean(exc.message)}" + (f" {exc.details}" if exc.details else ""))
        return
    row.update(status=ack.get("status"), replayed=bool(ack.get("replayed")))
    echo(f"   → {describe_ack(ack)}")
    if not ack.get("replayed") or retried:  # a replay of an earlier session was logged then
        session.log.append(
            _log_entry(session.project, q, decision, str(ack.get("status")), args["request_id"], now)
        )


async def open_items(
    call: Call, project: str, q: dict[str, Any], *, dry_run: bool, echo: Echo, width: int
) -> None:
    """The full subjects: memory.drilldown (records the owner's access) or, under --dry-run,
    hlm.export (records nothing)."""
    subjects = [s for s in q.get("subjects") or [] if s.get("clue")]
    try:
        if dry_run:
            from hlmemo.importers.runner import fetch_full

            lids = sorted({int(s["logical_id"]) for s in subjects if s.get("logical_id")})
            got = {
                int(it["version_id"]): it for it in (await fetch_full(call, project, lids) if lids else [])
            }
            items = []
            for s in subjects:
                it = got.get(_vid(s["clue"]) or -1)
                items.append(
                    {"clue": s["clue"], "kind": s.get("kind"), "title": s.get("title"), "text": it["body"]}
                    if it
                    else {"clue": s["clue"], "title": s.get("title"), "text": "(not live in hlm.export)"}
                )
            more = False
        else:
            res = await call(
                TOOL_DRILLDOWN,
                {"project": project, "clue_ids": [s["clue"] for s in subjects], "token_budget": OPEN_BUDGET},
            )
            items, more = list(res.get("items") or []), bool(res.get("next_cursor"))
    except ToolCallError as exc:
        echo(f"   open failed: {exc.code}: {clean(exc.message)}")
        return
    for it in items:
        echo(f"   ┌ {clean(it.get('clue'))} [{clean(it.get('kind') or '?')}] {clean(it.get('title'))}")
        for para in _UNSAFE.sub(" ", str(it.get("text") or "")).splitlines():
            for line in textwrap.wrap(
                para, width=width, initial_indent="   │ ", subsequent_indent="   │ "
            ) or ["   │"]:
                echo(line)
        links = [f"{clean(ln.get('rel'))} {clean(ln.get('clue'))}" for ln in it.get("links") or []]
        if links:
            echo("   └ links: " + ", ".join(links))
    if more:
        echo(f"   (cut at {OPEN_BUDGET} tokens; hlm query/drilldown for the rest)")


def summary_text(session: Session, *, log_path: Path | None, pending_after: int | None) -> str:
    c = session.counts()
    tag = " [dry-run: nothing sent]" if session.dry_run else ""
    lines = [
        f"summary: accept {c['accept']} · reject {c['reject']} · skip {c['skip']} · errors {c['error']}"
        + (f" · replayed {c['replayed']}" if c["replayed"] else "")
        + tag,
        f"acceptance by kind (this session): {rate_line(session.rates())}",
    ]
    if log_path is not None and not session.dry_run:
        lines.append(
            f"acceptance by kind (all logged sessions): {rate_line(logged_acceptance(read_log(log_path)))}"
            f"  [{log_path}, +{len(session.log)}]"
        )
    if pending_after is not None:
        lines.append(
            f"open questions now: {pending_after}"
            + (f" (was {session.pending_before})" if session.pending_before is not None else "")
        )
    if session.resume:
        kind = f" --kind {session.kind}" if session.kind else ""
        lines.append(
            "continue after the last reviewed question: "
            f"hlm review --project {session.project}{kind} --cursor {session.resume}"
        )
    return "\n".join(lines)


async def run_review(
    call: Call,
    opts: ReviewOptions,
    *,
    read_key: KeyReader,
    confirm: Confirm,
    echo: Echo,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Session:
    width = opts.width or max(60, min(110, shutil.get_terminal_size((100, 20)).columns))
    session = Session(project=opts.project, dry_run=opts.dry_run, kind=opts.kind)
    log_path = opts.log_path or default_log_path()
    if opts.decisions is not None:
        await _apply_file(call, opts, session, confirm=confirm, echo=echo, now=now, width=width)
    else:
        await _interactive(call, opts, session, read_key=read_key, echo=echo, now=now, width=width)
    if not session.dry_run:
        append_log(log_path, session.log)
    pending_after = None
    if session.rows and not session.dry_run and not session.aborted:
        try:
            pending_after = (await fetch_listing(call, opts.project, limit=1)).pending_total
        except ToolCallError:
            pending_after = None
    echo(summary_text(session, log_path=None if session.dry_run else log_path, pending_after=pending_after))
    return session


async def _interactive(
    call: Call,
    opts: ReviewOptions,
    session: Session,
    *,
    read_key: KeyReader,
    echo: Echo,
    now: Callable[[], datetime],
    width: int,
) -> None:
    listing = await fetch_listing(call, opts.project, limit=opts.batch, kind=opts.kind, cursor=opts.cursor)
    session.pending_before = listing.pending_total
    echo(header_text(opts.project, listing, opts))
    if not listing.questions:
        echo("nothing to review")
        return
    handled = await _interactive_cards(
        call, opts, session, listing, read_key=read_key, echo=echo, now=now, width=width
    )
    _set_resume(session, listing, handled)


def _set_resume(session: Session, listing: Listing, handled: int) -> None:
    """A cursor hint only when it helps: this session left some reviewed question open (skip,
    dry-run, refused) AND something comes after the last reviewed one (rest of the batch, the next
    page); a plain re-run would show the left-open ones first."""
    if listing.degraded or not handled:
        return
    left_open = any(r.get("status") in ("skipped", "dry_run", "error") for r in session.rows)
    more_after = handled < len(listing.questions) or listing.next_cursor is not None
    cursor = listing.questions[handled - 1].get("cursor")
    if left_open and more_after and cursor:
        session.resume = str(cursor)


async def _interactive_cards(
    call: Call,
    opts: ReviewOptions,
    session: Session,
    listing: Listing,
    *,
    read_key: KeyReader,
    echo: Echo,
    now: Callable[[], datetime],
    width: int,
) -> int:
    """The cards of one listing; returns how many were decided or skipped."""
    n = len(listing.questions)
    for i, q in enumerate(listing.questions, 1):
        echo("")
        echo(render_card(q, i, n, now=now(), width=width))
        while True:
            try:
                key = (read_key(PROMPT) or "").strip().lower()[:1]
            except (KeyboardInterrupt, EOFError):
                key = "q"
            action = KEYS.get(key)
            if action == "open":
                await open_items(call, opts.project, q, dry_run=opts.dry_run, echo=echo, width=width)
                continue
            if action is None:
                echo(NO_DEFER if key == "d" else KEY_HELP)
                continue
            break
        if action == "quit":
            echo(f"   quit; {n - i + 1} question(s) of this batch left open")
            return i - 1
        await decide(call, session, q, action, echo=echo, now=now())
    return n


async def _apply_file(
    call: Call,
    opts: ReviewOptions,
    session: Session,
    *,
    confirm: Confirm,
    echo: Echo,
    now: Callable[[], datetime],
    width: int,
) -> None:
    decisions = opts.decisions or []
    sent_ids = [qid for qid, d in decisions if d in SENT]
    listing = (
        await fetch_listing(call, opts.project, limit=BATCH_MAX, question_ids=[qid for qid, _ in decisions])
        if decisions
        else Listing([])
    )
    session.pending_before = listing.pending_total
    by_id = {q["question_id"]: q for q in listing.questions}
    tally = Counter(d for _, d in decisions)
    echo(
        f"{opts.project}: {len(decisions)} decision(s) from file: accept {tally['accept']} · reject "
        f"{tally['reject']} · skip {tally['skip']}" + (" [DRY-RUN: nothing is sent]" if opts.dry_run else "")
    )
    for qid, d in decisions:
        q = by_id.get(qid)
        if q is None:
            note = "sent anyway; the server replays or refuses" if d in SENT else "nothing to send"
            echo(f"   {d:<6} {qid}  (not open/visible here: {note})")
        else:
            titles = " / ".join(clean(s.get("title") or s.get("clue")) for s in q.get("subjects") or [])
            echo(clean_line(f"   {d:<6} {qid}  {q.get('kind')}  {titles}", width))
    if sent_ids and not opts.dry_run and not opts.yes:
        if not confirm(f"send {len(sent_ids)} answer(s) to {opts.project}?"):
            session.aborted = True
            echo("aborted; nothing sent")
            return
    for qid, d in decisions:
        q = by_id.get(qid) or {"question_id": qid, "kind": "?"}
        echo(clean_line(f"── {qid} {q.get('kind')} {d}", width))
        await decide(call, session, q, d, echo=echo, now=now())


def clean_line(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


# --------------------------------------------------------------------------- terminal I/O
def tty_read_key(prompt: str) -> str:
    """One keypress on a terminal (cbreak; Ctrl-C/Ctrl-D quit), else one line from stdin."""
    sys.stdout.write(prompt)
    sys.stdout.flush()
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        if not line:
            raise EOFError
        sys.stdout.write(clean(line)[:1] + "\n")  # piped input is not echoed
        return line.strip()
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    sys.stdout.write((ch if ch.isprintable() else "") + "\n")
    return "q" if ch in ("\x03", "\x04", "") else ch


__all__ = [
    "KEYS",
    "NS_REVIEW",
    "Listing",
    "ReviewOptions",
    "Session",
    "answer_args",
    "append_log",
    "decide",
    "default_log_path",
    "fetch_listing",
    "load_decisions",
    "logged_acceptance",
    "notices_listing",
    "render_card",
    "request_id_for",
    "run_review",
    "summary_text",
    "tty_read_key",
]
