"""F06: E_INVALID_ARG echoes pydantic `input` (whole items) unbounded.
F12: call_the_day notes(64000)+decisions passes validation, internal Item(body<=64000) fails -> retryable E_UNAVAILABLE."""
import json
import uuid

from tests.integration._mcp_fixtures import call_tool_raw, running_app, write_args, writer_on


async def test_f06_invalid_arg_envelope_is_bounded(db_dsn):
    async with running_app(db_dsn) as client:
        tok = await writer_on(client, "f06")
        items = [{"kind": "fact", "body": "z" * 64000} for _ in range(50)]  # no title
        res = await call_tool_raw(client, tok, "memory.write", write_args("f06", items, token_budget=256))
        text = res["content"][0]["text"]
        env = json.loads(text)
        print("code", env["code"], "bytes", len(text.encode()), "errors", len(env["details"].get("errors", [])))
        assert env["code"] == "E_INVALID_ARG"
        assert len(text.encode()) < 64_000, f"error envelope is {len(text.encode())} bytes for token_budget=256"


async def test_f12_close_aggregate_body_overflow(db_dsn):
    async with running_app(db_dsn) as client:
        tok = await writer_on(client, "f12")
        args = {"project": "f12", "request_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()),
                "client": "x", "notes": "x" * 64000, "decisions": ["x"]}
        res = await call_tool_raw(client, tok, "memory.call_the_day", args)
        env = json.loads(res["content"][0]["text"])
        print(env)
        if res.get("isError"):
            assert env["retryable"] is False, f"close rejected as retryable: {env}"
