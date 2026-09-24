"""Workstream F (D-094): the provider-agnostic "fallback profile per task" hook, without a database.

* Resolution: ``HLM_FALLBACK_PROFILE`` stays every task's default; ``HLM_FALLBACK_PROFILE__<TASK>``
  (env, task upper-cased) or ``[hlm] fallback_profile__<task>`` overrides it for one task, generic by
  task name (a task this build does not define picks up its override through the provider).
* Validation: an unknown profile fails fast naming the variable (api lifespan and librarian start);
  an unknown task or an unqualified override only warns.
* D-017: no model id outside ``profiles/``; the resolution code names no task.
* The production mapping of ``deploy/llm.env.example`` and its ops rendering.
"""

from __future__ import annotations

import asyncio
import logging
import re
import tomllib
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from hlmemo.config import get_settings
from hlmemo.librarian import profiles as pr
from hlmemo.librarian import risk_judge as rj
from hlmemo.librarian.budget import MemoryBudget
from hlmemo.librarian.errors import LlmConfigError
from hlmemo.librarian.ledger import MemoryLedger
from hlmemo.librarian.prompts import TaskSpec, load_task
from hlmemo.librarian.provider import ChainBreakers, Provider
from hlmemo.librarian.tasks import synthesis as syn
from hlmemo.librarian.tasks.write_review import verifier_chain
from hlmemo.ops.cli import chain_lines

ROOT = Path(__file__).resolve().parents[2]
PRIMARY = "wf-primary"


def _profile_file(d: Path, name: str, *, pin: str, pout: str, disabled: tuple[str, ...] = ()) -> None:
    lines = [
        f'HLM_LLM_BASE_URL = "http://{name}.invalid/v1"',
        f'HLM_LLM_MODEL = "stub/{name}"',
        'HLM_LLM_API_KEY = "test-key-not-secret"',
        'extra = { response_format = { type = "json_object" }, temperature = 0 }',
        f"price_in_per_m = {pin}",
        f"price_out_per_m = {pout}",
    ]
    if disabled:
        lines.append("disabled_tasks = [" + ", ".join(f'"{t}"' for t in disabled) + "]")
    (d / f"{name}.toml").write_text("\n".join(lines) + "\n")


@pytest.fixture
def profiles_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stub profiles only (no vendor), and no ambient fallback/primary configuration."""
    import os

    for key in list(os.environ):
        if key.upper().startswith(("HLM_FALLBACK_PROFILE", "HLM_PROFILE", "HLM_TASK_FALLBACK")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("HLM_CONFIG", str(tmp_path / "none.toml"))  # absent: no extra [hlm] keys
    _profile_file(tmp_path, PRIMARY, pin="0.2", pout="0.75")
    _profile_file(tmp_path, "wf-default", pin="0.45", pout="1.50")
    _profile_file(tmp_path, "wf-risk", pin="0.45", pout="4.40")
    _profile_file(tmp_path, "wf-syn", pin="0.30", pout="1.20")
    _profile_file(tmp_path, "wf-unq", pin="0.30", pout="1.20", disabled=("risk_judge",))
    monkeypatch.setenv("HLM_PROFILES_DIR", str(tmp_path))
    return tmp_path


def settings(**kw: Any):  # noqa: ANN201
    base: dict[str, Any] = {
        "profile": PRIMARY,
        "fallback_profile": "wf-default",
        "llm_base_url": f"http://{PRIMARY}.invalid/v1",
        "llm_model": f"stub/{PRIMARY}",
        "llm_api_key": "test-key-not-secret",
        "price_in_per_m": 0.2,
        "price_out_per_m": 0.75,
        "librarian_enabled": True,
        "llm_mode": "live",
    }
    return get_settings(**{**base, **kw})


def names(chain: list[pr.LlmProfile]) -> list[str]:
    return [p.name for p in chain]


# --------------------------------------------------------------------------- resolution
def test_env_overrides_are_generic_by_task_name(profiles_dir: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HLM_FALLBACK_PROFILE__RISK_JUDGE", "wf-risk")
    monkeypatch.setenv("HLM_FALLBACK_PROFILE__SOME_FUTURE_TASK", "wf-syn")
    monkeypatch.setenv("HLM_FALLBACK_PROFILE__SYNTHESIS", "  ")  # empty: no override
    monkeypatch.setenv("FALLBACK_PROFILE__RELATE", "wf-syn")  # not HLM_-prefixed: ignored
    s = settings()
    assert s.task_fallback_profiles == {"risk_judge": "wf-risk", "some_future_task": "wf-syn"}
    assert s.fallback_profile == "wf-default"


def test_hlm_toml_overrides_and_precedence(profiles_dir: Path, monkeypatch) -> None:  # noqa: ANN001
    toml = profiles_dir / "hlm.toml"
    toml.write_text(
        '[hlm]\nfallback_profile__synthesis = "wf-syn"\nHLM_FALLBACK_PROFILE__RELATE = "wf-default"\n'
        'fallback_profile__risk_judge = "wf-unq"\n'
    )
    monkeypatch.setenv("HLM_CONFIG", str(toml))
    monkeypatch.setenv("HLM_FALLBACK_PROFILE__RISK_JUDGE", "wf-risk")  # env beats hlm.toml
    s = get_settings(profile=PRIMARY)
    assert s.task_fallback_profiles == {
        "synthesis": "wf-syn",
        "relate": "wf-default",
        "risk_judge": "wf-risk",
    }
    s = get_settings(profile=PRIMARY, task_fallback_profiles={"relate": "wf-syn"})  # init beats both
    assert (
        s.task_fallback_profiles["relate"] == "wf-syn" and s.task_fallback_profiles["synthesis"] == "wf-syn"
    )


def test_default_fallback_for_every_task_without_overrides(profiles_dir: Path) -> None:
    s = settings()
    chain = pr.profile_chain(s)
    assert names(chain) == [PRIMARY, "wf-default"] and dict(chain[0].task_fallbacks) == {}
    for task in pr.known_tasks():
        assert names(pr.for_task(chain, task)) == [PRIMARY, "wf-default"], task
    assert pr.check_chains(s) == []


def test_override_replaces_the_default_for_its_task_only(profiles_dir: Path) -> None:
    s = settings(task_fallback_profiles={"risk_judge": "wf-risk", "synthesis": "wf-syn"})
    chain = pr.profile_chain(s)
    assert names(chain) == [PRIMARY, "wf-default"]  # the default chain is unchanged
    assert names(pr.profile_chain(s, "risk_judge")) == [PRIMARY, "wf-risk"]
    assert names(pr.profile_chain(s, "synthesis")) == [PRIMARY, "wf-syn"]
    assert names(pr.profile_chain(s, "relate")) == [PRIMARY, "wf-default"]
    assert names(rj.judge_chain(s)) == [PRIMARY, "wf-risk"]
    assert names(syn.synthesis_chain(s)) == [PRIMARY, "wf-syn"]
    risk = pr.profile_chain(s, "risk_judge")
    assert names(pr.for_task(risk, "risk_judge")) == names(risk)  # idempotent
    # the fallback's own prices (spend guard / ledger), not the default's
    assert risk[1].price_out_per_m == Decimal("4.40") and chain[1].price_out_per_m == Decimal("1.50")


def test_override_naming_the_primary_means_no_fallback(profiles_dir: Path) -> None:
    s = settings(task_fallback_profiles={"risk_judge": PRIMARY})
    assert names(pr.profile_chain(s, "risk_judge")) == [PRIMARY]
    assert names(pr.profile_chain(s, "place")) == [PRIMARY, "wf-default"]


def test_unqualified_override_keeps_retrieval_only_and_warns(profiles_dir: Path) -> None:
    """D-071: a configured risk fallback whose profile lists risk_judge in disabled_tasks is never
    used: the judge chain is the primary alone (retrieval-only during an outage), even when the
    default fallback would be qualified."""
    s = settings(task_fallback_profiles={"risk_judge": "wf-unq"})
    assert names(rj.judge_chain(s)) == [PRIMARY]
    assert names(pr.profile_chain(s, "risk_judge")) == [PRIMARY]
    (warning,) = pr.check_chains(s)
    assert "HLM_FALLBACK_PROFILE__RISK_JUDGE=wf-unq" in warning and "disabled_tasks" in warning
    # the same profile is fine for a task it does not disable
    s = settings(task_fallback_profiles={"synthesis": "wf-unq"})
    assert names(syn.synthesis_chain(s)) == [PRIMARY, "wf-unq"] and pr.check_chains(s) == []


def test_default_unqualified_fallback_is_dropped_for_that_task_only(profiles_dir: Path) -> None:
    s = settings(fallback_profile="wf-unq")
    assert names(rj.judge_chain(s)) == [PRIMARY]  # the pre-F D-071 behaviour
    assert names(pr.profile_chain(s, "relate")) == [PRIMARY, "wf-unq"]
    s = settings(fallback_profile="wf-unq", task_fallback_profiles={"risk_judge": "wf-risk"})
    assert names(rj.judge_chain(s)) == [PRIMARY, "wf-risk"]  # a qualified task fallback


# --------------------------------------------------------------------------- validation
def test_unknown_profile_fails_fast_naming_the_variable(profiles_dir: Path) -> None:
    s = settings(task_fallback_profiles={"risk_judge": "no-such-profile"})
    with pytest.raises(
        LlmConfigError, match=r"HLM_FALLBACK_PROFILE__RISK_JUDGE='no-such-profile'.*not found"
    ):
        pr.check_chains(s)
    s = settings(fallback_profile="no-such-default")
    with pytest.raises(LlmConfigError, match=r"HLM_FALLBACK_PROFILE='no-such-default'.*not found"):
        pr.check_chains(s)
    assert "error" in pr.describe_chains(s)  # ops reports it instead of raising
    assert chain_lines(pr.describe_chains(s))[0].startswith("chains      CONFIG ERROR HLM_FALLBACK_PROFILE=")


def test_describe_chains_never_raises_without_llm_settings() -> None:
    """ops status with a settings object that carries no LLM configuration (e.g. a minimal one)."""
    desc = pr.describe_chains(SimpleNamespace(librarian_role="observer"))
    assert desc == {"error": "no LLM configuration (AttributeError)"}
    assert chain_lines(desc) == ["chains      CONFIG ERROR no LLM configuration (AttributeError)"]


def test_unknown_task_only_warns(profiles_dir: Path, caplog) -> None:  # noqa: ANN001
    from hlmemo.server.app import check_llm_config

    s = settings(task_fallback_profiles={"query_rewritez": "wf-syn"})
    (warning,) = pr.check_chains(s)
    assert "HLM_FALLBACK_PROFILE__QUERY_REWRITEZ: unknown librarian task 'query_rewritez'" in warning
    with caplog.at_level(logging.WARNING, logger="hlmemo.server"):
        check_llm_config(s)  # warns, does not raise
    assert "unknown librarian task" in caplog.text
    assert pr.describe_chains(s)["tasks"]["query_rewritez"]["unknown_task"] is True


def test_api_refuses_to_start_on_an_unknown_profile(profiles_dir: Path) -> None:
    from hlmemo.server.app import check_llm_config, create_app

    bad = settings(task_fallback_profiles={"synthesis": "no-such-profile"})
    with pytest.raises(LlmConfigError, match="HLM_FALLBACK_PROFILE__SYNTHESIS"):
        check_llm_config(bad)
    check_llm_config(bad.model_copy(update={"librarian_enabled": False}))  # off: not used, not checked
    check_llm_config(bad.model_copy(update={"llm_mode": "off"}))
    app = create_app(bad)

    async def start() -> None:
        async with app.router.lifespan_context(app):
            pytest.fail("the api served with an unknown fallback profile")

    with pytest.raises(LlmConfigError, match="HLM_FALLBACK_PROFILE__SYNTHESIS='no-such-profile'"):
        asyncio.run(start())  # before the pool, the admin binding or the listener


def test_librarian_refuses_to_start_on_an_unknown_profile(profiles_dir: Path, monkeypatch, caplog) -> None:  # noqa: ANN001
    import hlmemo.config as config
    from hlmemo.librarian import worker

    bad = settings(task_fallback_profiles={"relate": "no-such-profile"}, librarian_heartbeat_file=None)
    monkeypatch.setattr(config, "get_settings", lambda **_kw: bad)
    with caplog.at_level(logging.ERROR, logger="hlmemo.librarian"):
        assert asyncio.run(worker._amain()) == 2  # before any pool or job
    assert "refusing to start: HLM_FALLBACK_PROFILE__RELATE='no-such-profile'" in caplog.text


# --------------------------------------------------------------------------- the provider path
def _spec(name: str) -> TaskSpec:
    """A task this build does not define (e.g. ``query_rewrite`` on another branch)."""
    schema = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
    return TaskSpec(
        name=name, prompt_version="v1", schema_version="v1", system="s", schema=schema, max_tokens=50
    )


def _answering(fail_host: str) -> tuple[httpx.MockTransport, list[str]]:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == fail_host:
            return httpx.Response(503, json={"error": {"code": 503}})
        content = '{"ok": true, "verdict": "none", "matches": []}'
        usage = {"prompt_tokens": 100, "completion_tokens": 20}
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": usage})

    return httpx.MockTransport(handler), hosts


async def _complete(provider: Provider, task: TaskSpec) -> Any:
    loop = asyncio.get_running_loop()
    return await provider.complete(
        task, "JOB: x\nINPUT: {}", deadline=loop.time() + 3.0, attempt_policy="latency"
    )


async def test_a_task_from_another_branch_picks_up_its_override_without_code(profiles_dir: Path) -> None:
    """The pivot branch builds ``query_rewrite``'s provider as ``[p for p in profile_chain(settings)
    if TASK not in disabled_tasks(p.name)]`` and calls ``complete`` with its spec: the override set
    by ``HLM_FALLBACK_PROFILE__QUERY_REWRITE`` is used with no task-specific code."""
    s = settings(task_fallback_profiles={"query_rewrite": "wf-syn"})
    chain = [p for p in pr.profile_chain(s) if "query_rewrite" not in rj.disabled_tasks(p.name)]
    transport, hosts = _answering(f"{PRIMARY}.invalid")
    ledger = MemoryLedger()
    provider = Provider(chain, mode="live", ledger=ledger, budget=MemoryBudget(1), transport=transport)
    try:
        res = await _complete(provider, _spec("query_rewrite"))
        assert res.profile == "wf-syn" and hosts == [f"{PRIMARY}.invalid", "wf-syn.invalid"]
        other = await _complete(provider, _spec("some_other_task"))  # no override: the default
        assert other.profile == "wf-default"
    finally:
        await provider.aclose()
    assert [(r.task, r.profile, r.outcome) for r in ledger.rows] == [
        ("query_rewrite", PRIMARY, "http_error"),
        ("query_rewrite", "wf-syn", "ok"),
        ("some_other_task", PRIMARY, "http_error"),
        ("some_other_task", "wf-default", "ok"),
    ]


async def test_breakers_and_ledger_prices_are_per_fallback_profile(profiles_dir: Path) -> None:
    s = settings(task_fallback_profiles={"risk_judge": "wf-risk"})
    transport, hosts = _answering(f"{PRIMARY}.invalid")
    ledger, budget = MemoryLedger(), MemoryBudget(1)
    provider = Provider(pr.profile_chain(s), mode="live", ledger=ledger, budget=budget, transport=transport)
    views = ChainBreakers(lambda: provider, task="risk_judge")
    try:
        res = await _complete(provider, load_task("risk_judge"))
        assert res.profile == "wf-risk"
        assert set(provider._breakers) == {PRIMARY, "wf-risk"}  # its own key; the default untouched
        assert provider.breaker(PRIMARY).failures == 1 and provider.breaker("wf-risk").failures == 0
        assert views.allow() and views.state == "closed"
        await _complete(provider, _spec("relate"))  # no override: the default fallback
        assert set(provider._breakers) == {PRIMARY, "wf-risk", "wf-default"}
        assert provider.breaker(PRIMARY).failures == 2
    finally:
        await provider.aclose()
    risk = pr.named_profile("wf-risk")
    assert [(r.task, r.profile, r.outcome) for r in ledger.rows] == [
        ("risk_judge", PRIMARY, "http_error"),
        ("risk_judge", "wf-risk", "ok"),
        ("relate", PRIMARY, "http_error"),
        ("relate", "wf-default", "ok"),
    ]
    ok = [r for r in ledger.rows if r.outcome == "ok"]
    assert [(r.task, r.profile, r.model_id) for r in ok] == [
        ("risk_judge", "wf-risk", "stub/wf-risk"),
        ("relate", "wf-default", "stub/wf-default"),
    ]
    assert ok[0].cost_usd == risk.cost_usd(100, 20)  # settled at the fallback's own prices
    assert ok[0].reserved_usd == risk.worst_usd(
        _input_tokens(provider, ok[0]), load_task("risk_judge").max_tokens
    )
    assert ok[1].cost_usd == pr.named_profile("wf-default").cost_usd(100, 20)
    assert budget.spent == sum((r.cost_usd for r in ledger.rows), Decimal(0))


def _input_tokens(provider: Provider, row: Any) -> int:
    """The input estimate the reservation used (the risk_judge prompt + the fixed user message)."""
    spec = load_task(row.task)
    messages = [{"role": "system", "content": spec.system}, {"role": "user", "content": "JOB: x\nINPUT: {}"}]
    return provider.estimate_input_tokens(messages)


def test_verifier_uses_the_relate_verify_chain(profiles_dir: Path) -> None:
    s = settings(task_fallback_profiles={"relate_verify": "wf-syn"})
    provider = Provider(pr.profile_chain(s), mode="live", ledger=MemoryLedger())
    w = SimpleNamespace(provider=provider, settings=s)
    assert names(verifier_chain(w, PRIMARY)) == ["wf-syn", PRIMARY]  # cross: the other one first
    assert names(verifier_chain(w, "wf-default")) == [PRIMARY, "wf-syn"]  # relate answered by the default
    w.settings = s.model_copy(update={"librarian_verifier": "self"})
    assert names(verifier_chain(w, "wf-default")) == [PRIMARY]


# --------------------------------------------------------------------------- D-017 + production mapping
def _model_ids() -> set[str]:
    ids = set()
    for f in (ROOT / "profiles").glob("*.toml"):
        model = tomllib.loads(f.read_text()).get("HLM_LLM_MODEL", "")
        if "/" in model or "-" in model:
            ids.add(model)
    return ids


def test_no_model_id_outside_profiles() -> None:
    """D-017: the fallback wiring, the api/librarian/ops code and the deploy configuration name
    profiles, never model ids (``bench/`` takes model ids as user input and is out of scope)."""
    files = [p for p in (ROOT / "src/hlmemo").rglob("*") if "bench" not in p.parts]
    files += [ROOT / "deploy/llm.env.example", *(ROOT / "deploy/scripts").glob("*")]
    files = [p for p in files if p.is_file() and "__pycache__" not in p.parts]
    ids = _model_ids()
    assert ids, "no model ids found in profiles/"
    hits = [
        (str(p.relative_to(ROOT)), m)
        for p in files
        for m in ids
        if m in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert hits == []
    # and the resolution code names no task: overrides are generic by task name
    for code in ("src/hlmemo/config.py", "src/hlmemo/librarian/profiles.py"):
        text = (ROOT / code).read_text()
        assert not re.search(r"\b(risk_judge|synthesis|query_rewrite|relate)\b", text), code


def _template_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for key in list(os.environ):
        if key.upper().startswith(("HLM_FALLBACK_PROFILE", "HLM_PROFILE")):
            monkeypatch.delenv(key)
    monkeypatch.delenv("HLM_PROFILES_DIR", raising=False)
    for line in (ROOT / "deploy/llm.env.example").read_text().splitlines():
        key, sep, value = line.partition("=")
        if (
            sep
            and not line.startswith("#")
            and (key.startswith("HLM_FALLBACK_PROFILE") or key == "HLM_PROFILE")
        ):
            monkeypatch.setenv(key, value)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-secret")


def test_production_mapping_of_the_template(monkeypatch) -> None:  # noqa: ANN001
    _template_env(monkeypatch)
    s = get_settings(librarian_enabled=True)
    assert s.profile == "openrouter-gpt6-luna" and s.fallback_profile == "openrouter-glm53-flash"
    assert s.task_fallback_profiles == {
        "synthesis": "openrouter",
        "query_rewrite": "openrouter",
        "risk_judge": "openrouter-qwen38-27b-fast",
    }
    # the risk fallback is qualified (G-LIVE-C PASS), so it replaces D-071's retrieval-only fallback
    assert "risk_judge" not in pr.profile_disabled_tasks("openrouter-qwen38-27b-fast")
    assert "risk_judge" not in pr.profile_disabled_tasks("openrouter-glm53-flash")  # PASS at the limit
    assert names(rj.judge_chain(s)) == ["openrouter-gpt6-luna", "openrouter-qwen38-27b-fast"]
    assert names(syn.synthesis_chain(s)) == ["openrouter-gpt6-luna", "openrouter"]
    tasks = pr.describe_chains(s)["tasks"]
    for task in ("place", "relate", "relate_verify", "contradiction", "placement", "summary"):
        assert (tasks[task]["fallback"], tasks[task]["source"]) == ("openrouter-glm53-flash", "default")
    assert tasks["query_rewrite"]["fallback"] == "openrouter"
    warnings = pr.check_chains(s)
    if "query_rewrite" in pr.known_tasks():
        assert warnings == []
    else:  # this base predates the query_rewrite task: a warning, not a failure
        assert len(warnings) == 1 and "query_rewrite" in warnings[0]
    lines = chain_lines(pr.describe_chains(s))
    assert lines[0] == "fallback    default=openrouter-glm53-flash"
    assert any(
        re.fullmatch(r"chain\s+risk_judge\s+primary=openrouter-gpt6-luna fallback=openrouter-qwen38-27b-fast"
                     r" \(task override\)", line)
        for line in lines
    ), lines  # fmt: skip
