"""CC-5 cassettes key the schema retry separately (attempt 2), backward compatible.

Before this change the retry of a schema-invalid answer had the same key as the first attempt: record
mode stored only the invalid answer, and replay served it twice (``schema_fail`` where the live run had
recovered). Now attempt 2 has its own key; attempt 1 keeps the legacy key, so every existing cassette
replays unchanged, and a legacy cassette without an attempt-2 entry falls back to the old behaviour.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from hlmemo.librarian.budget import NoBudget
from hlmemo.librarian.cassette import SEP, CassetteStore, canonical, cassette_key
from hlmemo.librarian.errors import SchemaFail
from hlmemo.librarian.ledger import MemoryLedger
from hlmemo.librarian.profiles import LlmProfile
from hlmemo.librarian.prompts import load_task
from hlmemo.librarian.provider import Provider
from hlmemo.librarian.redact import Redactor

PROFILE = LlmProfile(
    name="p",
    base_url="http://stub.invalid/v1",
    model_id="m",
    api_key=None,
    reasoning=None,
    extra={"temperature": 0},
    price_in_per_m=Decimal("0"),
    price_out_per_m=Decimal("0"),
    supports_json_schema=False,
    prompt_overrides={},
)
USER = 'JOB: contradiction\nINPUT: {"A": "x", "B": "y"}'
GOOD = {"contradicts": False, "supersedes": "none", "reason": "compatible"}


def _stub(answers: list[str]) -> tuple[httpx.MockTransport, list[int]]:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        content = answers[min(len(calls), len(answers)) - 1]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0},
            },
        )

    return httpx.MockTransport(handler), calls


def _provider(mode: str, store: CassetteStore, transport: httpx.MockTransport | None = None) -> Provider:
    return Provider(
        [PROFILE],
        mode=mode,
        budget=NoBudget(),
        ledger=MemoryLedger(),
        cassettes=store,
        transport=transport,
        redactor=Redactor(),
        budget_disabled=True,
    )


def test_attempt_one_keeps_the_legacy_key() -> None:
    msgs = [{"role": "user", "content": "x"}]
    legacy = hashlib.sha256(SEP.join(("m", "v1", "v1", canonical(msgs), canonical({}))).encode()).hexdigest()
    assert cassette_key("m", "v1", "v1", msgs, {}) == legacy == cassette_key("m", "v1", "v1", msgs, {}, 1)
    assert cassette_key("m", "v1", "v1", msgs, {}, attempt=2) != legacy


async def test_schema_retry_is_recorded_and_replayed(tmp_path: Path) -> None:
    transport, calls = _stub(["not json", json.dumps(GOOD)])
    task = load_task("contradiction")
    rec = _provider("record", CassetteStore(tmp_path, record_name="r"), transport)
    live = await rec.complete(task, USER)
    assert live.output == GOOD and len(calls) == 2
    lines = (tmp_path / "r.jsonl").read_text().splitlines()
    assert len(lines) == 2  # the invalid first answer AND the retry

    rep = _provider("replay", CassetteStore(tmp_path))
    replayed = await rep.complete(task, USER)
    assert replayed.output == live.output
    assert [r.outcome for r in rep.ledger.rows] == ["schema_fail", "schema_retry_ok"]


async def test_legacy_cassette_without_the_retry_entry_still_replays(tmp_path: Path) -> None:
    """A cassette recorded before attempt keys holds only attempt 1: replay falls back to it (legacy
    behaviour: the invalid answer twice -> SchemaFail), it never raises CassetteMiss."""
    transport, _ = _stub(["not json", json.dumps(GOOD)])
    task = load_task("contradiction")
    await _provider("record", CassetteStore(tmp_path, record_name="r"), transport).complete(task, USER)
    first = (tmp_path / "r.jsonl").read_text().splitlines()[0]
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    (legacy_dir / "old.jsonl").write_text(first + "\n")
    rep = _provider("replay", CassetteStore(legacy_dir))
    with pytest.raises(SchemaFail):
        await rep.complete(task, USER)
    assert [r.outcome for r in rep.ledger.rows] == ["schema_fail", "schema_fail"]
