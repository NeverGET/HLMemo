"""Exercise real deploy/backup shell control flow with isolated transport/runtime doubles."""
import json
import os
from pathlib import Path
from unittest.mock import patch
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = "a" * 40
NEXT = "b" * 40

DOCKER = r'''#!/usr/bin/env python3
import json, os, signal, sys
from pathlib import Path
args=sys.argv[1:]
with open(os.environ["EVENTS"], "a") as f: f.write(json.dumps(["docker", *args])+"\n")
fail=os.environ.get("FAIL", "")
if "-f" in args and ".rollback-compose." in args[args.index("-f")+1]:
    captured=json.loads(Path(args[args.index("-f")+1]).read_text())
    assert captured["services"]["api"]["environment"]["TOKEN"] == "literal$$VAR"
if args[0] == "ps": print("old-container")
elif args[0] == "inspect": print("sha256:old-image")
elif "config" in args:
    print(json.dumps({"name":"bake-astra", "services":{s:{"image":"mutable:prod", "environment":{"TOKEN":"literal$$VAR"}} for s in ("api","worker","db","caddy")}}))
elif "ps" in args: print("db-container")
elif "exec" in args:
    if "pg_dump" in args[-1]:
        if fail == "dump": sys.exit(7)
        print("valid-snapshot")
    elif "dropdb" in args[-1]:
        assert sys.stdin.read() == "valid-snapshot\n"
    elif "pg_restore" in args: sys.stdin.read()
elif "stop" in args and fail == "term":
    marker=Path(os.environ["EVENTS"]+".signalled")
    if not marker.exists():
        marker.touch()
        os.kill(os.getppid(), signal.SIGTERM)
elif "stop" in args and fail in ("stop", "stop-always"):
    if fail == "stop-always": sys.exit(8)
    marker=Path(os.environ["EVENTS"]+".stopped")
    if not marker.exists(): marker.touch(); sys.exit(8)
elif "run" in args and fail == "term-migration": os.kill(os.getppid(), signal.SIGTERM)
elif "run" in args and fail == "migration": sys.exit(9)
elif "up" in args and "--no-deps" not in args and "db" not in args and fail == "health": sys.exit(10)
'''

GIT = r'''#!/usr/bin/env python3
import json, os, sys
args=sys.argv[1:]
with open(os.environ["EVENTS"], "a") as f: f.write(json.dumps(["git", *args])+"\n")
if args[:2] == ["remote", "get-url"]: print("https://example.invalid/repo.git")
elif args[0] == "rev-parse": print("b"*40 if "FETCH_HEAD^{commit}" in args else "a"*40)
'''

COMMON = r'''set -euo pipefail
DEPLOY_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
COMPOSE_PROJECT=bake-astra
export COMPOSE_PROJECT
dc() { docker compose -p "$COMPOSE_PROJECT" -f "$DEPLOY_DIR/compose.prod.yaml" "$@"; }
env_value() { [[ $1 != HLM_DOMAIN ]] || echo localhost; }
backup_value() { echo ""; }
backup_env() { [[ ${FAIL:-} != upload ]] || echo S3_BUCKET=simulated-upload; }
'''


class DeployRecoveryTest(unittest.TestCase):
    def run_deploy(self, failure):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        app = root / "app"
        (app / ".git").mkdir(parents=True)
        shutil.copytree(ROOT / "deploy", app / "deploy", ignore=shutil.ignore_patterns("data", ".terraform"))
        (app / "deploy/scripts/common.sh").write_text(COMMON)
        (root / "current-ref").write_text(PREVIOUS + "\n")
        (root / "prod.env").write_text("HLM_DOMAIN=localhost\n")
        binary = root / "bin"
        binary.mkdir()
        programs = {"docker": DOCKER, "git": GIT,
                    "ssh": '#!/usr/bin/env bash\nexec bash -c "${@: -1}"\n',
                    "curl": '#!/usr/bin/env bash\necho ready\n',
                    "aws": '#!/usr/bin/env bash\necho simulated-upload-failure >&2\nexit 42\n'}
        for name, content in programs.items():
            path = binary / name
            path.write_text(content)
            path.chmod(0o700)
        events = root / "events"
        env = dict(os.environ, PATH=f"{binary}:{os.environ['PATH']}", FAIL=failure,
                   EVENTS=str(events), HLM_REMOTE_DIR=str(app), HLM_REMOTE_ENV=str(root / "prod.env"),
                   HLM_BACKUP_DIR=str(root / "backups"))
        result = subprocess.run(["bash", str(ROOT / "deploy/scripts/deploy.sh"), "local", "next",
                                 "https://example.invalid/repo.git"], env=env, text=True, capture_output=True)
        rows = [json.loads(line) for line in events.read_text().splitlines()]
        return root, result, rows

    def test_real_compose_rollback_roundtrip_preserves_dollar_literals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / "service.env"
            env_file.write_text("TOKEN='x$FOO'\n")
            compose_file = root / "compose.json"
            compose_file.write_text(json.dumps({
                "name": "bake-astra",
                "services": {name: {"image": "busybox", "env_file": [str(env_file)],
                    "healthcheck": {"test": ["CMD-SHELL", "echo $$HOME"]}}
                    for name in ("db", "api", "worker", "caddy")},
            }))
            command = ["docker", "compose", "-f", str(compose_file), "config", "--format", "json"]
            first = json.loads(subprocess.check_output(command))
            self.assertEqual("x$$FOO", first["services"]["db"]["environment"]["TOKEN"])
            self.assertEqual(["CMD-SHELL", "echo $$HOME"], first["services"]["db"]["healthcheck"]["test"])
            compose_file.write_text(json.dumps(first))
            # Run the actual deployment snapshot transformation, mocking only the
            # running-container lookup; Compose parsing above/below is real.
            script = (ROOT / "deploy/scripts/deploy.sh").read_text()
            transform = script.split("<<'PYCONFIG'\n", 1)[1].split("\nPYCONFIG", 1)[0]
            with patch("sys.argv", ["snapshot", str(compose_file)]), patch("subprocess.check_output", return_value=""):
                exec(compile(transform, "deploy-rollback-snapshot", "exec"), {})
            second = json.loads(subprocess.check_output(command))
            self.assertEqual(first, second)

    def test_upload_failure_is_nonfatal_and_dump_precedes_stop(self):
        root, result, rows = self.run_deploy("upload")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("WARNING: pre-upgrade S3 upload failed", result.stderr)
        self.assertEqual(NEXT, (root / "current-ref").read_text().strip())
        self.assertEqual(PREVIOUS, (root / "previous-ref").read_text().strip())
        dump = Path((root / "previous-dump").read_text().strip())
        self.assertEqual("pre-upgrade", dump.parent.name)
        self.assertEqual("valid-snapshot\n", dump.read_text())
        snapshot = next(i for i, row in enumerate(rows) if "pg_dump" in row[-1])
        stop = next(i for i, row in enumerate(rows) if "stop" in row)
        self.assertLess(snapshot, stop)

    def test_dump_failure_never_stops_writers(self):
        root, result, rows = self.run_deploy("dump")
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(any("stop" in row for row in rows))
        self.assertEqual(PREVIOUS, (root / "current-ref").read_text().strip())
        self.assertIn("previous stack remains running", result.stderr)

    def test_migration_and_health_failure_restore_dump_then_previous_stack(self):
        for failure in ("migration", "health", "term-migration"):
            with self.subTest(failure=failure):
                root, result, rows = self.run_deploy(failure)
                self.assertNotEqual(0, result.returncode)
                if failure == "term-migration":
                    self.assertEqual(143, result.returncode)
                self.assertIn("Previous stack restored", result.stderr)
                self.assertEqual(PREVIOUS, (root / "current-ref").read_text().strip())
                restores = [i for i, row in enumerate(rows) if "dropdb" in row[-1]]
                self.assertEqual(1, len(restores))
                restarts = [i for i, row in enumerate(rows) if "up" in row and "--no-deps" in row and "api" in row]
                self.assertEqual(1, len(restarts))
                self.assertLess(restores[0], restarts[0])
                self.assertEqual(1, sum("run" in row and "migrate" in row for row in rows))
                self.assertFalse(list(root.glob(".rollback-compose.*")))

    def test_sigterm_after_stop_recovers_previous_stack(self):
        root, result, rows = self.run_deploy("term")
        self.assertEqual(143, result.returncode, result.stderr)
        self.assertIn("Previous stack restored", result.stderr)
        self.assertTrue(any("up" in row and "--no-deps" in row and "api" in row for row in rows))
        self.assertFalse(any("dropdb" in row[-1] for row in rows))
        self.assertEqual(PREVIOUS, (root / "current-ref").read_text().strip())

    def test_partial_stop_failure_restarts_without_database_restore(self):
        for failure in ("stop", "stop-always"):
            with self.subTest(failure=failure):
                root, result, rows = self.run_deploy(failure)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("Previous stack restored", result.stderr)
                self.assertFalse(any("dropdb" in row[-1] for row in rows))
                self.assertTrue(any("up" in row and "--no-deps" in row and "api" in row for row in rows))


if __name__ == "__main__":
    unittest.main()
