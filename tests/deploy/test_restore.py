"""Restore ordering, fail-closed migrations, and repository backup path guards."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("flock"), "flock is required")
class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.log = self.base / "commands"
        self.schema = self.base / "schema"
        self.dump = self.base / "older.dump"
        self.dump.write_text("schema=old\n")
        self.schema.write_text("head")
        docker = self.bin / "docker"
        docker.write_text('''#!/usr/bin/env python3
import os, pathlib, sys
args = " ".join(sys.argv[1:])
data = sys.stdin.read()  # Every fake child reads stdin, including migrate and config.
with open(os.environ["TEST_CALLS"], "a") as log:
    log.write(args + "\\n")
if "config --environment" in args:
    print("BAKE_PROJECT=bake-astra")
elif "pg_dump" in args:
    print("schema=head")
elif "pg_restore --list" in args:
    assert data.startswith("schema=")
elif "pg_restore --username" in args:
    assert data == "schema=old\\n"
    pathlib.Path(os.environ["TEST_SCHEMA"]).write_text("old")
elif "run --rm --no-deps migrate" in args:
    if os.environ.get("TEST_FAIL_MIGRATE"):
        sys.exit(42)
    assert pathlib.Path(os.environ["TEST_SCHEMA"]).read_text() == "old"
    pathlib.Path(os.environ["TEST_SCHEMA"]).write_text("head")
elif "up -d" in args:
    assert pathlib.Path(os.environ["TEST_SCHEMA"]).read_text() == "head"
''')
        docker.chmod(0o755)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("HLM_", "BAKE_"))}
        self.env.update(PATH=f"{self.bin}:{os.environ['PATH']}",
                        BAKE_PROJECT="bake-astra", HLM_BACKUP_DIR=str(self.base / "backups"),
                        TEST_CALLS=str(self.log), TEST_SCHEMA=str(self.schema))
        empty = self.base / "empty.env"
        empty.touch()
        for name in ("HLM_ENV_FILE", "HLM_APP_ENV_FILE", "HLM_API_ENV_FILE", "HLM_DB_ENV_FILE",
                     "HLM_BACKUP_ENV_FILE"):
            self.env[name] = str(empty)

    def restore(self):
        return subprocess.run(["bash", str(ROOT / "deploy/backup/restore.sh"), str(self.dump), "--yes"],
                              env=self.env, capture_output=True, text=True, timeout=15)

    def test_restored_older_schema_migrates_before_starting_writers(self):
        result = self.restore()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Restore completed; stack healthy", result.stdout)
        calls = self.log.read_text()
        self.assertLess(calls.index("--single-transaction"), calls.index("run --rm --no-deps migrate"))
        self.assertLess(calls.index("run --rm --no-deps migrate"), calls.index("up -d --no-deps"))
        self.assertEqual(self.schema.read_text(), "head")

    def test_restore_migration_failure_leaves_writers_stopped(self):
        self.env["TEST_FAIL_MIGRATE"] = "1"
        result = self.restore()
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertIn("Restore failed; writers remain stopped", result.stderr)
        calls = self.log.read_text()
        self.assertNotIn("up -d", calls)
        self.assertEqual(calls.count("stop caddy api worker"), 2)
        self.assertEqual(self.schema.read_text(), "old")
        self.assertEqual(len(list((self.base / "backups/pre-restore").glob("*.dump.*"))), 1)

    def test_backup_directory_rejects_repository_and_symlink_destinations(self):
        link = self.base / "checkout-link"
        link.symlink_to(ROOT, target_is_directory=True)
        for destination in (ROOT, ROOT / "deploy/backup/data", link / "deploy/backup/data"):
            with self.subTest(destination=destination):
                self.env["HLM_BACKUP_DIR"] = str(destination)
                result = subprocess.run(
                    ["bash", str(ROOT / "deploy/backup/backup.sh"), "--pre-upgrade", "abcdef1234567"],
                    env=self.env, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn("Refusing backup directory inside repository", result.stderr)
                self.assertNotIn("pg_dump", self.log.read_text())

    def test_backup_directory_rejects_symlinked_tier(self):
        backup = self.base / "backups"
        backup.mkdir()
        (backup / "pre-upgrade").symlink_to(ROOT / "deploy", target_is_directory=True)
        result = subprocess.run(
            ["bash", str(ROOT / "deploy/backup/backup.sh"), "--pre-upgrade", "abcdef1234567"],
            env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("Refusing backup directory inside repository", result.stderr)
        self.assertNotIn("pg_dump", self.log.read_text())

    def test_restore_safety_override_rejects_repository_before_stopping_writers(self):
        link = self.base / "checkout-link"
        link.symlink_to(ROOT, target_is_directory=True)
        for destination in (ROOT / "deploy/backup/data", link / "deploy/backup/data"):
            with self.subTest(destination=destination):
                self.env["HLM_RESTORE_SAFETY_DIR"] = str(destination)
                result = self.restore()
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn("Refusing backup directory inside repository", result.stderr)
                self.assertNotIn("stop caddy", self.log.read_text())


if __name__ == "__main__":
    unittest.main()
