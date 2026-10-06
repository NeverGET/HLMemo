"""PV-1..PV-5 (protocol §5.2): the write-path validations that need no database.

The fake secrets are built by concatenation so that no key-shaped literal sits in the source (the
pre-push privacy gate scans it).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from hlmemo.core.errors import ToolError
from hlmemo.core.secret_guard import (
    STRONG_SECRET_RULES,
    find_secret,
    redact_for_log,
    strong_secret_rule,
)
from hlmemo.core.write_models import CloseRequest, WriteRequest, parse_request
from hlmemo.importers.common import SECRET_PATTERNS

PEM_HEAD = "-----BEGIN RSA " + "PRIVATE KEY-----"
OPENSSH_HEAD = "-----BEGIN OPENSSH " + "PRIVATE KEY-----"
B64 = "MIIEowIBAAKCAQEAt3Jk9Zq2Lx8vPr4mN7sQ1wY5uH0cB6dFgHjKlMnOp"
#: real-shaped fakes (random-looking, full structure): every one must be refused
FAKE: dict[str, str] = {
    "private-key": PEM_HEAD + "\n" + B64 + "\n" + B64[::-1] + "\n-----END",
    "aws-access-key": "AKIA" + "ABCDEFGHIJKLMNOP",
    "github-token": "ghp_" + "FakeTokenForTests" * 2 + "xy",
    "slack-token": "xox" + "b-" + "1234567890" + "-" + "9876543210123" + "-" + "AbCdEfGhIjKlMnOpQrStUvWx",
    "google-api-key": "AIza" + "FAKEkeyNotRealForTests" + "0123456789abc",
    "hlm-token": "hlm_" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789QwErTyU",
    "sk-api-key": "sk-" + "proj-" + "Ab3Cd9Ef2Gh7Ij4Kl8Mn1Op6Qr5St0Uv",
}
#: more real-shaped variants that must still be refused (review 112: tightening keeps real tokens out)
MORE_REAL = (
    "xox" + "p-" + "23984754863-2348975623103-2348975623103-0c1f3a9b2d4e5f60718293a4b5c6d7e8",
    PEM_HEAD + "\\n" + B64 + "\\n-----END",  # a key pasted with escaped line breaks
    OPENSSH_HEAD + " " + "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2g",
    PEM_HEAD + "\nProc-Type: 4,ENCRYPTED\nDEK-Info: AES-128-CBC,0123456789ABCDEF\n\n" + B64,
    "AKIA" + "Z3B5QK7M2RTN8WXY",
)
#: documentation examples and placeholders: legitimate memory text, never refused (review 112)
DOC_EXAMPLES = (
    "xox" + "b-placeholder-token",  # literals split so push-protection scanners stay quiet
    "SLACK_BOT_TOKEN=xox" + "b-your-token-here",
    "docs/xox" + "b-placeholder-token.md",
    "xox" + "b-" + "0" * 10 + "-" + "0" * 13 + "-" + "x" * 24,
    "aws_access_key_id = AKIA" + "IOSFODNN7EXAMPLE",
    "AKIA" + "I44QH8DHBEXAMPLE",
    "AKIA" + "X" * 16,
    "the file starts with " + OPENSSH_HEAD + " and is followed by base64 lines",
    PEM_HEAD + "\nMIIEpAIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----",
    PEM_HEAD + "\n" + "x" * 64,
    "GITHUB_TOKEN=ghp_" + "x" * 36,
    "ghp_" + "X" * 36,
    "AIzaSy" + "X" * 33,
    "AIza...",
    "hlm_<token>",
    "hlm_" + "x" * 43,
    "sk-proj-...",
    "sk-proj-" + "x" * 32,
    "sk-or-v1-" + "0" * 64,
    "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
)
#: the canonical jwt.io sample (a documentation example, not a credential) and the review's fake one
JWT_EXAMPLES = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    + ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ"
    + ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
    "eyJ" + "a" * 12 + ".eyJ" + "b" * 12 + "." + "c" * 12,
)
S = FAKE["github-token"]


# --------------------------------------------------------------------------- PV-1 secret_guard
@pytest.mark.parametrize("rule", sorted(FAKE))
def test_every_strong_rule_matches_its_shape(rule: str) -> None:
    assert strong_secret_rule(f"the value is {FAKE[rule]} here") == rule


@pytest.mark.parametrize(
    "text",
    [
        "Use sk-learn for the baseline model.",
        "The key looks like sk-... and lives in .env (OPENROUTER_API_KEY).",
        "sk-" + "x" * 30,  # an unprefixed sk- string: outside the narrowed rule (D-219 false positives)
        "password: see the keychain entry hlm/prod",
        "postgresql://hlm:hlm@127.0.0.1:5432/hlm_test",  # dsn-with-password stays out of PV-1
        "AKIA is the prefix of AWS access keys.",
        "eyJ is how a base64 JSON object starts.",
    ],
)
def test_placeholders_and_prose_pass(text: str) -> None:
    assert strong_secret_rule(text) is None


def test_shared_rules_equal_the_importer_rules_and_sk_is_narrowed() -> None:
    narrowed = {"sk-api-key", "slack-token", "private-key"}  # write-path forms (reviews 111, 112)
    shared = set(STRONG_SECRET_RULES) - narrowed
    for rule in shared:
        assert STRONG_SECRET_RULES[rule].pattern == SECRET_PATTERNS[rule].pattern, rule
    assert set(STRONG_SECRET_RULES) == shared | narrowed
    for rule in narrowed:  # every write-path refusal is also an importer refusal (the sets only narrow)
        assert SECRET_PATTERNS[rule].search(FAKE[rule]), rule
    assert "jwt" in SECRET_PATTERNS and "jwt" not in STRONG_SECRET_RULES  # kept for imports only
    assert "assigned-secret" not in STRONG_SECRET_RULES and "dsn-with-password" not in STRONG_SECRET_RULES
    assert STRONG_SECRET_RULES["sk-api-key"].pattern == r"\bsk-(?:or-v1-|ant-|proj-)[A-Za-z0-9_-]{24,}"


# --------------------------------------------------------------------------- PV-3 blank
def _write(**item: Any) -> dict[str, Any]:
    it = {"kind": "fact", "title": "T", "body": "B", **item}
    return {"project": "p-one", "request_id": str(uuid.uuid4()), "client": "pytest/0", "items": [it]}


def _close(**kw: Any) -> dict[str, Any]:
    return {
        "project": "p-one",
        "request_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "client": "pytest/0",
        "notes": "Done.",
        **kw,
    }


def _refused(model: Any, raw: dict[str, Any]) -> ToolError:
    with pytest.raises(ToolError) as exc:
        parse_request(model, raw)
    return exc.value


BLANKS = [" ", "\n\t ", " ", "  \n"]


@pytest.mark.parametrize("blank", BLANKS)
@pytest.mark.parametrize("field", ["title", "body"])
def test_blank_item_text_is_refused(field: str, blank: str) -> None:
    err = _refused(WriteRequest, _write(**{field: blank}))
    assert err.code == "E_INVALID_ARG"
    assert err.details["reason"] == "blank" and err.details["index"] == 0
    assert "must not be blank" in err.message


@pytest.mark.parametrize("blank", BLANKS)
def test_blank_close_fields_are_refused(blank: str) -> None:
    cases = [
        _close(notes=blank),
        _close(decisions=["fine", blank]),
        _close(lessons=[{"title": blank, "body": "b"}]),
        _close(lessons=[{"title": "t", "body": blank}]),
        _close(card_update={"body": blank}),
    ]
    for raw in cases:
        err = _refused(CloseRequest, raw)
        assert err.code == "E_INVALID_ARG" and err.details["reason"] == "blank", raw


def test_text_with_content_passes() -> None:
    parse_request(WriteRequest, _write(title=" a title ", body=" body "))
    parse_request(
        CloseRequest,
        _close(decisions=["one"], lessons=[{"title": "t", "body": "b"}], card_update={"body": "card"}),
    )


# --------------------------------------------------------------------------- PV-5 lesson status
@pytest.mark.parametrize(
    "tags", [["active", "resolved"], ["active", "historical"], ["x@1", "active", "resolved"]]
)
@pytest.mark.parametrize("kind", ["lesson", "experience"])
def test_active_with_resolved_or_historical_is_refused(kind: str, tags: list[str]) -> None:
    err = _refused(WriteRequest, _write(kind=kind, tags=tags))
    assert err.code == "E_INVALID_ARG"
    assert err.details["reason"] == "lesson_status_conflict" and err.details["index"] == 0


@pytest.mark.parametrize(
    ("kind", "tags"),
    [
        ("lesson", ["resolved", "historical"]),  # the D-234 import form
        ("lesson", ["active"]),
        ("lesson", ["resolved"]),
        ("fact", ["active", "resolved"]),  # only lessons carry a status
        ("episode", ["active", "historical"]),
    ],
)
def test_valid_status_tags_pass(kind: str, tags: list[str]) -> None:
    parse_request(WriteRequest, _write(kind=kind, tags=tags))


def test_call_the_day_lessons_get_the_same_status_check() -> None:
    err = _refused(
        CloseRequest, _close(lessons=[{"title": "t", "body": "b", "tags": ["active", "resolved"]}])
    )
    assert err.code == "E_INVALID_ARG" and err.details["reason"] == "lesson_status_conflict"


def test_the_reason_points_at_the_offending_item() -> None:
    raw = _write()
    raw["items"].append({"kind": "lesson", "title": "L", "body": "B", "tags": ["active", "historical"]})
    err = _refused(WriteRequest, raw)
    assert err.details["index"] == 1 and err.details["reason"] == "lesson_status_conflict"


# --------------------------------------------------------------------------- PV-4 reserved project (MCP path)
@pytest.mark.parametrize("tool", ["memory_write", "memory_call_the_day", "memory_register_lesson"])
async def test_mcp_write_tools_refuse_the_librarian_project(tool: str) -> None:
    from hlmemo.server.tools import handlers, risk

    fn = getattr(handlers, tool, None) or getattr(risk, tool)
    with pytest.raises(ToolError) as exc:
        await fn(None, None, {"project": "hlm-librarian", "items": []})  # refused before any database use
    assert exc.value.code == "E_FORBIDDEN_PROJECT"
    assert exc.value.details == {"project": "hlm-librarian", "reason": "reserved_project"}


async def test_mcp_write_refuses_the_librarian_project_in_project_ids() -> None:
    from hlmemo.server.tools import handlers

    args = _write(project_ids=["p-one", "hlm-librarian"])
    with pytest.raises(ToolError) as exc:
        await handlers.memory_write(None, None, args)
    assert exc.value.code == "E_FORBIDDEN_PROJECT" and exc.value.details["reason"] == "reserved_project"


# --------------------------------------------------------------------------- review 111 (round 1)
@pytest.mark.parametrize("example", JWT_EXAMPLES)
def test_jwt_documentation_examples_are_accepted(example: str) -> None:
    text = f"JWT format example: {example}"
    assert strong_secret_rule(text) is None and find_secret({"items": [{"body": text}]}) is None
    parse_request(WriteRequest, _write(body=text))


@pytest.mark.parametrize(
    ("args", "path"),
    [
        ({"project": S}, "project"),
        ({"project": "p", "client": S}, "client"),
        ({"items": [{"title": "t", "project_ids": ["p", S]}]}, "items[0].project_ids[1]"),
        ({"items": [{"updates": [{"old_span": "a", "replacement": S}]}]}, "items[0].updates[0].replacement"),
        ({"items": [{"updates": [{"old_span": S}]}]}, "items[0].updates[0].old_span"),
        ({"items": [{}, {"source": {"path": S}}]}, "items[1].source.path"),
        ({"items": [{"describes": ["a.py", S]}]}, "items[0].describes[1]"),
        ({"items": [{"links": [{"rel": "relates_to", "target": S}]}]}, "items[0].links[0].target"),
        ({"notes": "n", "decisions": ["ok", S]}, "decisions[1]"),
        ({"card_update": {"body": S}}, "card_update.body"),
        ({"context": S}, "context"),
        ({"items": [{S: "x"}]}, "items[0].<key>"),
        ({"items": [{"body": [S]}]}, "items[0].body[0]"),
    ],
)
def test_find_secret_names_the_path_and_never_the_value(args: dict[str, Any], path: str) -> None:
    hit = find_secret(args)
    assert hit == (path, "github-token") and S not in hit[0]


def _write_refusal(args: dict[str, Any]) -> ToolError:
    import asyncio

    from hlmemo.core import write_service

    with pytest.raises(ToolError) as exc:  # refused before any database use (conn/ctx are None)
        asyncio.run(write_service.write(None, None, args))  # type: ignore[arg-type]
    return exc.value


def _no_trace(err: ToolError) -> None:
    import json

    assert S not in json.dumps(err.as_error()) and S not in err.message
    assert err.__cause__ is None and err.__context__ is None


def test_astra_extra_key_shaped_like_a_secret_is_refused_without_echo() -> None:
    args = _write()
    args["items"][0][S] = "x"
    err = _write_refusal(args)
    assert err.details == {
        "field": "items[0].<key>",
        "reason": "secret_pattern",
        "rule": "github-token",
        "index": 0,
    }
    _no_trace(err)


def test_astra_secret_in_a_wrongly_typed_field_never_reaches_the_validation_error() -> None:
    err = _write_refusal(_write(body=[S]))
    assert err.details["field"] == "items[0].body[0]" and err.details["reason"] == "secret_pattern"
    _no_trace(err)


def test_parse_request_drops_the_chained_validation_error_and_echoes_no_value() -> None:
    import json

    value = "plain-input-value-7f3e"
    err = _refused(WriteRequest, _write(body=[value]))
    assert value not in json.dumps(err.as_error()) and err.__cause__ is None and err.__context__ is None
    err = _refused(WriteRequest, _write(device_scope="device:0" + "7"))
    assert "device:07" not in json.dumps(err.as_error())


def test_sol_secret_shaped_project_is_refused_before_authorization() -> None:
    args = _write()
    args["project"] = S
    err = _write_refusal(args)
    assert err.code == "E_INVALID_ARG" and err.details["field"] == "project"
    _no_trace(err)


@pytest.mark.parametrize("tool", ["memory_write", "memory_call_the_day", "memory_register_lesson"])
async def test_mcp_tools_refuse_a_secret_shaped_project_without_echo(tool: str) -> None:
    from hlmemo.server.tools import handlers, risk

    fn = getattr(handlers, tool, None) or getattr(risk, tool)
    with pytest.raises(ToolError) as exc:
        await fn(None, None, {"project": S, "request_id": str(uuid.uuid4()), "mistake": "m", "fix": "f"})
    assert exc.value.details["reason"] == "secret_pattern" and exc.value.details["field"] == "project"
    _no_trace(exc.value)


@pytest.mark.parametrize("tags", [["Active", "resolved"], [" active ", "HISTORICAL"], ["ACTIVE", "Resolved"]])
def test_astra_status_tags_compare_case_insensitively(tags: list[str]) -> None:
    err = _refused(WriteRequest, _write(kind="lesson", tags=tags))
    assert err.details["reason"] == "lesson_status_conflict"


@pytest.mark.parametrize("tags", [["Resolved", "HISTORICAL"], ["resolved", "historical"], ["Active"]])
def test_concluded_or_active_alone_passes_in_any_case(tags: list[str]) -> None:
    parse_request(WriteRequest, _write(kind="lesson", tags=tags))


@pytest.mark.parametrize("field", ["title", "body"])
def test_sol_empty_item_text_carries_reason_blank(field: str) -> None:
    err = _refused(WriteRequest, _write(**{field: ""}))
    assert err.details["reason"] == "blank" and err.details["index"] == 0


@pytest.mark.parametrize("value", ["\u00a0", "", " \n "])
@pytest.mark.parametrize("part", ["mistake", "fix", "context"])
def test_sol_register_lesson_blank_parts_carry_reason_blank(part: str, value: str) -> None:
    from hlmemo.core.lesson_service import LessonRequest

    args = {"project": "p-one", "request_id": str(uuid.uuid4()), "mistake": "m", "fix": "f", part: value}
    err = _refused(LessonRequest, args)
    assert err.code == "E_INVALID_ARG" and err.details["reason"] == "blank"


def test_empty_close_notes_and_lesson_title_carry_reason_blank() -> None:
    for raw in (
        _close(notes=""),
        _close(lessons=[{"title": "", "body": "b"}]),
        _close(card_update={"body": ""}),
    ):
        assert _refused(CloseRequest, raw).details["reason"] == "blank", raw


# --------------------------------------------------------------------------- review 112 (round 2)
@pytest.mark.parametrize("text", DOC_EXAMPLES)
def test_documentation_examples_and_placeholders_pass(text: str) -> None:
    assert strong_secret_rule(text) is None
    assert find_secret({"items": [{"body": text, "source": {"path": text}}]}) is None


@pytest.mark.parametrize("text", MORE_REAL)
def test_real_shaped_variants_are_still_refused(text: str) -> None:
    assert strong_secret_rule(f"pasted: {text}") is not None


def test_importer_and_capture_rules_are_unchanged() -> None:
    from hlmemo.importers.common import SECRET_PATTERNS as IMPORTER

    assert IMPORTER["slack-token"].search("xox" + "b-placeholder-token")  # the import path stays strict
    assert IMPORTER["private-key"].search("starts with " + OPENSSH_HEAD)
    assert IMPORTER["jwt"].search(JWT_EXAMPLES[0])


def test_redact_for_log() -> None:
    assert redact_for_log("agent/" + S) == "<redacted:github-token>"
    assert redact_for_log("hlm-cli/0.1 (darwin)") == "hlm-cli/0.1 (darwin)" and redact_for_log(None) is None


def test_find_secret_stops_at_the_first_match() -> None:
    class Untouchable(dict):
        def items(self):  # noqa: ANN201
            raise AssertionError("entered after the match")

    assert find_secret({"a": S, "b": Untouchable(x="y")}) == ("a", "github-token")


@pytest.mark.parametrize(
    "text",
    [
        PEM_HEAD + " " * 60000 + "x",
        PEM_HEAD + " " + "a: " * 20000,
        PEM_HEAD + "\n" + ("a:" + " " * 50 + "\n") * 5000,
        "xoxb-" + "1" * 60000,
    ],
)
def test_no_slow_backtracking_on_long_inputs(text: str) -> None:
    import time

    t = time.perf_counter()
    strong_secret_rule(text)
    assert time.perf_counter() - t < 0.5


def test_a_huge_legal_request_is_walked_without_copying_it() -> None:
    import tracemalloc

    big = {"items": [0] * 1_000_000}
    tracemalloc.start()
    assert find_secret(big) is None
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 1_000_000  # bytes: no list of the leaves


@pytest.mark.parametrize("same", [True, False])
def test_the_request_is_scanned_once_when_raw_is_the_request(
    monkeypatch: pytest.MonkeyPatch, same: bool
) -> None:
    import asyncio

    from hlmemo.core import write_service

    calls: list[Any] = []
    real = write_service.find_secret
    monkeypatch.setattr(write_service, "find_secret", lambda a: calls.append(a) or real(a))
    args = {"project": "p-one", "request_id": str(uuid.uuid4()), "client": "c", "items": []}  # fails parsing
    raw = args if same else dict(args)
    with pytest.raises(ToolError):
        asyncio.run(write_service.write(None, None, args, raw=raw))  # type: ignore[arg-type]
    assert len(calls) == (1 if same else 2)
    calls.clear()
    with pytest.raises(ToolError):
        asyncio.run(write_service.write(None, None, args))  # type: ignore[arg-type]
    assert len(calls) == 1
