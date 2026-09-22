"""Bootstrap preflight safety without modifying the workstation."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

BOOTSTRAP = Path(__file__).resolve().parents[2] / "deploy" / "bootstrap.sh"


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = self.root / "operator.pub"
        self.key.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakePublicKey test\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.marker = self.root / "unexpected-command"
        for command in ("apt-get", "curl", "ufw", "systemctl", "useradd", "usermod"):
            stub = self.bin / command
            stub.write_text('#!/bin/sh\ntouch "$BOOTSTRAP_TEST_MARKER"\nexit 99\n')
            stub.chmod(0o755)

    def run_bootstrap(self, *args, connection=""):
        env = dict(os.environ)
        env.update(
            PATH=f"{self.bin}:{env['PATH']}",
            BOOTSTRAP_TEST_MARKER=str(self.marker),
            SSH_CONNECTION=connection,
        )
        return subprocess.run(
            ["bash", str(BOOTSTRAP), "--ssh-key", str(self.key), *args],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_dry_run_has_all_host_actions_without_mutation(self):
        result = self.run_bootstrap("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        for action in (
            "Docker official signed apt repository",
            "docker-compose-plugin",
            "TCP 22 from anywhere",
            "UDP 443",
            "/var/backups/hlmemo",
            "fail2ban",
            "unattended upgrades",
            "--finalize-ssh",
        ):
            self.assertIn(action, result.stdout)
        self.assertNotIn("AAAAC3", result.stdout)
        self.assertFalse(self.marker.exists())

    def test_cidr_cannot_exclude_current_operator(self):
        result = self.run_bootstrap(
            "--dry-run",
            "--admin-cidr",
            "203.0.113.0/24",
            connection="198.51.100.7 4567 203.0.113.1 22",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("excludes the current SSH source", result.stderr)
        self.assertFalse(self.marker.exists())

    def test_cidr_accepts_current_ipv6_operator(self):
        result = self.run_bootstrap(
            "--dry-run",
            "--admin-cidr",
            "2001:db8::/32",
            connection="2001:db8::10 4567 2001:db8::1 22",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TCP 22 from 2001:db8::/32", result.stdout)

    def test_bad_cidr_rejected(self):
        result = self.run_bootstrap("--dry-run", "--admin-cidr", "anything")
        self.assertNotEqual(result.returncode, 0)

    def test_root_deploy_user_rejected(self):
        result = self.run_bootstrap("--dry-run", "--deploy-user", "root")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid non-root deploy user", result.stderr)

    def test_private_key_rejected(self):
        # Header only, no key material; split so secret scanners do not flag the test source.
        self.key.write_text("-----BEGIN OPENSSH " + "PRIVATE KEY-----\n")
        result = self.run_bootstrap("--dry-run")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid OpenSSH public key", result.stderr)

    def test_nonstandard_ssh_port_rejected_with_cidr(self):
        result = self.run_bootstrap(
            "--dry-run",
            "--admin-cidr",
            "203.0.113.0/24",
            connection="203.0.113.7 4567 203.0.113.1 2222",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SSH on port 22 only", result.stderr)

    def test_prepare_only_explicitly_reports_incomplete_host(self):
        result = self.run_bootstrap("--dry-run", "--prepare-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SKIP: systemd services, active firewall", result.stdout)

    def test_finalize_dry_run_requires_key_authenticated_session(self):
        result = self.run_bootstrap("--dry-run", "--finalize-ssh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("public-key SSH session", result.stdout)
        self.assertIn("roll back on failure", result.stdout)
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
