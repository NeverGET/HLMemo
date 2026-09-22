"""Preflight: query text, retry-once, exact prompt template (PHASE0-SPEC §5)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from test_cli_support import (  # noqa: F401
    _clean_tables,
    _isolated_home,
    compact,
    error_result,
    fake_server,
    query_ok,
)

from hlmemo.cli.mcp_client import MemoryClient, ToolCallError
from hlmemo.cli.preflight import (
    AWAIT_USER,
    FALLBACK_QUERY,
    INSTRUCTION_LINE,
    build_prompt,
    build_query_text,
    run_preflight,
)


def _git_repo(path: Path, subjects: list[str]) -> Path:
    path.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x",
    }
    subprocess.run(["git", "-C", str(path), "init", "-q", "-b", "feature/x"], check=True)
    for i, s in enumerate(subjects):
        (path / f"f{i}").write_text(s)
        subprocess.run(["git", "-C", str(path), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(path), "commit", "-q", "-m", s],
            check=True,
            env={**env, "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
        )
    return path


def test_query_text_task_wins(tmp_path: Path) -> None:
    assert build_query_text("  fix the pool bug ", tmp_path) == "fix the pool bug"


def test_query_text_from_git_branch_and_last_three_subjects(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo", ["one", "two", "three", "four"])
    assert build_query_text(None, repo) == "feature/x: four; three; two"


def test_query_text_fallback_outside_git(tmp_path: Path) -> None:
    d = tmp_path / "plain"
    d.mkdir()
    assert build_query_text("", d) == FALLBACK_QUERY


def test_prompt_template_exact() -> None:
    result = {"hits": [], "evidence": "none", "budget": {"limit": 3000, "used": 9, "tokenizer": "o200k_base"}}
    p = build_prompt(result, project="hlmemo", device="mbp", queried_at="2026-09-22T10:00:00Z", task=None)
    expected = (
        '<hlmemo-preflight project="hlmemo" device="mbp" queried_at="2026-09-22T10:00:00Z">'
        + compact(result)
        + "</hlmemo-preflight>\n"
        + INSTRUCTION_LINE
        + AWAIT_USER
    )
    assert p == expected
    assert "\n" not in p.split("</hlmemo-preflight>")[0]  # compact JSON, single line
    p2 = build_prompt(result, project="hlmemo", device="mbp", queried_at="t", task="ship it")
    assert p2.endswith("Task: ship it")


def test_prompt_escapes_attribute_values() -> None:
    p = build_prompt({}, project='a"b', device="<x>", queried_at="t", task=None)
    assert 'project="a&quot;b" device="&lt;x&gt;"' in p


def test_retry_once_on_unavailable_then_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state = {"n": 0}

    def handler(args):  # noqa: ANN001
        state["n"] += 1
        if state["n"] == 1:
            return error_result("E_UNAVAILABLE", "db down", retryable=True)
        return query_ok(args["project"], args["token_budget"])

    srv, calls = fake_server(handler)
    out = run_preflight(
        MemoryClient.in_memory(srv),
        project="hlmemo",
        device="mbp",
        budget=777,
        task="t",
        root=tmp_path,
        sleep_s=0,
    )
    assert out.ok and out.attempts == 2 and len(calls) == 2
    assert calls[0]["args"] == {"project": "hlmemo", "query": "t", "token_budget": 777}
    assert out.prompt is not None and out.prompt.startswith('<hlmemo-preflight project="hlmemo" device="mbp"')


def test_retry_only_once_then_failed_outcome(tmp_path: Path) -> None:
    srv, calls = fake_server(lambda _a: error_result("E_UNAVAILABLE", "still down", retryable=True))
    out = run_preflight(
        MemoryClient.in_memory(srv), project="p", device="d", budget=300, task="t", root=tmp_path, sleep_s=0
    )
    assert not out.ok and out.code == "E_UNAVAILABLE" and out.reason == "still down"
    assert len(calls) == 2 and out.attempts == 2


def test_non_retryable_error_not_retried(tmp_path: Path) -> None:
    srv, calls = fake_server(lambda _a: error_result("E_FORBIDDEN_PROJECT", "no grant", project="p"))
    out = run_preflight(
        MemoryClient.in_memory(srv), project="p", device="d", budget=300, task="t", root=tmp_path, sleep_s=0
    )
    assert out.code == "E_FORBIDDEN_PROJECT" and len(calls) == 1


def test_tool_call_error_carries_envelope() -> None:
    srv, _ = fake_server(lambda _a: error_result("E_BUDGET_TOO_SMALL", "min", min=256))
    with pytest.raises(ToolCallError) as ei:
        MemoryClient.in_memory(srv).query("p", "q", 300)
    assert ei.value.code == "E_BUDGET_TOO_SMALL" and ei.value.details == {"min": 256}
    assert ei.value.to_dict()["retryable"] is False
