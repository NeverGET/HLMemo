"""F05: behind a reverse proxy in another container (uvicorn default forwarded_allow_ips=127.0.0.1) every client
shares ONE register bucket keyed by the proxy IP; and RateLimiter._hits never evicts keys."""
import uuid

from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from docs.bakeoff.r1.repro._app import app_client
from hlmemo.server.middleware import RateLimiter

CADDY = ("172.18.0.5", 44321)  # proxy container address as seen by uvicorn


def _body(n):
    return {"name": n, "fingerprint": f"fp-{uuid.uuid4()}", "os": "linux", "client": "pytest/0"}


async def test_f05_register_limit_is_per_client_behind_proxy(db_dsn):
    # ProxyHeadersMiddleware with uvicorn's default trusted_hosts ("127.0.0.1"), as uvicorn.run() applies it.
    async with app_client(db_dsn, register_rate_limit=5, client_addr=CADDY,
                          wrap=lambda a: ProxyHeadersMiddleware(a, trusted_hosts="127.0.0.1")) as client:
        for i in range(5):
            r = await client.post("/devices/register", json=_body(f"atk-{i}"), headers={"X-Forwarded-For": "203.0.113.66"})
            assert r.status_code == 201, r.text
        r = await client.post("/devices/register", json=_body("victim"), headers={"X-Forwarded-For": "198.51.100.7"})
        print("victim:", r.status_code, r.text[:160])
        assert r.status_code == 201, f"a different client is rate-limited by the attacker's bucket: {r.status_code} {r.text}"


def test_f05_rate_limiter_keys_are_evicted():
    rl = RateLimiter(5, window_s=0.0)
    for i in range(10_000):
        rl.allow(f"10.{i // 65536}.{(i // 256) % 256}.{i % 256}")
    rl.allow("final")
    print("keys retained:", len(rl._hits))
    assert len(rl._hits) < 1000, f"{len(rl._hits)} expired keys retained"
