"""Units of ``dropped_by_judge`` (consult 114): the entry shape and the secret mask on titles."""

from __future__ import annotations

import json
import random
import string

from hlmemo.core import risk_service as rs
from hlmemo.core.budget import Meter


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


def test_dropped_entry_shape_and_deterministic_why() -> None:
    d = rs._dropped(_cand("Bump app.js?v= on every frontend change"))
    assert set(d) == {"clue", "title", "why", "source_project"}
    assert d["clue"] == "v812" and d["source_project"] == "demo"
    assert "LTV lists" in d["why"] and "did not warn on it" in d["why"] and len(d["why"]) <= rs.WHY_MAX


def test_secret_shaped_title_is_masked() -> None:
    rnd = random.Random(7)
    token = "gh" + "p_" + "".join(rnd.choice(string.ascii_letters + string.digits) for _ in range(36))
    d = rs._dropped(_cand(f"Rotate the deploy token {token} after use"))
    assert d["title"] == "<redacted:github-token>"
    assert token not in json.dumps(d)


class _Deps:
    meter = Meter()


def _env() -> dict[str, object]:
    return {"project": "demo", "verdict": "warn", "judged": True, "judge": "ok", "warnings": [], "omitted": 0}


def _item(i: int, words: int) -> dict[str, str]:
    return {"clue": f"v{i}", "title": f"lesson {i}", "why": "w " * words, "source_project": "demo"}


def test_pack_omits_the_dropped_list_while_a_warning_is_omitted() -> None:
    warnings = [_item(i, 140) for i in range(3)]
    dropped = [_item(10 + i, 20) for i in range(3)]
    out = rs._pack(_Deps(), _env(), 400, warnings, dropped)
    assert out["omitted"] > 0 and out["dropped_by_judge"] == [] and out["dropped_omitted"] == 3, out
    assert Meter().count(out) <= out["budget"]["used"] <= 400


def test_pack_fills_dropped_after_all_warnings() -> None:
    warnings = [_item(1, 20)]
    dropped = [_item(10 + i, 60) for i in range(3)]
    full = rs._pack(_Deps(), _env(), 4000, warnings, dropped)
    assert full["omitted"] == 0 and len(full["dropped_by_judge"]) == 3 and full["dropped_omitted"] == 0
    for budget in range(256, 600, 8):
        out = rs._pack(_Deps(), _env(), budget, warnings, dropped)
        assert Meter().count(out) <= out["budget"]["used"] <= budget, budget
        assert len(out["dropped_by_judge"]) + out["dropped_omitted"] == 3, out
        assert out["dropped_by_judge"] == dropped[: len(out["dropped_by_judge"])], out  # best first, a prefix


def test_pack_without_a_dropped_list_adds_no_field() -> None:
    out = rs._pack(_Deps(), _env(), 1000, [_item(1, 10)], None)
    assert "dropped_by_judge" not in out and "dropped_omitted" not in out


def test_plain_title_is_kept() -> None:
    title = "Test schema migrations on a prod copy"
    assert rs._dropped(_cand(title))["title"] == title
