"""Shared fixtures for hlmemo.core unit tests (model-backed fixtures skip when the model is absent)."""

from __future__ import annotations

import pytest

from hlmemo.core.embedder import default_model_dir

MODEL_DIR = default_model_dir()
_HAVE_MODEL = (MODEL_DIR / "onnx" / "model.onnx").is_file() and (
    MODEL_DIR / "onnx" / "tokenizer.json"
).is_file()
requires_model = pytest.mark.skipif(
    not _HAVE_MODEL,
    reason=f"E5 model not downloaded at {MODEL_DIR}; run hlmemo.core.embedder.download_model()",
)


@pytest.fixture(scope="session")
def model_dir():
    if not _HAVE_MODEL:
        pytest.skip(f"E5 model not present at {MODEL_DIR}")
    return MODEL_DIR


@pytest.fixture(scope="session")
def chunker(model_dir):
    from hlmemo.core.chunker import Chunker

    return Chunker(model_dir)


@pytest.fixture(scope="session")
def embedder(model_dir):
    from hlmemo.core.embedder import Embedder

    instance = Embedder(model_dir)
    yield instance
    instance.close()


@pytest.fixture(scope="session")
def meter():
    from hlmemo.core.budget import Meter

    return Meter()
