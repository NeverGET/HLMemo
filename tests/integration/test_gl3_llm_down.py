"""G-L3 — the LLM is down; the core does not notice (PHASE2-4-ROADMAP W2a; Sol 35 #3).

Production-shaped: the REAL ASGI app runs as its own uvicorn process (``python -m
hlmemo.server.app``) and the librarian as its own worker process (``python -m
hlmemo.librarian.worker``) against a stub OpenAI-compatible server, all on a CLONE of the loaded
G3 retrieval world (``hlm_retr``; this test writes into it).

While 100 ``memory.write`` calls (≈6 kB bodies each, a librarian job enqueued after every ack) are
being acknowledged, three query callers issue ``memory.query`` over HTTP continuously and every
query is timed. The stub STALLS 30 s per request for the first 50 writes, then returns 503 (the
breaker opens, jobs are handed back without consuming attempts). Afterwards the stub recovers and
every job must complete — 0 lost, 0 duplicate ``librarian`` events.

Gate: query p95 ≤ 500 ms over the queries timed DURING the writes; 100/100 writes acked.

Two body types: ``identifier`` bodies repeat the fixture's query identifiers (every new chunk is
a trigram candidate of identifier queries: the worst case) and ``neutral`` bodies are mixed prose.
Release-blocking (D-063): ``make gate-release HLM_TEST_DSN=<disposable clone of hlm_retr>``.
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import psycopg
import pytest

from hlmemo.auth.tokens import hash_token
from tests.integration._librarian_fixtures import CONTRADICTS_B, enqueue_pair, seed_reserved
from tests.integration._read_fixtures import (  # noqa: F401 - fixtures by import
    MAIN,
    RetrWorld,
    _clean_tables,
    embedder,
    load_queries,
    retr_world,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("HLM_GL3") != "1", reason="G-L3 runs on a hlm_retr clone (HLM_GL3=1)"),
]

ROOT = Path(__file__).resolve().parents[2]
CALLERS = 3
N_WRITES = 100
P95_LIMIT_MS = 500.0
STALL_S = 30.0
LOADER_TOKEN = "hlm_" + "L" * 43
READER_TOKEN = "hlm_" + "R" * 43
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


# --------------------------------------------------------------------------- stub provider
class _Stub:
    mode = "stall"
    requests = 0
    # [start, end | None, mode-at-arrival] per provider request (time.monotonic seconds)
    hits: list[list] = []


class _StubHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        _Stub.requests += 1
        mode = _Stub.mode
        hit = [time.monotonic(), None, mode]
        _Stub.hits.append(hit)
        if mode == "stall":
            time.sleep(STALL_S)
            mode = "ok"
        if mode == "503":
            body, status = b'{"error":{"code":503,"message":"stub down"}}', 503
        else:
            body = json.dumps(
                {
                    "choices": [{"message": {"content": json.dumps(CONTRADICTS_B)}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 200, "completion_tokens": 20},
                }
            ).encode()
            status = 200
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        hit[1] = time.monotonic()

    def log_message(self, *args: object) -> None:
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_WORDS = (
    "deploy review migration cache latency budget session card lesson retry queue worker index backup "
    "restore token grant project device policy archive summary decision note sprint release config "
    "Karte Dienst Speicher Abfrage Sitzung Entscheidung proje karar oturum kayıt yedek sürüm ayar"
).split()


def _identifier_body(i: int) -> str:
    """≈ 6 kB repeating the G3 fixture's identifier vocabulary (worst case for the trigram list)."""
    return "\n".join(
        f"Load note {i}.{k}: svc-qx7 reads APP_DB_DSN, retries E4193 with backoff; Karte {k} güncellendi,"
        f" der Dienst läuft auf Port {8000 + k}."
        for k in range(60)
    )


def _body(i: int) -> str:
    """≈ 6 kB of mixed-language prose with unique note ids. It deliberately avoids the G3 fixture's
    query identifiers (svc-*, APP_*, E4xxx): repeating those 6,000 times would make every new
    chunk a trigram candidate of every identifier query (a data artefact, measured: 6.5 s scans)."""
    import random

    rng = random.Random(i)
    lines = [f"note-{i}-{k}: " + " ".join(rng.choice(_WORDS) for _ in range(12)) + "." for k in range(60)]
    return "\n".join(lines)


async def _rpc(client: httpx.AsyncClient, token: str, name: str, args: dict) -> dict:
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
    r = await client.post("/mcp", json=msg, headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"})
    r.raise_for_status()
    text = r.text
    if text.startswith("event:") or "data:" in text[:20]:
        text = next(line[5:].strip() for line in text.splitlines() if line.startswith("data:"))
    result = json.loads(text)["result"]
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


def _spawn(module: str, env: dict[str, str], cwd: Path, log_name: str) -> subprocess.Popen:
    with open(cwd / log_name, "w") as log:  # the child keeps its own descriptor
        return subprocess.Popen(
            [sys.executable, "-m", module], cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT
        )


def _p95(values: list[float]) -> float:
    return statistics.quantiles(values, n=100)[94]


async def _wait_ready(base: str, proc: subprocess.Popen, limit_s: float = 180.0) -> None:
    deadline = time.monotonic() + limit_s
    async with httpx.AsyncClient(base_url=base, timeout=5) as c:
        while time.monotonic() < deadline:
            assert proc.poll() is None, "api process exited"
            try:
                if (await c.get("/ready")).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
    raise AssertionError("api never became ready")


@pytest.mark.parametrize("body_kind", ["identifier", "neutral"])
async def test_gl3_llm_down_core_unaffected(
    db_dsn, connect, retr_world: RetrWorld, tmp_path, body_kind
) -> None:  # noqa: ANN001
    world = retr_world
    make_body = _identifier_body if body_kind == "identifier" else _body
    run = f"gl3{body_kind[0]}"
    _Stub.mode, _Stub.requests, _Stub.hits = "stall", 0, []
    window: list[float] = []  # monotonic bounds of the timed queries
    async with await connect() as conn:
        await seed_reserved(conn)
        for did, tok in ((world.loader_id, LOADER_TOKEN), (world.reader_id, READER_TOKEN)):
            await conn.execute(
                "UPDATE devices SET token_sha256 = %s WHERE device_id = %s", (hash_token(tok), did)
            )
        await conn.commit()

    stub_port, api_port = _free_port(), _free_port()
    stub = ThreadingHTTPServer(("127.0.0.1", stub_port), _StubHandler)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("HLM_")}
    base_env.update(
        HLM_DB_DSN=db_dsn,
        HLM_MODELS_DIR=os.environ.get("HLM_MODELS_DIR", str(ROOT / "models")),
        PYTHONUNBUFFERED="1",
        ORT_DISABLE_TELEMETRY="1",
    )
    api_env = {
        **base_env,
        "HLM_API_HOST": "127.0.0.1",
        "HLM_API_PORT": str(api_port),
        "HLM_CURSOR_SECRET": "gl3",
    }
    lib_env = {
        **base_env,
        "HLM_LIBRARIAN_ENABLED": "true",
        "HLM_LLM_MODE": "live",
        "HLM_LLM_BASE_URL": f"http://127.0.0.1:{stub_port}/v1",
        "HLM_LLM_MODEL": "stub/model",
        "HLM_LLM_API_KEY": "stub",
        "HLM_PRICE_IN_PER_M": "1",
        "HLM_PRICE_OUT_PER_M": "1",
        "HLM_LLM_BREAKER_OPEN_S": "1",
        "HLM_LLM_BREAKER_MAX_OPEN_S": "2",
        "HLM_LIBRARIAN_POLL_S": "0.2",
        "HLM_LIBRARIAN_HEARTBEAT_FILE": str(tmp_path / "hb.json"),
    }
    api = _spawn("hlmemo.server.app", api_env, tmp_path, "api.log")
    lib = _spawn("hlmemo.librarian.worker", lib_env, tmp_path, "librarian.log")
    base = f"http://127.0.0.1:{api_port}"
    candidates = sorted(world.version_to_logical)[:50]
    lat: list[float] = []
    write_ms: list[float] = []
    quiet: list[float] = []
    try:
        await _wait_ready(base, api)
        queries = [q["query"] for q in load_queries()]
        async with httpx.AsyncClient(base_url=base, timeout=30) as client:
            for text in queries[:20]:  # warm-up, not timed
                await _rpc(
                    client,
                    READER_TOKEN,
                    "memory.query",
                    {"project": MAIN, "query": text, "token_budget": 2000},
                )

            async def quiet_caller(i: int) -> None:  # reference: the same load with no writes
                for k in range(i, 60, CALLERS):
                    t0 = time.perf_counter()
                    await _rpc(
                        client,
                        READER_TOKEN,
                        "memory.query",
                        {"project": MAIN, "query": queries[k % len(queries)], "token_budget": 2000},
                    )
                    quiet.append((time.perf_counter() - t0) * 1000)

            await asyncio.gather(*(quiet_caller(i) for i in range(CALLERS)))
            done = asyncio.Event()

            async def caller(i: int) -> None:
                k = i
                while not done.is_set():
                    t0 = time.perf_counter()
                    window.append(time.monotonic())
                    await _rpc(
                        client,
                        READER_TOKEN,
                        "memory.query",
                        {"project": MAIN, "query": queries[k % len(queries)], "token_budget": 2000},
                    )
                    lat.append((time.perf_counter() - t0) * 1000)
                    window.append(time.monotonic())
                    k += CALLERS

            async def writer() -> int:
                acked = 0
                async with await psycopg.AsyncConnection.connect(db_dsn) as conn:
                    for i in range(N_WRITES):
                        if i == N_WRITES // 2:
                            _Stub.mode = "503"
                        t0 = time.perf_counter()
                        ack = await _rpc(
                            client,
                            LOADER_TOKEN,
                            "memory.write",
                            {
                                "project": MAIN,
                                "request_id": str(uuid.uuid4()),
                                "client": "pytest/gl3",
                                "items": [{"kind": "fact", "title": f"G-L3 note {i}", "body": make_body(i)}],
                            },
                        )
                        write_ms.append((time.perf_counter() - t0) * 1000)
                        acked += 1
                        await enqueue_pair(
                            conn,
                            project_id=world.main_id,
                            trigger_device_id=world.loader_id,
                            subject_vid=ack["versions"][0]["version_id"],
                            candidate_vids=[candidates[i % len(candidates)]],
                            key=f"{run}:{i}",
                        )
                        await conn.commit()
                done.set()
                return acked

            results = await asyncio.gather(writer(), *(caller(i) for i in range(CALLERS)))
            acked = results[0]
        assert acked == N_WRITES

        # the provider is still down: wait for the breaker to have opened, then recover
        deadline = time.monotonic() + STALL_S + 60
        while _Stub.requests < 3 and time.monotonic() < deadline:  # noqa: ASYNC110
            await asyncio.sleep(0.5)
        _Stub.mode = "ok"
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            async with await connect() as conn:
                cur = await conn.execute(
                    "SELECT count(*) FILTER (WHERE status = 'done'), count(*) FROM jobs"
                    " WHERE dedupe_key LIKE 'librarian_write:' || %(run)s || ':%%'",
                    {"run": run},
                )
                n_done, total = await cur.fetchone()
                await conn.commit()
            if n_done == total == N_WRITES:
                break
            assert lib.poll() is None, "librarian process exited"
            await asyncio.sleep(1)
    finally:
        for proc in (lib, api):
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
        stub.shutdown()

    p95 = _p95(lat)
    # Sol 37 #3: the librarian really was stalled / failing WHILE the queries were being timed
    lo, hi = min(window), max(window)
    overlap = [m for a, b, m in _Stub.hits if m in ("stall", "503") and a <= hi and (b is None or b >= lo)]
    print(
        f"\nG-L3 [{body_kind} bodies] (real api + librarian processes):"
        f" {len(lat)} queries during {N_WRITES} writes:"
        f" p50 {statistics.median(lat):.1f} ms p95 {p95:.1f} ms max {max(lat):.1f} ms;"
        f" write p50 {statistics.median(write_ms):.1f} ms p95 {_p95(write_ms):.1f} ms;"
        f" stub requests {_Stub.requests}; reference without writes: {len(quiet)} queries"
        f" p50 {statistics.median(quiet):.1f} ms p95 {_p95(quiet):.1f} ms;"
        f" degraded provider requests overlapping the timed window:"
        f" stall {overlap.count('stall')} 503 {overlap.count('503')} ({hi - lo:.1f} s window)"
    )
    assert overlap, f"no stalled/503 provider request overlapped the timed queries: {_Stub.hits[:6]}"
    assert p95 <= P95_LIMIT_MS
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT status, count(*) FROM jobs"
            " WHERE dedupe_key LIKE 'librarian_write:' || %(run)s || ':%%' GROUP BY 1",
            {"run": run},
        )
        assert dict(await cur.fetchall()) == {"done": N_WRITES}  # 0 lost
        cur = await conn.execute(
            "SELECT count(*), count(DISTINCT payload->'resolved'->'done'->>'dedupe_key') FROM events"
            " WHERE kind = 'librarian'"
            " AND payload->'resolved'->'done'->>'dedupe_key' LIKE 'librarian_write:' || %(run)s || ':%%'",
            {"run": run},
        )
        assert await cur.fetchone() == (N_WRITES, N_WRITES)  # 0 duplicate librarian events
