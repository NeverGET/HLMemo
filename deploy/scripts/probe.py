#!/usr/bin/env python3
"""HTTPS-only production probes. Compose JSON arrives on stdin; never print its secrets.

W0a (D-061): no admin token and no registration. The probe device is minted server-side with
`python -m hlmemo.ops` inside this Compose project's api container (`docker exec`, stdin closed),
used over public HTTPS, and revoked with `hlmemo.ops device revoke` afterwards. Tokens travel
only through pipes, never argv, and are never printed.
"""

import argparse
import json
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


class Probe:
    def __init__(self, config):
        # Compose escapes literal dollars for config round-tripping. HTTP
        # credentials need the actual container values, decoded exactly once.
        self.settings = {
            key: value.replace("$$", "$") if isinstance(value, str) else value
            for key, value in config["services"]["api"]["environment"].items()
        }
        caddy = config["services"]["caddy"].get("environment", {})
        domain = os.environ.get("HLM_DOMAIN", caddy.get("HLM_DOMAIN", "localhost"))
        ports = config["services"]["caddy"].get("ports", [])
        published = next(
            (
                str(p["published"])
                for p in ports
                if p.get("target") == 443 and p.get("protocol", "tcp") == "tcp"
            ),
            "443",
        )
        port = os.environ.get("BAKE_HTTPS_PORT", os.environ.get("HLM_HTTPS_PORT", published))
        self.url = os.environ.get("HLM_SMOKE_URL", f"https://{domain}:{port}").rstrip("/")
        parsed = urllib.parse.urlsplit(self.url)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.path:
            raise ValueError("HLM_SMOKE_URL must be an HTTPS origin without credentials or path")
        internal = os.environ.get("HLM_TLS_MODE", caddy.get("HLM_TLS_MODE")) == "internal"
        self.context = ssl.create_default_context(cafile=os.environ.get("HLM_CA_FILE"))
        # The local drill explicitly exercises the internal CA without installing it on the Mac.
        if internal and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
            self.context.check_hostname = False
            self.context.verify_mode = ssl.CERT_NONE
        self.project_name = config.get("name")
        self.protocol = "2025-03-26"
        self.sequence = 0
        self.session = None

    def request(self, path, body=None, token=None, extra=None):
        headers = {"Accept": "application/json, text/event-stream", "X-HLM-Client": "deploy-probe/1"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            headers["Content-Type"] = "application/json"
        headers.update(extra or {})
        req = urllib.request.Request(
            self.url + path, data=None if body is None else json.dumps(body).encode(), headers=headers
        )
        with urllib.request.urlopen(req, context=self.context, timeout=90) as response:
            self.session = response.headers.get("Mcp-Session-Id", self.session)
            raw = response.read().decode()
            if not raw:
                return None
            if "text/event-stream" in response.headers.get("Content-Type", ""):
                for event in raw.split("\n\n"):
                    data = "\n".join(
                        line[5:].lstrip() for line in event.splitlines() if line.startswith("data:")
                    )
                    if data:
                        parsed = json.loads(data)
                        if "result" in parsed or "error" in parsed:
                            return parsed
                raise ValueError("SSE response contained no JSON-RPC result")
            return json.loads(raw)

    def rpc(self, token, method, params=None, notification=False):
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notification:
            self.sequence += 1
            body["id"] = self.sequence
        headers = {"MCP-Protocol-Version": self.protocol}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        answer = self.request("/mcp", body, token, headers)
        if notification:
            return None
        if not answer or "error" in answer:
            raise ValueError(f"JSON-RPC {method} failed")
        return answer["result"]

    def initialize(self, token):
        init = self.rpc(
            token,
            "initialize",
            {
                "protocolVersion": self.protocol,
                "capabilities": {},
                "clientInfo": {"name": "deploy-probe", "version": "1"},
            },
        )
        self.protocol = init["protocolVersion"]
        self.rpc(token, "notifications/initialized", notification=True)
        tools = self.rpc(token, "tools/list")["tools"]
        expected = {"memory.query", "memory.drilldown", "memory.raw", "memory.write", "memory.call_the_day"}
        if {tool["name"] for tool in tools} != expected:
            raise ValueError("tools/list does not expose exactly the five expected memory tools")

    def ops(self, *args):
        """`python -m hlmemo.ops ARGS` in this project's api container -> (stdout, stderr metadata)."""
        ids = subprocess.check_output(
            [
                "docker",
                "ps",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={self.project_name}",
                "--filter",
                "label=com.docker.compose.service=api",
            ],
            text=True,
            stdin=subprocess.DEVNULL,
        ).split()
        if len(ids) != 1:
            raise ValueError("expected exactly one running api container for hlmemo.ops")
        done = subprocess.run(
            ["docker", "exec", ids[0], "python", "-m", "hlmemo.ops", *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            raise ValueError(f"hlmemo.ops {args[0]} {args[1] if len(args) > 1 else ''} failed")
        return done.stdout, done.stderr

    def bootstrap(self):
        name = "deploy-" + uuid.uuid4().hex[:16]
        project = "deploy-smoke"
        out, _ = self.ops("project", "create", project, "--name", "Deployment verification", "--exists-ok")
        project_status = "created" if json.loads(out)["created"] else "reused"
        out, meta = self.ops(
            "device",
            "mint",
            "--name",
            name,
            "--class",
            "ci",
            "--grant",
            f"{project}:write",
            "--expires",
            "30m",
        )
        token = out.strip()
        minted = next(json.loads(line) for line in meta.splitlines() if line.startswith('{"'))
        device_id = minted["minted"]["id"]
        state = {"project": project, "project_status": project_status, "token": token, "device_id": device_id}
        try:
            self.initialize(token)
        except BaseException:
            self.revoke(state)
            raise
        return state

    def call(self, token, name, arguments):
        result = self.rpc(token, "tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise ValueError(f"{name} returned a tool error")
        return json.loads(result["content"][0]["text"])

    def revoke(self, state):
        self.ops("device", "revoke", str(state["device_id"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["smoke", "write", "read"])
    parser.add_argument("--state", type=Path)
    args = parser.parse_args()
    probe = Probe(json.load(sys.stdin))
    if args.mode == "smoke":
        state = probe.bootstrap()
        probe.revoke(state)
        print(
            "PASS G-D3: HTTPS initialize + tools/list returned all five memory tools; "
            f"project deploy-smoke {state['project_status']}; device revoked"
        )
        return
    if args.state is None:
        parser.error("--state is required for write/read")
    if args.mode == "write":
        state = probe.bootstrap()
        try:
            state["marker"] = "Backup drill payload " + str(uuid.uuid4())
            result = probe.call(
                state["token"],
                "memory.write",
                {
                    "project": state["project"],
                    "request_id": str(uuid.uuid4()),
                    "client": "deploy-probe/1",
                    "items": [{"kind": "fact", "title": "Backup restore drill", "body": state["marker"]}],
                    "token_budget": 2000,
                },
            )
            state["version_id"] = result["versions"][0]["version_id"]
            fd = os.open(args.state, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(state, stream)
        except BaseException:
            probe.revoke(state)
            raise
        print("Drill payload written through HTTPS memory.write")
    else:
        state = json.loads(args.state.read_text())
        try:
            probe.initialize(state["token"])
            result = probe.call(
                state["token"],
                "memory.raw",
                {"project": state["project"], "version_id": state["version_id"], "token_budget": 4000},
            )
            if result["payload_item"]["body"] != state["marker"]:
                raise ValueError("Restored payload does not match the original write")
        finally:
            probe.revoke(state)
        print("PASS G-D4: HTTPS memory.raw returned the identical payload after database wipe and restore")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        sys.exit(f"HTTPS probe failed: HTTP {exc.code}")
    except Exception as exc:
        # Do not dump request bodies, environment values, headers or credential-bearing state.
        sys.exit(f"HTTPS probe failed ({type(exc).__name__})")
