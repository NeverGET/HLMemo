"""Deployment-only regressions; no application imports or live services required."""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("deploy_probe", ROOT / "deploy/scripts/probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class FakeProbe(probe.Probe):
    """Records hlmemo.ops calls instead of running docker exec (W0a: no admin HTTP, D-061)."""

    def __init__(self):
        self.settings = {}
        self.project_name = "bake-test"
        self.projects = set()
        self.devices = {}
        self.calls = []
        self.fail_initialize = False
        self.fail_mint = False

    def ops(self, *args):
        self.calls.append(args)
        if args[:2] == ("project", "create"):
            created = args[2] not in self.projects
            self.projects.add(args[2])
            return json.dumps({"created": created, "project": {"slug": args[2]}}) + "\n", ""
        if args[:2] == ("device", "mint"):
            if self.fail_mint:
                raise ValueError("hlmemo.ops device mint failed")
            device_id = len(self.devices) + 2
            self.devices[device_id] = "trusted"
            meta = json.dumps({"minted": {"id": device_id, "name": args[3]}, "grants": []})
            return "hlm_" + "t" * 43 + "\n", meta + "\n"
        if args[:2] == ("device", "revoke"):
            self.devices[int(args[2])] = "revoked"
            return "{}\n", ""
        raise AssertionError(args)

    def request(self, path, body=None, token=None, extra=None):
        raise AssertionError(f"no admin/registration HTTP route may be called: {path}")

    def initialize(self, token):
        if self.fail_initialize:
            raise ValueError("initialization failed")


class SmokeLifecycleTests(unittest.TestCase):
    def test_probe_reads_no_credentials_from_compose(self):
        config = {
            "name": "bake-test",
            "services": {
                "api": {"environment": {"HLM_CURSOR_SECRET": "x$$VAR"}},
                "caddy": {"environment": {"HLM_DOMAIN": "localhost", "HLM_TLS_MODE": "internal"}},
            },
        }
        with mock.patch.dict(probe.os.environ, {}, clear=True):
            client = probe.Probe(config)
        self.assertFalse(hasattr(client, "admin"))
        self.assertEqual(client.project_name, "bake-test")

    def test_ops_runs_in_the_projects_api_container_with_closed_stdin(self):
        client = probe.Probe(
            {"name": "bake-test", "services": {"api": {"environment": {}}, "caddy": {"environment": {}}}}
        )
        done = mock.Mock(returncode=0, stdout="hlm_token\n", stderr="{}\n")
        with (
            mock.patch.object(probe.subprocess, "check_output", return_value="abc123\n") as ps,
            mock.patch.object(probe.subprocess, "run", return_value=done) as run,
        ):
            out, meta = client.ops("device", "mint", "--name", "x", "--class", "ci")
        self.assertIn("label=com.docker.compose.project=bake-test", ps.call_args.args[0])
        argv = run.call_args.args[0]
        self.assertEqual(argv[:6], ["docker", "exec", "abc123", "python", "-m", "hlmemo.ops"])
        self.assertIs(run.call_args.kwargs["stdin"], probe.subprocess.DEVNULL)
        self.assertEqual(out, "hlm_token\n")

    def test_second_smoke_reuses_project_and_revokes_devices(self):
        client = FakeProbe()
        first = client.bootstrap()
        client.revoke(first)
        second = client.bootstrap()
        client.revoke(second)
        self.assertEqual(first["project_status"], "created")
        self.assertEqual(second["project_status"], "reused")
        self.assertEqual(client.projects, {"deploy-smoke"})
        self.assertEqual(set(client.devices.values()), {"revoked"})
        mints = [c for c in client.calls if c[:2] == ("device", "mint")]
        self.assertTrue(all("--expires" in c and "deploy-smoke:write" in c for c in mints))

    def test_failed_initialize_revokes_minted_device(self):
        client = FakeProbe()
        client.fail_initialize = True
        with self.assertRaises(ValueError):
            client.bootstrap()
        self.assertEqual(client.devices, {2: "revoked"})

    def test_failed_mint_leaves_no_device(self):
        client = FakeProbe()
        client.fail_mint = True
        with self.assertRaises(ValueError):
            client.bootstrap()
        self.assertFalse(client.devices)


class CloudProvisioningTests(unittest.TestCase):
    def test_directories_precede_ssh_and_failure_does_not_abort(self):
        template = (ROOT / "deploy/terraform/hetzner/cloud-init.yaml.tftpl").read_text()
        self.assertLess(template.index("/opt/hlmemo /var/backups/hlmemo"), template.index("sshd -t"))
        self.assertLess(template.index("/etc/hlmemo\n"), template.index("sshd -t"))
        start = template.index("      if ! mkdir -p /run/sshd")
        end = template.index("      docker compose version", start)
        hardening = "\n".join(line[6:] for line in template[start:end].splitlines())
        # Exercise both missing runtime dir and socket-activated/inactive service
        # paths with shell stubs. No host directories or system services touched.
        for failed_command in ("mkdir", "sshd", "systemctl", "none"):
            with self.subTest(failed_command=failed_command), tempfile.TemporaryDirectory() as directory:
                drop_in = Path(directory) / "00-hlmemo.conf"
                drop_in.write_text("PasswordAuthentication no\n")
                safe_hardening = hardening.replace("/etc/ssh/sshd_config.d/00-hlmemo.conf", str(drop_in))
                stubs = "\n".join(
                    f'{name}() {{ printf "%s\\n" "{name} $*"; return {int(name == failed_command)}; }}'
                    for name in ("mkdir", "sshd", "systemctl")
                )
                result = subprocess.run(
                    [
                        "bash",
                        "-c",
                        f"set -euo pipefail\n{stubs}\n{safe_hardening}\nprintf 'provisioning continued\\n'",
                    ],
                    text=True,
                    capture_output=True,
                    check=True,
                )
                self.assertEqual(result.stdout.splitlines()[-1], "provisioning continued")
                self.assertIn("mkdir -p /run/sshd", result.stdout)
                if failed_command == "none":
                    self.assertIn("systemctl try-reload-or-restart ssh.service", result.stdout)
                else:
                    self.assertIn("WARNING: SSH hardening", result.stderr)
                if failed_command in ("mkdir", "sshd"):
                    self.assertFalse(
                        drop_in.exists(), "rejected configuration must not survive for ssh.socket"
                    )
                    self.assertNotIn("systemctl", result.stdout)
                    self.assertIn("removed HLMemo drop-in", result.stderr)
                else:
                    self.assertTrue(drop_in.exists(), "valid configuration must survive reload failure")


if __name__ == "__main__":
    unittest.main()
