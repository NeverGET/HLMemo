#!/usr/bin/env python3
"""Opt-in 38.4 MB worker/SIGTERM acceptance probe (not collected by pytest).

HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_exit \
    uv run --frozen python tests/deploy/worker_oom_smoke.py \
    --image hlmemo:worker-oom --build

Creates only the ``oom-worker`` Compose project, using a temporary compose file,
the existing hlm_exit database and read-only mounted models. Refuses to reuse an
existing project. Run separately from tests that reset this database/admin token.
Database rows are retained for inspection. Containers/network are always removed.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import secrets
import subprocess
import tempfile
import time
import urllib.error
import uuid
from pathlib import Path

import psycopg
from oom_smoke import MIB, ROOT, Client, Stats, docker

PROJECT = "oom-worker"
TEST_DSN = "postgresql://hlm:hlm@127.0.0.1:5432/hlm_exit"
MODELS = Path("/Users/cemalkurt/Projects/HLMemo/models")


def compose_file(image: str, token: str, models: Path, *, profile: bool = False) -> dict:
    common = {
        "image": image,
        "restart": "unless-stopped",
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "pids_limit": 256,
        "cpus": 1.0,
        "tmpfs": ["/tmp:size=64m,mode=1777"],
        "extra_hosts": ["host.docker.internal:host-gateway"],
        "volumes": [{"type": "bind", "source": str(models), "target": "/app/models", "read_only": True}],
        "stop_grace_period": "150s",
    }
    environment = {
        "HLM_DB_DSN": TEST_DSN.replace("127.0.0.1", "host.docker.internal"),
        "HLM_TEST_DSN": TEST_DSN,
        "HLM_MODELS_DIR": "/app/models",
        "PYTHONUNBUFFERED": "1",
    }
    return {
        "name": PROJECT,
        "services": {
            "api": {
                **common,
                "command": ["python", "-m", "hlmemo.server.app"],
                "mem_limit": "2560m",
                "memswap_limit": "2560m",
                "environment": {
                    **environment,
                    "HLM_ADMIN_TOKEN": token,
                    # W0a (D-061): dev admin HTTP contract (defaults are closed/disabled).
                    "HLM_ADMIN_HTTP": "enabled",
                    "HLM_REGISTRATION_MODE": "open",
                    "HLM_API_HOST": "0.0.0.0",
                    "HLM_API_PORT": "8765",
                    "HLM_REQUEST_SPOOL_DIR": "/var/spool/hlmemo",
                },
                "tmpfs": [
                    "/tmp:size=64m,mode=1777",
                    "/var/spool/hlmemo:size=320m,uid=10001,gid=10001,mode=0700",
                ],
                "ports": [{"target": 8765, "published": "0", "host_ip": "127.0.0.1"}],
            },
            "worker": {
                **common,
                "command": ["python", "-m", "hlmemo.worker.main"],
                "mem_limit": "1536m",
                "memswap_limit": "1536m",
                # Production defaults unless --profile: tracemalloc slows the run and adds overhead.
                "environment": {**environment, "HLM_WORKER_MEMORY_PROFILE": "1" if profile else "0"},
            },
        },
    }


def inspect(container: str) -> dict:
    return json.loads(docker("inspect", container))[0]


def healthy_worker(container: str) -> dict:
    info = inspect(container)
    assert info["State"]["Running"], "worker exited"
    assert not info["State"]["OOMKilled"], "worker was OOM-killed"
    assert info["RestartCount"] == 0, "worker restarted"
    assert info["HostConfig"]["Memory"] == 1536 * MIB
    assert info["HostConfig"]["MemorySwap"] == 1536 * MIB, "worker swap must be disabled"
    return info


def embedding_counts(conn: psycopg.Connection, versions: list[int]) -> dict:
    """Count exact versions, scoped to every job's pinned embedding identity."""
    rows = conn.execute(
        """
        SELECT c.version_id, count(*), count(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM embeddings e JOIN jobs j
                ON (j.payload->>'version_id')::bigint = c.version_id
            WHERE e.chunk_id = c.chunk_id AND j.kind IN ('embed', 'reembed')
              AND e.model = j.payload->>'model'
              AND e.model_revision = j.payload->>'model_revision'
              AND e.preproc_version = (j.payload->>'preproc_version')::integer
              AND e.dims = (j.payload->>'dims')::integer
        )) FROM chunks c WHERE c.version_id = ANY(%s) GROUP BY c.version_id
        """,
        (versions,),
    ).fetchall()
    jobs = conn.execute(
        """SELECT status, count(*) FROM jobs WHERE kind IN ('embed', 'reembed')
        AND (payload->>'version_id')::bigint = ANY(%s) GROUP BY status""",
        (versions,),
    ).fetchall()
    return {
        "versions": len(rows),
        "versions_complete": sum(expected > 0 and expected == done for _, expected, done in rows),
        "embeddings_expected": sum(expected for _, expected, _ in rows),
        "embeddings_complete": sum(done for _, _, done in rows),
        "jobs": dict(jobs),
    }


def cgroup_memory(container: str) -> dict:
    raw = docker(
        "exec",
        container,
        "python",
        "-c",
        "from pathlib import Path; import json; "
        "p=Path('/sys/fs/cgroup'); "
        "peak=p/'memory.peak'; old=p/'memory/memory.max_usage_in_bytes'; "
        "events=p/'memory.events'; "
        "print(json.dumps({'peak': int(peak.read_text()) if peak.exists() "
        "else int(old.read_text()) if old.exists() else None, "
        "'events': dict(line.split() for line in events.read_text().splitlines()) "
        "if events.exists() else {}}))",
    )
    return json.loads(raw)


def wait_ready(client: Client, container: str) -> None:
    deadline = time.monotonic() + 300
    while True:
        try:
            assert client.request("/ready")["status"] == "ready"
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            info = inspect(container)
            if not info["State"]["Running"] or info["RestartCount"] or time.monotonic() >= deadline:
                raise AssertionError(f"API did not become ready: {info['State']}") from None
            time.sleep(1)


def run(args: argparse.Namespace) -> None:
    if os.environ.get("HLM_TEST_DSN") != TEST_DSN:
        raise ValueError(f"Set HLM_TEST_DSN={TEST_DSN}; no other database is allowed")
    if not args.models_dir.is_dir():
        raise ValueError("models directory does not exist")
    for resource in ("container", "network", "volume"):
        existing = docker(
            resource, "ls", "--quiet", "--filter", f"label=com.docker.compose.project={PROJECT}"
        )
        if existing:
            raise RuntimeError(f"refusing to reuse an existing {PROJECT} Compose {resource}")
    with psycopg.connect(TEST_DSN, autocommit=True) as conn:
        assert conn.execute("SELECT current_database()").fetchone() == ("hlm_exit",)
    if args.build:
        subprocess.run(
            [
                "docker",
                "build",
                "--target",
                "runtime",
                "--build-arg",
                "BAKE_MODELS=0",
                "-t",
                args.image,
                str(ROOT),
            ],
            check=True,
        )
    token = "hlm_" + secrets.token_urlsafe(32)
    report = {"project": PROJECT, "image": args.image, "memory_limit_MiB": 1536, "passed": False}
    stats = None
    worker = api = None
    with tempfile.TemporaryDirectory(prefix="hlmemo-worker-oom-") as directory:
        compose = Path(directory) / "compose.json"
        spec = compose_file(args.image, token, args.models_dir.resolve(), profile=args.profile)
        compose.write_text(json.dumps(spec))
        compose.chmod(0o600)
        prefix = ("compose", "--project-name", PROJECT, "--file", str(compose))
        try:
            docker(*prefix, "up", "--detach", timeout=180)
            api = docker(*prefix, "ps", "--quiet", "api")
            worker = docker(*prefix, "ps", "--quiet", "worker")
            assert api and worker, "compose did not create both services"
            stats = Stats(worker)
            port = docker("port", api, "8765/tcp")
            assert port.startswith("127.0.0.1:") and int(port.rsplit(":", 1)[1]) not in {8765, 5432}, port
            client = Client("http://" + port, token)
            wait_ready(client, api)
            project = "worker-oom-" + secrets.token_hex(6)
            report["memory_project"] = project
            client.request("/admin/projects", {"slug": project, "name": "Worker memory acceptance"})
            init = client.rpc(
                "initialize",
                {
                    "protocolVersion": client.protocol,
                    "capabilities": {},
                    "clientInfo": {"name": "worker-oom", "version": "1"},
                },
            )
            client.protocol = init["protocolVersion"]
            client.rpc("notifications/initialized", {}, notification=True)
            params = {
                "name": "memory.write",
                "arguments": {
                    "project": project,
                    "request_id": str(uuid.uuid4()),
                    "client": "worker-oom/1",
                    "items": [{"kind": "fact", "title": f"Max {i}", "body": "😀" * 64000} for i in range(50)],
                    "token_budget": 32000,
                },
            }
            # Client uses ensure_ascii=True, exactly the authgate 38.4 MB wire case.
            wire = json.dumps(
                {"jsonrpc": "2.0", "method": "tools/call", "params": params, "id": client.sequence + 1}
            ).encode()
            report["wire_bytes"] = len(wire)
            assert 38_400_000 < len(wire) < 38_500_000
            del wire
            started = time.monotonic()
            response = client.rpc("tools/call", params)
            del params
            payload = json.loads(response["content"][0]["text"])
            versions = [int(version["version_id"]) for version in payload["versions"]]
            assert len(versions) == len(set(versions)) == 50, payload
            report["version_ids"] = versions
            print(
                json.dumps({"write": "accepted", "versions": 50, "wire_bytes": report["wire_bytes"]}),
                flush=True,
            )
            deadline = time.monotonic() + args.timeout
            previous = None
            with psycopg.connect(TEST_DSN, autocommit=True) as conn:
                while True:
                    healthy_worker(worker)
                    counts = embedding_counts(conn, versions)
                    report.update(counts)
                    if counts != previous:
                        print(json.dumps({"progress": counts}), flush=True)
                        previous = counts
                    assert not counts["jobs"].get("failed"), counts
                    if counts["versions_complete"] == 50 and set(counts["jobs"]) == {"done"}:
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"embedding completion exceeded {args.timeout}s: {counts}")
                    time.sleep(2)
            report["elapsed_seconds"] = round(time.monotonic() - started, 2)
            assert stats.samples, "no worker memory samples"
            assert not stats.errors, stats.errors
            report["passed"] = True
        finally:
            try:
                if stats is not None:
                    stats.close()
                    report["docker_stats_peak_MiB"] = round(max(stats.samples, default=0) / MIB, 2)
                    report["stats_samples"] = len(stats.samples)
                    report["stats_errors"] = stats.errors
                    assert not stats.errors, stats.errors
                if worker:
                    info = inspect(worker)
                    report.update(OOMKilled=info["State"]["OOMKilled"], RestartCount=info["RestartCount"])
                    if info["State"]["Running"]:
                        memory = cgroup_memory(worker)
                        report["cgroup_peak_MiB"] = round(memory["peak"] / MIB, 2) if memory["peak"] else None
                        report["cgroup_events"] = memory["events"]
                        assert int(memory["events"].get("oom_kill", "0")) == 0, memory
                    # Docker logging sends application stderr to subprocess stderr too.
                    logs = subprocess.run(
                        ["docker", "logs", worker], capture_output=True, text=True, check=True
                    )
                    args.worker_log.write_text((logs.stdout + logs.stderr).replace(token, "[redacted]"))
                    report["worker_log"] = str(args.worker_log)
                    healthy_worker(worker)
                if api:
                    assert inspect(api)["State"]["Running"], "API exited before SIGTERM test"
                    docker("stop", "--time", "150", api, timeout=180)
                    info = inspect(api)
                    report["api_stop_exit_code"] = info["State"]["ExitCode"]
                    assert info["State"]["ExitCode"] == 0, "API did not exit zero on SIGTERM"
            except BaseException:
                report["passed"] = False
                raise
            finally:
                print(json.dumps(report, sort_keys=True), flush=True)
                docker(*prefix, "down", "--timeout", "150", timeout=180)
    print("PASS worker 38.4 MB: embeddings complete; OOMKilled=false; RestartCount=0; API SIGTERM exit=0")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="runtime image built from this worktree")
    parser.add_argument("--build", action="store_true", help="build this checkout with BAKE_MODELS=0")
    parser.add_argument("--models-dir", type=Path, default=MODELS)
    parser.add_argument("--profile", action="store_true", help="enable worker RSS/tracemalloc stage logs")
    parser.add_argument("--timeout", type=float, default=7200, help="embedding completion timeout, seconds")
    parser.add_argument(
        "--worker-log",
        type=Path,
        default=Path(tempfile.gettempdir()) / f"hlmemo-worker-oom-{uuid.uuid4().hex}.log",
    )
    run(parser.parse_args())


if __name__ == "__main__":
    main()
