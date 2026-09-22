"""Hybrid G3 recall + G4 latency with google/gemini-embedding-2 (@1536, task prefixes) as the vector leg.

Uses the PRODUCTION read path (``hlmemo.core.read_service.query``) unchanged. Only these seams are
swapped, inside this process:

* ``ReadDeps._embedder`` -> an OpenRouter query embedder (query prefix
  ``task: search result | query: <q>``, dimensions=1536, L2-normalised);
* ``read_service.MODEL_ID`` / ``MODEL_REVISION`` -> the rows loaded by ``load_vectors.py`` into the
  ``hlm_retr_g2`` copy (``google/gemini-embedding-2`` / ``d1536-prefix-v1``);
* for the *concurrent* variant only: ``read_queries.vector_candidates`` is wrapped so that the
  query embedding (started as an asyncio task before ``query()``) is awaited only right before
  the vector SQL, i.e. it overlaps term split + lexical + trigram SQL.

Production ``Embedder.embed_query`` is synchronous. An HTTP embedder behind that sync interface
would block the event loop; that literal drop-in is measured as ``sync-dropin``. The other
variants model an async embedder: the harness awaits the HTTP call (``seq``) and hands the vector
to the sync ``embed_query`` through a contextvar.

G3 = test_g3_recall.py (100 queries, reader device, budget 8000, Recall@5 on deduped hits).
G4 = test_g4_latency.py (100 queries x 3 shuffled with seed 20260922, 3 callers, own connection
each, 20 warm-up queries, budget 2000, whole-call latency).

Every connection is set READ ONLY (``memory.query`` does not write). Usage:
    .venv/bin/python bench/embeddings/hybrid/run_hybrid.py [--only g3,g4] [--cap 1.5]
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import psycopg

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from hlmemo.core import read_service  # noqa: E402
from hlmemo.core.budget import Meter  # noqa: E402
from hlmemo.core.embedder import Embedder, default_model_dir  # noqa: E402
from hlmemo.core.read_service import ReadDeps, query  # noqa: E402
from hlmemo.db import read_queries  # noqa: E402
from tests.integration._read_fixtures import MAIN, _contexts, _maps, _state, load_items, load_queries  # noqa: E402

DSN_E5 = "postgresql://hlm:hlm@127.0.0.1:5432/hlm_retr"  # read-only use
DSN_G2 = "postgresql://hlm:hlm@127.0.0.1:5432/hlm_retr_g2"
G2_MODEL, G2_REV = "google/gemini-embedding-2", "d1536-prefix-v1"
URL = "https://openrouter.ai/api/v1/embeddings"
Q_PREFIX = "task: search result | query: {}"

K, G3_BUDGET = 5, 8000
CALLERS, N_QUERIES, WARMUP, G4_BUDGET = 3, 300, 20, 2000
ORIG_VECTOR_CANDIDATES = read_queries.vector_candidates


def load_key() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("OPENROUTER_API_KEY missing in .env")


def pct(sorted_ms: list[float], p: float) -> float:  # same as test_g4_latency._percentile
    if not sorted_ms:
        return float("nan")
    k = (len(sorted_ms) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(sorted_ms) - 1)
    return sorted_ms[lo] + (sorted_ms[hi] - sorted_ms[lo]) * (k - lo)


def summary(ms: list[float]) -> dict[str, float]:
    s = sorted(ms)
    return {
        "n": len(s),
        "p50": pct(s, 0.5),
        "p95": pct(s, 0.95),
        "p99": pct(s, 0.99),
        "mean": statistics.fmean(s),
        "max": s[-1],
    }


# --------------------------------------------------------------------------- query embedder
_staged: contextvars.ContextVar[Any] = contextvars.ContextVar("staged_qvec")


@dataclass
class _Pending:
    task: asyncio.Task


@dataclass
class GeminiQueryEmbedder:
    """Async OpenRouter query embedder + the sync ``embed_query`` seam the read path calls."""

    key: str
    cap: float
    spent: float = 0.0
    calls: int = 0
    cache: dict[str, np.ndarray] = field(default_factory=dict)
    use_cache: bool = False
    sync_http: httpx.Client | None = None
    http: httpx.AsyncClient | None = None
    api_ms: list[float] = field(default_factory=list)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    def _payload(self, text: str) -> dict:
        return {"model": G2_MODEL, "input": [Q_PREFIX.format(text)], "dimensions": 1536}

    def _parse(self, r: httpx.Response) -> np.ndarray:
        r.raise_for_status()
        j = r.json()
        self.spent += float((j.get("usage") or {}).get("cost") or 0.0)
        self.calls += 1
        if self.spent > self.cap:
            raise RuntimeError(f"cost cap ${self.cap} exceeded (${self.spent:.4f})")
        v = np.asarray(j["data"][0]["embedding"], dtype=np.float32)
        assert v.shape == (1536,)
        return v / np.linalg.norm(v)

    async def fetch(self, text: str) -> np.ndarray:
        if self.use_cache and text in self.cache:
            self.api_ms.append(0.0)
            return self.cache[text]
        assert self.http is not None
        t0 = time.perf_counter()
        for attempt in range(5):
            try:
                r = await self.http.post(URL, json=self._payload(text))
                if r.status_code == 429 or r.status_code >= 500:
                    raise httpx.HTTPStatusError("retryable", request=r.request, response=r)
                v = self._parse(r)
                break
            except (httpx.TransportError, httpx.HTTPStatusError, KeyError):
                if attempt == 4:
                    raise
                await asyncio.sleep(0.5 * 2**attempt)
        self.api_ms.append((time.perf_counter() - t0) * 1000)
        self.cache[text] = v
        return v

    # ---- the sync seam used by read_service.query
    def embed_query(self, text: str) -> Any:
        staged = _staged.get(None)
        if staged is not None:
            return staged  # ndarray (seq / warm) or _Pending (concurrent)
        # literal sync drop-in: blocking HTTP inside the event loop
        assert self.sync_http is not None
        t0 = time.perf_counter()
        v = self._parse(self.sync_http.post(URL, json=self._payload(text)))
        self.api_ms.append((time.perf_counter() - t0) * 1000)
        return v


async def _vector_candidates_awaiting(conn, f, qvec, **kw):  # noqa: ANN001, ANN003
    if isinstance(qvec, _Pending):
        qvec = await qvec.task
    return await ORIG_VECTOR_CANDIDATES(conn, f, qvec, **kw)


# --------------------------------------------------------------------------- world
async def connect(dsn: str) -> psycopg.AsyncConnection:
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    await conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    await conn.set_autocommit(False)
    await conn.execute("SET TIME ZONE 'UTC'")  # as tests/conftest.py connect()
    return conn


async def world(dsn: str) -> dict[str, Any]:
    async with await connect(dsn) as conn:
        state = await _state(conn, len(load_items()))
        assert state is not None, f"fixture state mismatch in {dsn}"
        key_to_logical, logical_to_version = await _maps(conn)
    _, reader, _ = _contexts(state["projects"], state["devices"])
    return {
        "ctx": reader,
        "key_to_logical": key_to_logical,
        "version_to_logical": {v: lid for lid, v in logical_to_version.items()},
    }


def use_model(model: str, rev: str) -> None:
    read_service.MODEL_ID = model
    read_service.MODEL_REVISION = rev


# --------------------------------------------------------------------------- G3
async def run_g3(dsn: str, deps: ReadDeps, w: dict, pre=None) -> dict:  # noqa: ANN001
    per = []
    async with await connect(dsn) as conn:
        for qd in load_queries():
            tok = _staged.set(await pre(qd["query"])) if pre else None
            try:
                res = await query(
                    conn, w["ctx"], {"project": MAIN, "query": qd["query"], "token_budget": G3_BUDGET}, deps=deps
                )
            finally:
                if tok is not None:
                    _staged.reset(tok)
            lids = [w["version_to_logical"][int(h["clue"][1:].split(".", 1)[0])] for h in res["hits"]]
            gold = w["key_to_logical"][qd["gold_logical_key"]]
            rank = lids.index(gold) + 1 if gold in lids else None
            per.append(
                {"qid": qd["qid"], "lang": qd["lang"], "heavy": bool(qd["identifier_heavy"]),
                 "hit": gold in lids[:K], "rank": rank}
            )

    def r(sub: list[dict]) -> dict:
        return {"n": len(sub), "r5": sum(o["hit"] for o in sub) / len(sub)}

    out = {"all": r(per), "per": per, "ident": r([o for o in per if o["heavy"]]),
           "nonident": r([o for o in per if not o["heavy"]])}
    for lang in ("tr", "de", "en"):
        out[lang] = r([o for o in per if o["lang"] == lang])
    return out


# --------------------------------------------------------------------------- G4
def g4_plan() -> list[str]:
    queries = [q["query"] for q in load_queries()]
    rng = random.Random(20260922)
    plan = [q for _ in range(N_QUERIES // len(queries)) for q in queries]
    rng.shuffle(plan)
    return plan


async def run_g4(dsn: str, deps: ReadDeps, w: dict, mode: str, emb: GeminiQueryEmbedder | None) -> dict:
    """mode: e5 | sync-dropin | seq | concurrent | warm"""
    plan = g4_plan()
    ctx = w["ctx"]
    sql_ms: list[float] = []  # query() time excluding the awaited embedding (seq/warm only)

    async def run_one(conn, text: str) -> float:  # noqa: ANN001
        t0 = time.perf_counter()
        tok = None
        if mode in ("seq", "warm"):
            tok = _staged.set(await emb.fetch(text))
        elif mode == "concurrent":
            tok = _staged.set(_Pending(asyncio.ensure_future(emb.fetch(text))))
        t1 = time.perf_counter()
        try:
            res = await query(conn, ctx, {"project": MAIN, "query": text, "token_budget": G4_BUDGET}, deps=deps)
        finally:
            if tok is not None:
                _staged.reset(tok)
        t2 = time.perf_counter()
        assert res["budget"]["used"] <= G4_BUDGET
        if mode in ("seq", "warm"):
            sql_ms.append((t2 - t1) * 1000)
        return (t2 - t0) * 1000

    async with await connect(dsn) as conn:
        for text in plan[:WARMUP]:
            await run_one(conn, text)
    warm_api = len(emb.api_ms) if emb else 0
    sql_ms.clear()

    todo: asyncio.Queue[str | None] = asyncio.Queue()
    for text in plan:
        todo.put_nowait(text)
    for _ in range(CALLERS):
        todo.put_nowait(None)
    lat: list[float] = []

    async def caller() -> None:
        async with await connect(dsn) as conn:
            while True:
                text = await todo.get()
                if text is None:
                    return
                lat.append(await run_one(conn, text))

    wall0 = time.perf_counter()
    await asyncio.gather(*(caller() for _ in range(CALLERS)))
    wall = time.perf_counter() - wall0
    out = {"mode": mode, "e2e": summary(lat), "wall_s": wall, "qps": N_QUERIES / wall}
    if emb:
        api = emb.api_ms[warm_api:]
        out["api"] = summary(api) if api else None
    if sql_ms:
        out["query_minus_embed"] = summary(sql_ms)
    out["raw_ms"] = lat
    return out


# --------------------------------------------------------------------------- main
async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="g3,g4")
    ap.add_argument("--cap", type=float, default=1.5)
    args = ap.parse_args()
    only = set(args.only.split(","))
    res_p = HERE / "results.json"
    results: dict[str, Any] = json.loads(res_p.read_text()) if res_p.exists() else {}

    def save() -> None:
        res_p.write_text(json.dumps(results, indent=1))

    e5 = Embedder(default_model_dir())
    e5.embed_query("warmup")
    deps_e5 = ReadDeps(meter=Meter(), model_dir=default_model_dir(), cursor_secret=b"x" * 32, _embedder=e5)
    key = load_key()
    emb = GeminiQueryEmbedder(key=key, cap=args.cap, spent=results.get("spend_usd", 0.0))
    emb.http = httpx.AsyncClient(timeout=30, headers=emb._headers(), limits=httpx.Limits(max_connections=10))
    emb.sync_http = httpx.Client(timeout=30, headers=emb._headers())
    deps_g2 = ReadDeps(meter=Meter(), model_dir=default_model_dir(), cursor_secret=b"x" * 32, _embedder=emb)

    # _state() requires chunks == embeddings, which the g2 copy (fx-main vectors only) violates by
    # design; the copy is a TEMPLATE clone, so ids are identical -- verified via _maps() on both.
    w_e5 = await world(DSN_E5)
    async with await connect(DSN_G2) as conn:
        k2l, l2v = await _maps(conn)
    assert k2l == w_e5["key_to_logical"]
    assert {v: lid for lid, v in l2v.items()} == w_e5["version_to_logical"]
    w_g2 = w_e5

    try:
        if "g3" in only:
            from hlmemo.core import MODEL_ID, MODEL_REVISION

            use_model(MODEL_ID, MODEL_REVISION)
            results["g3_e5"] = await run_g3(DSN_E5, deps_e5, w_e5)
            print("G3 e5  ", {k: v for k, v in results["g3_e5"].items() if k != "per"})
            use_model(G2_MODEL, G2_REV)
            results["g3_gemini2"] = await run_g3(DSN_G2, deps_g2, w_g2, pre=emb.fetch)
            print("G3 gem2", {k: v for k, v in results["g3_gemini2"].items() if k != "per"})
            results["spend_usd"] = emb.spent
            save()
        if "g4" in only:
            from hlmemo.core import MODEL_ID, MODEL_REVISION

            use_model(MODEL_ID, MODEL_REVISION)
            results["g4_e5"] = await run_g4(DSN_E5, deps_e5, w_e5, "e5", None)
            print("G4 e5", results["g4_e5"]["e2e"])
            use_model(G2_MODEL, G2_REV)
            for mode in ("seq", "concurrent", "sync-dropin"):
                emb.use_cache = False
                if mode == "concurrent":
                    read_queries.vector_candidates = _vector_candidates_awaiting
                try:
                    results[f"g4_gemini2_{mode}"] = await run_g4(DSN_G2, deps_g2, w_g2, mode, emb)
                finally:
                    read_queries.vector_candidates = ORIG_VECTOR_CANDIDATES
                r = results[f"g4_gemini2_{mode}"]
                print(f"G4 gem2 {mode}", r["e2e"], "api", r.get("api"), "q-emb", r.get("query_minus_embed"))
                results["spend_usd"] = emb.spent
                save()
            emb.use_cache = True  # cache holds every query from the passes above
            results["g4_gemini2_warm"] = await run_g4(DSN_G2, deps_g2, w_g2, "warm", emb)
            print("G4 gem2 warm", results["g4_gemini2_warm"]["e2e"])
            results["spend_usd"] = emb.spent
            save()
    finally:
        results["spend_usd"] = emb.spent
        results["api_calls_this_run"] = emb.calls
        save()
        await emb.http.aclose()
        emb.sync_http.close()
    print(f"SPEND ${emb.spent:.4f} ({emb.calls} calls this run)")


if __name__ == "__main__":
    asyncio.run(main())
