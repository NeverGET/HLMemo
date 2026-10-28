"""D-272: the shadow pipeline's pure parts: the cached system prefix and the opt-in relate/v3."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from hlmemo.librarian.profiles import LlmProfile, named_profile
from hlmemo.librarian.prompts import load_task
from hlmemo.librarian.provider import Provider


def _profile(cache: bool) -> LlmProfile:
    return LlmProfile(
        name="p",
        base_url="http://p.invalid/v1",
        model_id="stub/p",
        api_key="test-key-not-secret",
        reasoning=None,
        extra={},
        price_in_per_m=Decimal(1),
        price_out_per_m=Decimal(2),
        supports_json_schema=False,
        prompt_overrides={},
        cache_system=cache,
    )


def test_cache_system_marks_only_the_system_text_and_leaves_the_messages_alone() -> None:
    msgs = [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "USER"}]
    task = load_task("relate")
    prov = Provider.__new__(Provider)
    body = Provider.build_body(prov, _profile(True), task, msgs)
    assert body["messages"][0] == {
        "role": "system",
        "content": [{"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}],
    }
    assert body["messages"][1] == {"role": "user", "content": "USER"}
    assert msgs[0]["content"] == "SYSTEM"  # the caller's list (token meter, cassettes) is unchanged
    body = Provider.build_body(prov, _profile(False), task, msgs)
    assert body["messages"] == msgs


def test_relate_v3_is_opt_in_and_the_live_relate_stays_v2() -> None:
    assert load_task("relate").prompt_version == "v2" and load_task("relate").max_tokens == 2000
    v3 = load_task("relate", 3)
    assert v3.prompt_version == "v3" and v3.max_tokens == 12000
    assert (
        "HISTORY is never contradicted" in v3.system and "a plan or proposal next to a fact" not in v3.system
    )
    assert v3.schema == load_task("relate", 2).schema  # same answer shape: the guards apply unchanged


def test_the_claude_profiles_send_no_temperature_and_cache_the_prefix() -> None:
    for name in ("openrouter-claude-haiku55-high", "openrouter-claude-opus55-low"):
        p = named_profile(name)
        assert p.cache_system and "temperature" not in p.extra and p.reasoning is not None
        assert replace(p, cache_system=False).cache_system is False
