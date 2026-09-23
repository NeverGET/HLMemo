"""Unit checks of the librarian building blocks (no database, no network)."""

from __future__ import annotations

import json
import time
import uuid
from decimal import Decimal

import pytest

from hlmemo.config import Settings
from hlmemo.librarian import health
from hlmemo.librarian.budget import MemoryBudget
from hlmemo.librarian.cassette import CassetteStore, cassette_key, sanitize_response
from hlmemo.librarian.errors import CassetteMiss
from hlmemo.librarian.profiles import LlmProfile, named_profile
from hlmemo.librarian.prompts import MAX_TOKENS, load_task
from hlmemo.librarian.provider import parse_json_object
from hlmemo.librarian.redact import Redactor
from hlmemo.librarian.roles import lower
from hlmemo.librarian.tasks import user_message


def _profile(**kw) -> LlmProfile:  # noqa: ANN003
    base = dict(
        name="p",
        base_url="http://p.invalid/v1",
        model_id="m",
        api_key=None,
        reasoning=None,
        extra={},
        price_in_per_m=Decimal("0.30"),
        price_out_per_m=Decimal("1.20"),
        supports_json_schema=False,
        prompt_overrides={},
    )
    base.update(kw)
    return LlmProfile(**base)


def test_librarian_settings_defaults_are_safe() -> None:
    f = Settings.model_fields
    assert f["librarian_enabled"].default is False
    assert f["librarian_role"].default == "observer"
    assert "librarian_send_device_scoped" not in f  # hard-pinned: device:* content is never sent
    assert f["llm_budget_day_usd"].default == 10 and f["llm_budget_month_usd"].default == 60
    assert f["llm_budget_hour_usd"].default == 3 and f["llm_job_call_cap"].default == 20
    assert f["llm_budget_disabled"].default is False


def test_worst_case_formula() -> None:
    p = _profile()
    # ceil(1000 × 1.10) × 0.30 + 600 × 1.20, per million
    assert (
        p.worst_usd(1000, 600) == (Decimal(1100) * Decimal("0.30") + Decimal(600) * Decimal("1.20")) / 10**6
    )
    assert p.worst_usd(7, 0) == Decimal(8) * Decimal("0.30") / 10**6  # 7.7 rounds UP to 8


def test_shipped_profiles_are_priced_and_vendor_free_in_code() -> None:
    for name in ("openrouter", "openrouter-luna", "openai"):
        prof = named_profile(name)
        assert prof.priced, name
    assert (
        named_profile("openrouter").model_id == "deepseek/deepseek-v4.1-flash"
    )  # D-019 lives in the profile
    assert named_profile("openrouter-luna").model_id == "openai/gpt-5.6-luna"


def test_max_tokens_per_task() -> None:
    assert MAX_TOKENS == {
        "placement": 200,
        "contradiction": 600,
        "summary": 700,
        "risk": 500,
        "synthesis": 700,
        "place": 700,  # W2b batched tasks (<= 8 items / pairs per call)
        "relate": 1400,
        "relate_verify": 700,
    }
    for task in ("placement", "contradiction", "summary", "risk"):
        spec = load_task(task)
        assert spec.max_tokens == MAX_TOKENS[task] and spec.prompt_version == "v1"


def test_schema_errors_and_overrides() -> None:
    spec = load_task("contradiction")
    assert spec.schema_errors({"contradicts": True, "supersedes": "A", "reason": ""}) is None
    assert "supersedes" in (spec.schema_errors({"contradicts": True, "supersedes": "C", "reason": ""}) or "")
    assert spec.system_for({"contradiction": {"system_append": "QUIRK"}}).rstrip().endswith("QUIRK")
    assert spec.system_for({}) == spec.system


def test_parse_json_object_strips_fences() -> None:
    assert parse_json_object('```json\n{"a": 1}\n```') == ({"a": 1}, None)
    assert parse_json_object("[1]")[1] == "top-level JSON is not an object"
    assert parse_json_object(None)[1] == "empty response"


def test_cassette_strict_replay(tmp_path) -> None:  # noqa: ANN001
    store = CassetteStore(tmp_path, record_name="t")
    msgs = [{"role": "user", "content": "x"}]
    key = cassette_key("m", "v1", "v1", msgs, {"max_tokens": 5})
    assert key != cassette_key("m", "v2", "v1", msgs, {"max_tokens": 5})
    with pytest.raises(CassetteMiss):
        store.get(key)
    raw = {
        "id": "gen-1",
        "provider": "X",
        "choices": [{"message": {"content": "{}", "reasoning": "hidden"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "cost": 0.1, "is_byok": False},
    }
    store.put(
        key,
        task="t",
        model_id="m",
        prompt_version="v1",
        schema_version="v1",
        messages=msgs,
        params={"max_tokens": 5},
        response=raw,
    )
    again = CassetteStore(tmp_path)
    assert again.get(key) == sanitize_response(raw)
    rec = json.loads((tmp_path / "t.jsonl").read_text())
    assert "id" not in rec["response"] and "provider" not in rec["response"]
    assert "reasoning" not in rec["response"]["choices"][0]["message"]


async def test_memory_budget_reserve_settle() -> None:
    b = MemoryBudget(Decimal("0.01"))
    a, c = uuid.uuid4(), uuid.uuid4()
    assert await b.reserve(a, Decimal("0.006"), None)
    assert not await b.reserve(c, Decimal("0.006"), None)  # 0.012 > cap
    await b.settle(a, Decimal("0.001"))
    assert await b.reserve(c, Decimal("0.006"), None)
    await b.settle(c, None)  # unknown actual → worst
    assert b.spent == Decimal("0.00700000") and b.reserved == 0


def test_redactor_edge_cases() -> None:
    r = Redactor()
    assert r.redact("postgresql://127.0.0.1:5432/hlm and https://github.com/a/b").count == 0
    assert r.redact('HLM_LLM_API_KEY = "env:OPENROUTER_API_KEY"').count == 0
    red = r.redact("admin token = hlm_" + "A" * 43)
    assert red.count == 1 and "A" * 43 not in red.text and "⟦REDACTED:hlm_token:" in red.text
    assert Redactor(email=True).redact("mail me at cemal@example.org").count == 1
    assert r.redact("mail me at cemal@example.org").count == 0  # optional, off by default
    value = r.value({"reason": "the key is sk-" + "or-v1-" + "a" * 40, "n": 3})
    assert "sk-" not in value["reason"] and value["n"] == 3


def test_role_lower_only() -> None:
    assert lower("autonomous", "observer") == "observer"
    assert lower("observer", "autonomous") == "observer"
    assert lower("assistant", None) == "assistant"


def test_user_message_shape() -> None:
    msg = user_message("contradiction", {"A": {"text": "ä"}}, [{"clue": "v1", "rule": "r"}])
    assert msg.startswith('JOB: contradiction\nINPUT: {"A": {"text": "ä"}}\nRULES')


def test_health_fresh_and_stale(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    path = tmp_path / "hb.json"
    monkeypatch.setenv("HLM_LIBRARIAN_HEARTBEAT_FILE", str(path))
    assert health.main(["60"]) == 1  # missing
    path.write_text(json.dumps({"ts": time.time()}))
    assert health.main(["60"]) == 0
    path.write_text(json.dumps({"ts": time.time() - 600}))
    assert health.main(["60"]) == 1
