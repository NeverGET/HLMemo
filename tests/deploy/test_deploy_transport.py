"""Run the deploy observer and detached bootstrap against an isolated SSH double."""

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REVISION = "b" * 40

GIT = r"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["EVENTS"], "a") as stream:
    stream.write(json.dumps(args) + "\n")
if args[:2] == ["remote", "get-url"]:
    print("https://example.invalid/repo.git")
elif args[0] == "rev-parse":
    print("b" * 40)
elif args[0] == "show":
    assert args[1] == "b" * 40 + ":deploy/scripts/remote-deploy.sh"
    if os.environ["MODE"] == "missing-runner":
        sys.exit(128)
    print(Path(os.environ["TARGET_RUNNER"]).read_text())
"""

SSH = r"""#!/usr/bin/env bash
if [[ $MODE == blocked-ssh && ${@: -1} == *HLM_DEPLOY_STATUS* ]]; then exec sleep 30; fi
command() {
  if [[ $1 == -v && $2 == ${MISSING_COMMAND:-unused} ]]; then return 1; fi
  builtin command "$@"
}
export -f command
exec bash -c "${@: -1}"
"""

RUNNER = r"""#!/usr/bin/env bash
set -Eeuo pipefail
run_dir=$5
[[ $1 == bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb ]]
[[ $HLM_DEPLOY_PREPARED_REVISION == "$1" && $HLM_DEPLOY_LOCK_HELD == 0 ]]
[[ -z $(cat) ]]
printf 'TARGET-REF-RUNNER\n'
if [[ $MODE == sigkill ]]; then
  sleep 0.2
  kill -KILL $$
fi
if [[ $MODE == timeout || $MODE == blocked-ssh ]]; then
  sleep 30
fi
printf '0\n' > "$run_dir/status"
printf 'Deployment ready\n'
"""


class DeployTransportTest(unittest.TestCase):
    def prepare(self, mode="", missing=""):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "app/.git").mkdir(parents=True)
        binary = root / "bin"
        binary.mkdir()
        runner = root / "target-runner.sh"
        runner.write_text(RUNNER)
        programs = {
            "git": GIT,
            "ssh": SSH,
            "setsid": (
                "#!/usr/bin/env python3\n"
                "import os,sys\n"
                "if os.environ['MODE'] == 'setsid-fails': sys.exit(73)\n"
                "os.setsid()\n"
                "os.execvp(sys.argv[1],sys.argv[1:])\n"
            ),
        }
        for name, content in programs.items():
            path = binary / name
            path.write_text(content)
            path.chmod(0o700)
        env = dict(
            os.environ,
            PATH=f"{binary}:{os.environ['PATH']}",
            MODE=mode,
            MISSING_COMMAND=missing,
            EVENTS=str(root / "events"),
            TARGET_RUNNER=str(runner),
            HLM_REMOTE_DIR=str(root / "app"),
            HLM_REMOTE_ENV=str(root / "prod.env"),
            HLM_DEPLOY_POLL_SECONDS="0.02",
            HLM_DEPLOY_TIMEOUT_SECONDS="2" if mode in ("timeout", "blocked-ssh") else "10",
        )
        self.addCleanup(self.stop_runners, root)
        return root, env

    @staticmethod
    def stop_runners(root):
        for path in root.glob(".deploy-runs/*/pid"):
            pid = int(path.read_text())
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def deploy(self, mode="", missing=""):
        root, env = self.prepare(mode, missing)
        started = time.monotonic()
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "deploy/scripts/deploy.sh"),
                "local",
                "requested-ref",
                "https://example.invalid/repo.git",
            ],
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
        )
        return root, result, time.monotonic() - started

    def test_runner_is_extracted_from_resolved_target_ref(self):
        root, result, _ = self.deploy()
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertIn("TARGET-REF-RUNNER", result.stdout)
        self.assertIn("Deployment ready", result.stdout)
        events = [json.loads(line) for line in (root / "events").read_text().splitlines()]
        fetch = events.index(["fetch", "--prune", "origin", "requested-ref"])
        show = events.index(["show", f"{REVISION}:deploy/scripts/remote-deploy.sh"])
        self.assertLess(fetch, show)
        self.assertEqual(RUNNER + "\n", next(root.glob(".deploy-runs/*/deploy.sh")).read_text())

    def test_pre_runner_ref_fails_before_checkout_or_deploy_mutation(self):
        root, result, _ = self.deploy("missing-runner")
        self.assertEqual(1, result.returncode, result.stderr + result.stdout)
        self.assertIn("predates the detached deployment runner", result.stdout)
        events = [json.loads(line) for line in (root / "events").read_text().splitlines()]
        self.assertFalse(any(row[0] == "checkout" for row in events))
        self.assertNotIn("TARGET-REF-RUNNER", result.stdout)
        self.assertFalse((root / "current-ref").exists())
        self.assertFalse((root / ".deploy-managed").exists())
        self.assertEqual("1\n", next(root.glob(".deploy-runs/*/status")).read_text())

    def test_sigkill_runner_is_detected_without_final_status(self):
        root, result, elapsed = self.deploy("sigkill")
        self.assertEqual(1, result.returncode, result.stderr + result.stdout)
        self.assertIn("gone without final status", result.stderr)
        self.assertLess(elapsed, 8)
        self.assertTrue(next(root.glob(".deploy-runs/*/heartbeat")).read_text().strip())
        self.assertFalse(list(root.glob(".deploy-runs/*/status")))

    def test_observer_timeout_bounds_live_runner_and_stalled_ssh(self):
        for mode in ("timeout", "blocked-ssh"):
            with self.subTest(mode=mode):
                root, result, elapsed = self.deploy(mode)
                self.assertEqual(124, result.returncode, result.stderr + result.stdout)
                self.assertIn("timed out after 2s", result.stderr)
                self.assertLess(elapsed, 6)
                pid = int(next(root.glob(".deploy-runs/*/pid")).read_text())
                os.kill(pid, 0)  # Timing out the client must leave the remote job alive.

    def test_real_git_initial_clone_runs_legacy_runner_with_its_own_lock(self):
        root, env = self.prepare()
        (root / "bin/git").unlink()
        shutil.rmtree(root / "app")
        source = root / "source"
        runner = source / "deploy/scripts/remote-deploy.sh"
        runner.parent.mkdir(parents=True)
        runner.write_text(
            "#!/usr/bin/env bash\nset -Eeuo pipefail\n"
            'exec 9>"$(dirname "$3")/.deploy.lock"\n'
            "flock -n 9 || { echo legacy-lock-conflict; exit 9; }\n"
            'cd "$3"\n'
            "[[ -z $(git status --porcelain --untracked-files=no) ]]\n"
            'test -f "$(dirname "$3")/.deploy-managed"\n'
            'git fetch origin "$1" </dev/null\n'
            'git checkout --detach "$1" </dev/null\n'
            'printf "0\\n" > "$5/status"\n'
            'echo "Deployment ready: legacy target runner"\n'
        )
        identity = dict(
            os.environ,
            GIT_AUTHOR_NAME="Deployment test",
            GIT_AUTHOR_EMAIL="test@example.invalid",
            GIT_COMMITTER_NAME="Deployment test",
            GIT_COMMITTER_EMAIL="test@example.invalid",
        )

        def git(*args):
            return subprocess.check_output(
                ["git", "-C", str(source), *args],
                env=identity,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()

        git("init", "--quiet")
        git("add", "deploy/scripts/remote-deploy.sh")
        tree = git("write-tree")
        # Build an isolated fixture object; never commit or write the project repo.
        revision = git("commit-tree", tree, "-m", "isolated deployment fixture")
        git("update-ref", "refs/heads/main", revision)
        git("symbolic-ref", "HEAD", "refs/heads/main")
        result = subprocess.run(
            ["bash", str(ROOT / "deploy/scripts/deploy.sh"), "local", "main", str(source)],
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("Deployment ready: legacy target runner", result.stdout)
        self.assertEqual(runner.read_text(), next(root.glob(".deploy-runs/*/deploy.sh")).read_text())
        self.assertFalse((root / "current-ref").exists())

    def test_each_required_detachment_command_is_checked(self):
        for command in ("nohup", "setsid", "bash"):
            with self.subTest(command=command):
                root, result, _ = self.deploy(missing=command)
                self.assertEqual(1, result.returncode, result.stderr)
                self.assertIn(f"Missing required remote command: {command}", result.stderr)
                self.assertFalse((root / "events").exists())

    def test_setsid_launch_failure_does_not_poll_forever(self):
        _, result, elapsed = self.deploy("setsid-fails")
        self.assertEqual(1, result.returncode, result.stderr + result.stdout)
        self.assertIn("runner failed to start", result.stderr)
        self.assertLess(elapsed, 8)


if __name__ == "__main__":
    unittest.main()
