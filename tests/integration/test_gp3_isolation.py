"""G-P3 — capability isolation of W2b candidate selection (PHASE2-4-ROADMAP W2b).

1,000 randomized probes (seeded): a random device with random current grants (none/read/write per
project) triggers a ``write_review`` job for a random subject it wrote; between enqueue and plan
a random grant may be revoked or added. Every candidate the job selects must come from projects
the device reads NOW and held at enqueue (``question`` capability), with a visible, non-device
scope; every provider request may only contain the markers of those items. Gate: 0 candidates
from ungranted projects and 0 ``device:*`` items in any request.
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import os
import random
import re
from typing import Any

import pytest

from hlmemo.auth.context import AuthContext
from hlmemo.core.write_service import default_deps, write
from hlmemo.librarian.jobs import enqueue, job_spec
from hlmemo.librarian.tasks.write_review import OP
from tests.integration._librarian_fixtures import (
    ScriptedLLM,
    lib_settings,
    make_provider,
    make_worker,
    seed_reserved,
)
from tests.integration._read_fixtures import embedder  # noqa: F401 - fixture by import
from tests.integration._w2b_fixtures import Oracle, embed

pytestmark = pytest.mark.integration

N_PROBES = int(os.environ.get("HLM_GP3_PROBES", "1000"))
PROJECTS = ("gp3-a", "gp3-b", "gp3-c", "gp3-d", "gp3-e")
DEVICES = (("gp3-d1", "personal"), ("gp3-d2", "work"), ("gp3-d3", "personal"), ("gp3-d4", "other"))
KINDS = ("fact", "lesson", "experience", "episode")
TOPICS = (
    "deploy cache port backups restore migration",
    "worker threads memory limit container",
    "ssh stdin heredoc loop deploy script",
    "token secret rotation device grant",
)
MARK = re.compile(r"mk\d+x")


async def _world(connect: Any) -> dict[str, Any]:
    async with await connect() as conn:
        await seed_reserved(conn)
        pids = {}
        for slug in PROJECTS:
            cur = await conn.execute(
                "INSERT INTO projects (slug, name) VALUES (%s, %s) RETURNING project_id", (slug, slug)
            )
            pids[slug] = (await cur.fetchone())[0]
        dids = {}
        for name, cls in DEVICES:
            cur = await conn.execute(
                "INSERT INTO devices (name, class, fingerprint, token_sha256, status, approved_at,"
                " approved_by_device_id) VALUES (%s, %s, %s, %s, 'trusted', now(), 1) RETURNING device_id",
                (name, cls, f"fp-{name}", f"h-{name}"),
            )
            dids[name] = ((await cur.fetchone())[0], cls)
        await conn.commit()
    admin = AuthContext(device_id=1, device_class="server", is_admin=True, token_generation=1, client="gp3")
    rng = random.Random(3)
    items: dict[int, dict[str, Any]] = {}  # marker n -> item facts
    n = 0
    for slug in PROJECTS:
        batch = []
        for k in range(12):
            n += 1
            scope = rng.choice(
                [
                    "all",
                    "all",
                    "all",
                    "class:personal",
                    "class:work",
                    *(f"device:{d}" for d, _ in dids.values()),
                ]
            )
            extra = [s for s in PROJECTS if s != slug and rng.random() < 0.15]
            batch.append(
                {
                    "kind": rng.choice(KINDS),
                    "title": f"{TOPICS[k % len(TOPICS)].split()[0]} note {n}",
                    "body": f"{TOPICS[k % len(TOPICS)]} detail mk{n}x for the {slug} project.",
                    "device_scope": scope,
                    "project_ids": [slug, *extra],
                    "valid_from": "2026-01-01T00:00:00Z",
                }
            )
            items[n] = {"scope": scope, "projects": {pids[s] for s in [slug, *extra]}, "home": pids[slug]}
        async with await connect() as conn:
            res = await write(
                conn,
                admin,
                {
                    "project": slug,
                    "request_id": f"00000000-0000-4000-8000-{len(items):012d}",
                    "client": "gp3",
                    "items": batch,
                },
                deps=default_deps(),
            )
            await conn.commit()
        first = n - len(batch) + 1
        for i, v in enumerate(res.versions):
            items[first + i]["version_id"] = v.version_id
    return {"pids": pids, "dids": dids, "items": items}


async def _set_grants(connect: Any, device_id: int, grants: dict[int, str]) -> None:
    async with await connect() as conn:
        await conn.execute("DELETE FROM device_project_grants WHERE device_id = %s", (device_id,))
        for pid, role in grants.items():
            await conn.execute(
                "INSERT INTO device_project_grants (device_id, project_id, role, granted_by_device_id)"
                " VALUES (%s, %s, %s, 1)",
                (device_id, pid, role),
            )
        await conn.commit()


async def test_gp3_capability_isolation_randomized(db_dsn, connect, embedder) -> None:  # noqa: ANN001
    w = await _world(connect)
    await embed(connect, embedder)
    pids, dids, items = w["pids"], w["dids"], w["items"]
    by_vid = {it["version_id"]: (n, it) for n, it in items.items()}
    rng = random.Random(20260924)
    llm = ScriptedLLM(default=Oracle())
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    worker = make_worker(lib_settings(db_dsn, librarian_embed_wait_s=0), provider, connect)
    probes = candidates_seen = requests_checked = 0
    for i in range(N_PROBES):
        name = rng.choice([d for d, _ in DEVICES])
        did, cls = dids[name]
        grants = {pid: rng.choice(["read", "write"]) for pid in pids.values() if rng.random() < 0.6}
        writable = [p for p, r in grants.items() if r == "write"]
        if not writable:
            grants[rng.choice(list(pids.values()))] = "write"
            writable = [p for p, r in grants.items() if r == "write"]
        await _set_grants(connect, did, grants)
        home = rng.choice(writable)
        pool = [(n, it) for n, it in items.items() if it["home"] == home]
        visible_pool = [(n, it) for n, it in pool if it["scope"] in ("all", f"class:{cls}")]
        # mostly subjects the device can see; sometimes one the privacy gate must refuse
        subject_n, subject = rng.choice(visible_pool if visible_pool and rng.random() < 0.85 else pool)
        async with await connect() as conn:
            spec = job_spec(
                kind="librarian_write",
                dedupe_key=f"librarian_write:gp3:{i}",
                payload={
                    "op": OP,
                    "versions": [
                        {
                            "version_id": subject["version_id"],
                            "kind": "fact",
                            "client_importance": None,
                            "client_stability": False,
                        }
                    ],
                },
            )
            await enqueue(conn, project_id=home, trigger_device_id=did, specs=[spec])
            await conn.commit()
        caps_read = set(grants)
        change = rng.random()
        if change < 0.25 and len(grants) > 1:  # revoke one grant after enqueue
            gone = rng.choice([p for p in grants if p != home])
            grants = {p: r for p, r in grants.items() if p != gone}
            await _set_grants(connect, did, grants)
        elif change < 0.40:  # a grant added after enqueue never widens the job
            extra = [p for p in pids.values() if p not in grants]
            if extra:
                grants = {**grants, rng.choice(extra): "read"}
                await _set_grants(connect, did, grants)
        allowed = caps_read & set(grants)
        visible = {"all", f"class:{cls}"}
        seen_before = len(llm.requests)
        await worker.drain()
        probes += 1
        async with await connect() as conn:
            cur = await conn.execute(
                "SELECT payload->'request'->'candidates' FROM events WHERE kind = 'librarian'"
                " AND payload->'resolved'->'done'->>'dedupe_key' = %s",
                (f"librarian_write:gp3:{i}",),
            )
            row = await cur.fetchone()
        assert row is not None, f"probe {i}: job not done"
        for c in row[0] or []:
            n, it = by_vid[int(c["id"][1:])]
            candidates_seen += 1
            assert it["projects"] <= allowed, (i, "candidate from an ungranted project", c)
            assert it["scope"] in visible, (i, "candidate outside the device's scope", c, it["scope"])
        permitted = {subject_n} | {by_vid[int(c["id"][1:])][0] for c in row[0] or []}
        for req in llm.requests[seen_before:]:
            requests_checked += 1
            sent = " ".join(m["content"] for m in req["messages"])
            for mk in MARK.findall(sent):
                n = int(mk[2:-1])
                assert n in permitted, (i, "an item left the gate", n)
                assert not items[n]["scope"].startswith("device:"), (i, "device-scoped content sent", n)
                assert items[n]["projects"] <= allowed, (i, "ungranted project content sent", n)
    await provider.aclose()
    print(
        f"\nG-P3: {probes} probes, {candidates_seen} candidates checked, {requests_checked} requests checked:"
        " 0 ungranted, 0 device-scoped"
    )
    assert probes == N_PROBES and candidates_seen > N_PROBES // 2
