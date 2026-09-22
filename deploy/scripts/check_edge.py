#!/usr/bin/env python3
"""Local bake TLS proof: body reaches the API and API logs the actual TLS client."""

import http.client
import json
import subprocess
import sys
import urllib.parse
import uuid

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
        "Authorization": f"Bearer {probe.admin}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    },
)
response = connection.getresponse()
status = response.status
content = response.read()
connection.close()
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
network = next(n for n in worker["NetworkSettings"]["Networks"] if n in caddy["NetworkSettings"]["Networks"])
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
