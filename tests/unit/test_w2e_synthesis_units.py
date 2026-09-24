"""W2e units (no database, no network): the synthesis prompt + schema, the D-067 citation validator,
rendering and budget packing of ``synthesis`` (clues ⊆ returned hits, exact ``budget.used``), the
weak-evidence rule, the ``synthesize`` flag and ``query/2`` schema, and profile qualification."""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any

import pytest

from hlmemo.config import get_settings
from hlmemo.core import synthesis_service as ss
from hlmemo.core.budget import Meter
from hlmemo.core.errors import ToolError
from hlmemo.librarian.prompts import load_task
from hlmemo.librarian.redact import Redactor
from hlmemo.librarian.risk_judge import disabled_tasks
from hlmemo.librarian.tasks import synthesis as syn
from hlmemo.server.tools import TOOL_BY_NAME, schemas
from hlmemo.server.tools import query as query_tool

METER = Meter()
MARKERS = re.compile(r"\[(v\d+\.\d+(?:, v\d+\.\d+)*)\]")


@pytest.fixture(autouse=True)
def _clean_tables():
    """Override of tests/conftest.py::_clean_tables: these tests need no database."""
    yield


# --------------------------------------------------------------------------- prompt + schema
def test_prompt_and_schema() -> None:
    spec = load_task("synthesis")
    assert spec.prompt_version == "v1" and spec.max_tokens == 700
    assert 'JOB "synthesis"' in spec.system and "insufficient_evidence" in spec.system
    assert "never guess" in spec.system  # D-067: abstention is rewarded, first-class
    ok = {"status": "answered", "sentences": [{"text": "x.", "cite": ["C1"]}]}
    assert spec.schema_errors(ok) is None
    assert spec.schema_errors({"status": "insufficient_evidence", "sentences": []}) is None
    assert spec.schema_errors({"status": "maybe", "sentences": []}) is not None
    assert spec.schema_errors({"status": "answered", "sentences": [{"text": "x"}]}) is not None
    assert syn._consistency(ok) is None
    assert syn._consistency({"status": "answered", "sentences": []}) is not None
    assert syn._consistency({"status": "insufficient_evidence", "sentences": ok["sentences"]}) is not None


def test_user_message_is_data() -> None:
    msg = syn.user_message("why?", [{"id": "C1", "title": "t", "date": "2026-09-23", "text": "b"}])
    head, _, payload = msg.partition("\nINPUT: ")
    assert head == "JOB: synthesis"
    assert json.loads(payload) == {
        "question": "why?",
        "excerpts": [{"id": "C1", "title": "t", "date": "2026-09-23", "text": "b"}],
    }


# --------------------------------------------------------------------------- citation validator
def _local(n: int, text: str = "x") -> dict[str, syn.Excerpt]:
    return {
        f"C{i}": syn.Excerpt(100 + i, f"v{100 + i}.0", f"t{i}", "2026-09-23", text) for i in range(1, n + 1)
    }


def test_validator_drops_uncited_and_counts_foreign_ids() -> None:
    raw = [
        {"text": "Cited once.", "cite": ["C1"]},
        {"text": "Invented citation.", "cite": ["C9", "v101.0"]},  # neither was shown
        {"text": "No citation.", "cite": []},
        {"text": "Two, one repeated.", "cite": ["C2", "C2", "C1", "C77"]},
        {"text": "   ", "cite": ["C1"]},  # no text
        "not an object",
    ]
    kept, dropped, bad, unsupported = syn.validate_sentences(raw, _local(2), lambda s: s)
    assert [(s.text, s.clues) for s in kept] == [
        ("Cited once.", ["v101.0"]),
        ("Two, one repeated.", ["v102.0", "v101.0"]),
    ]
    assert dropped == 4 and bad == 3 and unsupported == 0


def test_validator_redacts_and_caps_sentences() -> None:
    secret = "hlm_" + "S" * 43
    raw = [{"text": f"The token is {secret}   and   more " + "y" * 900, "cite": ["C1"]}]
    kept, _, _, _ = syn.validate_sentences(raw, _local(1, f"a token {secret} here"), Redactor().text)
    assert secret not in kept[0].text and "REDACTED" in kept[0].text
    assert len(kept[0].text) <= syn.SENTENCE_CHARS and "  " not in kept[0].text
    many = [{"text": f"s{'abcdefghi'[i]}.", "cite": ["C1"]} for i in range(9)]
    kept, dropped, _, _ = syn.validate_sentences(many, _local(1), lambda s: s)
    assert len(kept) == syn.MAX_SENTENCES and dropped == 9 - syn.MAX_SENTENCES


# --------------------------------------------------------------------------- support guard (Sol 51 #3)
def test_claims_extraction() -> None:
    got = syn.claims(
        "Run `docker compose exec -T db` with --no-preflight; the port is 8765, see docs/USAGE.md and "
        'HLM_IMAGE_TAG=abc, "quoted value", plain words, e.g. not a claim, €33.91 and 20%.'
    )
    for c in (
        "docker compose exec -T db",
        "--no-preflight",
        "8765",
        "docs/USAGE.md",
        "HLM_IMAGE_TAG=abc",
        "quoted value",
        "33.91",
        "20",
    ):
        assert c in got, (c, got)
    assert not {"plain", "words", "e.g", "not", "claim"} & set(got)


@pytest.mark.parametrize(
    ("claim", "text", "ok"),
    [
        ("8765", "listens on :8765 now", True),
        ("0.155.1", "codex 0.155.1", True),
        ("0.155.2", "codex 0.155.1", False),
        ("HLM_IMAGE", "the hlm_image variable", True),  # case-insensitive
        ("1,427", "1427 chunks", True),  # thousands separators
        ("10s", "a 10 s timeout", True),  # number glued to its unit
        ("top-3", "the top 3 hits", True),  # hyphen-joined parts
        ("99999", "port 8765", False),
        ("docs/X.md", "docs/Y.md", False),
        # Sol 52 #2: whole tokens only
        ("42", "port 142 only", False),
        ("42", "version 4.2 and 42.5", False),
        ("42", "port 42.", True),
        ("foo", "foobar and barfoo", False),
        ("foo_bar", "foo_barbaz", False),
        ("0.155.1", "codex 0.155.12", False),
        ("HLM_IMAGE", "HLM_IMAGE_TAG only", False),
        ("docs/USAGE.md", "see docs/USAGE.md.", True),
        ("top-3", "the top 30 hits", False),
    ],
)
def test_supported(claim: str, text: str, ok: bool) -> None:
    assert syn.supported(claim, syn._norm(text)) is ok


def test_unsupported_sentences_are_dropped() -> None:
    local = _local(2, "The relay listens on port 8765 and runs `hlm serve --fast`.")
    raw = [
        {"text": "It listens on port 8765.", "cite": ["C1"]},
        {"text": "It listens on port 9999.", "cite": ["C1"]},  # a number no cited excerpt states
        {"text": "Start it with `hlm serve --slow`.", "cite": ["C2"]},  # a command it does not state
        {"text": "Start it with `hlm serve --fast`.", "cite": ["C2"]},
    ]
    kept, dropped, bad, unsupported = syn.validate_sentences(raw, local, lambda s: s)
    assert [s.text for s in kept] == ["It listens on port 8765.", "Start it with `hlm serve --fast`."]
    assert dropped == 2 and unsupported == 2 and bad == 0
    kept, _, _, unsupported = syn.validate_sentences(raw[1:3], local, lambda s: s)
    assert kept == [] and unsupported == 2  # the synthesizer then abstains


# --------------------------------------------------------------------------- weak evidence
def _hit(i: int, score: float = 0.02, preview: str = "p") -> dict[str, Any]:
    return {
        "clue": f"v{100 + i}.{i % 3}",
        "kind": "fact",
        "title": f"title {i}",
        "preview": preview,
        "score": score,
        "valid_from": "2026-09-23T00:00:00Z",
        "tags": [],
        "device_scope": "all",
    }


def test_weak_rule() -> None:
    assert ss.weak({"hits": [], "evidence": "none"}) == ss.NO_HITS
    assert ss.weak({"hits": [_hit(1, 0.9)], "evidence": "none"}) is None  # near misses
    assert ss.weak({"hits": [_hit(1, ss.TAU_S - 1e-6)], "evidence": "matched"}) is None
    assert ss.weak({"hits": [_hit(1, ss.TAU_S)], "evidence": "matched"}) == ss.STRONG
    assert ss.weak({"hits": [_hit(1, 0.05)], "evidence": "matched"}, tau=1.0) is None


# --------------------------------------------------------------------------- render + pack
def _envelope(n_hits: int, budget: int, preview_words: int = 30) -> dict[str, Any]:
    env: dict[str, Any] = {
        "project": "syn-docs",
        "as_of": {"valid_at": "2026-09-24T00:00:00Z", "known_at": "2026-09-24T00:00:00Z"},
        "device_class": "personal",
        "evidence": "matched",
        "indexing_pending": False,
        "card": {"clue": "v1", "text": "card " * 40, "stale": False, "stale_clues": [], "truncated": False},
        "hits": [_hit(i, 0.03, "word " * preview_words) for i in range(1, n_hits + 1)],
        "omitted": 7,
    }
    METER.settle(env, budget)
    return env


def _sentences(hits: list[dict[str, Any]], rng: random.Random, n: int = 4) -> list[syn.Sentence]:
    out = []
    for i in range(n):
        cited = rng.sample([h["clue"] for h in hits], k=min(len(hits), rng.randint(1, 3)))
        out.append(syn.Sentence(f"Sentence {i} states value {i}.{i} about the thing " + "x " * 20, cited))
    return out


def _check(env: dict[str, Any], budget: int, total: int) -> None:
    text = json.dumps(env, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    used = METER.count_text(text)
    assert env["budget"] == {"limit": budget, "used": used, "tokenizer": "o200k_base"}, (env["budget"], used)
    assert used <= budget
    assert env["contract_version"] == "query/2"
    assert len(env["hits"]) + env["omitted"] == total
    hit_clues = {h["clue"] for h in env["hits"]}
    s = env.get("synthesis")
    if s is not None:
        assert set(s["clues"]) <= hit_clues, (s["clues"], hit_clues)
        if s["status"] == "answered":
            marked = [c for group in MARKERS.findall(s["text"]) for c in group.split(", ")]
            assert marked and set(marked) == set(s["clues"])
            assert METER.count_text(s["text"]) <= ss.TEXT_MAX_TOKENS
    else:
        assert env["synthesis_unavailable"] is True and env["synthesis_reason"]


def test_pack_random_budgets_keeps_clues_in_hits_and_exact_budget() -> None:
    rng = random.Random(20260924)
    outcomes: dict[str, int] = {}
    for _ in range(300):
        budget = rng.choice([256, 300, 400, 600, 900, 1500, 3000, rng.randint(256, 4000)])
        n_hits = rng.randint(1, 25)
        env = _envelope(n_hits, 32000)
        env = _repack_to(env, budget)
        total = len(env["hits"]) + env["omitted"]
        sents = _sentences(env["hits"], rng) if env["hits"] else []
        res = syn.SynthResult(syn.OK, answer=syn.ANSWERED, sentences=sents)
        try:
            out = ss.pack(METER, env, budget, res, sents)
        except ToolError as exc:  # only when even the bare envelope does not fit
            assert exc.code == "E_BUDGET_TOO_SMALL"
            outcomes["too_small"] = outcomes.get("too_small", 0) + 1
            continue
        _check(out, budget, total)
        key = "synthesis" if "synthesis" in out else out["synthesis_reason"]
        outcomes[key] = outcomes.get(key, 0) + 1
    assert outcomes.get("synthesis", 0) >= 200, outcomes


def _repack_to(env: dict[str, Any], budget: int) -> dict[str, Any]:
    """A fast-path-like envelope already within ``budget`` (tail hits dropped), as query returns."""
    total = len(env["hits"]) + env["omitted"]
    while METER.settle(env, budget) > budget and env["hits"]:
        env["hits"] = env["hits"][:-1]
        env["omitted"] = total - len(env["hits"])
    if METER.settle(env, budget) > budget:
        env["card"] = None
        METER.settle(env, budget)
    return env


def test_pack_prefers_sentences_over_tail_hits() -> None:
    env = _envelope(20, 32000)
    hits = env["hits"]
    sents = [syn.Sentence("Answer one.", [hits[0]["clue"]]), syn.Sentence("Answer two.", [hits[19]["clue"]])]
    budget = METER.settle(env, 32000) + 5  # the fast path filled the budget
    out = ss.pack(METER, env, budget, syn.SynthResult(syn.OK, answer=syn.ANSWERED, sentences=sents), sents)
    _check(out, budget, 27)
    # room was made by dropping tail hits; the sentence citing the dropped last hit went with it
    assert len(out["hits"]) < 20 and out["synthesis"]["clues"] == [hits[0]["clue"]]
    assert out["synthesis"]["dropped"] == 1 and out["synthesis"]["tier"] == "primary"


def test_pack_abstention_and_fallback_tier() -> None:
    env = _envelope(3, 32000)
    res = syn.SynthResult(syn.OK_FALLBACK, answer=syn.INSUFFICIENT)
    out = ss.pack(METER, env, 3000, res, [])
    assert out["synthesis"] == {
        "status": "insufficient_evidence",
        "text": "",
        "clues": [],
        "tier": "fallback",
    }
    _check(out, 3000, 10)


def test_render_caps_text_at_400_tokens() -> None:
    long = [syn.Sentence("word " * 120 + str(i), ["v1.0"]) for i in range(6)]
    text, clues, n = ss.render(METER, long, {"v1.0"})
    assert METER.count_text(text) <= ss.TEXT_MAX_TOKENS and 1 <= n < 6 and clues == ["v1.0"]
    text, clues, n = ss.render(METER, long, {"v2.0"})
    assert (text, clues, n) == ("", [], 0)


def test_unavailable_markers_fit_the_budget() -> None:
    env = _envelope(12, 32000)
    budget = METER.settle(env, 32000)  # exactly full: the markers need room
    out = ss._unavailable(METER, env, budget, "timeout")
    _check(out, budget, 19)
    assert len(out["hits"]) < 12 and out["synthesis_reason"] == "timeout"


# --------------------------------------------------------------------------- flag + schema
def test_synthesize_flag_and_query2_schema() -> None:
    args = {"project": "p", "query": "q", "token_budget": 300, "synthesize": True}
    assert query_tool.synthesize_flag(args) is True and "synthesize" not in args
    assert query_tool.synthesize_flag({"project": "p"}) is False
    with pytest.raises(ToolError) as ei:
        query_tool.synthesize_flag({"synthesize": "yes"})
    assert ei.value.code == "E_INVALID_ARG"
    spec = TOOL_BY_NAME["memory.query"]
    assert spec.app_bound and spec.input_schema["properties"]["synthesize"] == {
        "type": "boolean",
        "default": False,
    }
    rest = {k: v for k, v in spec.input_schema["properties"].items() if k != "synthesize"}
    assert rest == schemas.QUERY_INPUT["properties"]  # additive: every Phase-0 field unchanged
    assert spec.input_schema["required"] == schemas.QUERY_INPUT["required"]
    assert "synthesize" not in schemas.QUERY_INPUT["properties"]


# --------------------------------------------------------------------------- qualification
def test_synthesis_chain_excludes_unqualified_profiles(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    (tmp_path / "unqualified.toml").write_text(
        'HLM_LLM_BASE_URL = "http://x.invalid/v1"\nHLM_LLM_MODEL = "stub/x"\ndisabled_tasks = ["synthesis"]\n'
    )
    (tmp_path / "qualified.toml").write_text(
        'HLM_LLM_BASE_URL = "http://y.invalid/v1"\nHLM_LLM_MODEL = "stub/y"\n'
    )
    monkeypatch.setenv("HLM_PROFILES_DIR", str(tmp_path))
    assert disabled_tasks("unqualified") == {"synthesis"}
    settings = get_settings(
        profile="qualified",
        fallback_profile="unqualified",
        llm_base_url="http://y.invalid/v1",
        llm_model="stub/y",
    )
    assert [p.name for p in syn.synthesis_chain(settings)] == ["qualified"]


def test_disabled_synthesizer_never_calls(monkeypatch) -> None:  # noqa: ANN001
    synth = syn.Synthesizer(get_settings(librarian_enabled=False))
    assert synth.unavailable() == syn.DISABLED and synth.provider is None


def test_both_live_profiles_are_qualified_for_synthesis(monkeypatch) -> None:  # noqa: ANN001
    """G-LIVE-D 2026-09-24 (eval/live/2026-09-24-synthesis): luna and the deepseek fallback both pass,
    so neither lists `synthesis` in disabled_tasks (deepseek keeps risk_judge, D-071)."""
    assert syn.TASK not in disabled_tasks("openrouter-gpt6-luna")
    assert syn.TASK not in disabled_tasks("openrouter") and "risk_judge" in disabled_tasks("openrouter")
    monkeypatch.setenv("HLM_PROFILE", "openrouter-gpt6-luna")
    settings = get_settings(profile="openrouter-gpt6-luna", fallback_profile="openrouter")
    assert [p.name for p in syn.synthesis_chain(settings)] == ["openrouter-gpt6-luna", "openrouter"]
