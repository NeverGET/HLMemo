"""O2 (codex final review 09 §4): a worker killed mid-commit restarts and reclaims its job.

The whole test runs against an **isolated Compose project and database** (`hlmemo-o2`, its own
`pgdata` volume on a free loopback port) with the **real baked models** and exactly **one worker**
under `restart: unless-stopped`. Nothing here touches the developer's stack, and no container is
ever restarted by hand: the worker kills itself and Docker's restart policy does the rest.

Shape of the run:

1. bring up `db -> migrate -> worker`, then warm the worker for ten seconds by letting it embed two
   seed versions with the real model (this is also where the reference vectors come from);
2. seed a revision that supersedes one of them with **one copyable chunk** (identical `text_norm`,
   so §1.1 (5) copies the predecessor vector) and **one novel chunk** (must be inferred);
3. a *persisted, one-shot* barrier fires inside the fenced transaction immediately before
   `_mark_done`, i.e. after both the copied and the inferred inserts. While it is held an
   independent connection must see the job `running` with **zero** committed revision vectors;
4. releasing the barrier makes PID 1 destroy itself (see `worker.main._self_destruct`);
5. Docker's `RestartCount` must increment on its own and the job must complete within
   `LEASE_SECONDS + 60`, with `attempts = 2`, the lease cleared, exactly one embedding per
   (chunk, model, model_revision, preproc_version), correct copied and inferred vectors, and no
   duplicate job row.

Opt-in (it costs ~`LEASE_SECONDS` + warm-up): `HLM_O2_RESTART=1`, or `make test-o2`.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest

from hlmemo.core import EMBEDDING_DIMS, MODEL_ID, MODEL_REVISION
from hlmemo.core.write_service import PREPROC_VERSION
from hlmemo.worker.main import LEASE_SECONDS

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PROJECT = "hlmemo-o2"
BARRIER_NAME = "o2-before-mark-done"
DEADLINE_SECONDS = LEASE_SECONDS + 60  # §4: the job must complete inside this window

TEXT_COPIED = "O2 predecessor paragraph, carried into the revision without a single change."
TEXT_NOVEL = "O2 paragraph that only the revision introduces, so it has to be inferred."


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Override tests/conftest.py's autouse truncation: this module owns its own database and must
    never touch the one `HLM_TEST_DSN` points at."""
    yield


# --------------------------------------------------------------------------- compose plumbing
@dataclass(slots=True)
class Stack:
    env: dict[str, str]
    dsn: str
    worker_cid: str


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _compose(env: dict[str, str], *args: str, check: bool = True, timeout: float = 900) -> str:
    proc = subprocess.run(
        ["docker", "compose", "-p", COMPOSE_PROJECT, *args],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"docker compose {' '.join(args)} failed ({proc.returncode})\n{proc.stderr}")
    return proc.stdout + proc.stderr


def _restart_count(cid: str) -> int:
    out = subprocess.run(
        ["docker", "inspect", "--format", "{{.RestartCount}}", cid],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    return int(out)


def _wait(predicate, *, timeout: float, what: str, interval: float = 1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


@pytest.fixture(scope="module")
def o2_stack():
    if os.environ.get("HLM_O2_RESTART") != "1":
        pytest.skip("O2 crash test is opt-in and slow: set HLM_O2_RESTART=1 (or run `make test-o2`)")
    if shutil.which("docker") is None:
        pytest.skip("docker not on PATH")

    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k != "HLM_TEST_DSN"}
    env.update(
        HLM_DB_PORT=str(port),
        HLM_WORKER_TEST_BARRIER=BARRIER_NAME,
        HLM_BAKE_MODELS="1",  # real baked models, no host bind mount
    )
    dsn = f"postgresql://hlm:hlm@127.0.0.1:{port}/hlm"

    _compose(env, "down", "-v", "--remove-orphans", check=False)
    try:
        _compose(env, "up", "-d", "--build", "db", "migrate", "worker", timeout=2400)
        _wait_for_migrated_db(dsn)
        cid = _compose(env, "ps", "-q", "worker").strip().splitlines()[-1].strip()
        assert cid, "worker container not created"
        assert _replicas(env) == 1, "O2 requires exactly one worker"
        _wait(
            lambda: "worker: polling" in _compose(env, "logs", "--no-color", "worker", check=False),
            timeout=180,
            what="the worker to start polling",
        )
        yield Stack(env=env, dsn=dsn, worker_cid=cid)
    finally:
        logs = _compose(env, "logs", "--no-color", "--tail", "400", "worker", check=False)
        print("\n----- o2 worker logs (tail) -----\n" + logs)
        _compose(env, "down", "-v", "--remove-orphans", check=False)


def _replicas(env: dict[str, str]) -> int:
    return len([line for line in _compose(env, "ps", "-q", "worker").splitlines() if line.strip()])


def _wait_for_migrated_db(dsn: str, timeout: float = 300) -> None:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(dsn, connect_timeout=3, autocommit=True) as conn:
                row = conn.execute("SELECT to_regclass('public.jobs')").fetchone()
                if row and row[0]:
                    return
        except psycopg.Error as exc:  # noqa: PERF203 - polling a container that is still booting
            last = exc
        time.sleep(1.0)
    raise AssertionError(f"isolated db never reached phase0@head: {last}")


# --------------------------------------------------------------------------- SQL seeding
def _norm(text: str) -> str:
    return text.lower()


def _seed_project_and_event(conn: psycopg.Connection) -> tuple[int, int]:
    project_id = conn.execute(
        "INSERT INTO projects (slug, name) VALUES ('o2-crash', 'O2 crash') RETURNING project_id"
    ).fetchone()[0]
    event_id = conn.execute(
        "INSERT INTO events (project_id, device_id, client, request_id, kind, payload,"
        " payload_sha256, occurred_at) VALUES (%s, 1, 'pytest-o2', %s, 'write', '{}'::jsonb,"
        " repeat('0', 64), now()) RETURNING event_id",
        (project_id, str(uuid.uuid4())),
    ).fetchone()[0]
    return project_id, event_id


def _seed_version(
    conn: psycopg.Connection,
    *,
    project_id: int,
    event_id: int,
    title: str,
    texts: list[str],
    supersedes: int | None = None,
) -> tuple[int, list[int]]:
    version_id = conn.execute(
        """
        INSERT INTO memory_versions (logical_id, project_id, project_ids, kind, title, body,
                                     token_count, valid_from, recorded_at, source_event_id,
                                     supersedes_version_id)
        VALUES (nextval('logical_id_seq'), %(pid)s, ARRAY[%(pid)s]::bigint[], 'fact', %(title)s,
                %(body)s, %(tokens)s, now(), now(), %(event)s, %(sup)s)
        RETURNING version_id
        """,
        {
            "pid": project_id,
            "title": title,
            "body": "\n\n".join(texts),
            "tokens": sum(len(t.split()) for t in texts),
            "event": event_id,
            "sup": supersedes,
        },
    ).fetchone()[0]
    chunk_ids = []
    for ordinal, text in enumerate(texts):
        chunk_ids.append(
            conn.execute(
                """
                INSERT INTO chunks (version_id, project_ids, device_scope, ordinal, char_start,
                                    char_end, text, text_norm, e5_tokens)
                VALUES (%s, ARRAY[%s]::bigint[], 'all', %s, 0, %s, %s, %s, %s)
                RETURNING chunk_id
                """,
                (version_id, project_id, ordinal, len(text), text, _norm(text), len(text.split())),
            ).fetchone()[0]
        )
    return version_id, chunk_ids


def _enqueue_embed(conn: psycopg.Connection, version_id: int, event_id: int) -> int:
    payload = {
        "version_id": version_id,
        "model": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "preproc_version": PREPROC_VERSION,
        "dims": EMBEDDING_DIMS,
    }
    return conn.execute(
        "INSERT INTO jobs (kind, dedupe_key, source_event_id, payload, run_after)"
        " VALUES ('embed', %s, %s, %s::jsonb, now()) RETURNING job_id",
        (f"embed:{version_id}:{MODEL_ID}@{MODEL_REVISION[:8]}", event_id, json.dumps(payload)),
    ).fetchone()[0]


def _create_barrier_table(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hlm_test_barrier (
            name       text PRIMARY KEY,
            armed      boolean NOT NULL DEFAULT false,
            released   boolean NOT NULL DEFAULT false,
            action     text    NOT NULL DEFAULT 'sigkill',
            hit_at     timestamptz,
            hit_job_id bigint,
            hit_pid    integer
        )
        """
    )


def _vec_text(conn: psycopg.Connection, chunk_id: int) -> str:
    row = conn.execute(
        "SELECT vec::text FROM embeddings WHERE chunk_id = %s AND model = %s"
        " AND model_revision = %s AND preproc_version = %s",
        (chunk_id, MODEL_ID, MODEL_REVISION, PREPROC_VERSION),
    ).fetchone()
    assert row, f"no embedding for chunk {chunk_id}"
    return row[0]


def _vec(conn: psycopg.Connection, chunk_id: int) -> list[float]:
    return [float(x) for x in _vec_text(conn, chunk_id).strip("[]").split(",")]


def _poll(conn_dsn: str, sql: str, params: tuple, *, timeout: float, what: str):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        with psycopg.connect(conn_dsn, autocommit=True, connect_timeout=5) as c:
            last = c.execute(sql, params).fetchone()
        if last and last[0]:
            return last
        time.sleep(1.0)
    raise AssertionError(f"timed out after {timeout}s waiting for {what} (last row: {last!r})")


# --------------------------------------------------------------------------- the test
def test_worker_sigkill_after_vector_inserts_restarts_and_reclaims(o2_stack: Stack) -> None:
    dsn = o2_stack.dsn

    # 1. warm-up: the single worker embeds two seed versions with the real baked model. This both
    #    warms the ONNX session and produces the reference vectors the assertions compare against.
    with psycopg.connect(dsn, autocommit=True) as conn:
        _create_barrier_table(conn)  # present but disarmed: warm-up jobs must not block
        project_id, event_id = _seed_project_and_event(conn)
        pred_vid, (pred_chunk,) = _seed_version(
            conn, project_id=project_id, event_id=event_id, title="pred", texts=[TEXT_COPIED]
        )
        ref_vid, (ref_chunk,) = _seed_version(
            conn, project_id=project_id, event_id=event_id, title="ref", texts=[TEXT_NOVEL]
        )
        warm_jobs = [_enqueue_embed(conn, pred_vid, event_id), _enqueue_embed(conn, ref_vid, event_id)]

    _poll(
        dsn,
        "SELECT count(*) = 2 FROM jobs WHERE job_id = ANY(%s) AND status = 'done'",
        (warm_jobs,),
        timeout=300,
        what="the warm-up jobs to be embedded by the real model",
    )
    with psycopg.connect(dsn, autocommit=True) as conn:
        expected_copied = _vec_text(conn, pred_chunk)  # copied byte-for-byte by §1.1 (5)
        expected_inferred = _vec(conn, ref_chunk)  # same text, same model => same vector
    time.sleep(10)  # §4: warm for ten seconds

    restarts_before = _restart_count(o2_stack.worker_cid)

    # 2. arm the one-shot barrier, then seed the revision: one copyable + one novel chunk.
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO hlm_test_barrier (name, armed, released, action)"
            " VALUES (%s, true, false, 'sigkill')"
            " ON CONFLICT (name) DO UPDATE SET armed = true, released = false, hit_at = NULL",
            (BARRIER_NAME,),
        )
        rev_vid, (rev_copy_chunk, rev_novel_chunk) = _seed_version(
            conn,
            project_id=project_id,
            event_id=event_id,
            title="revision",
            texts=[TEXT_COPIED, TEXT_NOVEL],
            supersedes=pred_vid,
        )
        rev_job = _enqueue_embed(conn, rev_vid, event_id)

    # 3. the barrier is claimed inside the fenced transaction, after both inserts.
    _poll(
        dsn,
        "SELECT hit_at IS NOT NULL, hit_job_id, hit_pid FROM hlm_test_barrier WHERE name = %s",
        (BARRIER_NAME,),
        timeout=180,
        what="the worker to reach the barrier before _mark_done",
    )
    with psycopg.connect(dsn, autocommit=True) as conn:
        _, hit_job_id, hit_pid = conn.execute(
            "SELECT hit_at, hit_job_id, hit_pid FROM hlm_test_barrier WHERE name = %s", (BARRIER_NAME,)
        ).fetchone()
        assert hit_job_id == rev_job
        assert hit_pid == 1, f"the worker must be PID 1 for a self-kill, got {hit_pid}"

        # An independent connection sees the job running with NOTHING committed for the revision.
        status, attempts, lease_token = conn.execute(
            "SELECT status, attempts, lease_token FROM jobs WHERE job_id = %s", (rev_job,)
        ).fetchone()
        assert (status, attempts) == ("running", 1)
        assert lease_token is not None
        assert _revision_vector_count(conn, rev_vid) == 0, (
            "the copied and inferred inserts must still be uncommitted while the barrier is held"
        )

    # 4. release the barrier: PID 1 destroys itself. Nothing restarts the container by hand.
    released_at = time.monotonic()
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("UPDATE hlm_test_barrier SET released = true WHERE name = %s", (BARRIER_NAME,))

    # 5. Docker's `restart: unless-stopped` brings the worker back on its own.
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and _restart_count(o2_stack.worker_cid) <= restarts_before:
        time.sleep(1.0)
    restarts_after = _restart_count(o2_stack.worker_cid)
    assert restarts_after > restarts_before, (
        f"docker RestartCount did not increase ({restarts_before} -> {restarts_after}): "
        "the worker did not die, or the restart policy did not fire"
    )

    # 6. the restarted worker reclaims the job once the lease expires and finishes it.
    _poll(
        dsn,
        "SELECT status = 'done', attempts FROM jobs WHERE job_id = %s",
        (rev_job,),
        timeout=DEADLINE_SECONDS,
        what=f"job {rev_job} to complete within LEASE_SECONDS+60",
    )
    elapsed = time.monotonic() - released_at
    assert elapsed <= DEADLINE_SECONDS

    with psycopg.connect(dsn, autocommit=True) as conn:
        status, attempts, lease_token, lease_until, done_at, last_error = conn.execute(
            "SELECT status, attempts, lease_token, lease_until, done_at, last_error"
            " FROM jobs WHERE job_id = %s",
            (rev_job,),
        ).fetchone()
        assert status == "done"
        assert attempts == 2, f"one crashed attempt + one successful re-lease, got {attempts}"
        assert lease_token is None and lease_until is None, "the lease must be cleared"
        assert done_at is not None and last_error is None

        # exactly one embedding per (chunk, model, model_revision, preproc_version)
        rows = conn.execute(
            "SELECT c.chunk_id, count(*) FROM chunks c JOIN embeddings e USING (chunk_id)"
            " WHERE c.version_id = %s GROUP BY c.chunk_id ORDER BY c.chunk_id",
            (rev_vid,),
        ).fetchall()
        assert [n for _, n in rows] == [1, 1], f"expected one vector per revision chunk, got {rows}"
        assert {cid for cid, _ in rows} == {rev_copy_chunk, rev_novel_chunk}
        assert _revision_vector_count(conn, rev_vid) == 2

        # the copyable chunk got the predecessor's vector verbatim (§1.1 (5), no re-inference)
        assert _vec_text(conn, rev_copy_chunk) == expected_copied

        # the novel chunk was really inferred, and matches the reference embedding of that text
        inferred = _vec(conn, rev_novel_chunk)
        assert len(inferred) == EMBEDDING_DIMS
        assert max(abs(a - b) for a, b in zip(inferred, expected_inferred, strict=True)) < 1e-5
        assert inferred != [float(x) for x in expected_copied.strip("[]").split(",")]

        # no duplicate job for the revision
        assert (
            conn.execute(
                "SELECT count(*) FROM jobs WHERE payload->>'version_id' = %s", (str(rev_vid),)
            ).fetchone()[0]
            == 1
        )
        # ... and no second worker was spawned to do it
        assert _replicas(o2_stack.env) == 1

    # 7. the restarted worker publishes the O2 heartbeat.
    logs = _compose(o2_stack.env, "logs", "--no-color", "worker", check=False)
    assert "worker heartbeat:" in logs, "no heartbeat line in the worker log"
    assert "TEST BARRIER" in logs


def _revision_vector_count(conn: psycopg.Connection, version_id: int) -> int:
    return conn.execute(
        "SELECT count(*) FROM embeddings e JOIN chunks c USING (chunk_id)"
        " WHERE c.version_id = %s AND e.model = %s AND e.model_revision = %s"
        " AND e.preproc_version = %s",
        (version_id, MODEL_ID, MODEL_REVISION, PREPROC_VERSION),
    ).fetchone()[0]
