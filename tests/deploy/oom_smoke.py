#!/usr/bin/env python3
"""Opt-in API memory smoke; never collected by unittest or pytest.

HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_body \
    python3 tests/deploy/oom_smoke.py --image hlmemo:oom-check --build

Builds this checkout when --build is passed; otherwise supply an image already built
from it. Models may be baked in or mounted read-only with --models-dir. Uses only
the existing hlm_body database: startup binds its admin token and the probe adds a
unique project. Run separately from tests that also bind the admin token. No
migrations, database deletion, or existing Compose stack operations are performed.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_DSN = "postgresql://hlm:hlm@127.0.0.1:5432/hlm_body"
CONTAINER = "oom-check-api"
MIB = 1024 * 1024


def docker(*args: str, timeout: int = 60) -> str:
    result = subprocess.run(["docker", *args], check=True, text=True, capture_output=True, timeout=timeout)
    return result.stdout.strip()


def memory_bytes(value: str) -> float:
    match = re.fullmatch(r"([\d.]+)\s*([KMGT]?i?B)", value.strip())
    if match is None:
        raise ValueError(f"unrecognised docker stats memory: {value!r}")
    number, unit = match.groups()
    units = {"B": 1, "kB": 1000, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}
    units.update({"KiB": 1024, "MiB": MIB, "GiB": 1024**3, "TiB": 1024**4})
    return float(number) * units[unit]


class Stats:
    def __init__(self, container_id: str):
        self.samples: list[float] = []
        self.errors: list[str] = []
        self.container_id = container_id
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.collect, daemon=True)
        self.thread.start()

    def collect(self) -> None:
        # Docker 29 emits ANSI cursor controls around streaming JSON even through
        # a pipe. Finite polls give clean JSON plus stderr/exit status on failure.
        while not self.stopped.is_set():
            try:
                result = subprocess.run(
                    ["docker", "stats", "--no-stream", "--format", "{{json .}}", self.container_id],
                    text=True,
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
                if result.returncode:
                    raise RuntimeError(f"docker stats exit {result.returncode}: {result.stderr.strip()}")
                if not result.stdout.strip():
                    raise RuntimeError(f"docker stats returned no sample; stderr={result.stderr.strip()!r}")
                usage = json.loads(result.stdout.strip())["MemUsage"].split("/", 1)[0]
                self.samples.append(memory_bytes(usage))
            except (KeyError, ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
                self.errors.append(f"{type(exc).__name__}: {exc}")
            self.stopped.wait(0.25)

    def close(self) -> None:
        self.stopped.set()
        self.thread.join(timeout=20)
        if self.thread.is_alive():
            self.errors.append("docker stats collector did not stop")


class Client:
    def __init__(self, origin: str, token: str):
        self.origin = origin
        self.token = token
        self.protocol = "2025-03-26"
        self.sequence = 0

    def request(self, path: str, body: dict | None = None) -> dict:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
            "MCP-Protocol-Version": self.protocol,
            "X-HLM-Client": "oom-check/1",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.origin + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=120) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}

    def rpc(self, method: str, params: dict, *, notification: bool = False) -> dict:
        self.sequence += 1
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            body["id"] = self.sequence
        answer = self.request("/mcp", body)
        if notification:
            return {}
        if "error" in answer or "result" not in answer:
            raise AssertionError(f"{method} failed: {answer}")
        result = answer["result"]
        if result.get("isError"):
            raise AssertionError(f"{method} returned a tool error: {result}")
        return result


def run(args: argparse.Namespace) -> None:
    if os.environ.get("HLM_TEST_DSN") != TEST_DSN:
        raise ValueError(f"Set HLM_TEST_DSN={TEST_DSN}; no other database is allowed")
    if args.build:
        subprocess.run(
            [
                "docker",
                "build",
                "--target",
                "runtime",
                "--build-arg",
                f"BAKE_MODELS={0 if args.models_dir else 1}",
                "-t",
                args.image,
                str(ROOT),
            ],
            check=True,
        )
    token = "hlm_" + secrets.token_urlsafe(32)
    command = [
        "run",
        "--detach",
        "--name",
        CONTAINER,
        "--label",
        "com.docker.compose.project=oom-check",
        "--label",
        "com.docker.compose.service=api",
        "--memory=1536m",
        "--memory-swap=1536m",
        "--cpus=1",
        "--restart=no",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--pids-limit=256",
        "--tmpfs",
        "/tmp:size=64m,mode=1777",
        "--tmpfs",
        "/var/spool/hlmemo:size=320m,uid=10001,gid=10001,mode=0700",
        "--publish",
        "127.0.0.1::8765",
        "--add-host",
        "host.docker.internal:host-gateway",
        "--env",
        "HLM_DB_DSN=postgresql://hlm:hlm@host.docker.internal:5432/hlm_body",
        "--env",
        f"HLM_TEST_DSN={TEST_DSN}",
        "--env",
        f"HLM_ADMIN_TOKEN={token}",
        # W0a (D-061): this drill uses the dev admin HTTP contract (defaults are closed/disabled).
        "--env",
        "HLM_ADMIN_HTTP=enabled",
        "--env",
        "HLM_REGISTRATION_MODE=open",
        "--env",
        "HLM_API_HOST=0.0.0.0",
        "--env",
        "HLM_API_PORT=8765",
        "--env",
        "HLM_REQUEST_SPOOL_DIR=/var/spool/hlmemo",
    ]
    if args.models_dir:
        command.extend(["--mount", f"type=bind,src={args.models_dir.resolve()},dst=/app/models,readonly"])
    command.extend([args.image, "python", "-m", "hlmemo.server.app"])
    # An existing same-name container makes docker run fail; never remove it.
    container_id = docker(*command)
    stats = None
    completed = 0
    try:
        stats = Stats(container_id)
        port = docker("port", container_id, "8765/tcp")
        if not port.startswith("127.0.0.1:") or int(port.rsplit(":", 1)[1]) in {8765, 5432}:
            raise AssertionError(f"expected isolated ephemeral loopback port: {port}")
        client = Client("http://" + port, token)
        deadline = time.monotonic() + 240
        while True:
            try:
                ready = client.request("/ready")
                assert ready["status"] == "ready", ready
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
                state = json.loads(docker("inspect", container_id))[0]["State"]
                if not state["Running"] or time.monotonic() >= deadline:
                    raise AssertionError(f"API did not become ready: {state}") from None
                time.sleep(1)
        project = "oom-check-" + secrets.token_hex(6)
        client.request("/admin/projects", {"slug": project, "name": "API memory smoke"})
        init = client.rpc(
            "initialize",
            {
                "protocolVersion": client.protocol,
                "capabilities": {},
                "clientInfo": {"name": "oom-check", "version": "1"},
            },
        )
        client.protocol = init["protocolVersion"]
        client.rpc("notifications/initialized", {}, notification=True)
        for number in range(20):
            result = client.rpc(
                "tools/call",
                {
                    "name": "memory.query",
                    "arguments": {
                        "project": project,
                        "query": f"Memory safety smoke request {number}: deployment limits and readiness",
                        "token_budget": 2000,
                    },
                },
            )
            payload = json.loads(result["content"][0]["text"])
            assert "budget" in payload, payload
            completed += 1
            # Keep the 20 calls visible to docker stats' one-second sampler.
            time.sleep(0.15)
        assert client.request("/ready")["status"] == "ready"
        assert completed == 20
        time.sleep(2)
        assert stats.samples, "docker stats produced no memory samples"
        assert not stats.errors, stats.errors
    finally:
        try:
            if stats is not None:
                stats.close()
            info = json.loads(docker("inspect", container_id))[0]
            state = info["State"]
            peak = max(stats.samples, default=0) / MIB if stats else 0
            cgroup_peak = None
            if state["Running"]:
                raw_peak = docker(
                    "exec",
                    container_id,
                    "python",
                    "-c",
                    (
                        "from pathlib import Path; "
                        "p=Path('/sys/fs/cgroup/memory.peak'); "
                        "q=Path('/sys/fs/cgroup/memory/memory.max_usage_in_bytes'); "
                        "print(p.read_text().strip() if p.exists() else "
                        "q.read_text().strip() if q.exists() else 'unavailable')"
                    ),
                )
                if raw_peak.isdigit():
                    cgroup_peak = round(int(raw_peak) / MIB, 2)
            print(
                json.dumps(
                    {
                        "project": "oom-check",
                        "image": args.image,
                        "memory_limit_MiB": info["HostConfig"]["Memory"] / MIB,
                        "queries": completed,
                        "OOMKilled": state["OOMKilled"],
                        "RestartCount": info["RestartCount"],
                        "Running": state["Running"],
                        "docker_stats_peak_MiB": round(peak, 2),
                        "cgroup_peak_MiB": cgroup_peak,
                        "stats_samples": len(stats.samples) if stats else 0,
                        "stats_errors": stats.errors if stats else [],
                    }
                ),
                flush=True,
            )
            assert not state["OOMKilled"], "API was OOM-killed"
            assert info["RestartCount"] == 0, "API restarted"
            assert info["HostConfig"]["Memory"] == 1536 * MIB
            assert state["Running"], "API exited"
        finally:
            docker("rm", "--force", container_id)
    print("PASS memory smoke: /ready + 20 memory.query; OOMKilled=false; RestartCount=0; cleaned up")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="runtime image built from this worktree")
    parser.add_argument("--build", action="store_true", help="build the runtime image from this worktree")
    parser.add_argument("--models-dir", type=Path, help="optional read-only models directory")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
