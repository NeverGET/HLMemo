"""Load cached gemini-embedding-2 @1536 (+prefix) chunk vectors into the hlm_retr_g2 COPY.

Deletes the e5 rows in the copy (the copy then holds only the new model's vectors, as a
production deployment on gemini-2 would) and inserts one row per fx-main chunk under
model='google/gemini-embedding-2', model_revision='d1536-prefix-v1', preproc_version=1.
Never touches hlm_retr.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import psycopg

HERE = Path(__file__).resolve().parent
EMB = HERE.parent
DSN = "postgresql://hlm:hlm@127.0.0.1:5432/hlm_retr_g2"
MODEL = "google/gemini-embedding-2"
REV = "d1536-prefix-v1"

chunks = [json.loads(line) for line in (EMB / "chunks.jsonl").open(encoding="utf-8")]
vecs = np.load(EMB / "cache" / "google__gemini-embedding-2_at_1536_prefix.npy").astype(np.float32)
assert len(chunks) == vecs.shape[0] == 9642 and vecs.shape[1] == 1536
vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)

with psycopg.connect(DSN) as conn:
    assert conn.info.dbname == "hlm_retr_g2"
    # verify chunk ids / texts line up with the copy
    rows = dict(conn.execute("SELECT chunk_id, text FROM chunks WHERE chunk_id = ANY(%s)",
                             ([c["chunk_id"] for c in chunks],)).fetchall())
    assert all(rows[c["chunk_id"]] == c["text"] for c in chunks), "chunk text mismatch"
    conn.execute("DELETE FROM embeddings WHERE model <> %s", (MODEL,))
    conn.execute("DELETE FROM embeddings WHERE model = %s", (MODEL,))
    with conn.cursor().copy(
        "COPY embeddings (chunk_id, model, model_revision, preproc_version, dims, vec) FROM STDIN"
    ) as cp:
        for c, v in zip(chunks, vecs, strict=True):
            cp.write_row((c["chunk_id"], MODEL, REV, 1, 1536, "[" + ",".join(f"{x:.8g}" for x in v) + "]"))
    conn.commit()
    print(conn.execute("SELECT model, model_revision, dims, count(*) FROM embeddings GROUP BY 1,2,3").fetchall())
