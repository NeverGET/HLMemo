"""Kernel lock and backup dotenv isolation checks; no live stack or real credentials."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class BackupLockTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("flock"), "flock is required")
    def test_backup_rejects_holder_and_reacquires_after_sigkill(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "backup"
            scripts = root / "scripts"
            data = root / "data"
            for path in (backup, scripts, data):
                path.mkdir()
            for name in ("backup.sh", "upload.py"):
                shutil.copy2(ROOT / "deploy/backup" / name, backup / name)
            (scripts / "common.sh").write_text(
                'dc() { printf "%s\\n" "$*" >> "$TEST_DUMP_CALLS"; printf "fake dump\\n"; }\n'
                'backup_env() { printf "S3_BUCKET=\\n"; }\n'
            )
            # A legacy mkdir lock and leftover flock file are harmless without a holder.
            (data / ".operation.lock").mkdir()
            lockfile = data / ".operation.flock"
            holder = subprocess.Popen(
                ["bash", "-c", 'exec 8>"$1"; flock -n 8; printf "LOCKED\\n"; read -r value',
                 "lock-holder", str(lockfile)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            marker = root / "dump-calls"
            environment = dict(os.environ, HLM_BACKUP_DIR=str(data), TEST_DUMP_CALLS=str(marker))
            command = ["bash", str(backup / "backup.sh"), "--pre-upgrade", "abcdef1234567"]
            try:
                self.assertEqual(holder.stdout.readline().strip(), "LOCKED")
                rejected = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn("Another backup/restore is active; stack remains running.", rejected.stderr)
                self.assertFalse(marker.exists(), "contending backup must not invoke dump")
                holder.kill()
                holder.wait(timeout=10)
                self.assertTrue(lockfile.exists(), "SIGKILL leaves the file, not the kernel lock")
                completed = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=10)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                dump = Path(completed.stdout.strip())
                self.assertEqual(dump.parent, data / "pre-upgrade")
                self.assertEqual(dump.read_text(), "fake dump\n")
                self.assertEqual(len(marker.read_text().splitlines()), 2)
            finally:
                if holder.poll() is None:
                    holder.kill()
                holder.communicate(timeout=10)


class BackupEnvironmentTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("docker"), "Docker Compose parser is required")
    def test_real_common_parser_exposes_only_backup_file_values(self):
        with tempfile.TemporaryDirectory() as directory:
            backup_file = Path(directory) / "backup.env"
            backup_file.write_text("S3_BUCKET=isolated-test\nAWS_ACCESS_KEY_ID='backup-$literal'\n")
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith(("HLM_", "BAKE_", "AWS_", "S3_"))}
            environment.update(
                HLM_ENV_FILE=str(ROOT / "deploy/.env.prod.example"),
                HLM_BACKUP_ENV_FILE=str(backup_file),
                BAKE_PROJECT="bake-astra",
                AWS_ACCESS_KEY_ID="host-must-not-win",
                AWS_SECRET_ACCESS_KEY="host-must-not-leak",
                HLM_ADMIN_TOKEN="host-admin-must-not-leak",
            )
            for name in ("APP", "API", "DB"):
                environment[f"HLM_{name}_ENV_FILE"] = str(ROOT / "deploy" / f"{name.lower()}.env.example")
            result = subprocess.run(
                ["bash", "-c", 'source "$1"; backup_env', "backup-env-test", str(ROOT / "deploy/scripts/common.sh")],
                env=environment, capture_output=True, text=True, timeout=30, check=True,
            )
            settings = dict(line.split("=", 1) for line in result.stdout.splitlines())
            self.assertEqual(settings, {"S3_BUCKET": "isolated-test", "AWS_ACCESS_KEY_ID": "backup-$literal"})


if __name__ == "__main__":
    unittest.main()
