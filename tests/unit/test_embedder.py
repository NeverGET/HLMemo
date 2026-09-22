import time

import numpy as np
import pytest

from hlmemo.core import EMBEDDER_VERSION, EMBEDDING_DIMS, MODEL_ID, MODEL_REVISION
from hlmemo.core.embedder import (
    HASHED_FILES,
    MODEL_FILES,
    Embedder,
    ModelHashMismatch,
    default_model_dir,
    model_hashes,
)


def cos(a, b):
    return float(np.dot(a, b))


def test_version_constant():
    assert EMBEDDER_VERSION == f"{MODEL_ID}@{MODEL_REVISION}"
    assert EMBEDDER_VERSION == "intfloat/multilingual-e5-small@614241f622f53c4eeff9890bdc4f31cfecc418b3"
    assert set(MODEL_FILES) == {
        "onnx/model.onnx",
        "onnx/tokenizer.json",
        "tokenizer_config.json",
        "config.json",
    }


def test_model_files_present(model_dir):
    for f in MODEL_FILES:
        assert (model_dir / f).is_file(), f
    assert default_model_dir() == model_dir


def test_shapes_dtype_and_unit_norm(embedder):
    vecs = embedder.embed_passages(["kedi", "docker compose up", "Die Straße über den Fluss"])
    assert vecs.shape == (3, EMBEDDING_DIMS) and vecs.dtype == np.float32
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1.0, atol=1e-5)
    q = embedder.embed_query("kedi")
    assert q.shape == (EMBEDDING_DIMS,) and q.dtype == np.float32
    assert abs(np.linalg.norm(q) - 1.0) < 1e-5
    assert embedder.embed_passages([]).shape == (0, EMBEDDING_DIMS)


def test_prefixes_matter_and_are_deterministic(embedder):
    q1 = embedder.embed_query("kedi")
    q2 = embedder.embed_query("kedi")
    p = embedder.embed_passages(["kedi"])[0]
    raw = embedder.embed_texts(["kedi"])[0]
    assert np.array_equal(q1, q2)
    assert not np.array_equal(q1, p)  # "query: " vs "passage: "
    assert not np.array_equal(q1, raw)


def test_query_passage_relevance(embedder):
    q = embedder.embed_query("kedi")
    p_kedi, p_docker = embedder.embed_passages(["kedi", "docker compose"])
    c_rel, c_irr = cos(q, p_kedi), cos(q, p_docker)
    print(f"\ncos(query kedi, passage kedi)={c_rel:.4f}  cos(query kedi, passage docker compose)={c_irr:.4f}")
    assert c_rel > c_irr


def test_cross_lingual_tr_en(embedder):
    cat, kedi, database = embedder.embed_passages(["cat", "kedi", "database"])
    c_same, c_diff = cos(cat, kedi), cos(cat, database)
    print(f"\ncos(cat, kedi)={c_same:.4f}  cos(cat, database)={c_diff:.4f}")
    assert c_same > c_diff


def test_batching_matches_single(embedder):
    texts = [f"passage number {i} about İstanbul and Straße" for i in range(5)]
    whole = embedder.embed_passages(texts)
    single = np.stack([embedder.embed_passages([t])[0] for t in texts])
    assert np.allclose(whole, single, atol=1e-4)  # padding must not change pooled result


def test_truncation_at_512_tokens(embedder):
    long = "kelime " * 3000
    v = embedder.embed_passages([long])
    assert v.shape == (1, EMBEDDING_DIMS) and np.isfinite(v).all()


def test_latency_32_passages(embedder):
    passages = [
        f"Chunk {i}: " + ("The docker compose stack failed because APP_DB_DSN was unset. " * 12)
        for i in range(32)
    ]
    embedder.embed_passages(passages[:2])  # warm-up
    t0 = time.perf_counter()
    vecs = embedder.embed_passages(passages)
    dt = time.perf_counter() - t0
    assert vecs.shape == (32, EMBEDDING_DIMS)
    print(f"\nembed 32 passages (~{len(embedder._tok.encode(passages[0]).ids)} tok each): {dt * 1000:.0f} ms")
    assert dt < 60


def test_hash_check(model_dir):
    hashes = model_hashes(model_dir)
    assert set(hashes) == set(HASHED_FILES) and all(len(h) == 64 for h in hashes.values())
    Embedder(model_dir, expected_hashes=hashes)  # matches
    bad = dict(hashes)
    bad["onnx/model.onnx"] = "0" * 64
    with pytest.raises(ModelHashMismatch):
        Embedder(model_dir, expected_hashes=bad)


def test_missing_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        Embedder(tmp_path)
