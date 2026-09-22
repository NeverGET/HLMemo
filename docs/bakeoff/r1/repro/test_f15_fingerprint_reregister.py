"""F15: `hlm device register` always sends the deterministic device_fingerprint(); after the machine's device is
revoked (row retained, UNIQUE(fingerprint)) no re-registration is possible. Spec assumption 5: collisions fall back
to a random id."""
from hlmemo.cli.client_config import device_fingerprint
from tests.integration._mcp_fixtures import ADMIN_TOKEN, bearer, running_app


async def test_f15_reregister_after_revoke(db_dsn):
    fp = device_fingerprint()
    assert fp == device_fingerprint()  # deterministic per machine+user
    async with running_app(db_dsn) as client:
        body = {"name": "laptop-a", "fingerprint": fp, "os": "darwin", "client": "hlm/0"}
        r = await client.post("/devices/register", json=body)
        assert r.status_code == 201, r.text
        did = r.json()["device"]["id"]
        assert (await client.post(f"/admin/devices/{did}/approve", json={"class": "personal"},
                                  headers=bearer(ADMIN_TOKEN))).status_code == 200
        assert (await client.post(f"/admin/devices/{did}/revoke", json={},
                                  headers=bearer(ADMIN_TOKEN))).status_code == 200
        r = await client.post("/devices/register", json={**body, "name": "laptop-b", "fingerprint": device_fingerprint()})
        print("re-register:", r.status_code, r.text[:200])
        assert r.status_code == 201, f"re-registration from the same machine impossible: {r.status_code} {r.text}"
