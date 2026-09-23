#!/usr/bin/env python3
"""Edge checks.

`check_edge.py --routes --base URL [--mint-ops] [--cafile F | --insecure]` (W0a, D-061): verify the
public route table with anonymous, trusted, junk and admin-token-shaped bearers. Standard library
only, so it also runs inside the api container (`python - --routes ... < check_edge.py`). The
trusted bearer comes from $HLM_ROUTES_TOKEN, or with --mint-ops from `python -m hlmemo.ops device
mint` (inside the api container). The check ends by revoking that device through the public
self-revoke route, which is itself one of the table's rows. Tokens are never printed.

Without --routes: the local bake TLS drill (Compose JSON on stdin): 5 MB body through Caddy and
the actual TLS client IP in the API log.
"""

import http.client
import json
import os
import secrets
import ssl
import subprocess
import sys
import urllib.parse
import uuid

ROUTES_TOKEN_ENV = "HLM_ROUTES_TOKEN"
# Closed in production (404 before any body byte), for every bearer kind.
CLOSED = [
    ("POST", "/devices/register"),
    ("POST", "/devices/approve"),
    ("POST", "/devices/grant"),
    ("DELETE", "/devices/grant"),
    ("GET", "/devices/list"),
    ("POST", "/admin/devices/2/approve"),
    ("POST", "/admin/devices/2/revoke"),
    ("POST", "/admin/projects"),
    ("GET", "/admin/projects"),
    ("POST", "/admin/projects/proj/grants"),
    ("DELETE", "/admin/projects/proj/grants"),
]
MCP_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "check-edge-routes", "version": "1"},
    },
}


class RouteClient:
    def __init__(self, base, context=None, timeout=20):
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme not in ("http", "https") or parsed.path not in ("", "/") or parsed.username:
            raise ValueError("--base must be an http(s) origin without path or credentials")
        self.parsed = parsed
        self.context = context
        self.timeout = timeout

    def call(self, method, path, token=None, body=None):
        host, port = self.parsed.hostname, self.parsed.port
        if self.parsed.scheme == "https":
            conn = http.client.HTTPSConnection(host, port or 443, context=self.context, timeout=self.timeout)
        else:
            conn = http.client.HTTPConnection(host, port or 80, timeout=self.timeout)
        headers = {"Accept": "application/json, text/event-stream", "X-HLM-Client": "check-edge/1"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            conn.request(method, path, body=data, headers=headers)
            response = conn.getresponse()
            payload = response.read()
            return response.status, payload
        finally:
            conn.close()


def _code(payload):
    try:
        return json.loads(payload).get("code")
    except (ValueError, AttributeError):
        return None


def check_routes(client, token):
    """Returns the list of failures (empty = PASS). Revokes the trusted device at the end."""
    failures = []
    junk = "hlm_" + secrets.token_urlsafe(32)  # well-formed, unknown
    admin_shaped = secrets.token_hex(32)  # the old HLM_ADMIN_TOKEN format

    def expect(label, method, path, bearer, want, body=None, code=None):
        status, payload = client.call(method, path, bearer, body)
        ok = status == want and (code is None or _code(payload) == code)
        line = f"{'PASS' if ok else 'FAIL'} routes {label:<12} {method:<6} {path:<30} -> {status}"
        print(line + ("" if ok else f" (want {want}{' ' + code if code else ''})"), flush=True)
        if not ok:
            failures.append(line)
        return status, payload

    for label, bearer in (("anonymous", None), ("trusted", token)):
        expect(label, "GET", "/health", bearer, 200)
        expect(label, "GET", "/ready", bearer, 200)
    for label, bearer in (
        ("anonymous", None),
        ("trusted", token),
        ("junk", junk),
        ("admin-shaped", admin_shaped),
    ):
        for method, path in CLOSED:
            # Through Caddy the @rest matcher answers first (plain 404); on the API listener the
            # route filter answers E_NOT_FOUND. Either way: 404 and no body is ever read.
            expect(label, method, path, bearer, 404, body={} if method != "GET" else None)
    expect("anonymous", "GET", "/devices/whoami", None, 401, code="E_AUTH")
    expect("junk", "GET", "/devices/whoami", junk, 401, code="E_AUTH")
    expect("admin-shaped", "GET", "/devices/whoami", admin_shaped, 401, code="E_AUTH")
    expect("anonymous", "POST", "/mcp", None, 401, body=MCP_INIT)
    expect("junk", "POST", "/mcp", junk, 401, body=MCP_INIT)
    expect("anonymous", "POST", "/devices/revoke", None, 401, body={"id": 2})
    expect("junk", "POST", "/devices/revoke", junk, 401, body={"id": 2})
    status, payload = expect("trusted", "GET", "/devices/whoami", token, 200)
    device_id = None
    if status == 200:
        device_id = int(json.loads(payload)["device"]["id"])
    expect("trusted", "POST", "/mcp", token, 200, body=MCP_INIT)
    # Self-only revoke: another id (existing device 1 or an unknown id) answers 404 identically.
    expect("trusted", "POST", "/devices/revoke", token, 404, body={"id": 1}, code="E_NOT_FOUND")
    expect("trusted", "POST", "/devices/revoke", token, 404, body={"id": 2_000_000_000}, code="E_NOT_FOUND")
    if device_id is not None:
        expect("trusted", "POST", "/devices/revoke", token, 200, body={"id": device_id})
        expect("revoked", "GET", "/devices/whoami", token, 401, code="E_AUTH")
    else:
        failures.append("trusted whoami failed; self-revoke not exercised")
    return failures


def mint_ops():
    name = "routes-check-" + secrets.token_hex(4)
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "hlmemo.ops",
            "device",
            "mint",
            "--name",
            name,
            "--class",
            "ci",
            "--expires",
            "10m",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def routes_main(argv):
    import argparse

    ap = argparse.ArgumentParser(prog="check_edge.py --routes")
    ap.add_argument("--routes", action="store_true", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument(
        "--mint-ops", action="store_true", help="mint the trusted device with python -m hlmemo.ops"
    )
    ap.add_argument("--cafile")
    ap.add_argument("--insecure", action="store_true", help="rehearsal only: skip TLS verification")
    args = ap.parse_args(argv)
    context = ssl.create_default_context(cafile=args.cafile)
    if args.insecure:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    token = mint_ops() if args.mint_ops else os.environ.get(ROUTES_TOKEN_ENV, "").strip()
    if not token:
        print(f"FAIL routes: no trusted bearer (set {ROUTES_TOKEN_ENV} or pass --mint-ops)")
        return 1
    try:
        failures = check_routes(RouteClient(args.base, context), token)
    except (OSError, http.client.HTTPException, ValueError) as exc:
        print(f"FAIL routes: {type(exc).__name__}: {exc}")
        return 1
    print(f"RESULT routes {'FAIL' if failures else 'PASS'} base={args.base} failures={len(failures)}")
    return 1 if failures else 0


def edge_drill():
    from probe import Probe

    config = json.load(sys.stdin)
    project = config["name"]
    probe = Probe(config)
    origin = urllib.parse.urlsplit(probe.url)
    if not project.startswith("bake-") or origin.hostname not in {"localhost", "127.0.0.1", "::1"}:
        sys.exit("Edge drill requires a disposable bake-* project and loopback TLS")

    def container(service):
        ids = subprocess.check_output(
            [
                "docker",
                "ps",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--filter",
                f"label=com.docker.compose.service={service}",
            ],
            text=True,
        ).split()
        if len(ids) != 1:
            sys.exit(f"Expected exactly one running {service}")
        return json.loads(subprocess.check_output(["docker", "inspect", ids[0]]))[0]

    api = container("api")
    body_marker = "edge-body-" + uuid.uuid4().hex
    state = probe.bootstrap()  # operator-minted (hlmemo.ops) trusted device; no admin token (D-061)
    try:
        # Authenticated initialize padded with JSON whitespace: the proxy must forward all
        # five million bytes. Verify the API handled this exact request via its access log.
        body = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": probe.protocol,
                        "capabilities": {},
                        "clientInfo": {"name": "edge-drill", "version": "1"},
                    },
                }
            )
            .encode()
            .ljust(5_000_000, b" ")
        )
        connection = http.client.HTTPSConnection(
            origin.hostname, origin.port or 443, context=probe.context, timeout=90
        )
        connection.request(
            "POST",
            "/mcp?" + body_marker,
            body,
            {
                "Authorization": f"Bearer {state['token']}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        response = connection.getresponse()
        status = response.status
        content = response.read()
        connection.close()
    finally:
        probe.revoke(state)
    body_logs = subprocess.check_output(["docker", "logs", api["Id"]], stderr=subprocess.STDOUT, text=True)
    if not any(body_marker in line for line in body_logs.splitlines()):
        sys.exit("FAIL edge 5 MB: API did not log this request")
    if status == 200:
        print("PASS edge 5 MB: Caddy forwarded body; application returned 200")
    elif status == 413 and content == b"Request body too large":
        # Old main's SDK has its own smaller bound. Only accept this diagnostic when
        # the outer app middleware's cap permits the complete body before SDK dispatch.
        cap = int(
            subprocess.check_output(
                [
                    "docker",
                    "exec",
                    api["Id"],
                    "python",
                    "-c",
                    "from hlmemo.config import get_settings; print(get_settings().request_max_body_bytes)",
                ]
            )
        )
        if cap < len(body):
            sys.exit("FAIL edge 5 MB: application cap prevents complete-body verification")
        print("PASS edge 5 MB: Caddy forwarded body; legacy SDK returned 413 (API cap permits 5 MB)")
    else:
        sys.exit(f"FAIL edge 5 MB: unexpected response {status}; check edge/app body caps")

    worker, caddy, api = (container(service) for service in ("worker", "caddy", "api"))
    # Worker is a real TLS client on outbound, outside the trusted frontend. This
    # avoids Docker Desktop's host-NAT ambiguity and needs no extra published ports.
    network = next(
        n for n in worker["NetworkSettings"]["Networks"] if n in caddy["NetworkSettings"]["Networks"]
    )
    expected = worker["NetworkSettings"]["Networks"][network]["IPAddress"]
    marker = "edge-real-ip-" + uuid.uuid4().hex
    client = """import http.client, socket, ssl, sys
c = http.client.HTTPSConnection("localhost", 443)
c.sock = ssl._create_unverified_context().wrap_socket(
    socket.create_connection(("caddy", 443), timeout=15), server_hostname="localhost")
c.request("GET", "/ready?" + sys.argv[1],
          headers={"Host": "localhost", "X-Forwarded-For": "198.51.100.77"})
r = c.getresponse()
assert r.status == 200, r.status
r.read()
c.close()
"""
    subprocess.run(["docker", "exec", worker["Id"], "python", "-c", client, marker], check=True)
    logs = subprocess.check_output(["docker", "logs", api["Id"]], stderr=subprocess.STDOUT, text=True)
    lines = [line for line in logs.splitlines() if marker in line]
    for line in lines:
        print(line)
    if not any(expected in line for line in lines):
        sys.exit(f"FAIL TLS client IP: expected {expected}; API must implement HLM_TRUSTED_PROXY_IPS (D-039)")
    print(f"PASS TLS client IP: API logged {expected}; spoofed XFF ignored")


if __name__ == "__main__":
    if "--routes" in sys.argv[1:]:
        sys.exit(routes_main(sys.argv[1:]))
    edge_drill()
