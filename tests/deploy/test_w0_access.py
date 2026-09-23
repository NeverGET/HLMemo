"""W0a deploy tooling (D-061) under the fake ssh/docker judge harness (D-037b): no real host.

G-W0-7 `test_env_migration_idempotent`: `migrate_env_w0` (remote-deploy.sh) run twice.
G-W0-8 `test_token_never_in_argv_or_logs`: minted tokens travel through stdout/env only.
Plus `hlm_ops.sh` quoting (printf %q, ssh -n, closed stdin) and the post-cutover route checks.
"""

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_deploy_recovery as harness  # noqa: E402  (module import: its tests are not re-collected)

COMMON, NEXT, PREVIOUS = harness.COMMON, harness.NEXT, harness.PREVIOUS

ROOT = Path(__file__).resolve().parents[2]
ADMIN_SECRET = "adm1n5ecret" + "0" * 53
REGISTRATION_SECRET = "reg15tration" + "1" * 52
CURSOR_SECRET = "curs0r" + "2" * 58
API_ENV = (
    "# API ONLY\n"
    f"HLM_ADMIN_TOKEN={ADMIN_SECRET}\n"
    f"export HLM_REGISTRATION_SECRET={REGISTRATION_SECRET}\n"
    f"HLM_CURSOR_SECRET={CURSOR_SECRET}\n"
    "HLM_TRUSTED_PROXY_IPS=172.30.39.0/24\n"
)
APP_ENV = "HLM_DB_DSN=postgresql://hlm:pw@db:5432/hlm\nHLM_PROFILE=openrouter\n"
SPLIT_COMMON = (
    COMMON
    + 'HLM_APP_ENV_FILE="$(dirname "$HLM_ENV_FILE")/app.env"\n'
    + 'HLM_API_ENV_FILE="$(dirname "$HLM_ENV_FILE")/api.env"\n'
    + "export HLM_APP_ENV_FILE HLM_API_ENV_FILE\n"
)


def reset_current(root: Path) -> None:
    """Point both the atomic state and the legacy marker back at PREVIOUS (harness re-runs)."""
    state_path = root / "release-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    state["current_ref"] = PREVIOUS
    state_path.write_text(json.dumps(state))
    (root / "current-ref").write_text(PREVIOUS + "\n")


class W0DeployTest(unittest.TestCase):
    """The recovery harness's fake docker/git/ssh/curl/python3 doubles, W0a scenarios only."""

    prepare_deploy = harness.DeployRecoveryTest.prepare_deploy

    def prepare_split(self, failure, **extra_env):
        root, env = self.prepare_deploy(failure)
        (root / "app/deploy/scripts/common.sh").write_text(SPLIT_COMMON)
        (root / "prod.env").write_text(
            f"HLM_DOMAIN=localhost\nHLM_REGISTRATION_SECRET={REGISTRATION_SECRET}\n"
        )
        for name, content in (("api.env", API_ENV), ("app.env", APP_ENV)):
            (root / name).write_text(content)
            (root / name).chmod(0o600)
        env.update(extra_env)
        return root, env

    def deploy(self, root, env, *flags):
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "deploy/scripts/deploy.sh"),
                *flags,
                "local",
                "next",
                "https://example.invalid/repo.git",
            ],
            env=env,
            text=True,
            capture_output=True,
            timeout=60,
        )
        logs = "".join(p.read_text() for p in root.glob(".deploy-runs/*/log"))
        return result, result.stdout + result.stderr + logs

    def test_env_migration_idempotent(self):
        """G-W0-7: env files correct, a backup exists, no secret in stdout or logs, twice."""
        root, env = self.prepare_split("")
        first, output = self.deploy(root, env)
        self.assertEqual(0, first.returncode, output)
        api = (root / "api.env").read_text()
        self.assertNotIn("HLM_ADMIN_TOKEN", api)
        self.assertNotIn("HLM_REGISTRATION_SECRET", api)
        self.assertIn(f"HLM_CURSOR_SECRET={CURSOR_SECRET}\n", api)
        self.assertIn("HLM_TRUSTED_PROXY_IPS=172.30.39.0/24\n", api)
        self.assertEqual(stat.S_IMODE((root / "api.env").stat().st_mode), 0o600)
        self.assertEqual((root / "app.env").read_text(), APP_ENV)  # nothing to migrate: untouched
        self.assertNotIn("HLM_REGISTRATION_SECRET", (root / "prod.env").read_text())
        backups = sorted(p.name for p in root.glob("*.pre-w0-*"))
        self.assertEqual(2, len(backups), backups)  # api.env + prod.env, not app.env
        api_backup = next(root.glob("api.env.pre-w0-*"))
        self.assertEqual(API_ENV, api_backup.read_text())
        self.assertEqual(stat.S_IMODE(api_backup.stat().st_mode), 0o600)
        self.assertIn("migrate_env_w0: api.env: removed HLM_ADMIN_TOKEN,HLM_REGISTRATION_SECRET", output)
        self.assertIn("migrate_env_w0: app.env: already migrated", output)
        for secret in (ADMIN_SECRET, REGISTRATION_SECRET, CURSOR_SECRET):
            self.assertNotIn(secret, output)
        # Second run: already migrated, no new backup, files unchanged.
        reset_current(root)  # the harness only verifies PREVIOUS as an existing commit
        before = {n: (root / n).read_text() for n in ("api.env", "app.env")}
        second, output = self.deploy(root, env)
        self.assertEqual(0, second.returncode, output)
        self.assertEqual(backups, sorted(p.name for p in root.glob("*.pre-w0-*")))
        self.assertEqual(before["api.env"], (root / "api.env").read_text())
        self.assertEqual(before["app.env"], (root / "app.env").read_text())
        self.assertIn("migrate_env_w0: api.env: already migrated", output)
        self.assertIn("migrate_env_w0: done (0 file(s) changed)", output)
        for secret in (ADMIN_SECRET, REGISTRATION_SECRET, CURSOR_SECRET):
            self.assertNotIn(secret, output)

    def test_failed_deployment_restores_migrated_env(self):
        """The previous release must never restart with open registration after a failed deploy."""
        for failure in ("migration", "health", "routes-internal"):
            with self.subTest(failure=failure):
                root, env = self.prepare_split(failure)
                result, output = self.deploy(root, env)
                self.assertNotEqual(0, result.returncode, output)
                self.assertIn("Previous stack restored", output)
                self.assertEqual(API_ENV, (root / "api.env").read_text())
                self.assertIn("migrate_env_w0: restored api.env", output)
                self.assertEqual(PREVIOUS, (root / "current-ref").read_text().strip())
                self.assertNotIn(ADMIN_SECRET, output)

    def test_route_checks_run_after_cutover(self):
        root, env = self.prepare_split("")
        result, output = self.deploy(root, env)
        self.assertEqual(0, result.returncode, output)
        rows = [json.loads(line) for line in (root / "events").read_text().splitlines()]
        internal = [r for r in rows if r[0] == "docker" and "--routes" in r]
        self.assertEqual(1, len(internal))
        self.assertIn("--mint-ops", internal[0])
        self.assertIn("http://127.0.0.1:8765", internal[0])
        public = (root / "events.routes-argv").read_text()
        self.assertIn("--routes --base https://localhost", public)
        publish = next(i for i, r in enumerate(rows) if r[0] == "docker" and "--routes" in r)
        mint = [i for i, r in enumerate(rows) if "hlmemo.ops" in r]
        self.assertTrue(mint and mint[0] > publish, "public mint happens after the internal check")

    def test_public_route_failure_keeps_new_stack_without_db_rollback(self):
        root, env = self.prepare_split("routes-public")
        result, output = self.deploy(root, env)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Public route verification failed", output)
        self.assertEqual(NEXT, (root / "current-ref").read_text().strip())
        rows = [json.loads(line) for line in (root / "events").read_text().splitlines()]
        self.assertFalse(any(".rollback-compose." in " ".join(r) and "up" in r for r in rows))
        self.assertFalse(any("dropdb" in " ".join(r) for r in rows))

    def test_token_never_in_argv_or_logs(self):
        """G-W0-8: the minted route-check token reaches the checker via env, never argv or logs."""
        token = "hlm_" + "Zq9" * 14 + "x"
        root, env = self.prepare_split("", ROUTES_TOKEN=token)
        result, output = self.deploy(root, env)
        self.assertEqual(0, result.returncode, output)
        self.assertEqual(token, (root / "events.routes-token").read_text().strip())
        self.assertNotIn(token, (root / "events.routes-argv").read_text())
        self.assertNotIn(token, (root / "events").read_text())  # every docker/git argv
        self.assertNotIn(token, output)

    # ------------------------------------------------------------------ accepted Compose change

    def prepare_compose_change(self, failure=""):
        root, env = self.prepare_split(failure, COMPOSE_CHANGE="1")
        # The previous release's own model (the fake git serves it for the previous SHA).
        (root / "app/deploy/compose.previous.yaml").write_text("# previous release model\nname: old\n")
        digest = hashlib.sha256((root / "app/deploy/compose.prod.yaml").read_bytes()).hexdigest()
        return root, env, f"--accept-compose-change={digest}"

    @staticmethod
    def rows(root):
        return [json.loads(line) for line in (root / "events").read_text().splitlines()]

    def test_compose_change_accepted_happy_path(self):
        root, env, accept = self.prepare_compose_change()
        result, output = self.deploy(root, env, accept)
        self.assertEqual(0, result.returncode, output)
        self.assertIn("Compose model change accepted: sha256 " + accept.split("=")[1], output)
        # Recovery would run the PREVIOUS model, rendered before anything stopped.
        self.assertIn("previous release model", (root / "events.previous-model").read_text())
        rows = self.rows(root)
        stop = next(i for i, r in enumerate(rows) if "stop" in r)
        dumps = [i for i, r in enumerate(rows) if "pg_dump" in r[-1]]
        migrate = next(i for i, r in enumerate(rows) if "run" in r and "migrate" in r)
        self.assertEqual(2, len(dumps))
        self.assertTrue(
            dumps[0] < stop < dumps[1] < migrate, "live dump, stop writers, quiesced dump, migrate"
        )
        # Markers move together, only on success.
        self.assertEqual(NEXT, (root / "current-ref").read_text().strip())
        self.assertEqual(PREVIOUS, (root / "previous-ref").read_text().strip())
        dump = Path((root / "previous-dump").read_text().strip())
        self.assertTrue(dump.is_file())
        self.assertIn(f"Quiesced pre-upgrade dump: {dump}", output)
        self.assertEqual(1, len(list((root / "backups/pre-upgrade").glob("*.dump"))), "live dump superseded")
        self.assertIn("Device inventory after cutover", output)
        self.assertIn("g7-mac", output)
        # Re-run (idempotent): same acknowledgement, env already migrated, same result.
        reset_current(root)  # the harness only verifies PREVIOUS as an existing commit
        backups = sorted(p.name for p in root.glob("*.pre-w0-*"))
        again, output = self.deploy(root, env, accept)
        self.assertEqual(0, again.returncode, output)
        self.assertIn("migrate_env_w0: done (0 file(s) changed)", output)
        self.assertEqual(backups, sorted(p.name for p in root.glob("*.pre-w0-*")))
        self.assertEqual(NEXT, (root / "current-ref").read_text().strip())
        self.assertEqual(PREVIOUS, (root / "previous-ref").read_text().strip())

    def test_compose_change_missing_or_wrong_hash_refused_before_stopping_anything(self):
        for flags in ((), ("--accept-compose-change=" + "0" * 64,)):
            with self.subTest(flags=flags):
                root, env, _ = self.prepare_compose_change()
                result, output = self.deploy(root, env, *flags)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("refusing" if not flags else "does not match", output)
                rows = self.rows(root)
                self.assertFalse(any("build" in r or "stop" in r or "pg_dump" in r[-1] for r in rows))
                self.assertEqual(API_ENV, (root / "api.env").read_text(), "env untouched")
                self.assertEqual(PREVIOUS, (root / "current-ref").read_text().strip())
                self.assertEqual("c" * 40, (root / "previous-ref").read_text().strip())
        result = subprocess.run(
            ["bash", str(ROOT / "deploy/scripts/deploy.sh"), "--accept-compose-change=abc", "local", "next"],
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(64, result.returncode)

    def test_compose_change_migration_failure_starts_previous_image_with_restored_env(self):
        root, env, accept = self.prepare_compose_change("migration")
        result, output = self.deploy(root, env, accept)
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn("Rollback image verified: sha256:old-image (hlmemo:" + PREVIOUS + ")", output)
        self.assertIn("Previous stack restored", output)
        self.assertEqual("sha256:old-image", (root / "events.running-image").read_text())
        self.assertEqual(API_ENV, (root / "api.env").read_text(), "retired secrets restored")
        rows = self.rows(root)
        up = [r for r in rows if "up" in r and "api" in r]
        self.assertIn(".rollback-compose.", " ".join(up[-1]), "recovery used the captured previous model")
        # Markers untouched on failure.
        self.assertEqual(PREVIOUS, (root / "current-ref").read_text().strip())
        self.assertEqual("c" * 40, (root / "previous-ref").read_text().strip())
        self.assertEqual("older-marker.dump", (root / "previous-dump").read_text().strip())
        self.assertIn(f"HLM_IMAGE=hlmemo:{PREVIOUS}\n", (root / "prod.env").read_text())

    # ------------------------------------------------------------------ rollback after cutover (Sol 36 H)

    def cutover(self, api_env=API_ENV, prod_env=None):
        root, env, accept = self.prepare_compose_change()
        (root / "api.env").write_text(api_env)
        if prod_env is not None:
            (root / "prod.env").write_text(prod_env)
        result, output = self.deploy(root, env, accept)
        self.assertEqual(0, result.returncode, output)
        env = {k: v for k, v in env.items() if k != "COMPOSE_CHANGE"}
        return root, env

    def remote(self, root, env, flag):
        before = len(self.rows(root))
        result = subprocess.run(
            ["bash", str(ROOT / "deploy/scripts/deploy.sh"), flag, "local"],
            env=env,
            text=True,
            capture_output=True,
            timeout=60,
        )
        logs = "".join(p.read_text() for p in sorted(root.glob(".deploy-runs/*/log")))
        return result, result.stdout + result.stderr + logs, self.rows(root)[before:]

    @staticmethod
    def state(root):
        return json.loads((root / "release-state.json").read_text())

    def test_rollback_between_w0_releases_restores_image_and_dump(self):
        root, env = self.cutover()
        state = self.state(root)
        self.assertEqual((state["current_ref"], state["previous_ref"]), (NEXT, PREVIOUS))
        self.assertEqual(state["previous_image_id"], "sha256:old-image")
        self.assertEqual(2, len(state["retired_backups"]))  # api.env + prod.env backups
        result, output, rows = self.remote(root, env, "--rollback")
        self.assertEqual(0, result.returncode, output)
        self.assertIn("Rollback validated", output)
        self.assertIn("Rollback complete", output)
        self.assertEqual("sha256:old-image", (root / "events.running-image").read_text())
        self.assertIn(f"HLM_IMAGE=hlmemo:{PREVIOUS}\n", (root / "prod.env").read_text())
        stop = next(i for i, r in enumerate(rows) if "stop" in r)
        restore = next(i for i, r in enumerate(rows) if "dropdb" in r[-1])
        self.assertLess(stop, restore)
        state = self.state(root)
        self.assertEqual((state["current_ref"], state["rolled_back_from"]), (PREVIOUS, NEXT))
        self.assertNotIn("previous_ref", state)
        self.assertNotIn("rollback_in_progress", state)
        self.assertEqual(PREVIOUS, (root / "current-ref").read_text().strip())
        self.assertFalse((root / "previous-ref").exists(), "a consumed rollback pair is never reused")
        self.assertEqual([], list(root.glob(".rollback-helpers.*")), "private helper copies removed")
        again, output, rows = self.remote(root, env, "--rollback")
        self.assertNotEqual(0, again.returncode)
        self.assertIn("records no previous release", output)
        self.assertFalse(any("stop" in r for r in rows))

    def test_rollback_to_pre_w0_release_is_refused_unconditionally(self):
        """D-065: W0a is a one-way door, whatever the env backups contain."""
        root, env = self.cutover()
        env["PREW0_TARGET"] = "1"
        result, output, rows = self.remote(root, env, "--rollback")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Rollback refused (nothing was stopped)", output)
        self.assertIn("one-way door (D-065). Roll FORWARD", output)
        self.assertFalse(any("stop" in r or "dropdb" in r[-1] for r in rows))
        self.assertEqual(NEXT, self.state(root)["current_ref"])
        self.assertTrue(list(root.glob("*.pre-w0-*")), "backups untouched")

    def test_rollback_and_accept_refuse_when_running_release_differs_from_state(self):
        root, env = self.cutover()
        (root / "events.running-revision").write_text("c" * 40)
        for flag in ("--rollback", "--accept-release"):
            with self.subTest(flag=flag):
                result, output, rows = self.remote(root, env, flag)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(f"the running api is {'c' * 40}, but release-state.json says {NEXT}", output)
                self.assertFalse(any("stop" in r for r in rows))
        self.assertEqual(2, len(list(root.glob("*.pre-w0-*"))), "acceptance did not delete backups")

    def test_accept_release_deletes_every_recorded_backup(self):
        root, env = self.cutover()
        result, output, _ = self.remote(root, env, "--accept-release")
        self.assertEqual(0, result.returncode, output)
        self.assertEqual([], list(root.glob("*.pre-w0-*")))
        state = self.state(root)
        self.assertTrue(state["accepted"])
        self.assertEqual([], state["retired_backups"])

    def assert_no_retired_secret_anywhere(self, root):
        leftovers = sorted(str(p) for p in root.rglob("*.pre-w0-*"))
        self.assertEqual([], leftovers)
        for path in root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                data = path.read_bytes()
                for secret in (ADMIN_SECRET, REGISTRATION_SECRET):
                    self.assertNotIn(secret.encode(), data, f"retired secret left in {path}")

    def test_failed_recovered_deploy_then_success_then_accept_leaves_no_backup(self):
        """R1 finding: the backup of a failed, auto-recovered W0 deploy is recorded at once and
        deleted by --accept-release after a later successful deployment."""
        root, env = self.prepare_split("migration")
        failed, output = self.deploy(root, env)
        self.assertNotEqual(0, failed.returncode, output)
        self.assertIn("Previous stack restored", output)
        self.assertEqual(API_ENV, (root / "api.env").read_text())
        recorded = self.state(root)["retired_backups"]
        self.assertEqual(2, len(recorded), recorded)  # api.env + prod.env of the FAILED run
        self.assertTrue(all(".pre-w0-" in p for p in recorded))
        self.assertEqual(PREVIOUS, (root / "current-ref").read_text().strip(), "legacy marker kept")
        time.sleep(1.1)  # distinct backup stamps for the second run
        env["FAIL"] = ""
        result, output = self.deploy(root, env)
        self.assertEqual(0, result.returncode, output)
        retired = self.state(root)["retired_backups"]
        self.assertTrue(set(recorded) <= set(retired), (recorded, retired))
        self.assertEqual(4, len(retired), retired)
        result, output, _ = self.remote(root, env, "--accept-release")
        self.assertEqual(0, result.returncode, output)
        self.assertEqual([], self.state(root)["retired_backups"])
        self.assert_no_retired_secret_anywhere(root)

    def test_accept_release_sweeps_unrecorded_stray_backup(self):
        root, env = self.cutover()
        stray = root / "api.env.pre-w0-20250101T000000Z"
        stray.write_text(API_ENV)
        stray.chmod(0o600)
        self.assertNotIn(str(stray), self.state(root)["retired_backups"])
        result, output, _ = self.remote(root, env, "--accept-release")
        self.assertEqual(0, result.returncode, output)
        self.assertIn(f"deleted unrecorded env backup {stray}", output)
        self.assert_no_retired_secret_anywhere(root)

    def test_accept_release_does_not_sweep_when_current_release_predates_w0(self):
        root, env = self.cutover()
        stray = root / "api.env.pre-w0-20250101T000000Z"
        stray.write_text(API_ENV)
        # The fake git's cat-file fails for NEXT (b*40) only when PREW0_CURRENT=1.
        result, output, _ = self.remote(root, dict(env, PREW0_CURRENT="1"), "--accept-release")
        self.assertEqual(0, result.returncode, output)
        self.assertIn("predates W0a: unrecorded *.pre-w0-* backups are left in place", output)
        self.assertTrue(stray.exists())
        self.assertEqual([str(stray)], [str(p) for p in root.glob("*.pre-w0-*")], "recorded ones deleted")

    def test_interrupted_rollback_can_be_rerun(self):
        root, env = self.cutover()
        killed, output, rows = self.remote(root, dict(env, FAIL="rollback-kill"), "--rollback")
        self.assertNotEqual(0, killed.returncode)
        self.assertTrue(any("checkout" in r and PREVIOUS in r for r in rows), "killed after the checkout")
        self.assertEqual(PREVIOUS, self.state(root)["rollback_in_progress"])
        self.assertEqual(NEXT, self.state(root)["current_ref"])
        for _ in range(20):  # the killed runner's last child may still hold the deploy lock briefly
            result, output, rows = self.remote(root, env, "--rollback")
            if "Another deployment is running" not in output:
                break
            time.sleep(0.25)
        self.assertEqual(0, result.returncode, output)
        self.assertIn("Rollback complete", output)
        self.assertEqual("sha256:old-image", (root / "events.running-image").read_text())
        state = self.state(root)
        self.assertEqual(PREVIOUS, state["current_ref"])
        self.assertNotIn("rollback_in_progress", state)

    def test_failed_rollback_step_restores_current_release_and_saved_database(self):
        root, env = self.cutover()
        result, output, rows = self.remote(root, dict(env, FAIL="rollback-up"), "--rollback")
        self.assertNotEqual(0, result.returncode)
        self.assertIn(f"Current release {NEXT} restored (database from", output)
        restores = [r for r in rows if "dropdb" in r[-1]]
        self.assertEqual(2, len(restores), "previous dump, then the saved current database")
        self.assertNotIn(".rollback-compose.", " ".join(restores[-1]))
        self.assertEqual("sha256:new-image", (root / "events.running-image").read_text())
        self.assertIn(f"HLM_IMAGE=hlmemo:{NEXT}\n", (root / "prod.env").read_text())
        state = self.state(root)
        self.assertEqual((state["current_ref"], state["previous_ref"]), (NEXT, PREVIOUS))
        self.assertNotIn("rollback_in_progress", state)


def _fake_ssh(bin_dir: Path) -> None:
    ssh = bin_dir / "ssh"
    ssh.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, subprocess, sys\n"
        "leaked = sys.stdin.read()\n"
        "with open(os.environ['EVENTS'], 'a') as f:\n"
        "    f.write(json.dumps({'argv': sys.argv[1:], 'stdin': leaked}) + '\\n')\n"
        "sys.exit(subprocess.run(['bash', '-c', sys.argv[-1]], stdin=subprocess.DEVNULL).returncode)\n"
    )
    ssh.chmod(0o700)


class HlmOpsWrapperTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        (self.state / "ssh_config").write_text("Host hlm-deploy\n  HostName 203.0.113.10\n")
        (self.state / "deploy.conf").write_text("URL=https://mcp.example.com\n")
        self.remote = self.root / "remote_app"
        (self.remote / "deploy/scripts").mkdir(parents=True)
        # Fake remote stack.sh: records the exact argv the remote bash handed to it.
        (self.remote / "deploy/scripts/stack.sh").write_text(
            "python3 -c 'import json,os,sys; "
            'open(os.environ["ARGV_OUT"],"w").write(json.dumps(sys.argv[1:]))\' "$@"\n'
            'printf \'%s\\n\' "env=$HLM_ENV_FILE" >> "$ARGV_OUT.env"\n'
            "echo hlm_" + "T" * 43 + "\n"
        )
        self.bin = self.root / "bin"
        self.bin.mkdir()
        _fake_ssh(self.bin)
        self.events = self.root / "events"
        self.env = dict(
            os.environ,
            PATH=f"{self.bin}:{os.environ['PATH']}",
            EVENTS=str(self.events),
            ARGV_OUT=str(self.root / "argv.json"),
            HLM_REMOTE_DIR="/nonexistent",
        )

    def run_ops(self, *args, stdin="caller data that ssh must never read\n", env=None):
        return subprocess.run(
            ["bash", str(ROOT / "deploy/scripts/hlm_ops.sh"), *args],
            input=stdin,
            env=env or self.env,
            text=True,
            capture_output=True,
            timeout=30,
        )

    def test_arguments_survive_remote_shell_verbatim(self):
        hostile = [
            "device",
            "mint",
            "--name",
            "my-mac",
            "--class",
            "personal",
            "--grant",
            "proj:write",
            "--notes",
            'it\'s "quoted" $(touch PWNED) `touch PWNED2`; rm -rf /tmp/x | cat & \n newline * ?',
        ]
        env = dict(self.env, HLM_REMOTE_DIR=str(self.remote))
        result = self.run_ops("--state", str(self.state), *hostile, env=env)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(result.stdout, "hlm_" + "T" * 43 + "\n")  # only the token on stdout
        argv = json.loads((self.root / "argv.json").read_text())
        self.assertEqual(argv, ["exec", "-T", "api", "python", "-m", "hlmemo.ops", *hostile])
        self.assertFalse(list(self.root.rglob("PWNED*")))
        call = json.loads(self.events.read_text().splitlines()[0])
        self.assertEqual("", call["stdin"], "ssh read the caller's stdin")
        self.assertIn("-n", call["argv"])
        self.assertEqual(call["argv"][call["argv"].index("-F") + 1], str(self.state / "ssh_config"))
        self.assertIn("hlm-deploy", call["argv"])
        self.assertIn("env=/etc/hlmemo/prod.env", (self.root / "argv.json.env").read_text())

    def test_refuses_ambiguous_or_missing_state(self):
        env = dict(self.env, HLM_OPS_STATE="")
        result = self.run_ops("--state", str(self.root / "missing"), "status", env=env)
        self.assertEqual(64, result.returncode)
        self.assertIn("no SSH config", result.stderr)
        result = self.run_ops("--state", str(self.state), env=env)
        self.assertEqual(64, result.returncode)
        bad = dict(self.env, HLM_REMOTE_DIR="/opt/x;rm -rf /")
        result = self.run_ops("--state", str(self.state), "status", env=bad)
        self.assertEqual(64, result.returncode)
        self.assertFalse(self.events.exists(), "ssh must not run on refused input")


if __name__ == "__main__":
    unittest.main()
