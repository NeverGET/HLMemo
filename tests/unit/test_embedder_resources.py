"""Bound ONNX session resources and propagate the same settings to the worker."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import onnxruntime as ort
import pytest

from hlmemo import config
from hlmemo.core import embedder as embedding
from hlmemo.worker import main as worker


@pytest.fixture(autouse=True)
def _clean_tables():
    """Session option and worker wiring tests never connect to a database."""
    yield


@pytest.mark.parametrize("threads", [None, 3])
def test_onnx_cpu_arena_disabled_and_threads_bounded(monkeypatch, tmp_path, threads):
    model_dir = tmp_path / "model"
    (model_dir / "onnx").mkdir(parents=True)
    for filename in ("model.onnx", "tokenizer.json"):
        (model_dir / "onnx" / filename).write_bytes(b"test fixture")
    tokenizer = Mock()
    tokenizer.token_to_id.return_value = 1
    tokenizer_loader = Mock(return_value=tokenizer)
    monkeypatch.setattr(embedding, "Tokenizer", SimpleNamespace(from_file=tokenizer_loader))
    session = Mock()
    session.get_inputs.return_value = [SimpleNamespace(name="input_ids")]
    session.get_outputs.return_value = [SimpleNamespace(name="last_hidden_state")]
    telemetry = Mock()
    monkeypatch.setattr(ort, "disable_telemetry_events", telemetry)

    def create_session(*args, **kwargs):
        assert os.environ["ORT_DISABLE_TELEMETRY"] == "1"
        telemetry.assert_called_once_with()
        return session

    session_factory = Mock(side_effect=create_session)
    monkeypatch.setattr(ort, "InferenceSession", session_factory)

    options = {} if threads is None else {"threads": threads}
    embedder = embedding.Embedder(model_dir, **options)

    session_factory.assert_called_once()
    args, kwargs = session_factory.call_args
    assert args == (str(model_dir / "onnx" / "model.onnx"),)
    assert kwargs["providers"] == ["CPUExecutionProvider"]
    assert kwargs["sess_options"].enable_cpu_mem_arena is False
    assert kwargs["sess_options"].intra_op_num_threads == (2 if threads is None else threads)
    tokenizer_loader.assert_called_once_with(str(model_dir / "onnx" / "tokenizer.json"))
    embedder.close()
    embedder.close()
    assert embedder._sess is None and embedder._tok is None


@pytest.mark.parametrize("configured", [False, True])
async def test_worker_passes_configured_thread_budget_to_its_single_embedder(
    monkeypatch, tmp_path, configured
):
    if configured:
        monkeypatch.setenv("HLM_EMBED_INTRA_OP_NUM_THREADS", "3")
    else:
        monkeypatch.delenv("HLM_EMBED_INTRA_OP_NUM_THREADS", raising=False)
    settings = config.get_settings()
    assert settings.embed_intra_op_num_threads == (3 if configured else 2)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "default_model_dir", lambda: tmp_path)
    sentinel = object()
    factory = Mock(return_value=sentinel)
    run_forever = AsyncMock()
    monkeypatch.setattr(worker, "Embedder", factory)
    monkeypatch.setattr(worker, "run_forever", run_forever)
    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", Mock())

    assert await worker._amain() == 0

    factory.assert_called_once_with(
        tmp_path,
        threads=settings.embed_intra_op_num_threads,
        max_batch_tokens=settings.embed_max_batch_tokens,
    )
    run_forever.assert_awaited_once()
    args, kwargs = run_forever.await_args
    assert args[1] is sentinel
    assert isinstance(kwargs["stop"], asyncio.Event)
