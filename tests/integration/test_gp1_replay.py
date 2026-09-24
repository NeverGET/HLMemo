"""G-P1 — CI replay of the W2b pipeline over the W2b fixtures (PHASE2-4-ROADMAP W2b).

The full production path — the write trigger, scope-parameterized candidate retrieval over the
stored embeddings, the drop rule, placement, relation, the D-067 guards and the cross-profile
verifier, the observer role, batches and the one ``librarian`` event per job — runs over
``tests/fixtures/w2b/relations.json`` (≥ 150 pairs: 40+ adversarial near-misses, 20+ cross-project,
TR/DE/EN) and ``placement.json`` (64 items), with the provider in STRICT cassette replay
(``tests/cassettes/w2b/gp1_pipeline.jsonl``, recorded from the default profile chain: gpt-6-luna,
verifier deepseek; re-recorded deliberately for the v2 relation prompts, D-076 judgement v2).

Gate: every job's ``payload.resolved`` equals the golden file (timestamps masked); observer →
0 links, 0 invalidations; a client-set importance is never overwritten; a projection rebuild is
identical. The quality numbers (exact decisions vs gold) are printed; model quality itself is
G-LIVE-B, never this test.

Recording (manual, keyed): ``HLM_LLM_MODE=record HLM_RECORD_ENV_FILE=<.env> HLM_GP1_WRITE_GOLDEN=1
pytest tests/integration/test_gp1_replay.py`` appends cassettes and rewrites the golden file.
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest

from hlmemo.auth.context import AuthContext, Role
from hlmemo.core.write_service import default_deps, write
from hlmemo.db.replay import rebuild_projections
from hlmemo.librarian.budget import NoBudget
from hlmemo.librarian.cassette import CassetteStore
from hlmemo.librarian.ledger import DbLedger
from hlmemo.librarian.profiles import named_profile
from hlmemo.librarian.provider import Provider
from hlmemo.librarian.redact import Redactor
from tests.integration._librarian_fixtures import conn_ctx, lib_settings, make_worker, seed_reserved
from tests.integration._read_fixtures import embedder  # noqa: F401 - fixture by import
from tests.integration._w2b_fixtures import dump_w2b, embed, review_deps

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "w2b"
CASSETTES = ROOT / "tests" / "cassettes" / "w2b"
GOLDEN = FIX / "gp1_golden.json"
CHAIN = ("openrouter-gpt6-luna", "openrouter")  # D-066: primary gpt-6-luna, fallback/verifier deepseek


def _mode() -> str:
    mode = os.environ.get("HLM_LLM_MODE", "replay")
    if mode == "record" and os.environ.get("HLM_RECORD_ENV_FILE"):  # key file, never echoed
        for line in Path(os.environ["HLM_RECORD_ENV_FILE"]).read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))
    return mode if mode in ("record", "replay") else "replay"


def _mask(obj: Any) -> Any:
    """Golden form: wall-clock timestamps and call timing masked; everything else exact."""
    if isinstance(obj, dict):
        return {
            k: ("<ts>" if k in ("recorded_at", "done_at", "run_after", "expires_at") and v else _mask(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_mask(x) for x in obj]
    return obj


AUDIT_KEYS = ("subjects", "candidates", "dropped_by_rule", "judgements", "guards", "lexical_only")


def _golden(payload: dict[str, Any]) -> dict[str, Any]:
    req = payload["request"]
    outputs = [c.get("output") for c in req.get("calls", [])] or [req.get("output")]
    return _mask(
        {
            "request": {k: req.get(k) for k in AUDIT_KEYS},
            "outputs": outputs,
            "resolved": {k: v for k, v in payload["resolved"].items() if k != "jobs"},
        }
    )


async def _device(conn: Any, name: str, grants: dict[int, str]) -> AuthContext:
    cur = await conn.execute(
        "INSERT INTO devices (name, class, fingerprint, token_sha256, status, approved_at,"
        " approved_by_device_id) VALUES (%s, 'personal', %s, %s, 'trusted', now(), 1) RETURNING device_id",
        (name, f"fp-{name}", f"h-{name}"),
    )
    (did,) = await cur.fetchone()
    for pid, role in grants.items():
        await conn.execute(
            "INSERT INTO device_project_grants (device_id, project_id, role, granted_by_device_id)"
            " VALUES (%s, %s, %s, 1)",
            (did, pid, role),
        )
    return AuthContext(did, "personal", False, 1, {p: Role(r) for p, r in grants.items()}, "gp1/0")


async def _project(conn: Any, slug: str) -> int:
    cur = await conn.execute(
        "INSERT INTO projects (slug, name) VALUES (%s, %s) RETURNING project_id", (slug, slug)
    )
    return (await cur.fetchone())[0]


def _day(d: str) -> str:
    """Fixture day → valid_from; capped at 2026-09-22 (the write path refuses a valid_from more
    than 5 min in the future, and the recording ran on 2026-09-23 UTC). Order is preserved."""
    return f"{min(d, '2026-09-22')}T00:00:00Z"


async def _write(
    conn: Any, ctx: AuthContext, slug: str, items: list[dict[str, Any]], deps: Any, key: str
) -> list[Any]:
    res = await write(
        conn,
        ctx,
        {
            "project": slug,
            "request_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"gp1:{slug}:{key}")),
            "client": "gp1/0",
            "items": items,
        },
        deps=deps,
    )
    await conn.commit()
    return list(res.versions)


async def _build(connect: Any) -> dict[str, Any]:
    rel = json.loads((FIX / "relations.json").read_text())
    place = json.loads((FIX / "placement.json").read_text())
    pair_of: dict[tuple[int, int], dict[str, Any]] = {}
    client_imp: dict[int, int] = {}
    async with await connect() as conn:
        await seed_reserved(conn)
        for g in rel["groups"]:
            same = f"gp1-{g['id']}"
            other = f"gp1-{g['id']}-o"
            pids = {same: await _project(conn, same)}
            if any(e["project"] == "other" for e in g["existing"]):
                pids[other] = await _project(conn, other)
            ctx = await _device(conn, f"gp1-dev-{g['id']}", {p: "write" for p in pids.values()})
            await conn.commit()
            olds = []
            for e in g["existing"]:
                slug = other if e["project"] == "other" else same
                item = {"kind": e["kind"], "title": e["title"], "body": e["text"], "valid_from": _day(e["t"])}
                (v,) = await _write(conn, ctx, slug, [item], default_deps(), e["id"])  # old items: no review
                olds.append((e, v))
            n = g["new"]
            new_item = {"kind": n["kind"], "title": n["title"], "body": n["text"], "valid_from": _day(n["t"])}
            (nv,) = await _write(conn, ctx, same, [new_item], review_deps(), g["id"])
            for e, v in olds:
                pair_of[(nv.version_id, v.version_id)] = e
        items = place["items"]
        for b in range(0, len(items), 4):
            slug = f"gp1-p{b // 4:02d}"
            pid = await _project(conn, slug)
            ctx = await _device(conn, f"gp1-pdev-{b // 4:02d}", {pid: "write"})
            await conn.commit()
            batch = []
            for k, it in enumerate(items[b : b + 4]):
                row = {
                    "kind": it["kind"],
                    "title": it["title"],
                    "body": it["text"],
                    "valid_from": _day("2026-09-01"),
                }
                if (b + k) % 5 == 0:  # the client set importance itself: never overwritten
                    row["importance"] = 3
                batch.append(row)
            versions = await _write(conn, ctx, slug, batch, review_deps(), slug)
            for k, v in enumerate(versions):
                if "importance" in batch[k]:
                    client_imp[v.version_id] = batch[k]["importance"]
    return {
        "pairs": pair_of,
        "client_importance": client_imp,
        "n_pairs": sum(len(g["existing"]) for g in rel["groups"]),
    }


async def test_gp1_pipeline_replays_golden(db_dsn, connect, embedder) -> None:  # noqa: ANN001
    mode = _mode()
    world = await _build(connect)
    await embed(connect, embedder)
    provider = Provider(
        [named_profile(name) for name in CHAIN],
        mode=mode,
        budget=NoBudget(),
        ledger=DbLedger(conn_ctx(db_dsn)),
        cassettes=CassetteStore(CASSETTES, record_name="gp1_pipeline"),
        redactor=Redactor(),
        budget_disabled=True,
        timeout_s=120,
    )
    worker = make_worker(lib_settings(db_dsn, librarian_role="observer"), provider, connect)
    n_jobs = await worker.drain()
    await provider.aclose()
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT payload->'resolved'->'done'->>'dedupe_key', payload FROM events"
            " WHERE kind = 'librarian' AND payload->'request'->>'op' = 'write_review' ORDER BY event_id"
        )
        rows = await cur.fetchall()
        # observer: 0 links, 0 invalidations of user items
        cur = await conn.execute("SELECT count(*) FROM links")
        assert (await cur.fetchone())[0] == 0
        cur = await conn.execute("SELECT count(*) FROM memory_versions WHERE superseded_at <> 'infinity'")
        assert (await cur.fetchone())[0] == 0
        cur = await conn.execute("SELECT version_id, importance, importance_src FROM version_signals")
        signals = {v: (imp, src) for v, imp, src in await cur.fetchall()}
    assert n_jobs == len(rows) == 37 + 16
    for vid, imp in world["client_importance"].items():
        assert signals[vid] == (imp, "client")  # a client-set importance is never overwritten
    assert sum(1 for _v, (_i, src) in signals.items() if src == "librarian") >= 40

    golden = {key: _golden(p) for key, p in rows}
    if os.environ.get("HLM_GP1_WRITE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(golden, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    expected = json.loads(GOLDEN.read_text())
    assert set(golden) == set(expected)
    for key in sorted(expected):
        assert golden[key] == expected[key], key

    # the pipeline's decisions vs the fixture gold (reported; model quality is G-LIVE-B)
    decided: dict[tuple[int, int], tuple[str, str]] = {}
    reached: set[tuple[int, int]] = set()
    for _key, p in rows:
        for c in p["request"].get("candidates") or []:
            reached.add((int(c["subject"][1:]), int(c["id"][1:])))
        for q in p["resolved"].get("questions") or []:
            prop = q["proposal"]
            s, c = (int(x[1:]) for x in q["subject_clues"][:2])
            rel = prop.get("relation")
            # the fixture's v1 gold is frozen (Sol 54j #8): a v2 "relates" (a restated claim that is not
            # near-identical text) is the fixture's semantic "duplicate"
            decided[(s, c)] = ("duplicate" if rel == "relates" else rel, prop.get("supersedes"))
    exact = sum(decided.get(k, ("none", "none")) == tuple(e["gold"]) for k, e in world["pairs"].items())
    n_reached = len(reached & set(world["pairs"]))
    print(
        f"\nG-P1: {len(rows)} jobs, {world['n_pairs']} fixture pairs, {n_reached} reached the model after"
        f" the drop rule, pipeline exact {exact / world['n_pairs']:.3f}, questions {len(decided)}"
    )
    assert n_reached >= 150
    # the recorded pipeline's decisions must stay at the G-LIVE-B bar (a regression in candidates,
    # guards, dedupe or question planning fails here, with no network)
    assert exact / world["n_pairs"] >= 0.90

    async with await connect() as conn:  # replay identity with the new kinds and projections
        before = await dump_w2b(conn)
        await rebuild_projections(conn)
        await conn.commit()
        after = await dump_w2b(conn)
        for table in before:
            assert sorted(set(before[table]) ^ set(after[table])) == [], table
            assert sorted(before[table]) == sorted(after[table]), table  # multiset: duplicate rows too
        assert after == before
