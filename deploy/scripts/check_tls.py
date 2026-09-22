"""Read Compose JSON on stdin without logging secrets; check the actual running containers."""
import json
import subprocess
import sys
import urllib.error

from probe import Probe

config = json.load(sys.stdin)
probe = Probe(config)
project = config["name"]
container_ids = subprocess.check_output(
    ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"], text=True
).split()
if not container_ids:
    sys.exit("No containers for selected project")
containers = json.loads(subprocess.check_output(["docker", "inspect", *container_ids]))
by_service = {c["Config"]["Labels"]["com.docker.compose.service"]: c for c in containers}
for name in ("db", "api", "worker", "caddy"):
    if by_service[name]["State"].get("Health", {}).get("Status") != "healthy":
        sys.exit(f"Unhealthy service: {name}")
for name in ("db", "api", "worker", "migrate"):
    if by_service[name]["HostConfig"].get("PortBindings"):
        sys.exit(f"Unexpected host ports: {name}")
if by_service["migrate"]["State"]["ExitCode"] != 0:
    sys.exit("Migration did not complete successfully")
networks = list(by_service["db"]["NetworkSettings"]["Networks"])
for network in json.loads(subprocess.check_output(["docker", "network", "inspect", *networks])):
    if not network["Internal"]:
        sys.exit("DB attached to non-private network")
if probe.request("/ready")["status"] != "ready":
    sys.exit("TLS readiness failed")
for path in ("/anything-else", "/mcp/", "/mcp-extra", "/ready/extra", "/admin"):
    try:
        probe.request(path)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            continue
    sys.exit(f"Unexpected allowlist response: {path}")
print("PASS G-D2: healthy stack; TLS ready=200, unknown=404; DB has no host ports and only internal networking")
