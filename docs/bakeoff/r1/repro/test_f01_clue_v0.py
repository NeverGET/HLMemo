"""F01: clue 'v0' passes the wire pattern but decode_clue raises InvalidClue(ValueError) -> E_UNAVAILABLE retryable."""
import json

from tests.integration._mcp_fixtures import call_tool_raw, running_app, writer_on


async def test_f01_v0_clue_is_invalid_arg(db_dsn):
    async with running_app(db_dsn) as client:
        tok = await writer_on(client, "f01")
        for clue in ("v0", "v01"):
            res = await call_tool_raw(
                client, tok, "memory.drilldown", {"project": "f01", "clue_ids": [clue], "token_budget": 1000}
            )
            env = json.loads(res["content"][0]["text"])
            print(clue, env)
            assert res["isError"] is True
            assert env["code"] == "E_INVALID_ARG", env
            assert env["retryable"] is False, env
