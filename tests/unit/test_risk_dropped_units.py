"""Units of ``dropped_by_judge`` (consults 114, 115): the entry shape, title redaction, and packing
that leaves the warnings exactly as the pre-change code packed them."""

from __future__ import annotations

import json
import random
import string
from typing import Any

import pytest

from hlmemo.core import risk_service as rs
from hlmemo.core.budget import BudgetError, Meter
from hlmemo.core.errors import ToolError
from hlmemo.core.retrieval import pack_prefix


def _cand(title: str, score: float = 0.06) -> rs.RiskCandidate:
    return rs.RiskCandidate(
        version_id=812,
        logical_id=40,
        project_id=3,
        project_ids=[3],
        project="demo",
        device_scope="all",
        kind="lesson",
        title=title,
        rrf=score,
        det_score=score,
        vector_dist=0.1,
        ranks=(1, None, 2, 1),
    )


# --------------------------------------------------------------------------- entry shape and redaction
def test_dropped_entry_shape_and_deterministic_why() -> None:
    d = rs._dropped(_cand("Bump app.js?v= on every frontend change"))
    assert set(d) == {"clue", "title", "why", "source_project"}
    assert d["clue"] == "v812" and d["source_project"] == "demo"
    # the judge gives no reason for a non-match: a fixed sentence, no judge or memory text
    assert d["why"].startswith("Retrieval matched this lesson (LTV lists, score 0.060)")
    assert "did not warn on it" in d["why"] and len(d["why"]) <= rs.WHY_MAX


def test_strong_secret_shape_in_a_title_is_masked() -> None:
    rnd = random.Random(7)
    token = "gh" + "p_" + "".join(rnd.choice(string.ascii_letters + string.digits) for _ in range(36))
    d = rs._dropped(_cand(f"Rotate the deploy token {token} after use"))
    assert token not in json.dumps(d)


@pytest.mark.parametrize(
    ("title", "secret"),
    [
        # review 115 (both reviewers): narrower than strong_secret_rule, caught by the librarian redaction
        ("Never ship " + "pass" + "word=" + "ExampleSecret123" + " in a config", "ExampleSecret123"),
        (
            "Prod DSN " + "postgresql://alice:" + "VerySecret123" + "@db/prod" + " leaked into a log",
            "VerySecret123",
        ),
    ],
)
def test_librarian_redaction_applies_to_titles(title: str, secret: str) -> None:
    d = rs._dropped(_cand(title))
    assert secret not in d["title"] and secret not in json.dumps(d), d
    assert "REDACTED" in d["title"], d


def test_plain_title_is_kept() -> None:
    title = "Test schema migrations on a prod copy"
    assert rs._dropped(_cand(title))["title"] == title


# --------------------------------------------------------------------------- packing
class _Deps:
    meter = Meter()


def _env() -> dict[str, Any]:
    return {"project": "demo", "verdict": "warn", "judged": True, "judge": "ok", "warnings": [], "omitted": 0}


def _item(i: int, words: int) -> dict[str, str]:
    return {"clue": f"v{i}", "title": f"lesson {i}", "why": "w " * words, "source_project": "demo"}


def _old_pack(envelope: dict[str, Any], budget: int, warnings: list[dict[str, Any]]) -> dict[str, Any]:
    """``risk_service._pack`` as it was before ``dropped_by_judge`` (origin/main bb7ab28), verbatim."""
    meter = _Deps.meter
    prefix = [0]
    base = meter.settle(envelope, budget)
    for w in warnings:
        prefix.append(prefix[-1] + meter.count(w) + 1)

    def apply(n: int) -> None:
        envelope["warnings"] = warnings[:n]
        envelope["omitted"] = len(warnings) - n

    try:
        pack_prefix(meter, envelope, budget, len(warnings), apply, lambda n: base + prefix[n])
    except BudgetError as exc:
        raise ToolError(exc.code, str(exc), **exc.details) from exc
    return envelope


def _outcome(fn: Any) -> tuple[str, Any]:
    try:
        return "ok", fn()
    except ToolError as exc:
        return "error", exc.code


def test_reviewers_case_the_new_fields_never_push_a_warning_out() -> None:
    """Review 115: at budget 256 one long warning fits; an empty dropped list used to push it out."""
    warnings = [_item(1, 176)]
    old = _old_pack(_env(), 256, list(warnings))
    for dropped in ([], [_item(9, 5)], [_item(9, 300)]):
        new = rs._pack(_Deps(), _env(), 256, list(warnings), dropped)
        assert (new["warnings"], new["omitted"]) == (old["warnings"], old["omitted"]), (dropped, new)
        assert new["omitted"] == 0 and len(new["warnings"]) == 1


@pytest.mark.parametrize("n_warn", [0, 1, 2, 3])
@pytest.mark.parametrize("warn_words", [10, 90, 176])
def test_warnings_and_budget_errors_match_the_pre_change_packing(n_warn: int, warn_words: int) -> None:
    warnings = [_item(i, warn_words) for i in range(n_warn)]
    dropped = [_item(10 + i, 40) for i in range(3)]
    for budget in range(256, 1600, 12):
        old = _outcome(lambda b=budget: _old_pack(_env(), b, list(warnings)))
        new = _outcome(lambda b=budget: rs._pack(_Deps(), _env(), b, list(warnings), dropped))
        assert old[0] == new[0], (budget, old, new)  # never raises where it did not before, and vice versa
        if old[0] == "error":
            continue
        o, n = old[1], new[1]
        assert (n["warnings"], n["omitted"]) == (o["warnings"], o["omitted"]), budget
        assert Meter().count(n) <= n["budget"]["used"] <= budget, budget
        if "dropped_by_judge" in n:
            assert n["omitted"] == 0 and n["dropped_by_judge"], n  # only after every warning, never empty
            assert n["dropped_by_judge"] == dropped[: len(n["dropped_by_judge"])], n  # a best-first prefix
            assert len(n["dropped_by_judge"]) + n["dropped_omitted"] == len(dropped), n
        else:
            assert "dropped_omitted" not in n and n == {**o, "budget": n["budget"]}, (budget, n)


def test_pack_fills_the_dropped_list_when_there_is_room() -> None:
    full = rs._pack(_Deps(), _env(), 4000, [_item(1, 20)], [_item(10 + i, 60) for i in range(3)])
    assert full["omitted"] == 0 and len(full["dropped_by_judge"]) == 3 and full["dropped_omitted"] == 0


def test_pack_with_an_omitted_warning_adds_no_dropped_fields() -> None:
    out = rs._pack(_Deps(), _env(), 400, [_item(i, 140) for i in range(3)], [_item(10, 5)])
    assert out["omitted"] > 0 and "dropped_by_judge" not in out and "dropped_omitted" not in out, out


def test_pack_without_a_dropped_list_adds_no_field() -> None:
    for dropped in (None, []):
        out = rs._pack(_Deps(), _env(), 1000, [_item(1, 10)], dropped)
        assert "dropped_by_judge" not in out and "dropped_omitted" not in out
