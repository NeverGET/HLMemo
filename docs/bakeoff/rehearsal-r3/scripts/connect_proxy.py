"""Minimal HTTP CONNECT proxy for the rehearsal VM's Docker daemon (environment workaround only).

Why: on 2026-09-25 the owner's network lost its IPv4 path to Docker Hub (registry-1.docker.io timed
out over IPv4 from the Mac; IPv6 worked). The lima VM is IPv4-only, and the deploy runner always
runs `compose pull db caddy` / `build --pull`, so no deploy could proceed. This proxy runs on the Mac,
where the OS resolves and connects over IPv6. The VM's dockerd uses it only for registry traffic
(HTTPS_PROXY in a systemd drop-in, removed afterwards). It is TLS pass-through (CONNECT) and never
sees plaintext. It only allows the registry hosts; everything else is refused.

    python3 connect_proxy.py [PORT] [BIND]   # default 127.0.0.1:3128 (lima maps host.lima.internal = 192.168.5.2 to it)
"""
import asyncio
import sys

ALLOW = ("registry-1.docker.io", "auth.docker.io", "production.cloudflare.docker.com",
         "index.docker.io", "ghcr.io", "pkg-containers.githubusercontent.com")


async def pipe(r, w):
    try:
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
    except Exception:  # noqa: BLE001
        pass
    finally:
        w.close()


async def handle(cr, cw):
    try:
        line = (await cr.readline()).decode("latin-1").split()
        while (await cr.readline()) not in (b"\r\n", b"\n", b""):
            pass
        if len(line) < 2 or line[0] != "CONNECT":
            cw.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n"); await cw.drain(); cw.close(); return
        host, _, port = line[1].rpartition(":")
        if host not in ALLOW or port != "443":
            cw.write(b"HTTP/1.1 403 Forbidden\r\n\r\n"); await cw.drain(); cw.close(); return
        ur, uw = await asyncio.wait_for(asyncio.open_connection(host, 443), 20)
        print(f"CONNECT {host}:443", flush=True)
        cw.write(b"HTTP/1.1 200 Connection Established\r\n\r\n"); await cw.drain()
        await asyncio.gather(pipe(cr, uw), pipe(ur, cw))
    except Exception as exc:  # noqa: BLE001
        print(f"error {exc!r}", flush=True)
        cw.close()


async def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 3128
    bind = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    srv = await asyncio.start_server(handle, bind, port)
    print(f"listening on {bind}:{port}", flush=True)
    async with srv:
        await srv.serve_forever()

asyncio.run(main())
