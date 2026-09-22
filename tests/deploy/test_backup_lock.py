"""Kernel lock and backup dotenv isolation checks; no live stack or real credentials."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class BackupLockTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("flock"), "flock is required")
    def test_same_second_daily_backups_have_distinct_upload_keys_and_keep_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup, scripts, commands, data = (root / name for name in ("backup", "scripts", "bin", "data"))
            for path in (backup, scripts, commands, data):
                path.mkdir()
            for name in ("backup.sh", "retention.py", "upload.py"):
                shutil.copy2(ROOT / "deploy/backup" / name, backup / name)
            (scripts / "common.sh").write_text(
                'dc() { printf "%s\\n" "$TEST_PAYLOAD"; }\n'
                'backup_env() { printf "S3_BUCKET=fake-bucket\\n"; }\n'
                'backup_dir() { printf "%s\\n" "$HLM_BACKUP_DIR"; }\n'
                'backup_path() { printf "%s\\n" "$1"; }\n'
            )
            frozen_date = commands / "date"
            frozen_date.write_text('#!/bin/sh\nprintf "2026-09-22T100000Z\\n"\n')
            frozen_date.chmod(0o755)
            aws = commands / "aws"
            aws.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
assert sys.argv[1:3] == ["s3", "cp"]
with open(os.environ["TEST_UPLOAD_LOG"], "a") as log:
    log.write(json.dumps([sys.argv[4], pathlib.Path(sys.argv[3]).read_text()]) + "\\n")
""")
            aws.chmod(0o755)
            uploads = root / "uploads.jsonl"
            environment = dict(
                os.environ,
                PATH=f"{commands}:{os.environ['PATH']}",
                HLM_BACKUP_DIR=str(data),
                TEST_UPLOAD_LOG=str(uploads),
            )
            paths = []
            for payload in ("first snapshot", "second snapshot"):
                completed = subprocess.run(
                    ["bash", str(backup / "backup.sh")],
                    env=dict(environment, TEST_PAYLOAD=payload),
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                paths.append(Path(completed.stdout.strip()))
            self.assertNotEqual(paths[0], paths[1])
            uploaded = [json.loads(line) for line in uploads.read_text().splitlines()]
            daily = [entry for entry in uploaded if "/daily/" in entry[0]]
            self.assertEqual(
                daily,
                [
                    [f"s3://fake-bucket/hlmemo/daily/{paths[0].name}", "first snapshot\n"],
                    [f"s3://fake-bucket/hlmemo/daily/{paths[1].name}", "second snapshot\n"],
                ],
            )
            self.assertEqual(list((data / "daily").glob("*.dump")), [paths[1]])
            self.assertEqual(paths[1].read_text(), "second snapshot\n")
            self.assertEqual((data / "weekly/hlmemo-2026-W39.dump").read_text(), "second snapshot\n")

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
                'backup_dir() { printf "%s\\n" "$HLM_BACKUP_DIR"; }\n'
                'backup_path() { printf "%s\\n" "$1"; }\n'
            )
            # A legacy mkdir lock and leftover flock file are harmless without a holder.
            (data / ".operation.lock").mkdir()
            lockfile = data / ".operation.flock"
            holder = subprocess.Popen(
                [
                    "bash",
                    "-c",
                    'exec 8>"$1"; flock -n 8; printf "LOCKED\\n"; read -r value',
                    "lock-holder",
                    str(lockfile),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            marker = root / "dump-calls"
            environment = dict(os.environ, HLM_BACKUP_DIR=str(data), TEST_DUMP_CALLS=str(marker))
            command = ["bash", str(backup / "backup.sh"), "--pre-upgrade", "abcdef1234567"]
            try:
                self.assertEqual(holder.stdout.readline().strip(), "LOCKED")
                rejected = subprocess.run(
                    command, env=environment, capture_output=True, text=True, timeout=10
                )
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn("Another backup/restore is active; stack remains running.", rejected.stderr)
                self.assertFalse(marker.exists(), "contending backup must not invoke dump")
                holder.kill()
                holder.wait(timeout=10)
                self.assertTrue(lockfile.exists(), "SIGKILL leaves the file, not the kernel lock")
                completed = subprocess.run(
                    command, env=environment, capture_output=True, text=True, timeout=10
                )
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
            environment = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("HLM_", "BAKE_", "AWS_", "S3_"))
            }
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
                [
                    "bash",
                    "-c",
                    'source "$1"; backup_env',
                    "backup-env-test",
                    str(ROOT / "deploy/scripts/common.sh"),
                ],
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            settings = dict(line.split("=", 1) for line in result.stdout.splitlines())
            self.assertEqual(settings, {"S3_BUCKET": "isolated-test", "AWS_ACCESS_KEY_ID": "backup-$literal"})

    @unittest.skipUnless(shutil.which("docker"), "Docker Compose parser is required")
    def test_empty_and_comment_only_backup_env_are_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            backup_file = Path(directory) / "backup.env"
            environment = {
                key: value for key, value in os.environ.items() if not key.startswith(("HLM_", "BAKE_"))
            }
            environment.update(
                HLM_ENV_FILE=str(ROOT / "deploy/.env.prod.example"),
                HLM_BACKUP_ENV_FILE=str(backup_file),
                BAKE_PROJECT="bake-astra",
            )
            for name in ("APP", "API", "DB"):
                environment[f"HLM_{name}_ENV_FILE"] = str(ROOT / "deploy" / f"{name.lower()}.env.example")
            for content in ("", "# S3 uploads disabled\n"):
                with self.subTest(content=content):
                    backup_file.write_text(content)
                    result = subprocess.run(
                        [
                            "bash",
                            "-c",
                            'source "$1"; backup_env; backup_value S3_BUCKET; backup_dir',
                            "backup-empty-test",
                            str(ROOT / "deploy/scripts/common.sh"),
                        ],
                        env=environment,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, f"\n{Path('/var/backups/hlmemo').resolve()}\n")


if __name__ == "__main__":
    unittest.main()
