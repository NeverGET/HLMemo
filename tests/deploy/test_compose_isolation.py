"""Render-only regression checks: no daemon, no application test fixtures."""

import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ComposeIsolationTests(unittest.TestCase):
    def render(self, local=False):
        env = {key: value for key, value in os.environ.items() if not key.startswith(("HLM_", "BAKE_"))}
        for name in ("APP", "API", "DB"):
            env[f"HLM_{name}_ENV_FILE"] = str(ROOT / "deploy" / f"{name.lower()}.env.example")
        if local:
            env.update(
                BAKE_PROJECT="bake-astra",
                BAKE_BIND_IP="127.0.0.1",
                BAKE_HTTP_PORT="18080",
                BAKE_HTTPS_PORT="18443",
            )
        return json.loads(
            subprocess.check_output(
                [
                    "docker",
                    "compose",
                    "-f",
                    "deploy/compose.prod.yaml",
                    "--env-file",
                    "deploy/.env.prod.example",
                    "config",
                    "--format",
                    "json",
                ],
                cwd=ROOT,
                env=env,
            )
        )

    def test_secrets_reach_only_their_consumers(self):
        services = self.render()["services"]
        for name, service in services.items():
            keys = set(service.get("environment", {}))
            self.assertFalse(any(key.startswith(("AWS_", "S3_")) for key in keys), name)
            if name != "api":
                self.assertTrue(
                    keys.isdisjoint({"HLM_ADMIN_TOKEN", "HLM_REGISTRATION_SECRET", "HLM_CURSOR_SECRET"}), name
                )
        self.assertEqual(
            set(services["db"]["environment"]),
            {"POSTGRES_USER", "POSTGRES_DB", "POSTGRES_PASSWORD", "POSTGRES_INITDB_ARGS"},
        )
        # W0a (D-061): the api keeps only its cursor secret; admin token and registration secret are gone.
        self.assertIn("HLM_CURSOR_SECRET", services["api"]["environment"])
        self.assertNotIn("HLM_ADMIN_TOKEN", services["api"]["environment"])
        self.assertNotIn("HLM_REGISTRATION_SECRET", services["api"]["environment"])

    def test_access_settings_ship_with_the_release(self):
        """W0a (D-061): production access mode is pinned in the tracked Compose model, not env files."""
        services = self.render()["services"]
        for name in ("api", "worker", "migrate"):
            env = services[name]["environment"]
            self.assertEqual(env["HLM_DEPLOYMENT"], "production", name)
            self.assertEqual(env["HLM_REGISTRATION_MODE"], "closed", name)
            self.assertEqual(env["HLM_ADMIN_HTTP"], "disabled", name)
        self.assertEqual(services["migrate"]["command"], ["alembic", "upgrade", "main@head"])
        caddy = (ROOT / "deploy/Caddyfile").read_text()
        self.assertIn("@rest path /health /ready /devices/whoami /devices/revoke\n", caddy)
        self.assertNotIn("/admin/*", caddy)

    def test_proxy_trust_matches_frontend_only_route(self):
        config = self.render()
        subnet = config["networks"]["frontend"]["ipam"]["config"][0]["subnet"]
        api = config["services"]["api"]
        self.assertEqual(subnet, "172.30.39.0/24")
        self.assertEqual(api["environment"]["HLM_TRUSTED_PROXY_IPS"], subnet)
        self.assertNotIn("FORWARDED_ALLOW_IPS", api["environment"])
        self.assertIn("api-frontend", api["networks"]["frontend"]["aliases"])
        for network in ("backend", "outbound"):
            self.assertNotIn("api-frontend", (api["networks"][network] or {}).get("aliases", []))
        self.assertIn("reverse_proxy api-frontend:8765", (ROOT / "deploy/Caddyfile").read_text())

    def test_eight_gb_host_fits_measured_api_peak_and_spool(self):
        services = self.render()["services"]
        # §6 (PHASE2-4-ROADMAP): the one-shot `migrate` runs only with the writers stopped
        # (deploy.sh), so it is not part of the concurrent sum; the librarian (W2a, 512m) is.
        total = sum(int(s["mem_limit"]) for name, s in services.items() if name != "migrate")
        self.assertEqual(int(services["librarian"]["mem_limit"]), 512 * 1024**2)
        self.assertLessEqual(float(services["librarian"]["cpus"]), 0.5)
        self.assertFalse(services["librarian"].get("ports"))
        self.assertLessEqual(total, 7 * 1024**3)
        self.assertLess(total, 8_000_000_000)  # Fits even a decimal 8 GB host.
        self.assertEqual(int(services["db"]["mem_limit"]), 2 * 1024**3)
        api = services["api"]
        self.assertEqual(api["environment"]["HLM_REQUEST_SPOOL_DIR"], "/var/spool/hlmemo")
        self.assertIn("/var/spool/hlmemo:size=320m,uid=10001,gid=10001,mode=0700", api["tmpfs"])
        self.assertIn("/tmp:size=64m,mode=1777", api["tmpfs"])
        self.assertGreaterEqual(int(api["mem_limit"]), (1536 + 320 + 64) * 1024**2 * 1.25)
        for setting in ("shared_buffers=512MB", "work_mem=4MB", "max_connections=50"):
            self.assertIn(setting, services["db"]["command"])
        for name in ("api", "worker"):
            self.assertGreaterEqual(int(services[name]["mem_limit"]), 1536 * 1024**2)
            self.assertLessEqual(float(services[name]["cpus"]), 2)

    def test_production_all_interfaces_and_http3_local_loopback(self):
        for local in (False, True):
            services = self.render(local)["services"]
            ports = services["caddy"]["ports"]
            self.assertEqual(
                {(p["target"], p["protocol"]) for p in ports}, {(80, "tcp"), (443, "tcp"), (443, "udp")}
            )
            for port in ports:
                if local:
                    self.assertEqual(port["host_ip"], "127.0.0.1")
                    self.assertIn(str(port["published"]), {"18080", "18443"})
                else:
                    self.assertNotIn("host_ip", port)
            for name in ("db", "api", "worker", "migrate"):
                self.assertFalse(services[name].get("ports"))


if __name__ == "__main__":
    unittest.main()
