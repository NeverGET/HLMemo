import json
import random

import pytest

from hlmemo.core import METER_VERSION
from hlmemo.core.budget import (
    BUDGET_MAX,
    BUDGET_MIN,
    BudgetError,
    BudgetTooLarge,
    BudgetTooSmall,
    Meter,
    canonical,
    validate_budget,
)


def test_meter_version():
    assert METER_VERSION == "o200k_base"
    assert Meter.tokenizer == "o200k_base"


def test_canonical_is_compact_sorted_unicode():
    obj = {"b": 1, "a": {"z": [1, 2], "y": "şç"}}
    s = canonical(obj)
    assert s == '{"a":{"y":"şç","z":[1,2]},"b":1}'
    assert s == json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def test_count_matches_tiktoken_over_canonical(meter):
    import tiktoken

    enc = tiktoken.get_encoding("o200k_base")
    obj = {"hits": [{"title": "İstanbul yağmuru", "preview": "docker compose up -d"}], "omitted": 0}
    assert meter.count(obj) == len(enc.encode(canonical(obj)))
    assert meter.count_text("") == 0


def test_truncate(meter):
    text = "one two three four five six seven eight nine ten"
    cut, truncated = meter.truncate(text, 3)
    assert truncated and meter.count_text(cut) == 3 and text.startswith(cut)
    same, t2 = meter.truncate(text, 1000)
    assert same == text and t2 is False


@pytest.mark.parametrize("n", [BUDGET_MIN, 2000, BUDGET_MAX])
def test_validate_budget_ok(n):
    assert validate_budget(n) == n


def test_validate_budget_default_for_write():
    assert validate_budget(None, default=2000) == 2000


@pytest.mark.parametrize("n", [0, 1, 255, -5])
def test_validate_budget_too_small(n):
    with pytest.raises(BudgetTooSmall) as ei:
        validate_budget(n)
    assert ei.value.code == "E_BUDGET_TOO_SMALL"
    assert ei.value.details == {"min": 256}
    assert ei.value.as_error()["retryable"] is False


@pytest.mark.parametrize("n", [32001, 10**9])
def test_validate_budget_too_large(n):
    with pytest.raises(BudgetTooLarge) as ei:
        validate_budget(n)
    assert ei.value.code == "E_BUDGET_TOO_LARGE"


@pytest.mark.parametrize("n", [None, True, 1.5, "3000"])
def test_validate_budget_wrong_type(n):
    with pytest.raises(BudgetError) as ei:
        validate_budget(n)
    assert ei.value.code == "E_INVALID_ARG"


def _envelope():
    return {"project": "fx-main", "card": None, "evidence": "matched", "indexing_pending": False}


def _hit(i, size_words):
    return {"clue": f"v{i}.0", "kind": "fact", "title": f"t{i}", "preview": " ".join(f"w{i}x{j}" for j in range(size_words))}


def test_pack_basic_shape(meter):
    env = _envelope()
    cands = [_hit(i, 5) for i in range(10)]
    res = meter.pack(env, cands, 400, render=lambda h: h)
    assert res.packed == cands[: len(res.packed)]
    assert res.omitted == 10 - len(res.packed)
    assert env["hits"] == res.packed and env["omitted"] == res.omitted
    assert env["budget"] == {"limit": 400, "used": res.used, "tokenizer": "o200k_base"}
    assert res.used <= 400
    # `used` is the exact size of the wire serialisation
    assert meter.count(env) == res.used


def test_pack_all_fit_when_budget_large(meter):
    env = _envelope()
    cands = [_hit(i, 3) for i in range(5)]
    res = meter.pack(env, cands, 32000, render=lambda h: h)
    assert len(res.packed) == 5 and res.omitted == 0


def test_pack_empty_envelope_too_big_raises(meter):
    env = _envelope()
    env["card"] = {"text": "x " * 2000}
    with pytest.raises(BudgetTooSmall) as ei:
        meter.pack(env, [_hit(1, 2)], 256, render=lambda h: h)
    assert ei.value.details["min"] > 256


def test_pack_stops_at_first_nonfit(meter):
    # a huge candidate early must block later small ones (spec: stop at first non-fit)
    env = _envelope()
    cands = [_hit(0, 2), _hit(1, 5000), _hit(2, 2)]
    res = meter.pack(env, cands, 300, render=lambda h: h)
    assert len(res.packed) == 1 and res.omitted == 2


def test_pack_render_is_applied(meter):
    env = _envelope()
    res = meter.pack(env, [1, 2, 3], 1000, render=lambda i: {"clue": f"v{i}", "n": i})
    assert env["hits"] == [{"clue": "v1", "n": 1}, {"clue": "v2", "n": 2}, {"clue": "v3", "n": 3}]
    assert res.omitted == 0


def test_pack_1000_seeded_random_cases_never_exceed(meter):
    rng = random.Random(20260922)
    words = ["kedi", "docker", "Straße", "İstanbul", "APP_DB_DSN", "E4193", "compose", "yağmur", "über", "path/to/file.py"]
    for case in range(1000):
        budget = rng.randint(BUDGET_MIN, 8000)
        n = rng.randint(0, 40)
        cands = []
        for i in range(n):
            size = rng.choice([1, 3, 8, 20, 60, 150, 400])
            preview = " ".join(rng.choice(words) for _ in range(size))
            cands.append({"clue": f"v{rng.randint(1, 10**6)}.{rng.randint(0, 9)}", "kind": "fact", "title": f"t{i}", "preview": preview})
        env = _envelope()
        res = meter.pack(env, cands, budget, render=lambda h: h)
        wire = canonical(env)
        measured = meter.count_text(wire)
        assert measured <= budget, f"case {case}: {measured} > {budget}"
        assert res.used <= budget
        assert measured <= res.used <= measured + 1  # used is exact, or an overestimate by 1 at a digit boundary
        assert len(res.packed) + res.omitted == n
        assert env["hits"] == cands[: len(res.packed)]
        assert env["budget"]["limit"] == budget and env["budget"]["tokenizer"] == "o200k_base"
        assert json.loads(wire)["omitted"] == res.omitted
