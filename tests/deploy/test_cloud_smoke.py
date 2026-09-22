"""Deployment-only regressions; no application imports or live services required."""

import importlib.util
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("deploy_probe", ROOT / "deploy/scripts/probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class FakeProbe(probe.Probe):
    def __init__(self):
        self.admin = "test"
        self.settings = {}
        self.projects = set()
        self.devices = {}
        self.fail_initialize = False
        self.fail_approve = False
        self.reject_project = False

    def request(self, path, body=None, token=None, extra=None):
        if path == "/admin/projects":
            if body is None:
                return {"projects": [{"slug": slug} for slug in self.projects]}
            if body["slug"] in self.projects or self.reject_project:
                raise urllib.error.HTTPError(path, 400, "invalid argument", {}, None)
            self.projects.add(body["slug"])
        elif path == "/devices/register":
            device_id = len(self.devices) + 1
            self.devices[device_id] = "pending"
            return {"device": {"id": device_id}, "token": "test"}
        elif path == "/devices/approve":
            if self.fail_approve:
                raise ValueError("approval failed")
            self.devices[body["id"]] = "approved"
        elif path == "/devices/revoke":
            self.devices[body["id"]] = "revoked"
        else:
            raise AssertionError(path)

    def initialize(self, token):
        if self.fail_initialize:
            raise ValueError("initialization failed")


class SmokeLifecycleTests(unittest.TestCase):
    def test_compose_dollar_escaping_is_removed_from_http_credentials_once(self):
        config = {
            "services": {
                "api": {"environment": {"HLM_ADMIN_TOKEN": "x$$VAR", "HLM_REGISTRATION_SECRET": "y$$$$VAR"}},
                "caddy": {"environment": {"HLM_DOMAIN": "localhost", "HLM_TLS_MODE": "internal"}},
            }
        }
        with mock.patch.dict(probe.os.environ, {}, clear=True):
            client = probe.Probe(config)
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.headers = {}
        response.read.return_value = b"{}"
        with mock.patch.object(probe.urllib.request, "urlopen", return_value=response) as opened:
            client.request("/admin/projects", token=client.admin)
            self.assertEqual(opened.call_args.args[0].get_header("Authorization"), "Bearer x$VAR")
            client.request(
                "/devices/register",
                {},
                extra={"X-HLM-Registration-Secret": client.settings["HLM_REGISTRATION_SECRET"]},
            )
            self.assertEqual(opened.call_args.args[0].get_header("X-hlm-registration-secret"), "y$$VAR")
        self.assertEqual(config["services"]["api"]["environment"]["HLM_ADMIN_TOKEN"], "x$$VAR")

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

    def test_failed_approval_or_initialize_revokes_registered_device(self):
        for failure in ("fail_approve", "fail_initialize"):
            with self.subTest(failure=failure):
                client = FakeProbe()
                setattr(client, failure, True)
                with self.assertRaises(ValueError):
                    client.bootstrap()
                self.assertEqual(client.devices, {1: "revoked"})

    def test_other_project_validation_failure_is_not_ignored(self):
        client = FakeProbe()
        client.reject_project = True
        with self.assertRaises(urllib.error.HTTPError):
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
