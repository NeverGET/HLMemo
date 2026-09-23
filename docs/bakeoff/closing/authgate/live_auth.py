"""Live checks for the pre-body auth gate (judge-auth stack, through Caddy TLS).

Modes:
  setup      register+approve a writer device (grant write on project) and a revoked device;
             writes tokens to --state (0600).
  attack     N attackers per bearer kind (none / invalid / revoked) each declare a 64 MiB body,
             burst as much as the socket accepts, then trickle 1 byte/s; concurrently one trusted
             38.4 MB memory.write. Reports per-attacker status, time-to-response, bytes sent
             before the response.
  write      trusted write: --size-mode max (38.4 MB, as fast as possible) or
             --size-mode slow (5 MB at 64 KiB/s with a 25 s pause).
  load       1 query, then 200 queries from 3 callers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import ssl
import statistics
import sys
import time
import uuid

import httpx

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
PROJECT = "authgate"


def now() -> str:
    return time.strftime("%H:%M:%S")


def load_state(path: str) -> dict:
    with open(path) as fh:
        return json.load(fh)


def rpc_wire(name: str, args: dict) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}},
        ensure_ascii=True,
    ).encode()


def parse_rpc(r: httpx.Response) -> dict:
    if r.headers.get("content-type", "").startswith("text/event-stream"):
        last = {}
        for line in r.text.splitlines():
            if line.startswith("data:") and line[5:].strip():
                last = json.loads(line[5:])
        return last
    return r.json()


def setup(base: str, admin: str, reg: str, state: str) -> int:
    with httpx.Client(base_url=base, verify=False, timeout=30) as c:
        A = {"Authorization": f"Bearer {admin}"}
        R = {"X-HLM-Registration-Secret": reg}
        out = {}
        for role in ("writer", "revoked"):
            body = {
                "name": f"ag-{role}-{uuid.uuid4().hex[:6]}",
                "class": "ci",
                "fingerprint": f"ag-{uuid.uuid4()}",
                "os": sys.platform,
                "client": "authgate/1",
            }
            r = c.post("/devices/register", json=body, headers=R)
            assert r.status_code == 201, r.text
            tok, did = r.json()["token"], r.json()["device"]["id"]
            r = c.post(f"/admin/devices/{did}/approve", json={"class": "ci"}, headers=A)
            assert r.status_code == 200, r.text
            out[role] = {"token": tok, "id": did}
        r = c.post("/admin/projects", json={"slug": PROJECT, "name": PROJECT}, headers=A)
        print(now(), "project create", r.status_code)
        r = c.post(f"/admin/projects/{PROJECT}/grants", json={"device": out["writer"]["id"], "role": "write"}, headers=A)
        assert r.status_code == 200, r.text
        # revoked device: trusted first, then revoked
        r = c.get("/devices/whoami", headers={"Authorization": f"Bearer {out['revoked']['token']}"})
        print(now(), "revoked-device whoami before revoke", r.status_code)
        r = c.post(f"/admin/devices/{out['revoked']['id']}/revoke", headers=A)
        print(now(), "revoke", r.status_code, r.text[:120])
        assert r.status_code == 200
        r = c.get("/devices/whoami", headers={"Authorization": f"Bearer {out['revoked']['token']}"})
        print(now(), "revoked-device whoami after revoke", r.status_code, r.text[:80])
        fd = os.open(state, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(out, fh)
    print(now(), "SETUP PASS writer id", out["writer"]["id"], "revoked id", out["revoked"]["id"])
    return 0


async def raw_upload(host: str, port: int, bearer: str | None, declared: int, burst: int, trickle_s: float, label: str):
    """Declare `declared` bytes, send `burst` bytes as fast as possible, then 1 B/s for trickle_s.
    Returns (status_line, t_response, bytes_sent_at_response, total_sent)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    reader, writer = await asyncio.open_connection(host, port, ssl=ctx, server_hostname="localhost")
    hdr = (
        f"POST /mcp HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
        f"Accept: application/json, text/event-stream\r\nContent-Length: {declared}\r\n"
        + (f"Authorization: Bearer {bearer}\r\n" if bearer else "")
        + "\r\n"
    ).encode()
    t0 = time.monotonic()
    writer.write(hdr)
    sent = 0
    resp: dict = {}

    async def read_resp():
        line = await reader.readline()
        resp["status"] = line.decode(errors="replace").strip()
        resp["t"] = time.monotonic() - t0
        resp["sent_at"] = sent
        # read headers + a bit of body
        data = b""
        try:
            data = await asyncio.wait_for(reader.read(2048), 2)
        except Exception:
            pass
        resp["body"] = data.decode(errors="replace")[-200:].replace("\r\n", " | ")

    rt = asyncio.create_task(read_resp())
    chunk = b"x" * 65536
    try:
        while sent < burst and not rt.done():
            n = min(len(chunk), burst - sent)
            writer.write(chunk[:n])
            try:
                await asyncio.wait_for(writer.drain(), 5)
            except TimeoutError:
                # peer stopped reading (not consuming the body); keep waiting for a response
                break
            sent += n
        end = time.monotonic() + trickle_s
        while not rt.done() and time.monotonic() < end:
            writer.write(b"x")
            try:
                await asyncio.wait_for(writer.drain(), 1)
                sent += 1
            except TimeoutError:
                pass
            await asyncio.sleep(1)
    except (ConnectionError, OSError) as exc:
        resp.setdefault("err", type(exc).__name__)
    try:
        await asyncio.wait_for(rt, 10)
    except Exception as exc:  # noqa: BLE001
        resp.setdefault("err", type(exc).__name__)
    writer.close()
    return label, resp, sent


async def trusted_write(base: str, token: str, mode: str) -> tuple[bool, str]:
    if mode == "max":
        items = [{"kind": "fact", "title": f"Max {i} {uuid.uuid4().hex[:6]}", "body": "😀" * 64000} for i in range(50)]
        rate, pause = None, 0.0
    else:
        # ~5 MB: 7 items of 64000 emoji (~768 KB each) -> 5.38 MB
        items = [{"kind": "fact", "title": f"Slow {i} {uuid.uuid4().hex[:6]}", "body": "😀" * 58000} for i in range(7)]
        rate, pause = 65536, 25.0
    args = {"project": PROJECT, "request_id": str(uuid.uuid4()), "client": "authgate/1", "items": items, "token_budget": 32000}
    wire = rpc_wire("memory.write", args)
    n = len(wire)

    async def gen():
        if rate is None:
            step = 1 << 20
            for i in range(0, n, step):
                yield wire[i : i + step]
            return
        step = 16384
        paused = False
        for i in range(0, n, step):
            if not paused and i >= n // 2:
                paused = True
                print(now(), f"slow write: pausing {pause}s at {i} bytes", flush=True)
                await asyncio.sleep(pause)
            yield wire[i : i + step]
            await asyncio.sleep(step / rate)

    print(now(), f"write start mode={mode} wire={n} bytes", flush=True)
    t0 = time.monotonic()
    async with httpx.AsyncClient(base_url=base, verify=False, timeout=httpx.Timeout(600)) as c:
        headers = {**MCP_HEADERS, "Authorization": f"Bearer {token}", "Content-Length": str(n)}
        r = await c.post("/mcp", content=gen(), headers=headers)
    dt = time.monotonic() - t0
    ok = False
    detail = f"status={r.status_code}"
    if r.status_code == 200:
        res = parse_rpc(r).get("result") or {}
        vers = json.loads(res["content"][0]["text"]).get("versions", []) if res.get("content") else []
        ok = not res.get("isError") and len(vers) == len(items)
        detail += f" isError={res.get('isError')} versions={len(vers)}/{len(items)}"
    else:
        detail += " " + r.text[:200]
    line = f"WRITE mode={mode} wire={n} {detail} {dt:.1f}s -> {'PASS' if ok else 'FAIL'}"
    print(now(), line, flush=True)
    return ok, line


async def attack(base: str, host: str, port: int, st: dict, per_kind: int, trickle_s: float) -> int:
    kinds = {"none": None, "invalid": "hlm_" + "x" * 43, "revoked": st["revoked"]["token"]}
    declared = 64 * 1024 * 1024
    burst = declared - 1024
    tasks = []
    for k, b in kinds.items():
        for i in range(per_kind):
            tasks.append(asyncio.create_task(raw_upload(host, port, b, declared, burst, trickle_s, f"{k}-{i}")))
    await asyncio.sleep(1.0)
    wtask = asyncio.create_task(trusted_write(base, st["writer"]["token"], "max"))
    res = await asyncio.gather(*tasks)
    wok, _ = await wtask
    bad = 0
    for label, resp, sent in res:
        kind = label.split("-")[0]
        want = "401" if kind in ("none", "invalid", "revoked") else "?"
        st_line = resp.get("status", "")
        ok = f" {want} " in st_line + " " and resp.get("t", 99) < 5
        bad += not ok
        print(
            f"ATTACK {label:10s} status='{st_line}' t_resp={resp.get('t', -1):.2f}s "
            f"body_sent_before_resp={resp.get('sent_at', -1)} total_sent={sent} err={resp.get('err')} "
            f"body='{resp.get('body', '')[-110:]}' -> {'PASS' if ok else 'FAIL'}"
        )
    sent_at = [r[1].get("sent_at", 0) for r in res]
    print(f"ATTACK summary n={len(res)} fails={bad} max_sent_before_resp={max(sent_at)} "
          f"median_sent_before_resp={statistics.median(sent_at):.0f} concurrent_write={'PASS' if wok else 'FAIL'}")
    return 0 if bad == 0 and wok else 1


async def sustained(base: str, host: str, port: int, st: dict, per_kind: int) -> int:
    """Attackers reconnect in a loop (64 MiB burst+trickle each) for the whole trusted write."""
    kinds = {"none": None, "invalid": "hlm_" + "x" * 43, "revoked": st["revoked"]["token"]}
    declared = 64 * 1024 * 1024
    done = asyncio.Event()
    stats: dict = {}
    worst = {"t": 0.0, "sent": 0}

    async def loop(kind, b, i):
        while not done.is_set():
            _, resp, sent = await raw_upload(host, port, b, declared, declared - 1024, 20, f"{kind}-{i}")
            key = (kind, resp.get("status", "") or f"err:{resp.get('err')}")
            stats[key] = stats.get(key, 0) + 1
            worst["t"] = max(worst["t"], resp.get("t", 99))
            worst["sent"] = max(worst["sent"], resp.get("sent_at", 0))

    tasks = [asyncio.create_task(loop(k, b, i)) for k, b in kinds.items() for i in range(per_kind)]
    await asyncio.sleep(2.0)
    wok, _ = await trusted_write(base, st["writer"]["token"], "max")
    done.set()
    await asyncio.gather(*tasks)
    bad = 0
    for (kind, status), n in sorted(stats.items()):
        ok = " 401 " in status + " "
        bad += 0 if ok else n
        print(f"SUSTAINED {kind:8s} {status!r} x{n} -> {'PASS' if ok else 'FAIL'}")
    print(f"SUSTAINED summary attempts={sum(stats.values())} non401={bad} worst_t_resp={worst['t']:.2f}s "
          f"worst_sent_before_resp={worst['sent']} concurrent_write={'PASS' if wok else 'FAIL'}")
    return 0 if bad == 0 and wok else 1


async def load(base: str, token: str) -> int:
    async with httpx.AsyncClient(base_url=base, verify=False, timeout=60) as c:
        H = {**MCP_HEADERS, "Authorization": f"Bearer {token}"}

        async def q(i: int):
            t = time.monotonic()
            r = await c.post("/mcp", content=rpc_wire("memory.query", {"project": PROJECT, "query": f"emoji fact number {i}", "token_budget": 2000}), headers=H)
            ok = r.status_code == 200 and not (parse_rpc(r).get("result") or {}).get("isError")
            return ok, (time.monotonic() - t) * 1000, r.status_code

        ok, ms, sc = await q(0)
        print(now(), f"Q1 status={sc} ok={ok} {ms:.0f} ms", flush=True)
        results = []
        idx = iter(range(1, 201))

        async def caller():
            for i in idx:
                results.append(await q(i))

        t0 = time.monotonic()
        await asyncio.gather(*(caller() for _ in range(3)))
        wall = time.monotonic() - t0
        lat = sorted(x[1] for x in results)
        fails = sum(1 for x in results if not x[0])
        codes = {}
        for x in results:
            codes[x[2]] = codes.get(x[2], 0) + 1
        print(now(), f"200 queries/3 callers: n={len(results)} fails={fails} codes={codes} p50={lat[len(lat)//2]:.0f} ms "
              f"p95={lat[int(len(lat)*0.95)]:.0f} ms max={lat[-1]:.0f} ms wall={wall:.1f}s")
        print("LOAD", "PASS" if fails == 0 and ok else "FAIL")
        return 0 if fails == 0 and ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("setup", "attack", "sustained", "write", "load"))
    ap.add_argument("--port", type=int, default=28443)
    ap.add_argument("--state", required=True)
    ap.add_argument("--per-kind", type=int, default=5)
    ap.add_argument("--trickle", type=float, default=20)
    ap.add_argument("--size-mode", choices=("max", "slow"), default="max")
    a = ap.parse_args()
    base = f"https://localhost:{a.port}"
    if a.mode == "setup":
        return setup(base, os.environ["HLM_ADMIN_TOKEN"], os.environ["HLM_REGISTRATION_SECRET"], a.state)
    st = load_state(a.state)
    if a.mode == "attack":
        return asyncio.run(attack(base, "127.0.0.1", a.port, st, a.per_kind, a.trickle))
    if a.mode == "sustained":
        return asyncio.run(sustained(base, "127.0.0.1", a.port, st, a.per_kind))
    if a.mode == "write":
        ok, _ = asyncio.run(trusted_write(base, st["writer"]["token"], a.size_mode))
        return 0 if ok else 1
    return asyncio.run(load(base, st["writer"]["token"]))


if __name__ == "__main__":
    sys.exit(main())
