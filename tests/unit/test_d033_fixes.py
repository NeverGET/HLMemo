"""D-033 regression tests that need no database (bake-off R1 findings F04, F06, F09, F12, F17)."""

from __future__ import annotations

import logging
import uuid

import pytest

from hlmemo.core import MODEL_ID, MODEL_REVISION
from hlmemo.core.budget import Meter
from hlmemo.core.embedder import (
    EmbedConfigMismatch,
    embed_config_check,
    pinned_model,
    require_pinned_embed_config,
)
from hlmemo.core.errors import ToolError
from hlmemo.core.read_models import DrilldownRequest
from hlmemo.core.read_service import _pack_page
from hlmemo.core.write_models import CloseRequest, WriteRequest, parse_request, session_note_body
from hlmemo.core.write_service import _close_items
from hlmemo.worker import main as worker_main

SECRET = "s3cr3t-pw"


# --------------------------------------------------------------------------- F09
@pytest.mark.parametrize(
    "dsn",
    [
        f"host=127.0.0.1 port=5432 dbname=hlm user=hlm password={SECRET}",
        f"postgresql://hlm:{SECRET}@db:5432/hlm",
        f"postgresql://hlm@db:5432/hlm?password={SECRET}&sslmode=require",
        f"host=db password='{SECRET}' sslpassword={SECRET}",
    ],
)
def test_f09_redact_dsn_never_prints_the_password(dsn):
    out = worker_main.redact_dsn(dsn)
    assert SECRET not in out
    assert "password" not in out
    assert "db" in out or "127.0.0.1" in out


def test_f09_worker_polling_log_line_is_redacted(monkeypatch, caplog):
    """The worker's start-up log line (formerly ``dsn.split("@")[-1]``) holds no credential."""
    dsn = f"host=127.0.0.1 port=5432 dbname=hlm user=hlm password={SECRET}"
    monkeypatch.setenv("HLM_DB_DSN", dsn)

    class _Stop(Exception):
        pass

    async def fake_run_forever(*_a, **_k):
        raise _Stop

    monkeypatch.setattr(worker_main, "run_forever", fake_run_forever)
    monkeypatch.setattr(worker_main, "Embedder", lambda *_a, **_k: object())
    import asyncio

    with caplog.at_level(logging.INFO, logger="hlmemo.worker"), pytest.raises(_Stop):
        asyncio.run(worker_main._amain())
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "worker: polling host=127.0.0.1" in text
    assert SECRET not in text and "password" not in text


# --------------------------------------------------------------------------- F04
def test_f04_pinned_model_matches_models_lock_and_constants():
    assert pinned_model() == (MODEL_ID, MODEL_REVISION)
    assert embed_config_check(None, None)["ok"] is True
    assert embed_config_check(MODEL_ID, MODEL_REVISION)["ok"] is True
    require_pinned_embed_config(MODEL_ID, MODEL_REVISION)  # no raise


@pytest.mark.parametrize(
    "model, revision",
    [
        ("intfloat/multilingual-e5-base", "deadbeef"),
        ("intfloat/multilingual-e5-base", MODEL_REVISION),
        (MODEL_ID, "deadbeef"),
    ],
)
def test_f04_non_pinned_embed_config_fails_fast(model, revision):
    check = embed_config_check(model, revision)
    assert check["ok"] is False
    assert "HLM_EMBED_MODEL" in str(check["error"])
    with pytest.raises(EmbedConfigMismatch, match="pinned"):
        require_pinned_embed_config(model, revision)


def test_f04_worker_refuses_to_start_with_foreign_embed_model(monkeypatch, caplog):
    monkeypatch.setenv("HLM_EMBED_MODEL", "intfloat/multilingual-e5-base")
    monkeypatch.setenv("HLM_EMBED_REVISION", "deadbeef")
    monkeypatch.setenv("HLM_DB_DSN", "host=127.0.0.1 dbname=never user=never")
    monkeypatch.setattr(logging, "basicConfig", lambda **_k: None)
    with caplog.at_level(logging.ERROR, logger="hlmemo.worker"):
        assert worker_main.main() == 2
    assert "refusing to start" in caplog.text and "e5-base@deadbeef" in caplog.text


# --------------------------------------------------------------------------- F06
def test_f06_validation_error_details_are_bounded_and_echo_no_input():
    big = "q" * 64000
    raw = {
        "project": "p1",
        "request_id": str(uuid.uuid4()),
        "client": "c",
        "items": [{"kind": "fact", "body": big, "bogus": big} for _ in range(50)],
    }
    with pytest.raises(ToolError) as ei:
        parse_request(WriteRequest, raw)
    err = ei.value
    assert err.code == "E_INVALID_ARG"
    assert err.details["error_count"] == 100  # missing title + extra field, per item
    assert len(err.details["errors"]) == 20
    blob = repr(err.details) + err.message
    assert "qqqq" not in blob
    assert len(blob) < 8000


def test_f06_long_error_messages_are_clipped():
    with pytest.raises(ToolError) as ei:
        parse_request(
            DrilldownRequest, {"project": "p1", "clue_ids": ["v" + "0" * 5000], "token_budget": 300}
        )
    assert len(ei.value.message) <= 400
    assert all(len(e["msg"]) <= 300 for e in ei.value.details["errors"])


# --------------------------------------------------------------------------- F12
@pytest.mark.parametrize("decisions", [[], ["a"], ["a", "b c", ""]])
def test_f12_session_note_body_matches_close_items(decisions):
    req = CloseRequest.model_validate(
        {
            "project": "p1",
            "request_id": str(uuid.uuid4()),
            "session_id": str(uuid.uuid4()),
            "client": "c",
            "notes": "some notes",
            "decisions": decisions,
        }
    )
    assert _close_items(req)[0].body == session_note_body(req.notes, req.decisions)


def test_f12_close_request_rejects_oversized_session_note():
    raw = {
        "project": "p1",
        "request_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "client": "c",
        "notes": "x" * 64000,
        "decisions": ["x"],
    }
    with pytest.raises(ToolError) as ei:
        parse_request(CloseRequest, raw)
    assert ei.value.code == "E_INVALID_ARG"
    assert "64000" in ei.value.message


# --------------------------------------------------------------------------- F17
class _FakeMeter(Meter):
    """Sizes: partial page n → 100*n + 300 (cursor), complete page → 100*n."""

    def __init__(self, n_total: int) -> None:
        self.n_total = n_total
        self.state = 0

    def settle(self, envelope, limit, *, budget_key="budget"):  # noqa: ANN001
        n = self.state
        return 100 * n + (0 if n >= self.n_total else 300)


def _pack(n_total: int, budget: int) -> int:
    meter = _FakeMeter(n_total)

    def apply(n: int) -> None:
        meter.state = n

    return _pack_page(meter, {}, budget, n_total, apply, lambda k: 100 * k + 300)


def test_f17_complete_page_wins_when_it_fits():
    assert _pack(3, 300) == 3  # complete page = 300; any partial page with a cursor is ≥ 400


def test_f17_partial_page_when_complete_does_not_fit():
    assert _pack(5, 499) == 1  # complete = 500
    assert _pack(10, 700) == 4  # complete = 1000; partial 4 = 700
    assert _pack(5, 700) == 5  # complete = 500 fits


def test_f17_min_is_the_smallest_servable_size():
    with pytest.raises(ToolError) as ei:
        _pack(2, 199)  # complete = 200, first partial = 400
    assert ei.value.code == "E_BUDGET_TOO_SMALL"
    assert ei.value.details["min"] == 200
    with pytest.raises(ToolError) as ei:
        _pack(9, 350)  # complete = 900, first partial = 400
    assert ei.value.details["min"] == 400
