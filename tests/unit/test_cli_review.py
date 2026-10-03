"""``hlm review`` (LLM-free batch review of librarian questions) against a fake server: card
rendering, key handling, dry-run, idempotent request ids, retries, the decisions file, the
notices fallback, the private log and the summary statistics."""

from __future__ import annotations

import json
import stat
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from test_cli_support import _clean_tables, _isolated_home, runner, write_toml  # noqa: F401

from hlmemo.cli import hlm as hlm_mod
from hlmemo.cli.client_config import EX_USAGE
from hlmemo.cli.hlm import app
from hlmemo.cli.mcp_client import ToolCallError
from hlmemo.cli.review import (
    NS_REVIEW,
    ReviewOptions,
    Session,
    acceptance,
    answer_args,
    append_log,
    fetch_listing,
    load_decisions,
    logged_acceptance,
    read_log,
    render_card,
    request_id_for,
    run_review,
    summary_text,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
P = "hlmemo"
QC, QL, QW = (str(uuid.UUID(int=i, version=4)) for i in (1, 2, 3))


def contradiction(qid: str = QC) -> dict[str, Any]:
    return {
        "question_id": qid,
        "kind": "contradiction",
        "created_at": "2026-09-28T12:00:00.000000Z",
        "expires_at": "2026-10-28T12:00:00.000000Z",
        "subjects": [
            {
                "clue": "v812",
                "logical_id": 81,
                "kind": "fact",
                "title": "Deploy host moved",
                "valid_from": "2026-06-01T00:00:00.000000Z",
                "projects": ["hlmemo"],
                "current": True,
                "head": "Production moved to the Hostinger KVM 2 host.",
            },
            {
                "clue": "v455",
                "logical_id": 45,
                "kind": "fact",
                "title": "Deploy \x1b[31mhost‮",
                "valid_from": "2026-01-01T00:00:00.000000Z",
                "projects": ["hlmemo"],
                "current": False,
                "head": "Production runs on the Hetzner CX33 host.",
            },
        ],
        "relation": "contradicts",
        "supersedes": "new",
        "scope": "part",
        "resolution": "split_and_supersede",
        "confidence": "high",
        "tier": "question",
        "verified": True,
        "flags": [],
        "reason": "The newer item moves production\nto Hostinger.",
        "quotes": {"new": "Production moved to the Hostinger KVM 2 host", "old": "runs on the Hetzner CX33"},
        "actions": [
            {"op": "link_insert", "rel": "contradicts", "src": "v812", "dst": "v455"},
            {
                "op": "link_insert",
                "rel": "supersedes",
                "src": "v812",
                "dst": "v455",
                "scope": "part",
                "quote": "Production runs on the Hetzner CX33 host",
            },
        ],
    }


def link(qid: str = QL) -> dict[str, Any]:
    return {
        "question_id": qid,
        "kind": "link",
        "created_at": "2026-10-02T09:00:00.000000Z",
        "subjects": [
            {
                "clue": "v20",
                "logical_id": 20,
                "kind": "lesson",
                "title": "Pool rule",
                "head": "Keep pools small.",
            },
            {
                "clue": "v21",
                "logical_id": 21,
                "kind": "lesson",
                "title": "Pool note",
                "head": "Pools stay small.",
            },
        ],
        "relation": "duplicate",
        "confidence": "high",
        "tier": "action",
        "actions": [{"op": "link_insert", "rel": "relates_to", "src": "v20", "dst": "v21", "dup": True}],
    }


def widen(qid: str = QW) -> dict[str, Any]:
    return {
        "question_id": qid,
        "kind": "widen_scope",
        "created_at": "2026-10-01T12:00:00.000000Z",
        "subjects": [
            {
                "clue": "v30",
                "logical_id": 30,
                "kind": "lesson",
                "title": "ssh heredoc",
                "projects": ["hlmemo"],
            },
            {"clue": "v31", "logical_id": 31, "kind": "lesson", "title": "Never pipe", "projects": ["other"]},
        ],
        "relation": "refines",
        "cross_project": True,
        "quotes": {"new": "bash -s over ssh", "old": "bash -s with an ssh heredoc"},
        "flags": ["quote_unverified"],
        "actions": [{"op": "widen_scope", "target": "v31", "add_projects": ["hlmemo"]}],
    }


class FakeServer:
    """The server side of the review protocol: ``hlm.questions`` (or ``unknown tool``, or the
    owner-only refusal), ``memory.answer`` with per-request-id idempotency, drilldown, export and
    the query notices. The listing order is the insertion order (the server's (created_at,
    question_id)); paging is a keyset over that order, like the server's."""

    def __init__(
        self, questions: list[dict[str, Any]], *, has_list: bool = True, owner_ok: bool = True
    ) -> None:
        self.questions = {q["question_id"]: q for q in questions}
        self.position = {qid: i for i, qid in enumerate(self.questions)}
        self.status = {qid: "open" for qid in self.questions}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.answers: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        self.has_list = has_list
        self.owner_ok = owner_ok
        self.timeout_after_commit = 0  # the next N answers commit, then the reply is lost

    def cursor(self, project: str, kind: str | None, qid: str) -> str:
        return f"{project}/{kind or '*'}/{self.position[qid]:06d}/{qid}"

    def tools(self) -> list[str]:
        return [t for t, _ in self.calls]

    def answer_calls(self) -> list[dict[str, Any]]:
        return [a for t, a in self.calls if t == "memory.answer"]

    async def __call__(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, json.loads(json.dumps(args))))
        open_qs = [q for qid, q in self.questions.items() if self.status[qid] == "open"]
        if tool == "hlm.questions":
            if not self.has_list:
                raise ToolCallError("E_INVALID_ARG", f"unknown tool {tool!r}", details={"tool": tool})
            if not self.owner_ok:
                raise ToolCallError("E_FORBIDDEN", "hlm.questions is an owner-only client tool")
            kind = args.get("kind")
            after = -1
            if args.get("cursor"):
                p_, k_, pos, _qid = args["cursor"].split("/")
                if (p_, k_) != (args["project"], kind or "*"):
                    raise ToolCallError("E_INVALID_CURSOR", "cursor belongs to another listing")
                after = int(pos)
            sel = [
                {**q, "cursor": self.cursor(args["project"], kind, q["question_id"])}
                for q in open_qs
                if (not kind or q["kind"] == kind)
                and (not args.get("question_ids") or q["question_id"] in args["question_ids"])
                and self.position[q["question_id"]] > after
            ]
            page = sel[: args["limit"]]
            return {
                "project": args["project"],
                "pending": {"total": len(open_qs), "by_kind": dict(Counter(q["kind"] for q in open_qs))},
                "questions": page,
                "omitted": 0,
                "next_cursor": page[-1]["cursor"] if len(sel) > len(page) else None,
            }
        if tool == "memory.answer":
            rid = args["request_id"]
            if rid in self.answers:
                prev, ack = self.answers[rid]
                if prev != args:
                    raise ToolCallError("E_REQUEST_ID_CONFLICT", "request_id was already used")
                return {**ack, "replayed": True}
            qid = args["question_id"]
            if qid not in self.status:
                raise ToolCallError("E_NOT_FOUND", "question not found")
            if self.status[qid] != "open":
                raise ToolCallError(
                    "E_VERSION_CONFLICT",
                    f"the question is {self.status[qid]}",
                    details={"status": self.status[qid]},
                )
            status = {"accept": "accepted_pending", "reject": "rejected"}[args["decision"]]
            self.status[qid] = status
            ack = {
                "request_id": rid,
                "question_id": qid,
                "status": status,
                "role": "observer",
                "applied": {"links": 0, "closed": [], "widened": []},
                "rule": "v99",
                "replayed": False,
            }
            self.answers[rid] = (dict(args), ack)
            if self.timeout_after_commit:
                self.timeout_after_commit -= 1
                raise ToolCallError("E_UNAVAILABLE", "timeout waiting for the memory server", retryable=True)
            return ack
        if tool == "memory.drilldown":
            items = [
                {"clue": c, "kind": "fact", "title": f"T{c}", "text": f"full {c}\nline two", "links": []}
                for c in args["clue_ids"]
            ]
            items[0]["links"] = [{"rel": "relates_to", "clue": "v9", "stale": False}]
            return {"items": items, "next_cursor": None}
        if tool == "hlm.export":
            rows = [
                {"logical_id": 81, "version_id": 812, "kind": "fact", "title": "Deploy host moved"},
                {"logical_id": 45, "version_id": 455, "kind": "fact", "title": "Deploy host"},
            ]
            if args.get("view") == "full":
                rows = [
                    {**r, "body": f"body of {r['title']}"}
                    for r in rows
                    if r["logical_id"] in args["logical_ids"]
                ]
            return {"items": rows, "next_cursor": None}
        if tool == "memory.query":
            return {
                "hits": [],
                "librarian": {
                    "pending_questions": 7,
                    "notices": [
                        {
                            "question_id": QC,
                            "kind": "contradiction",
                            "clues": ["v812", "v455"],
                            "text": "contradiction: v812 vs v455; proposed: v812 supersedes v455",
                        }
                    ],
                },
            }
        raise AssertionError(f"unexpected tool {tool}")


def keys(*seq: str):  # noqa: ANN201
    it = iter(seq)

    def read(_prompt: str) -> str:
        return next(it)

    return read


def never(prompt: str) -> Any:
    raise AssertionError(f"unexpected prompt {prompt!r}")


async def review(
    server: FakeServer, opts: ReviewOptions, *, read_key=never, confirm=never
) -> tuple[Session, str]:  # noqa: ANN001
    out: list[str] = []
    session = await run_review(
        server, opts, read_key=read_key, confirm=confirm, echo=out.append, now=lambda: NOW
    )
    return session, "\n".join(out)


# --------------------------------------------------------------------------- card rendering
def test_supersession_card_shows_newer_and_older_spans_with_handles() -> None:
    card = render_card(contradiction(), 1, 10, now=NOW, width=100)
    lines = card.splitlines()
    assert lines[0] == "━━ 1/10 · contradiction · conf high · tier question · 4d old · expires in 26d"
    assert lines[1] == f"   id {QC}"
    assert lines[2].startswith("   newer     v812 [fact] Deploy host moved  · hlmemo · valid from 2026-06-01")
    assert '» "Production moved to the Hostinger KVM 2 host"' in lines[3]
    assert (
        lines[4].startswith("   older     v455 [fact] Deploy [31mhost  ·") and "no longer current" in lines[4]
    )
    assert (
        '» outdated span: "Production runs on the Hetzner CX33 host"' in lines[5]
    )  # the link's verified span
    assert (
        "proposed  v812 contradicts v455 · v812 supersedes part of v455 · (split_and_supersede: no close)"
        in card
    )
    assert "reason    The newer item moves production to Hostinger." in card
    assert "flags     verifier agreed" in card
    assert "\x1b" not in card and "‮" not in card  # terminal escapes and bidi overrides never printed


def test_supersedes_old_swaps_the_sides() -> None:
    q = contradiction()
    q["supersedes"] = "old"
    q["actions"][1].update(src="v455", dst="v812", quote="")
    card = render_card(q, 1, 1, now=NOW)
    newer = next(line for line in card.splitlines() if "newer" in line)
    older_i = next(i for i, line in enumerate(card.splitlines()) if "older" in line)
    assert "v455" in newer
    assert (
        '» "Production moved to the Hostinger KVM 2 host"' in card.splitlines()[older_i + 1]
    )  # no span: side quote


def test_link_and_widen_cards_fall_back_to_body_heads_and_flag_unverified_quotes() -> None:
    card = render_card(link(), 2, 3, now=NOW, width=90)
    assert card.splitlines()[0] == "━━ 2/3 · link · duplicate · conf high · tier action · 3h old"
    assert "   A         v20 [lesson] Pool rule" in card and '» starts: "Keep pools small."' in card
    assert "proposed  v20 relates_to v21 (duplicate)" in card
    card = render_card(widen(), 3, 3, now=NOW, width=90)
    assert "proposed  also show v31 in hlmemo" in card
    assert '» "bash -s over ssh" (quote not verified)' in card
    assert "flags     quote_unverified · cross-project" in card


def test_long_text_wraps_under_its_label() -> None:
    q = contradiction()
    q["reason"] = "word " * 60
    lines = render_card(q, 1, 1, now=NOW, width=60).splitlines()
    reason = [line for line in lines if line.startswith("   reason") or line.startswith(" " * 13 + "word")]
    assert len(reason) > 2 and all(len(line) <= 60 for line in reason)


# --------------------------------------------------------------------------- keys
async def test_keys_accept_reject_skip_open_and_help(tmp_path: Path) -> None:
    server = FakeServer([contradiction(), link(), widen()])
    log = tmp_path / "review.log"
    session, out = await review(
        server,
        ReviewOptions(project=P, log_path=log, width=100),
        read_key=keys("x", "d", "o", "a", "R", "s"),
    )
    assert server.answer_calls() == [answer_args(P, QC, "accept"), answer_args(P, QL, "reject")]
    assert answer_args(P, QC, "accept") == {
        "project": P,
        "request_id": request_id_for(P, QC, "accept"),
        "question_id": QC,
        "decision": "accept",
    }
    assert (
        "memory.drilldown",
        {"project": P, "clue_ids": ["v812", "v455"], "token_budget": 8000},
    ) in server.calls
    assert "keys: a accept" in out and "memory.answer has no defer" in out
    assert "┌ v812 [fact] Tv812" in out and "│ line two" in out and "└ links: relates_to v9" in out
    assert "→ recorded as accepted_pending (librarian is observer: nothing applied yet); rule v99" in out
    assert "→ rejected; rule v99" in out and "→ skipped (stays open)" in out
    assert [(r["kind"], r["decision"], r["status"]) for r in session.rows] == [
        ("contradiction", "accept", "accepted_pending"),
        ("link", "reject", "rejected"),
        ("widen_scope", "skip", "skipped"),
    ]
    assert [(e["question_id"], e["decision"]) for e in read_log(log)] == [
        (QC, "accept"),
        (QL, "reject"),
        (QW, "skip"),
    ]
    assert "open questions now: 1 (was 3)" in out
    assert out.startswith(
        f"{P}: 3 open question(s) (contradiction 1 · link 1 · widen_scope 1); this batch: 3"
    )


async def test_quit_and_interrupt_stop_the_session(tmp_path: Path) -> None:
    server = FakeServer([contradiction(), link(), widen()])
    session, out = await review(
        server, ReviewOptions(project=P, log_path=tmp_path / "l"), read_key=keys("a", "q")
    )
    assert len(server.answer_calls()) == 1 and "quit; 2 question(s) of this batch left open" in out
    assert session.counts()["accept"] == 1

    def interrupt(_prompt: str) -> str:
        raise KeyboardInterrupt

    server = FakeServer([link()])
    session, out = await review(
        server, ReviewOptions(project=P, log_path=tmp_path / "l2"), read_key=interrupt
    )
    assert server.answer_calls() == [] and "quit; 1 question(s)" in out and session.rows == []


async def test_batch_kind_and_cursor_reach_the_listing(tmp_path: Path) -> None:
    server = FakeServer([contradiction(), link(), widen()])
    await review(
        server,
        ReviewOptions(project=P, batch=2, kind="link", log_path=tmp_path / "l"),
        read_key=keys("s"),
    )
    assert server.calls[0] == (
        "hlm.questions",
        {"project": P, "limit": 2, "token_budget": 32000, "kind": "link"},
    )
    server = FakeServer([contradiction(), link(), widen()])
    after_qc = server.cursor(P, None, QC)
    _s, out = await review(
        server, ReviewOptions(project=P, cursor=after_qc, log_path=tmp_path / "l"), read_key=keys("q")
    )
    assert server.calls[0][1]["cursor"] == after_qc and "continuing after the given cursor" in out
    assert f"id {QL}" in out and f"id {QC}" not in out


async def test_paging_by_cursor_skips_and_repeats_nothing_when_questions_are_answered_between_pages(
    tmp_path: Path,
) -> None:
    """Review 101 (both reviewers, MEDIUM): an OFFSET over the shrinking open set skipped the
    questions after an answered page. The keyset cursor continues after the last listed one."""
    qids = [str(uuid.UUID(int=100 + i, version=4)) for i in range(7)]
    server = FakeServer([link(q) for q in qids])
    seen: list[str] = []
    cursor = None
    for _ in range(4):
        listing = await fetch_listing(server, P, limit=3, cursor=cursor)
        page = [q["question_id"] for q in listing.questions]
        seen += page
        for qid in page[:2]:  # answer some of the page (they leave the open set) before the next page
            server.status[qid] = "rejected"
        if listing.next_cursor is None:
            break
        cursor = listing.next_cursor
    assert seen == qids  # every question once, in order: none skipped, none repeated
    # a question answered by someone else before its page is simply not listed (and not skipped
    # past others): answer qids[4] now and re-page from the start of the remaining set
    server = FakeServer([link(q) for q in qids])
    first = await fetch_listing(server, P, limit=3)
    server.status[qids[3]] = "rejected"
    server.status[qids[0]] = "rejected"
    second = await fetch_listing(server, P, limit=3, cursor=first.next_cursor)
    assert [q["question_id"] for q in second.questions] == qids[4:7]


async def test_a_session_that_left_questions_open_prints_where_to_continue(tmp_path: Path) -> None:
    server = FakeServer([contradiction(), link(), widen()])
    session, out = await review(
        server, ReviewOptions(project=P, batch=2, log_path=tmp_path / "l"), read_key=keys("s", "a")
    )
    # QC skipped (stays open, first on a plain re-run); QL accepted; QW is on the next page
    assert session.resume == server.cursor(P, None, QL)
    assert (
        f"continue after the last reviewed question: hlm review --project {P} --cursor {session.resume}"
        in out
    )
    _s, out = await review(
        server, ReviewOptions(project=P, cursor=session.resume, log_path=tmp_path / "l"), read_key=keys("q")
    )
    assert f"id {QW}" in out and f"id {QC}" not in out
    # nothing left open, or nothing after: no hint
    server = FakeServer([contradiction(), link()])
    session, out = await review(
        server, ReviewOptions(project=P, log_path=tmp_path / "l"), read_key=keys("a", "s")
    )
    assert session.resume is None and "--cursor" not in out
    # quit after a skip: the rest of the batch comes after the cursor, with the kind filter kept
    server = FakeServer([link(QC), link(QL), link(QW)])
    session, out = await review(
        server, ReviewOptions(project=P, kind="link", log_path=tmp_path / "l"), read_key=keys("s", "q")
    )
    assert session.resume == server.cursor(P, "link", QC) and f"--kind link --cursor {session.resume}" in out


async def test_empty_listing(tmp_path: Path) -> None:
    session, out = await review(FakeServer([]), ReviewOptions(project=P, log_path=tmp_path / "l"))
    assert "nothing to review" in out and session.rows == [] and not (tmp_path / "l").exists()


# --------------------------------------------------------------------------- dry-run
async def test_dry_run_sends_nothing_and_logs_nothing(tmp_path: Path) -> None:
    server = FakeServer([contradiction(), link(), widen()])
    log = tmp_path / "review.log"
    session, out = await review(
        server, ReviewOptions(project=P, dry_run=True, log_path=log), read_key=keys("o", "a", "r", "s")
    )
    assert set(server.tools()) == {"hlm.questions", "hlm.export"}  # export: no access event
    assert "DRY-RUN: nothing is sent" in out
    assert (
        f"→ DRY-RUN would send memory.answer {json.dumps(answer_args(P, QC, 'accept'), sort_keys=True)}"
        in out
    )
    assert "┌ v812 [fact] Deploy host moved" in out and "│ body of Deploy host moved" in out
    assert [r["status"] for r in session.rows] == ["dry_run", "dry_run", "skipped"]
    assert not log.exists() and "[dry-run: nothing sent]" in out
    assert "acceptance by kind (this session): contradiction 1/1 (100%) · link 0/1 (0%)" in out
    assert all(s == "open" for s in server.status.values())


async def test_dry_run_decisions_file_needs_no_confirmation_and_sends_nothing(tmp_path: Path) -> None:
    server = FakeServer([contradiction(), link()])
    opts = ReviewOptions(
        project=P, dry_run=True, decisions=[(QC, "accept"), (QL, "reject")], log_path=tmp_path / "l"
    )
    _session, out = await review(server, opts)
    assert server.answer_calls() == [] and out.count("DRY-RUN would send memory.answer") == 2


# --------------------------------------------------------------------------- idempotency
def test_request_ids_are_deterministic_uuid5() -> None:
    rid = request_id_for(P, QC, "accept")
    assert (
        rid == request_id_for(P, QC, "accept") == str(uuid.uuid5(NS_REVIEW, f"hlm-review/1:{P}:{QC}:accept"))
    )
    assert uuid.UUID(rid).version == 5
    others = {
        request_id_for(P, QC, "reject"),
        request_id_for(P, QL, "accept"),
        request_id_for("other", QC, "accept"),
    }
    assert rid not in others and len(others) == 3
    assert set(answer_args(P, QC, "accept")) == {"project", "request_id", "question_id", "decision"}


async def test_a_rerun_is_replayed_and_logged_once(tmp_path: Path) -> None:
    server = FakeServer([contradiction(), link()])
    log = tmp_path / "review.log"
    opts = ReviewOptions(project=P, decisions=[(QC, "accept"), (QL, "reject")], yes=True, log_path=log)
    first, _ = await review(server, opts)
    second, out = await review(server, opts)
    assert first.counts()["accept"] == 1 and second.counts()["replayed"] == 2 and second.errors == 0
    assert server.answer_calls()[:2] == server.answer_calls()[2:]  # byte-identical arguments
    assert "[replayed: this exact answer was already recorded]" in out
    assert "not open/visible here: sent anyway" in out
    assert [e["decision"] for e in read_log(log)] == ["accept", "reject"]


async def test_a_lost_reply_is_retried_with_the_same_request_id(tmp_path: Path) -> None:
    server = FakeServer([contradiction()])
    server.timeout_after_commit = 1
    log = tmp_path / "review.log"
    session, out = await review(server, ReviewOptions(project=P, log_path=log), read_key=keys("a"))
    calls = server.answer_calls()
    assert len(calls) == 2 and calls[0] == calls[1]
    assert session.errors == 0 and session.rows[0]["replayed"] is True
    assert [e["decision"] for e in read_log(log)] == ["accept"]  # the retried replay is this session's answer


async def test_a_server_refusal_is_reported_and_not_logged(tmp_path: Path) -> None:
    server = FakeServer([contradiction()])
    server.status[QC] = "expired"
    log = tmp_path / "review.log"
    session, out = await review(
        server, ReviewOptions(project=P, decisions=[(QC, "accept")], yes=True, log_path=log)
    )
    assert session.errors == 1 and "→ error E_VERSION_CONFLICT: the question is expired" in out
    assert read_log(log) == []


# --------------------------------------------------------------------------- decisions file
def test_load_decisions(tmp_path: Path) -> None:
    f = tmp_path / "d.json"
    f.write_text(json.dumps({QL: "R", QC: "accept", QW: "s"}))
    assert load_decisions(f) == [(QL, "reject"), (QC, "accept"), (QW, "skip")]
    for bad, msg in (
        ("[1]", "non-empty JSON object"),
        ("{}", "non-empty JSON object"),
        ("{nope", "cannot read"),
        (json.dumps({"v812": "accept"}), "not a full question id"),
        (json.dumps({QC: "defer"}), "accept|reject|skip"),
        (json.dumps({QC: True}), "accept|reject|skip"),
    ):
        f.write_text(bad)
        with pytest.raises(ValueError, match=msg):
            load_decisions(f)


async def test_decisions_need_confirmation(tmp_path: Path) -> None:
    server = FakeServer([contradiction(), link()])
    asked: list[str] = []
    opts = ReviewOptions(project=P, decisions=[(QC, "accept"), (QL, "skip")], log_path=tmp_path / "l")
    session, out = await review(server, opts, confirm=lambda m: asked.append(m) or False)
    assert asked == [f"send 1 answer(s) to {P}?"] and server.answer_calls() == []
    assert session.aborted and "aborted; nothing sent" in out and not (tmp_path / "l").exists()
    session, _ = await review(server, opts, confirm=lambda _m: True)
    assert server.answer_calls() == [answer_args(P, QC, "accept")] and server.status[QL] == "open"
    assert f"accept {QC}  contradiction  Deploy host moved / Deploy [31mhost" in _
    listing_call = next(a for t, a in server.calls if t == "hlm.questions")
    assert listing_call["question_ids"] == [QC, QL]


# --------------------------------------------------------------------------- fallback (old server)
async def test_owner_refusal_falls_back_to_the_notices_and_says_why(tmp_path: Path) -> None:
    server = FakeServer([contradiction()], owner_ok=False)
    _session, out = await review(
        server, ReviewOptions(project=P, dry_run=True, log_path=tmp_path / "l"), read_key=keys("s")
    )
    assert "the server refused hlm.questions (owner-only: set HLM_OWNER_TOKEN" in out
    assert "notice    contradiction: v812 vs v455" in out


async def test_a_cursor_is_refused_when_the_listing_cannot_page(tmp_path: Path) -> None:
    """Review 101 (Sol): the notices fallback silently ignored the paging and repeated the newest."""
    for server in (
        FakeServer([contradiction()], has_list=False),
        FakeServer([contradiction()], owner_ok=False),
    ):
        with pytest.raises(ToolCallError) as ei:
            await review(
                server, ReviewOptions(project=P, cursor=f"{P}/*/000000/{QC}", log_path=tmp_path / "l")
            )
        assert ei.value.code == "E_INVALID_ARG" and "the notices cannot page" in ei.value.message
        assert "memory.query" not in server.tools()


async def test_without_hlm_questions_the_notices_are_reviewed(tmp_path: Path) -> None:
    server = FakeServer([contradiction()], has_list=False)
    session, out = await review(
        server, ReviewOptions(project=P, dry_run=True, log_path=tmp_path / "l"), read_key=keys("a")
    )
    query = next(a for t, a in server.calls if t == "memory.query")
    assert query["valid_at"] == "1970-01-01T00:00:00Z"
    assert "memory.drilldown" not in server.tools() and "memory.answer" not in server.tools()
    assert "7 open question(s); this server has no hlm.questions" in out
    assert "v812 [fact] Deploy host moved" in out and '» starts: "body of Deploy host moved"' in out
    assert "notice    contradiction: v812 vs v455; proposed: v812 supersedes v455" in out
    assert session.rows[0]["status"] == "dry_run"


# --------------------------------------------------------------------------- summary / log
def test_summary_stats_and_logged_acceptance(tmp_path: Path) -> None:
    s = Session(project=P)
    s.rows = [
        {"kind": "contradiction", "decision": "accept", "status": "accepted_pending"},
        {"kind": "contradiction", "decision": "reject", "status": "rejected"},
        {"kind": "contradiction", "decision": "accept", "status": "superseded", "replayed": True},
        {"kind": "link", "decision": "skip", "status": "skipped"},
        {"kind": "link", "decision": "accept", "status": "error", "error": "E_VERSION_CONFLICT"},
    ]
    assert s.rates() == {"contradiction": (2, 3)}
    log = tmp_path / "review.log"
    append_log(
        log,
        [
            {"project": P, "question_id": QC, "kind": "contradiction", "decision": "reject"},
            {"project": P, "question_id": QC, "kind": "contradiction", "decision": "accept"},
            {"project": P, "question_id": QL, "kind": "link", "decision": "reject"},
            {"project": P, "question_id": QW, "kind": "widen_scope", "decision": "skip"},
        ],
    )
    assert logged_acceptance(read_log(log)) == {"contradiction": (1, 1), "link": (0, 1)}
    text = summary_text(s, log_path=log, pending_after=170)
    assert text.splitlines() == [
        "summary: accept 2 · reject 1 · skip 1 · errors 1 · replayed 1",
        "acceptance by kind (this session): contradiction 2/3 (67%)",
        f"acceptance by kind (all logged sessions): contradiction 1/1 (100%) · link 0/1 (0%)  [{log}, +0]",
        "open questions now: 170",
    ]
    assert acceptance([("a", "skip")]) == {}


def test_log_is_private_and_holds_no_item_text(tmp_path: Path) -> None:
    log = tmp_path / "state" / "hlm" / "review.log"
    log.parent.mkdir(parents=True)
    log.write_text("")
    log.chmod(0o644)
    append_log(log, [{"question_id": QC, "decision": "accept"}])
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    append_log(log, [{"question_id": QL, "decision": "reject"}])
    assert [e["decision"] for e in read_log(log)] == ["accept", "reject"]
    fresh = tmp_path / "new" / "hlm" / "review.log"
    append_log(fresh, [{"x": 1}])
    assert stat.S_IMODE(fresh.parent.stat().st_mode) == 0o700 and stat.S_IMODE(fresh.stat().st_mode) == 0o600


async def test_session_log_entries_have_ids_kinds_and_decisions_only(tmp_path: Path) -> None:
    log = tmp_path / "review.log"
    await review(FakeServer([contradiction()]), ReviewOptions(project=P, log_path=log), read_key=keys("a"))
    (entry,) = read_log(log)
    assert entry == {
        "ts": NOW.isoformat(timespec="seconds"),
        "project": P,
        "question_id": QC,
        "kind": "contradiction",
        "decision": "accept",
        "status": "accepted_pending",
        "relation": "contradicts",
        "supersedes": "new",
        "scope": "part",
        "confidence": "high",
        "tier": "question",
        "request_id": request_id_for(P, QC, "accept"),
        "age_days": 4.0,
    }


# --------------------------------------------------------------------------- the typer command
class _Memory:
    def __init__(self, server: FakeServer) -> None:
        self.call_async = server


def _install(
    monkeypatch: pytest.MonkeyPatch,
    server: FakeServer,
    tmp_path: Path,
    seen: list[dict[str, Any]] | None = None,
) -> Path:
    monkeypatch.setenv("HLM_DEVICE_TOKEN", "hlm_" + "b" * 43)
    monkeypatch.setenv("HLM_REVIEW_LOG", str(tmp_path / "review.log"))
    monkeypatch.delenv("HLM_OWNER_TOKEN", raising=False)

    def memory(self: Any, **kw: Any) -> _Memory:
        if seen is not None:
            seen.append(kw)
        return _Memory(server)

    monkeypatch.setattr(hlm_mod.Ctx, "memory", memory)
    write_toml(Path.cwd() / "hlm.toml")
    return tmp_path / "review.log"


def test_cli_sends_the_owner_token_header_only_when_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[dict[str, Any]] = []
    _install(monkeypatch, FakeServer([]), tmp_path, seen)
    res = runner().invoke(app, ["review", "--dry-run"])
    assert res.exit_code == 0, res.output
    assert seen[-1].get("extra_headers") is None
    monkeypatch.setenv("HLM_OWNER_TOKEN", "o" * 64)
    res = runner().invoke(app, ["review", "--dry-run", "--cursor", f"{P}/*/000000/{QC}"])
    assert res.exit_code == 0, res.output
    assert seen[-1]["extra_headers"] == {"X-HLM-Owner-Token": "o" * 64}


def test_the_agent_launcher_never_passes_the_owner_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from hlmemo.cli.launch import child_env

    env = child_env("hlm_" + "d" * 43, {"HLM_OWNER_TOKEN": "o" * 64, "PATH": "/bin"})
    assert "HLM_OWNER_TOKEN" not in env and env["HLM_DEVICE_TOKEN"] == "hlm_" + "d" * 43


def test_cli_dry_run_reads_keys_from_stdin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    server = FakeServer([contradiction(), link()])
    log = _install(monkeypatch, server, tmp_path)
    res = runner().invoke(app, ["review", "--dry-run", "--batch", "2"], input="a\ns\n")
    assert res.exit_code == 0, res.output
    assert server.answer_calls() == [] and "DRY-RUN would send memory.answer" in res.output
    assert server.calls[0][1]["limit"] == 2 and server.calls[0][1]["project"] == "hlmemo"
    assert not log.exists()


def test_cli_decisions_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    server = FakeServer([contradiction(), link()])
    log = _install(monkeypatch, server, tmp_path)
    f = tmp_path / "d.json"
    f.write_text(json.dumps({QC: "accept", QL: "reject"}))
    res = runner().invoke(app, ["review", "--project", "hlmemo", "--decisions", str(f)], input="n\n")
    assert res.exit_code == 1 and "aborted; nothing sent" in res.output and server.answer_calls() == []
    res = runner().invoke(app, ["review", "--decisions", str(f), "--yes"])
    assert res.exit_code == 0, res.output
    assert server.answer_calls() == [answer_args("hlmemo", QC, "accept"), answer_args("hlmemo", QL, "reject")]
    assert len(read_log(log)) == 2
    server.status[QC] = "expired"  # a refused answer → exit 1
    f.write_text(json.dumps({QC: "reject"}))
    res = runner().invoke(app, ["review", "--decisions", str(f), "--yes"])
    assert res.exit_code == 1 and "E_VERSION_CONFLICT" in res.output


def test_cli_usage_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    server = FakeServer([])
    _install(monkeypatch, server, tmp_path)
    f = tmp_path / "d.json"
    f.write_text(json.dumps({"not-a-uuid": "accept"}))
    res = runner().invoke(app, ["review", "--decisions", str(f)])
    assert res.exit_code == EX_USAGE and "not a full question id" in res.output
    res = runner().invoke(app, ["review", "--kind", "link; drop"])
    assert res.exit_code == EX_USAGE
    res = runner().invoke(app, ["review", "--batch", "51"])
    assert res.exit_code != 0 and server.calls == []
    res = runner().invoke(app, ["review", "--cursor", "a b"])
    assert res.exit_code == EX_USAGE and server.calls == []
