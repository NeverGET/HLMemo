"""F15: real CLI onboarding can reclaim a revoked device's default hostname."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from hlmemo import config
from hlmemo.auth.tokens import hash_token
from hlmemo.cli import credentials
from hlmemo.cli.client_config import default_device_name, device_fingerprint
from hlmemo.cli.hlm import app as cli_app
from hlmemo.cli.http_client import HlmHttp
from hlmemo.server import devices
from tests.integration.test_g5_auth import ADMIN_TOKEN, approve, bearer, register, running_app

pytestmark = pytest.mark.integration


class _AppTransport(httpx.BaseTransport):
    """Forward synchronous CLI HTTP requests to the actual app on its running loop."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.loop = asyncio.get_running_loop()
        self.registrations: list[tuple[dict[str, Any], httpx.Response]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = asyncio.run_coroutine_threadsafe(
            self.client.request(
                request.method,
                str(request.url),
                headers=request.headers,
                content=request.content,
            ),
            self.loop,
        ).result(timeout=15)
        if request.url.path == "/devices/register":
            self.registrations.append((json.loads(request.content), response))
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )


async def _stored_device(connect, device_id: int) -> tuple[Any, ...]:
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT name, status, fingerprint, token_sha256, token_generation, revoked_at"
            " FROM devices WHERE device_id = %s",
            (device_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        return row


async def test_cli_default_name_and_fingerprint_reregister_after_revoke(
    db_dsn: str, connect, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Keep the real hostname and machine fingerprint; isolate only configuration and storage.
    for key in list(os.environ):
        if key.startswith("HLM_") and key not in {"HLM_TEST_DSN", "HLM_MODELS_DIR"}:
            monkeypatch.delenv(key)
    monkeypatch.setenv("HLM_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("HLM_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setattr(config, "_toml_candidates", lambda: [])
    monkeypatch.setattr(credentials, "_keyring", lambda: None)
    name, fingerprint = default_device_name(), device_fingerprint()

    async with running_app(db_dsn) as client:
        transport = _AppTransport(client)
        original_init = HlmHttp.__init__

        def with_app_transport(self, *args: Any, **kwargs: Any) -> None:
            original_init(self, *args, transport=transport, **kwargs)

        monkeypatch.setattr(HlmHttp, "__init__", with_app_transport)

        async def invoke(*args: str) -> dict[str, Any]:
            result = await asyncio.to_thread(
                CliRunner().invoke,
                cli_app,
                ["--server", "http://test/mcp", "--json", *args],
            )
            assert result.exit_code == 0, result.output
            return json.loads(result.output)

        first = await invoke("device", "register")
        old_id = first["device"]["id"]
        old_token = credentials.load_token("http://test/mcp", name)
        assert old_token is not None
        assert first["device"]["name"] == name
        assert first["device"]["status"] == "pending"
        approved = await invoke("--admin", "device", "approve", name, "--class", "personal")
        assert approved["device"]["id"] == old_id
        await invoke("--admin", "device", "revoke", name)
        old_row = await _stored_device(connect, old_id)
        assert old_row[1] == "revoked" and old_row[5] is not None

        second = await invoke("device", "register")
        new_id = second["device"]["id"]
        assert new_id != old_id
        assert second["device"]["name"] == name
        assert second["device"]["status"] == "pending"
        assert [response.status_code for _, response in transport.registrations] == [201, 201]
        assert [body["name"] for body, _ in transport.registrations] == [name, name]
        assert [body["fingerprint"] for body, _ in transport.registrations] == [fingerprint, fingerprint]
        new_token = credentials.load_token("http://test/mcp", name)
        assert new_token and new_token != old_token
        assert new_token == transport.registrations[-1][1].json()["token"]
        assert old_token not in credentials.credentials_path().read_text()
        assert (await client.get("/devices/whoami", headers=bearer(old_token))).status_code == 401
        new_health = await client.get("/health", headers=bearer(new_token))
        assert new_health.json()["device"]["id"] == new_id
        assert new_health.json()["device"]["status"] == "pending"
        archived = await _stored_device(connect, old_id)
        assert archived[0] != name
        assert archived[1:] == old_row[1:]

        # Name-based administration must now resolve the new registration, not its history.
        approved = await invoke("--admin", "device", "approve", name, "--class", "personal")
        assert approved["device"]["id"] == new_id
        assert approved["device"]["status"] == "trusted"
        assert (await invoke("device", "whoami"))["device"]["id"] == new_id


@pytest.mark.parametrize("status", ["pending", "trusted"])
@pytest.mark.parametrize("same_fingerprint", [False, True], ids=["different-fingerprint", "same-fingerprint"])
async def test_active_name_collision_still_rejected(
    db_dsn: str, connect, status: str, same_fingerprint: bool
) -> None:
    async with running_app(db_dsn) as client:
        device_id, _ = await register(client, "occupied", fingerprint="occupied-fingerprint")
        if status == "trusted":
            assert (await approve(client, device_id)).status_code == 200
        before = await _stored_device(connect, device_id)
        response = await client.post(
            "/devices/register",
            json={
                "name": "occupied",
                "fingerprint": "occupied-fingerprint" if same_fingerprint else "other-fingerprint",
                "client": "pytest/f15",
            },
        )
        assert response.status_code == 400, response.text
        assert response.json()["code"] == "E_INVALID_ARG"
        assert response.json()["details"]["constraint"] == "devices_user_id_name_key"
        assert await _stored_device(connect, device_id) == before


async def test_concurrent_reclaimed_name_has_one_winner(db_dsn: str, connect) -> None:
    async with running_app(db_dsn) as client:
        old_id, _ = await register(client, "concurrent", fingerprint="same-machine")
        assert (
            await client.post(f"/admin/devices/{old_id}/revoke", headers=bearer(ADMIN_TOKEN))
        ).status_code == 200
        body = {"name": "concurrent", "fingerprint": "same-machine", "client": "pytest/f15"}
        responses = await asyncio.wait_for(
            asyncio.gather(*(client.post("/devices/register", json=body) for _ in range(2))),
            timeout=15,
        )
        assert sorted(response.status_code for response in responses) == [201, 400]
        failed = next(response for response in responses if response.status_code == 400)
        assert failed.json()["code"] == "E_INVALID_ARG"
        winner = next(response.json()["device"] for response in responses if response.status_code == 201)
        assert winner["id"] != old_id and winner["name"] == "concurrent"
        assert winner["status"] == "pending"
        assert (await _stored_device(connect, old_id))[1] == "revoked"
        async with await connect() as conn:
            cur = await conn.execute("SELECT count(*) FROM devices WHERE name = 'concurrent'")
            assert (await cur.fetchone())[0] == 1


async def test_repeated_max_length_name_reclaims_preserve_revoked_devices(db_dsn: str, connect) -> None:
    name = "a" * 64
    async with running_app(db_dsn) as client:
        old_rows: dict[int, tuple[Any, ...]] = {}
        for _ in range(3):
            device_id, _ = await register(client, name, fingerprint="same-max-length-machine")
            assert (await approve(client, device_id)).status_code == 200
            assert (
                await client.post(f"/admin/devices/{device_id}/revoke", headers=bearer(ADMIN_TOKEN))
            ).status_code == 200
            old_rows[device_id] = await _stored_device(connect, device_id)
        new_id, _ = await register(client, name, fingerprint="same-max-length-machine")
        assert new_id not in old_rows
        names = {name}
        for device_id, before in old_rows.items():
            after = await _stored_device(connect, device_id)
            assert after[0] not in names
            assert re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", after[0])
            assert after[1:] == before[1:]
            names.add(after[0])


async def test_preoccupied_archive_name_is_preserved(db_dsn: str, connect) -> None:
    async with running_app(db_dsn) as client:
        old_id, _ = await register(client, "workstation", fingerprint="same-workstation")
        archive_name = f"workstation-revoked-{old_id}"
        occupied_id, _ = await register(client, archive_name)
        assert (await approve(client, occupied_id)).status_code == 200
        occupied_before = await _stored_device(connect, occupied_id)
        assert (
            await client.post(f"/admin/devices/{old_id}/revoke", headers=bearer(ADMIN_TOKEN))
        ).status_code == 200
        new_id, _ = await register(client, "workstation", fingerprint="same-workstation")
        assert new_id not in {old_id, occupied_id}
        assert await _stored_device(connect, occupied_id) == occupied_before
        archived = await _stored_device(connect, old_id)
        assert archived[0] not in {"workstation", archive_name}
        assert archived[1] == "revoked"


async def test_archive_candidate_equal_to_original_name_is_skipped(db_dsn: str, connect) -> None:
    async with running_app(db_dsn) as client:
        old_id, _ = await register(client, "rename-me", fingerprint="same-suffix-machine")
        suffix = f"-revoked-{old_id}"
        name = "a" * (64 - len(suffix)) + suffix
        async with await connect() as conn:
            await conn.execute("UPDATE devices SET name = %s WHERE device_id = %s", (name, old_id))
            await conn.commit()
        assert (
            await client.post(f"/admin/devices/{old_id}/revoke", headers=bearer(ADMIN_TOKEN))
        ).status_code == 200
        new_id, _ = await register(client, name, fingerprint="same-suffix-machine")
        assert new_id != old_id
        assert (await _stored_device(connect, new_id))[0] == name
        archived = await _stored_device(connect, old_id)
        assert archived[0] != name and archived[1] == "revoked"
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", archived[0])


@pytest.mark.parametrize("same_fingerprint", [False, True], ids=["first-insert", "fingerprint-fallback"])
async def test_failed_registration_rolls_back_revoked_name_rename(
    db_dsn: str, connect, monkeypatch: pytest.MonkeyPatch, same_fingerprint: bool
) -> None:
    async with running_app(db_dsn) as client:
        old_id, _ = await register(client, "rollback", fingerprint="rollback-machine")
        assert (
            await client.post(f"/admin/devices/{old_id}/revoke", headers=bearer(ADMIN_TOKEN))
        ).status_code == 200
        before = await _stored_device(connect, old_id)
        body = {
            "name": "rollback",
            "fingerprint": "rollback-machine" if same_fingerprint else "other-machine",
            "client": "pytest/f15",
        }
        # Force INSERT failure after name release, optionally after fingerprint fallback.
        original_insert = devices.q.insert_device
        insert_calls = 0

        async def collide_token(conn, **kwargs: Any):
            nonlocal insert_calls
            insert_calls += 1
            if not same_fingerprint or insert_calls == 2:
                kwargs["token_hash"] = hash_token(ADMIN_TOKEN)
            return await original_insert(conn, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(devices.q, "insert_device", collide_token)
            response = await client.post("/devices/register", json=body)
        assert insert_calls == (2 if same_fingerprint else 1)
        assert response.status_code == 400, response.text
        assert response.json()["code"] == "E_INVALID_ARG"
        assert response.json()["details"]["constraint"] == "devices_token_sha256_key"
        assert await _stored_device(connect, old_id) == before
        async with await connect() as conn:
            cur = await conn.execute("SELECT count(*) FROM events WHERE kind = 'device_registered'")
            assert (await cur.fetchone())[0] == 1
        response = await client.post("/devices/register", json=body)
        assert response.status_code == 201, response.text
        assert response.json()["device"]["id"] != old_id
