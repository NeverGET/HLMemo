"""Vector-only embedding bake-off on the G3 fixture (fx-main).

Compares the stored local e5-small vectors against cloud embedding models via OpenRouter,
with an identical pipeline: exact cosine top-50 over all fx-main chunks, dedupe by logical_id
(best chunk wins), Recall@5/@10 + MRR@10 overall / per language / identifier-heavy.

The fixture DB is used READ-ONLY. Usage:
    .venv/bin/python bench/embeddings/run_embed_bench.py [--models m1,m2] [--cap 3.0]
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
import psycopg

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
DSN = os.environ.get("HLM_BENCH_DSN", "postgresql://hlm:hlm@127.0.0.1:5432/hlm_retr")
URL = "https://openrouter.ai/api/v1/embeddings"
MODELS = [
    "openai/text-embedding-3-small",
    "openai/text-embedding-3-large",
    "google/gemini-embedding-001",
    "baai/bge-m3",
    "mistralai/mistral-embed-2312",
]
# variant id -> request/format spec (id doubles as cache key)
VARIANTS: dict[str, dict] = {
    "google/gemini-embedding-2@1536+prefix": {
        "model": "google/gemini-embedding-2",
        "extra": {"dimensions": 1536},
        "q": "task: search result | query: {}",
        "d": "title: none | text: {}",
    },
    "google/gemini-embedding-2@1536": {"model": "google/gemini-embedding-2", "extra": {"dimensions": 1536}},
    "voyageai/voyage-4": {"model": "voyageai/voyage-4"},
    "perplexity/pplx-embed-v1-4b@1024": {"model": "perplexity/pplx-embed-v1-4b", "extra": {"dimensions": 1024}},
    "qwen/qwen3-embedding-4b@1024": {"model": "qwen/qwen3-embedding-4b", "extra": {"dimensions": 1024}},
    # Qwen3-Embedding is instruction-aware on the query side only; documents stay raw -> reuse corpus
    "qwen/qwen3-embedding-4b@1024+instruct": {
        "model": "qwen/qwen3-embedding-4b",
        "extra": {"dimensions": 1024},
        "q": "Instruct: Given a search query, retrieve relevant passages that answer the query\nQuery:{}",
        "corpus": "qwen/qwen3-embedding-4b@1024",
    },
}


def spec(vid: str) -> dict:
    v = VARIANTS.get(vid, {"model": vid})
    return {
        "model": v["model"],
        "extra": v.get("extra", {}),
        "q": v.get("q", "{}"),
        "d": v.get("d", "{}"),
        "corpus": v.get("corpus", vid),
    }


BATCH = 96
CONCURRENCY = 4
TOPN = 50
N_LAT = 20


def load_key() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("OPENROUTER_API_KEY missing in .env")


# --------------------------------------------------------------------------- data
def export_chunks() -> list[dict]:
    path = HERE / "chunks.jsonl"
    if path.exists():
        return [json.loads(line) for line in path.open(encoding="utf-8")]
    with psycopg.connect(DSN) as conn:
        conn.read_only = True
        rows = conn.execute(
            """
            SELECT c.chunk_id, mv.logical_id, c.text
            FROM chunks c JOIN memory_versions mv USING (version_id)
            JOIN projects p ON p.project_id = mv.project_id
            WHERE p.slug = 'fx-main' AND mv.superseded_at = 'infinity'
            ORDER BY c.chunk_id
            """
        ).fetchall()
    out = [{"chunk_id": r[0], "logical_id": r[1], "text": r[2]} for r in rows]
    with path.open("w", encoding="utf-8") as fh:
        for o in out:
            fh.write(json.dumps(o, ensure_ascii=False) + "\n")
    return out


def gold_map() -> dict[str, int]:
    with psycopg.connect(DSN) as conn:
        conn.read_only = True
        rows = conn.execute(
            """
            SELECT mv.logical_id, t.tag FROM memory_versions mv, unnest(mv.tags) AS t(tag)
            WHERE t.tag LIKE 'lk:%%' AND mv.superseded_at = 'infinity'
            """
        ).fetchall()
    return {tag[3:]: lid for lid, tag in rows}


def load_queries() -> list[dict]:
    p = ROOT / "tests/fixtures/g3/queries.jsonl"
    return [json.loads(line) for line in p.open(encoding="utf-8") if line.strip()]


def baseline_vectors(chunk_ids: list[int]) -> np.ndarray:
    with psycopg.connect(DSN) as conn:
        conn.read_only = True
        rows = conn.execute(
            "SELECT chunk_id, vec::text FROM embeddings WHERE chunk_id = ANY(%s)", (chunk_ids,)
        ).fetchall()
    by_id = {cid: np.array(json.loads(v), dtype=np.float32) for cid, v in rows}
    return np.stack([by_id[c] for c in chunk_ids])


# --------------------------------------------------------------------------- API
class Client:
    def __init__(self, key: str, cap: float, spent: float) -> None:
        self.http = httpx.Client(
            timeout=120, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        )
        self.cap = cap
        self.spent = spent

    def embed(self, vid: str, texts: list[str]) -> tuple[np.ndarray, float]:
        sp = spec(vid)
        model = sp["model"]
        delay = 2.0
        for attempt in range(8):
            try:
                r = self.http.post(URL, json={"model": model, "input": texts, **sp["extra"]})
            except httpx.HTTPError as e:
                err = f"transport {type(e).__name__}"
            else:
                if r.status_code == 200:
                    j = r.json()
                    if "data" in j and len(j["data"]) == len(texts):
                        data = sorted(j["data"], key=lambda d: d.get("index", 0))
                        cost = float((j.get("usage") or {}).get("cost") or 0.0)
                        self.spent += cost
                        return np.array([d["embedding"] for d in data], dtype=np.float32), cost
                    err = f"bad body {str(j)[:200]}"
                elif r.status_code == 429 or r.status_code >= 500:
                    err = f"HTTP {r.status_code} {r.text[:200]}"
                elif r.status_code in (400, 413) and len(texts) > 1:
                    # payload too large for this provider: split and recurse
                    mid = len(texts) // 2
                    a, ca = self.embed(vid, texts[:mid])
                    b, cb = self.embed(vid, texts[mid:])
                    return np.concatenate([a, b]), ca + cb
                else:
                    raise RuntimeError(f"{model}: HTTP {r.status_code} {r.text[:300]}")
            print(f"  retry {attempt + 1} {model}: {err}", file=sys.stderr)
            time.sleep(delay)
            delay = min(delay * 2, 60)
        raise RuntimeError(f"{model}: retries exhausted")


def slug(model: str) -> str:
    return model.replace("/", "__").replace("@", "_at_").replace("+", "_")


def embed_corpus(cl: Client, model: str, texts: list[str]) -> dict:
    CACHE.mkdir(exist_ok=True)
    npy = CACHE / f"{slug(model)}.npy"
    meta_p = CACHE / f"{slug(model)}.meta.json"
    if npy.exists() and meta_p.exists():
        return {"vecs": np.load(npy), **json.loads(meta_p.read_text())}
    parts = CACHE / f"{slug(model)}_parts"
    parts.mkdir(exist_ok=True)
    batches = [(i, texts[i : i + BATCH]) for i in range(0, len(texts), BATCH)]
    prior = json.loads((parts / "meta.json").read_text()) if (parts / "meta.json").exists() else {}
    cost = prior.get("cost", 0.0)
    wall = prior.get("wall", 0.0)
    todo = [(i, b) for i, b in batches if not (parts / f"{i:06d}.npy").exists()]
    t0 = time.perf_counter()

    def job(ib: tuple[int, list[str]]) -> float:
        i, b = ib
        if cl.spent > cl.cap:
            raise RuntimeError(f"cost cap ${cl.cap} exceeded (${cl.spent:.4f})")
        v, c = cl.embed(model, b)
        np.save(parts / f"{i:06d}.npy", v)
        return c

    try:
        with ThreadPoolExecutor(CONCURRENCY) as ex:
            for n, c in enumerate(ex.map(job, todo)):
                cost += c
                if n % 20 == 0:
                    print(f"  {model}: {n + 1}/{len(todo)} batches, model ${cost:.4f}, total ${cl.spent:.4f}")
    finally:
        wall += time.perf_counter() - t0
        (parts / "meta.json").write_text(json.dumps({"cost": cost, "wall": wall}))
    vecs = np.concatenate([np.load(parts / f"{i:06d}.npy") for i, _ in batches])
    meta = {"corpus_cost": cost, "corpus_wall_s": wall, "dims": int(vecs.shape[1])}
    np.save(npy, vecs)
    meta_p.write_text(json.dumps(meta))
    return {"vecs": vecs, **meta}


def embed_queries(cl: Client, model: str, queries: list[str]) -> dict:
    p = CACHE / f"{slug(model)}.queries.npz"
    if p.exists():
        z = np.load(p)
        return {"qv": z["qv"], "lat": z["lat"].tolist(), "qcost": float(z["qcost"])}
    qv, qcost = cl.embed(model, queries)
    lat = []
    for q in queries[:N_LAT]:
        t = time.perf_counter()
        _, c = cl.embed(model, [q])
        lat.append((time.perf_counter() - t) * 1000)
        qcost += c
    np.savez(p, qv=qv, lat=np.array(lat), qcost=np.array(qcost))
    return {"qv": qv, "lat": lat, "qcost": qcost}


# --------------------------------------------------------------------------- eval
def normalise(m: np.ndarray) -> np.ndarray:
    return m / np.clip(np.linalg.norm(m, axis=1, keepdims=True), 1e-12, None)


def evaluate(cv: np.ndarray, qv: np.ndarray, lids: np.ndarray, queries: list[dict], gold: dict) -> dict:
    cv, qv = normalise(cv), normalise(qv)
    sims = qv @ cv.T
    per = []
    for qi, q in enumerate(queries):
        top = np.argsort(-sims[qi])[:TOPN]
        seen: list[int] = []
        for ci in top:
            lid = int(lids[ci])
            if lid not in seen:
                seen.append(lid)
        g = gold[q["gold_logical_key"]]
        rank = seen.index(g) + 1 if g in seen else None
        per.append({"qid": q["qid"], "lang": q["lang"], "heavy": bool(q["identifier_heavy"]), "rank": rank})

    def agg(sub: list[dict]) -> dict:
        n = len(sub)
        return {
            "n": n,
            "r5": sum(1 for o in sub if o["rank"] and o["rank"] <= 5) / n,
            "r10": sum(1 for o in sub if o["rank"] and o["rank"] <= 10) / n,
            "mrr10": sum(1 / o["rank"] for o in sub if o["rank"] and o["rank"] <= 10) / n,
        }

    res = {"all": agg(per), "per": per}
    for lang in ("tr", "de", "en"):
        res[lang] = agg([o for o in per if o["lang"] == lang])
    res["ident"] = agg([o for o in per if o["heavy"]])
    res["nonident"] = agg([o for o in per if not o["heavy"]])
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(MODELS))
    ap.add_argument("--cap", type=float, default=3.0)
    args = ap.parse_args()

    chunks = export_chunks()
    texts = [c["text"] for c in chunks]
    lids = np.array([c["logical_id"] for c in chunks])
    queries = load_queries()
    qtexts = [q["query"] for q in queries]
    gold = gold_map()
    print(f"{len(chunks)} fx-main chunks, {len(set(lids.tolist()))} logical items, {len(queries)} queries")

    results: dict[str, dict] = {}
    res_p = HERE / "results.json"
    if res_p.exists():
        results = json.loads(res_p.read_text())

    # baseline: stored e5-small vectors + local query embedding
    os.environ.setdefault("HLM_MODELS_DIR", str(ROOT / "models"))
    sys.path.insert(0, str(ROOT / "src"))
    from hlmemo.core.embedder import Embedder, default_model_dir

    emb = Embedder(default_model_dir())
    cv = baseline_vectors([c["chunk_id"] for c in chunks])
    qv = np.stack([emb.embed_query(q) for q in qtexts])
    emb.embed_query("warmup")
    lat = []
    for q in qtexts[:N_LAT]:
        t = time.perf_counter()
        emb.embed_query(q)
        lat.append((time.perf_counter() - t) * 1000)
    results["local/multilingual-e5-small"] = {
        **evaluate(cv, qv, lids, queries, gold),
        "dims": int(cv.shape[1]),
        "corpus_cost": 0.0,
        "corpus_wall_s": None,
        "lat_ms": lat,
        "qcost": 0.0,
    }
    res_p.write_text(json.dumps(results, indent=1))

    spent = sum(
        (r.get("corpus_cost") or 0) + (r.get("qcost") or 0) for k, r in results.items() if not k.startswith("local/")
    )
    cl = Client(load_key(), args.cap, spent)
    for model in args.models.split(","):
        print(f"== {model} (spent so far ${cl.spent:.4f})")
        if cl.spent > args.cap:
            print("COST CAP REACHED, stopping")
            break
        try:
            sp = spec(model)
            c = embed_corpus(cl, sp["corpus"], [sp["d"].format(t) for t in texts])
            q = embed_queries(cl, model, [sp["q"].format(t) for t in qtexts])
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED {model}: {e}")
            results[model] = {"error": str(e)[:500]}
            res_p.write_text(json.dumps(results, indent=1))
            continue
        results[model] = {
            **evaluate(c["vecs"], q["qv"], lids, queries, gold),
            "dims": c["dims"],
            "corpus_cost": c["corpus_cost"],
            "corpus_wall_s": c["corpus_wall_s"],
            "lat_ms": q["lat"],
            "qcost": q["qcost"],
        }
        r = results[model]
        print(f"  R@5={r['all']['r5']:.3f} R@10={r['all']['r10']:.3f} cost=${c['corpus_cost']:.4f}")
        res_p.write_text(json.dumps(results, indent=1))
    print(f"TOTAL SPEND ${cl.spent:.4f}")


if __name__ == "__main__":
    main()
