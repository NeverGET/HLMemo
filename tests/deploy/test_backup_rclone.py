"""The optional rclone copy of `upload.py` (an encrypted Google Drive remote); a fake rclone, no network."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FAKE = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["TEST_RCLONE_LOG"], "a") as log:
    log.write(json.dumps([sys.argv[1:], sorted(k for k in os.environ if k.startswith("RCLONE_"))]) + "\\n")
sys.exit(int(os.environ.get("TEST_RCLONE_EXIT", "0")))
"""


class RcloneUploadTests(unittest.TestCase):
    def run_upload(self, settings: str, names: list[str], exit_code: int = 0):  # noqa: ANN201
        root = Path(self.directory)
        commands = root / "bin"
        commands.mkdir(exist_ok=True)
        (commands / "rclone").write_text(FAKE)
        (commands / "rclone").chmod(0o755)
        log = root / "calls.jsonl"
        log.unlink(missing_ok=True)
        paths = []
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("dump\n")
            paths.append(str(path))
        environment = dict(
            os.environ,
            PATH=f"{commands}:{os.environ['PATH']}",
            TEST_RCLONE_LOG=str(log),
            TEST_RCLONE_EXIT=str(exit_code),
            RCLONE_PASSWORD_COMMAND="leak",  # never inherited
        )
        done = subprocess.run(
            [sys.executable, str(ROOT / "deploy/backup/upload.py"), *paths],
            input=settings,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return done, calls

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_daily_and_weekly_are_copied_then_pruned(self) -> None:
        settings = "RCLONE_REMOTE=crypt:\nRCLONE_CONFIG=/etc/x/rclone.conf\nRCLONE_KEEP_DAYS=10\n"
        done, calls = self.run_upload(settings, ["daily/a.dump", "weekly/w.dump"])
        self.assertEqual(done.returncode, 0, done.stderr)
        argv = [c[0] for c in calls]
        base = ["--config", "/etc/x/rclone.conf", "-q"]
        self.assertEqual(argv[0][:4] + argv[0][5:], [*base, "copyto", "crypt:/daily/a.dump"])
        self.assertEqual(argv[1][-1], "crypt:/weekly/w.dump")
        self.assertEqual(
            argv[2], [*base, "delete", "crypt:/daily", "--min-age", "10d", "--include", "*.dump"]
        )
        self.assertEqual(
            argv[3], [*base, "delete", "crypt:/weekly", "--min-age", "70d", "--include", "*.dump"]
        )
        self.assertTrue(all(env == [] for _argv, env in calls))  # no RCLONE_* from the caller

    def test_pre_upgrade_is_copied_and_never_pruned(self) -> None:
        done, calls = self.run_upload("RCLONE_REMOTE=crypt:\n", ["pre-upgrade/p.dump"])
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([c[0][3] for c in calls], ["copyto"])

    def test_failure_exits_nonzero_and_without_a_remote_nothing_runs(self) -> None:
        done, calls = self.run_upload("RCLONE_REMOTE=crypt:\n", ["daily/a.dump"], exit_code=3)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("rclone upload of a.dump failed (exit 3)", done.stderr)
        self.assertEqual(len(calls), 1)  # no prune after a failed copy
        done, calls = self.run_upload("S3_BUCKET=\n", ["daily/a.dump"])
        self.assertEqual((done.returncode, calls), (0, []))


if __name__ == "__main__":
    unittest.main()
