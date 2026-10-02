"""``hlm review`` end to end on a real DB: three librarian questions (one per kind) proposed by the
real W2b pipeline (scripted model, observer role), listed through the unlisted client tool
``hlm.questions`` over the MCP wire, reviewed with ``--decisions`` and checked in the server state.

* listing is read-only (no event, no access event) and authorized (``read`` on the project);
* ``--dry-run`` sends nothing (no answer event, no access event, ``o`` included);
* the decisions file sends exactly ``memory.answer`` with the derived request ids: accept →
  ``accepted_pending`` (observer: zero user mutations), reject → ``rejected``, skip → still open;
* a re-run of the same file is a server replay: no new event, no new rule, nothing logged twice.
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.client.streamable_http import streamable_http_client

from hlmemo.auth.tokens import hash_token
from hlmemo.cli.mcp_client import MemoryClient, ToolCallError
from hlmemo.cli.review import (
    ReviewOptions,
    fetch_listing,
    load_decisions,
    read_log,
    render_card,
    request_id_for,
    run_review,
)
from hlmemo.core.errors import ToolError
from hlmemo.librarian.questions import review_list
from tests.integration._librarian_fixtures import (
    ScriptedLLM,
    lib_settings,
    make_provider,
    make_worker,
    seed_reserved,
)
from tests.integration._mcp_fixtures import bearer, running_app
from tests.integration._read_fixtures import embedder  # noqa: F401 - fixture by import
from tests.integration._w2b_fixtures import Oracle, embed, write_items
from tests.integration._write_fixtures import MAIN, OTHER, World, count, item, seed_world

pytestmark = pytest.mark.integration

TOKEN = "hlm_" + "R" * 43
D_OLD = datetime(2026, 1, 1, tzinfo=UTC).isoformat()
D_NEW = datetime(2026, 6, 1, tzinfo=UTC).isoformat()
HOST_OLD = ("Deploy host", "Production runs on the Hetzner CX33 host in Falkenstein.")
HOST_NEW = ("Deploy host moved", "Production moved to the Hostinger KVM 2 host; the Hetzner host is gone.")
POOL_OLD = ("Pool size note", "The asyncpg pool uses at most ten connections per api worker.")
POOL_NEW = ("Pool sizing rule", "The asyncpg pool uses at most ten connections per api worker process.")
SSH_OTHER = ("Never pipe an ssh heredoc", "Never use bash -s with an ssh heredoc: stdin is swallowed.")
SSH_MAIN = ("ssh heredoc stdin", "Using bash -s over ssh with a heredoc swallows stdin; avoid it.")
ORACLE = Oracle(
    relations={
        (HOST_NEW[0], HOST_OLD[0]): ("contradicts", "new", "high"),
        (POOL_NEW[0], POOL_OLD[0]): ("duplicate", "none", "high"),
        (SSH_MAIN[0], SSH_OTHER[0]): ("duplicate", "none", "high"),
    }
)


def _never(prompt: str) -> Any:
    raise AssertionError(f"unexpected prompt: {prompt}")


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        await conn.execute(
            "UPDATE devices SET token_sha256 = %s WHERE device_id = %s", (hash_token(TOKEN), w.dev_a)
        )
        await conn.commit()
        return w


async def _seed_questions(db_dsn: str, connect, world: World, embedder) -> dict[str, str]:  # noqa: ANN001
    """Three open questions in MAIN: contradiction, link, widen_scope → {kind: question_id}."""
    await write_items(
        connect,
        world.ctx_a,
        MAIN,
        [item(*HOST_OLD, valid_from=D_OLD), item(*POOL_OLD, valid_from=D_OLD)],
    )
    await write_items(connect, world.ctx_a, OTHER, [{**item(*SSH_OTHER, valid_from=D_OLD), "kind": "lesson"}])
    await write_items(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(*HOST_NEW, valid_from=D_NEW),
            item(*POOL_NEW, valid_from=D_NEW),
            {**item(*SSH_MAIN), "kind": "lesson"},
        ],
    )
    await embed(connect, embedder)
    provider = make_provider(db_dsn, ScriptedLLM(default=ORACLE), budget_disabled=True)
    await make_worker(lib_settings(db_dsn, librarian_role="observer"), provider, connect).drain()
    await provider.aclose()
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT kind, question_id::text FROM librarian_questions WHERE status = 'open' ORDER BY kind"
        )
        rows = await cur.fetchall()
    assert [k for k, _ in rows] == ["contradiction", "link", "widen_scope"], rows
    return dict(rows)


async def _state(connect) -> dict[str, Any]:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT question_id::text, status, answer->>'decision', decided_by FROM librarian_questions"
        )
        questions = {qid: (st, dec, by) for qid, st, dec, by in await cur.fetchall()}
        cur = await conn.execute(
            "SELECT request_id::text FROM events WHERE kind = 'answer' ORDER BY event_id"
        )
        answers = [r[0] for r in await cur.fetchall()]
        state = {
            "questions": questions,
            "answers": answers,
            "access": await count(conn, "events", "kind = 'access'"),
            "events": await count(conn, "events"),
            "links": await count(conn, "links"),
            "closed": await count(
                conn, "memory_versions", "superseded_at <> 'infinity' OR valid_to <> 'infinity'"
            ),
            "rules": await count(conn, "memory_versions", "'librarian-rule' = ANY(tags)"),
        }
        await conn.rollback()
    return state


async def test_review_lists_dry_runs_and_applies_a_decisions_file(
    db_dsn, connect, world: World, embedder, tmp_path: Path
) -> None:  # noqa: ANN001
    qids = await _seed_questions(db_dsn, connect, world, embedder)
    log = tmp_path / "state" / "hlm" / "review.log"
    async with running_app(db_dsn) as client:
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app), base_url="http://test", headers=bearer(TOKEN)
        )
        async with http:
            memory = MemoryClient(
                "http://test/mcp",
                TOKEN,
                timeout_s=30,
                target=streamable_http_client("http://test/mcp", http_client=http),
            )
            async with memory.session() as call:
                before = await _state(connect)
                # --- the listing: read-only, everything a card needs, nothing internal
                listing = await fetch_listing(call, MAIN, limit=10)
                assert listing.pending_total == 3 and not listing.degraded
                assert listing.by_kind == {"contradiction": 1, "link": 1, "widen_scope": 1}
                by_kind = {q["kind"]: q for q in listing.questions}
                contra = by_kind["contradiction"]
                assert [s["title"] for s in contra["subjects"]] == [HOST_NEW[0], HOST_OLD[0]]
                assert contra["supersedes"] == "new" and contra["quotes"]["new"] and contra["quotes"]["old"]
                assert {a.get("rel") for a in contra["actions"]} >= {"contradicts", "supersedes"}
                assert "capabilities" not in json.dumps(listing.questions)
                assert by_kind["widen_scope"]["actions"][0]["add_projects"] == [MAIN]
                card = render_card(contra, 1, 3, now=datetime.now(UTC), width=100)
                assert "newer" in card and "older" in card and HOST_OLD[0] in card and "expires in" in card
                one = await fetch_listing(call, MAIN, limit=10, kind="link")
                assert [q["kind"] for q in one.questions] == ["link"] and one.pending_total == 3
                assert await _state(connect) == before  # no event, no access event

                # --- dry-run (interactive keys, o included): nothing is sent or recorded
                keys = iter(["o", "a", "r", "s"])
                out: list[str] = []
                session = await run_review(
                    call,
                    ReviewOptions(project=MAIN, dry_run=True, log_path=log, width=100),
                    read_key=lambda _p: next(keys),
                    confirm=_never,
                    echo=out.append,
                )
                text = "\n".join(out)
                assert text.count("DRY-RUN would send memory.answer") == 2 and "┌ v" in text
                assert [r["status"] for r in session.rows] == ["dry_run", "dry_run", "skipped"]
                assert await _state(connect) == before and not log.exists()

                # --- the decisions file: accept, reject, skip
                path = tmp_path / "decisions.json"
                plan = {qids["contradiction"]: "accept", qids["link"]: "reject", qids["widen_scope"]: "skip"}
                path.write_text(json.dumps(plan))
                opts = ReviewOptions(project=MAIN, decisions=load_decisions(path), yes=True, log_path=log)
                out = []
                session = await run_review(call, opts, read_key=_never, confirm=_never, echo=out.append)
                assert session.errors == 0, out
                after = await _state(connect)
                assert after["questions"][qids["contradiction"]] == (
                    "accepted_pending",
                    "accept",
                    world.dev_a,
                )
                assert after["questions"][qids["link"]] == ("rejected", "reject", world.dev_a)
                assert after["questions"][qids["widen_scope"]] == ("open", None, None)
                assert after["answers"] == [
                    request_id_for(MAIN, qids["contradiction"], "accept"),
                    request_id_for(MAIN, qids["link"], "reject"),
                ]
                # observer: an accept is a label; no user item, link or validity changed
                assert (after["links"], after["closed"]) == (before["links"], before["closed"])
                assert after["rules"] == before["rules"] + 2 and after["access"] == before["access"]
                assert "open questions now: 1 (was 3)" in "\n".join(out)
                assert "contradiction 1/1 (100%)" in "\n".join(out) and "link 0/1 (0%)" in "\n".join(out)
                logged = read_log(log)
                assert [(e["kind"], e["decision"], e["status"]) for e in logged] == [
                    ("contradiction", "accept", "accepted_pending"),
                    ("link", "reject", "rejected"),
                    ("widen_scope", "skip", "skipped"),
                ]
                assert stat.S_IMODE(log.stat().st_mode) == 0o600
                assert HOST_OLD[0] not in log.read_text()  # ids and decisions only, never item text

                # --- the same file again: replayed by the server, nothing new anywhere
                out = []
                session = await run_review(call, opts, read_key=_never, confirm=_never, echo=out.append)
                assert session.errors == 0 and session.counts()["replayed"] == 2, out
                again = await _state(connect)
                assert again["answers"] == after["answers"] and again["rules"] == after["rules"]
                assert again["events"] == after["events"]
                assert [e["decision"] for e in read_log(log)].count("accept") == 1

                # --- a question that is no longer open, with a different decision: refused
                path.write_text(json.dumps({qids["link"]: "accept"}))
                out = []
                session = await run_review(
                    call,
                    ReviewOptions(project=MAIN, decisions=load_decisions(path), yes=True, log_path=log),
                    read_key=_never,
                    confirm=_never,
                    echo=out.append,
                )
                assert session.errors == 1 and "E_VERSION_CONFLICT" in "\n".join(out)
                assert (await _state(connect))["answers"] == after["answers"]

                # --- the listing is authorized like a read
                with pytest.raises(ToolCallError) as ei:
                    await fetch_listing(call, "no-such-project", limit=1)
                assert ei.value.code == "E_FORBIDDEN_PROJECT"


async def test_listing_needs_read_on_the_project_and_hides_unreadable_subjects(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    await _seed_questions(db_dsn, connect, world, embedder)
    async with await connect() as conn:
        with pytest.raises(ToolError) as ei:  # dev-b holds OTHER only
            await review_list(conn, world.ctx_b, {"project": MAIN})
        assert ei.value.code == "E_FORBIDDEN_PROJECT"
        # a MAIN-only reader cannot see the widen_scope question (its OTHER subject is unreadable)
        from hlmemo.auth.context import AuthContext, Role

        main_only = AuthContext(
            device_id=world.dev_a,
            device_class="personal",
            is_admin=False,
            token_generation=1,
            grants={world.main_id: Role.READ},
        )
        res = await review_list(conn, main_only, {"project": MAIN, "limit": 50})
        assert res["pending"] == {"total": 2, "by_kind": {"contradiction": 1, "link": 1}}
        assert sorted(q["kind"] for q in res["questions"]) == ["contradiction", "link"]
        assert res["budget"]["used"] <= res["budget"]["limit"]
        tiny = await review_list(conn, main_only, {"project": MAIN, "limit": 50, "token_budget": 400})
        assert tiny["omitted"] >= 1 and len(tiny["questions"]) + tiny["omitted"] == 2
        await conn.rollback()
