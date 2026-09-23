"""first_deploy.sh argument safety and plan, offline (no SSH, no network)."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIRST_DEPLOY = REPO / "deploy" / "scripts" / "first_deploy.sh"


def git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@invalid", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


class FirstDeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        # A throwaway checkout whose origin is a local bare repository: `git fetch origin main`
        # and the "pushed" check work offline.
        origin = root / "origin.git"
        git("init", "-q", "--bare", "-b", "main", str(origin), cwd=root)
        self.checkout = root / "checkout"
        git("clone", "-q", str(origin), str(self.checkout), cwd=root)
        (self.checkout / "deploy" / "scripts").mkdir(parents=True)
        shutil.copy(FIRST_DEPLOY, self.checkout / "deploy" / "scripts" / "first_deploy.sh")
        shutil.copy(REPO / "deploy" / "bootstrap.sh", self.checkout / "deploy" / "bootstrap.sh")
        for name in (
            ".env.prod.example",
            "app.env.example",
            "api.env.example",
            "db.env.example",
            "backup.env.example",
            "Caddyfile",
        ):
            shutil.copy(REPO / "deploy" / name, self.checkout / "deploy" / name)
        git("add", "-A", cwd=self.checkout)
        git("commit", "-q", "-m", "tooling", cwd=self.checkout)
        git("push", "-q", "origin", "HEAD:main", cwd=self.checkout)
        self.state = root / "state"
        self.key = root / "id_test"
        self.key.write_text("not a real key\n")
        (root / "id_test.pub").write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakePublicKey t\n")

    def run_first_deploy(self, *args: str):
        env = dict(os.environ, HLM_LOCAL_STATE_DIR=str(self.state))
        return subprocess.run(
            [
                "bash",
                str(self.checkout / "deploy" / "scripts" / "first_deploy.sh"),
                "--ssh-key",
                str(self.key),
                *args,
            ],
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )

    def test_public_host_refuses_rehearsal_affordances(self):
        public = ("--host", "203.0.113.10", "--domain", "mcp.example.com", "--dry-run")
        for extra, message in (
            (("--tls", "internal"), "--tls internal is rehearsal-only"),
            (("--ssh-port", "2223"), "--ssh-port is rehearsal-only"),
            (("--public-port", "19443"), "--public-port is rehearsal-only"),
            (("--repo", "git://127.0.0.1/HLMemo.git"), "--repo git:// is rehearsal-only"),
        ):
            with self.subTest(extra=extra):
                result = self.run_first_deploy(*public, *extra)
                self.assertEqual(result.returncode, 64, result.stdout)
                self.assertIn(message, result.stderr)
        self.assertFalse(self.state.exists())

    def test_localhost_domain_requires_loopback_and_internal_tls(self):
        result = self.run_first_deploy("--host", "203.0.113.10", "--domain", "localhost", "--dry-run")
        self.assertEqual(result.returncode, 64)
        self.assertIn("rehearsal-only", result.stderr)

    def test_rehearsal_repo_must_be_loopback_git_daemon(self):
        result = self.run_first_deploy(
            "--host",
            "127.0.0.1",
            "--ssh-port",
            "2223",
            "--domain",
            "localhost",
            "--tls",
            "internal",
            "--repo",
            "git://10.0.0.5/HLMemo.git",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 64)
        self.assertIn("git://127.0.0.1", result.stderr)

    def test_repository_credentials_refused(self):
        result = self.run_first_deploy(
            "--host",
            "203.0.113.10",
            "--domain",
            "mcp.example.com",
            "--repo",
            "https://user:pw@github.com/x/y.git",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 64)

    def test_public_dry_run_plan(self):
        result = self.run_first_deploy(
            "--host",
            "203.0.113.10",
            "--domain",
            "mcp.example.com",
            "--tls",
            "acme",
            "--repo",
            "https://github.com/NeverGET/HLMemo.git",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("url=https://mcp.example.com", result.stdout)
        self.assertIn("Ubuntu 24.04 (noble) or 26.04 (resolute)", result.stdout)
        self.assertIn(f"state={self.state}/203.0.113.10 ", result.stdout)
        self.assertNotIn("BLOCKER: HEAD", result.stdout)
        self.assertNotIn("REHEARSAL", result.stdout)
        self.assertFalse(self.state.exists(), "dry-run must not write local state")

    def test_rehearsal_state_is_keyed_by_ssh_port(self):
        result = self.run_first_deploy(
            "--host",
            "127.0.0.1",
            "--ssh-port",
            "2223",
            "--public-port",
            "19443",
            "--domain",
            "localhost",
            "--tls",
            "internal",
            "--repo",
            "git://127.0.0.1/HLMemo.git",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"state={self.state}/127.0.0.1-2223 ", result.stdout)
        self.assertIn("url=https://localhost:19443", result.stdout)
        self.assertIn("REHEARSAL MODE", result.stdout)

    def test_unpushed_head_is_a_blocker(self):
        (self.checkout / "extra").write_text("x\n")
        git("add", "extra", cwd=self.checkout)
        git("commit", "-q", "-m", "local only", cwd=self.checkout)
        result = self.run_first_deploy(
            "--host",
            "203.0.113.10",
            "--domain",
            "sslip",
            "--repo",
            "https://github.com/NeverGET/HLMemo.git",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("domain=hlm.203-0-113-10.sslip.io", result.stdout)
        self.assertIn("BLOCKER: HEAD", result.stdout)

    def test_acme_without_email_secrets(self):
        """Owner decision: ACME without an account email. Nothing may write an empty email."""
        result = self.run_first_deploy(
            "--host",
            "203.0.113.10",
            "--domain",
            "mcp.example.com",
            "--tls",
            "acme",
            "--repo",
            "https://github.com/NeverGET/HLMemo.git",
            "--secrets-only",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.state / "203.0.113.10"
        secrets = state / "secrets"
        self.assertEqual(state.stat().st_mode & 0o777, 0o700)
        self.assertEqual(secrets.stat().st_mode & 0o777, 0o700)
        for path in [*secrets.iterdir(), state / "admin.token", state / "registration.secret"]:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600, path)
        prod = (secrets / "prod.env").read_text().splitlines()
        self.assertIn("HLM_DOMAIN=mcp.example.com", prod)
        self.assertIn("HLM_TLS_MODE=acme", prod)
        self.assertFalse([line for line in prod if line.startswith("HLM_ACME_EMAIL")])
        for name in ("db.env", "app.env", "api.env"):
            self.assertNotIn("CHANGE_ME", (secrets / name).read_text())
        token = (state / "admin.token").read_text().strip()
        self.assertRegex(token, r"^[0-9a-f]{64}$")
        self.assertNotIn(token, result.stdout + result.stderr)
        # Idempotent: a second run reuses the same secrets.
        again = self.run_first_deploy(
            "--host",
            "203.0.113.10",
            "--domain",
            "mcp.example.com",
            "--repo",
            "https://github.com/NeverGET/HLMemo.git",
            "--secrets-only",
        )
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("reusing existing secrets", again.stdout)
        self.assertEqual((state / "admin.token").read_text().strip(), token)

    def test_caddyfile_has_no_email_directive(self):
        # An empty `email` global option is a Caddyfile parse error; ACME works without one.
        text = (REPO / "deploy" / "Caddyfile").read_text()
        self.assertNotRegex(text, r"(?m)^\s*email\b")
        self.assertIn("issuer acme", text)


if __name__ == "__main__":
    unittest.main()
