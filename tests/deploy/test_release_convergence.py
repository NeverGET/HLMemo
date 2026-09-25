"""D-116 (review 75): the R3 release tooling converges under kills and interruptions, under the fake
ssh/docker harness (D-037b). Every test injects the fault at the exact step and asserts that a re-run
completes or recovers with exactly the recorded state:

1. snapshot provenance: the llm.env on disk must be what the api AND the librarian run;
2. a same-ref re-run only verifies (the R2 -> R3 pair and the R2 env snapshot are kept);
3. install_llm_env.sh runs under the deploy lock as one journalled, resumable step;
4. a killed rollback's retry reuses the FIRST safety dump;
5. --accept-release refuses during an unfinished rollback;
6. the deploy-attempt journal: a kill between the new stack's start and the state publish;
9. secret-bearing llm.env copies are journalled and deleted idempotently.
(7, 8 and the D-121 budget rules are the check's: tests/deploy/test_r2_librarian.py.)
"""

import fcntl
import importlib.util
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_deploy_recovery as harness  # noqa: E402  (module import: its tests are not re-collected)
import test_llm_env_release as d108  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
NEXT, PREVIOUS = harness.NEXT, harness.PREVIOUS
R2_ENV, R3_ENV, R2_KEY, R3_KEY = d108.R2_ENV, d108.R3_ENV, d108.R2_KEY, d108.R3_KEY
INSTALL = ROOT / "deploy/scripts/install_llm_env.sh"
INSTALL_KEY = "fake-or-" + "IN" * 16


def _release_state():
    spec = importlib.util.spec_from_file_location("rs116", ROOT / "deploy/scripts/release_state.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConvergenceTest(d108.D108RollbackTest):
    """Reuses the D-108 harness (R2 host with its llm.env -> R3 deployed, Order B)."""

    # the inherited D-108 tests run in their own module
    for name in [n for n in dir(d108.D108RollbackTest) if n.startswith("test_")]:
        locals()[name] = None
    del name

    def r3_deployed(self):
        root, env, result, output = self.r3_over_the_r2_env()
        self.assertEqual(0, result.returncode, output)
        env.pop("LIBRARIAN_REPORT_LIBRARIAN", None)  # from here the reports are what each container
        env.pop("LIBRARIAN_REPORT_API", None)  # was created with (the fake's default)
        return root, env

    def running_env(self, root, service):
        path = root / f"events.running-env.{service}"
        return path.read_text() if path.exists() else None

    def deploy_until_unlocked(self, root, env, *flags):
        for _ in range(40):  # a killed runner's last child may hold the deploy lock briefly
            result, output = self.deploy(root, env, *flags)
            if "Another deployment is running" not in output:
                return result, output
            time.sleep(0.25)
        self.fail("deploy lock never released")

    # ------------------------------------------------------------------ 1 snapshot provenance
    def test_deploy_refuses_a_snapshot_whose_disk_env_is_not_what_runs(self):
        """The R3 env is on disk, but the R2 api/librarian still run the R2 env (installed without
        recreating them): the runner must not record the R3 file as R2's env; it stops first."""
        for stale in ("api", "librarian"):
            with self.subTest(stale=stale):
                root, env, accept = self.prepare_compose_change()
                (root / "llm.env").write_text(R3_ENV)
                for service in ("api", "librarian"):
                    (root / f"events.running-env.{service}").write_text(
                        R2_ENV if service == stale else R3_ENV
                    )
                result, output = self.deploy(root, env, accept)
                self.assertNotEqual(0, result.returncode, output)
                self.assertIn(f"llm.env provenance: {stale} HLM_ENV_RELEASE: disk=r3 running=-", output)
                self.assertIn("refusing before anything stops", output)
                self.assertFalse(any("stop" in r for r in self.rows(root)), "nothing was stopped")
                self.assertFalse((root / f"llm.env.release-{PREVIOUS}").exists(), "no snapshot recorded")
                self.assertEqual(PREVIOUS, (root / "current-ref").read_text().strip())

    def test_rollback_refuses_when_the_disk_env_is_not_what_runs(self):
        root, env = self.r3_deployed()
        (root / "llm.env").write_text(R3_ENV)  # written, but api/librarian not recreated
        result, output, rows = self.remote(root, env, "--rollback")
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn("Rollback refused (nothing was stopped): the llm.env on disk is not the env", output)
        self.assertFalse(any("stop" in r for r in rows))
        self.assertNotIn("rollback_in_progress", self.state(root))

    # ------------------------------------------------------------------ 2 same-ref re-run
    def test_same_ref_rerun_only_verifies_and_keeps_the_r2_pair(self):
        root, env = self.r3_deployed()
        before = self.state(root)
        snapshot = Path(before["previous_llm_env"])
        self.install_r3_env(root)
        rows_before = len(self.rows(root))
        result, output = self.deploy(root, env)
        self.assertEqual(0, result.returncode, output)
        self.assertIn(f"Same-ref re-run: {NEXT} is already the published release; verifying only", output)
        self.assertIn("state unchanged", output)
        after = self.state(root)
        self.assertEqual((NEXT, PREVIOUS), (after["current_ref"], after["previous_ref"]), "never R3 -> R3")
        self.assertEqual(before["previous_llm_env"], after["previous_llm_env"])
        self.assertEqual(R2_ENV, snapshot.read_text(), "the R2 env snapshot is kept")
        rows = self.rows(root)[rows_before:]
        self.assertFalse(
            any("stop" in r or ("run" in r and "migrate" in r) for r in rows), "verification only"
        )
        # and the pair still rolls back to R2 with the R2 env
        result, output, _ = self.remote(root, env, "--rollback")
        self.assertEqual(0, result.returncode, output)
        self.assertEqual(R2_ENV, (root / "llm.env").read_text())

    def test_same_ref_rerun_refuses_when_another_release_runs(self):
        root, env = self.r3_deployed()
        (root / "events.running-revision").write_text(PREVIOUS)
        result, output = self.deploy(root, env)
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn(f"Same-ref re-run of {NEXT}, but the running api is {PREVIOUS}", output)
        self.assertEqual(PREVIOUS, self.state(root)["previous_ref"])

    def test_publish_never_pairs_a_release_with_itself(self):
        rs = _release_state()
        root = Path(self.id().replace(".", "_"))
        with self.assertRaises(SystemExit):
            rs.main(["publish", str(root), "--current", NEXT, "--previous", NEXT, "--previous-dump", "/d"])

    # ------------------------------------------------------------------ 3 env install under the lock
    def install(self, root, env, *extra, fail=""):
        state = root / "install-state"
        state.mkdir(exist_ok=True)
        (state / "ssh_config").write_text("Host hlm-deploy\n  HostName 203.0.113.10\n")
        key_file = root / "operator.env"
        key_file.write_text(f"OPENROUTER_API_KEY={INSTALL_KEY}\n")
        run_env = dict(
            env, FAIL=fail, HLM_REMOTE_DIR=str(root / "app"), HLM_REMOTE_ENV=str(root / "prod.env")
        )
        result = subprocess.run(
            ["bash", str(INSTALL), "--state", str(state), "--key-file", str(key_file), *extra],
            env=run_env,
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=120,
        )
        return result, result.stdout + result.stderr

    def test_install_switches_both_services_under_the_deploy_lock_and_verifies(self):
        root, env = self.r3_deployed()
        result, output = self.install(root, env)
        self.assertEqual(0, result.returncode, output)
        self.assertIn("recreating librarian and api with this llm.env (deploy lock held)", output)
        self.assertIn("RESULT librarian PASS llm.env=present release=r3 manifest=r3", output)
        self.assertIn("switch complete", output)
        disk = (root / "llm.env").read_text()
        self.assertIn("HLM_ENV_RELEASE=r3\n", disk)
        self.assertEqual(disk, self.running_env(root, "api"))
        self.assertEqual(disk, self.running_env(root, "librarian"))
        self.assertNotIn("env_switch", self.state(root))
        self.assertNotIn(INSTALL_KEY, output)
        # the deploy lock is the SAME lock deploy/rollback take: held by another run, nothing changes
        with open(root / ".deploy.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result, output = self.install(root, env, "--reset-operator-values")
        self.assertEqual(3, result.returncode, output)
        self.assertIn("another deployment is running; nothing changed", output)
        self.assertEqual(disk, (root / "llm.env").read_text())

    def test_interrupted_env_switch_blocks_deploys_until_a_rerun_finishes_it(self):
        root, env = self.r3_deployed()
        result, output = self.install(root, env, fail="switch-kill")  # killed between the two services
        self.assertNotEqual(0, result.returncode, output)
        disk = (root / "llm.env").read_text()
        self.assertEqual(disk, self.running_env(root, "librarian"))
        self.assertNotEqual(disk, self.running_env(root, "api"), "the api was not recreated yet")
        self.assertIn("env_switch", self.state(root))
        result, output = self.deploy(root, env)
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn("An llm.env switch is unfinished", output)
        result, output, rows = self.remote(root, env, "--rollback")
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn("an llm.env switch is unfinished", output)
        self.assertFalse(any("stop" in r for r in rows))
        result, output = self.install(root, env)  # the re-run finishes the step
        self.assertEqual(0, result.returncode, output)
        self.assertEqual(disk, self.running_env(root, "api"))
        self.assertEqual(disk, self.running_env(root, "librarian"))
        self.assertNotIn("env_switch", self.state(root))

    # ------------------------------------------------------------------ 4 rollback DB restore retry
    def test_killed_rollback_retry_reuses_the_first_safety_dump(self):
        root, env = self.r3_deployed()
        self.install_r3_env(root)
        killed, output, _ = self.remote(root, dict(env, FAIL="rollback-kill"), "--rollback")
        self.assertNotEqual(0, killed.returncode, output)
        state = self.state(root)
        safety = state["rollback_safety"]
        self.assertTrue(state["rollback_destructive"], "journalled before the database changed")
        self.assertIn(f"Saved the current database before replacing it: {safety}", output)
        dumps = sum("pg_dump" in r[-1] for r in self.rows(root))
        result, output, rows = self.rerun_until_unlocked(root, dict(env, FAIL="rollback-up"))
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn(f"Re-using the safety dump of the interrupted attempt: {safety}", output)
        self.assertIn(f"Current release {NEXT} restored (database from {safety})", output)
        self.assertEqual(dumps, sum("pg_dump" in r[-1] for r in self.rows(root)), "never re-snapshotted")
        state = self.state(root)
        self.assertNotIn("rollback_safety", state)
        self.assertNotIn("rollback_destructive", state)

    # ------------------------------------------------------------------ 5 accept during rollback
    def test_accept_refuses_during_an_unfinished_rollback(self):
        root, env = self.r3_deployed()
        self.install_r3_env(root)
        killed, output, _ = self.remote(root, dict(env, FAIL="rollback-kill"), "--rollback")
        self.assertNotEqual(0, killed.returncode, output)
        backups = sorted(root.glob("*.pre-w0-*"))
        self.assertTrue(backups)
        for _ in range(40):
            result, output, _ = self.remote(root, env, "--accept-release")
            if "Another deployment is running" not in output:
                break
            time.sleep(0.25)
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn(f"an unfinished rollback to {PREVIOUS} is recorded", output)
        self.assertEqual(backups, sorted(root.glob("*.pre-w0-*")), "no backup deleted")
        self.assertFalse(self.state(root).get("accepted"))

    # ------------------------------------------------------------------ 6 deploy-attempt journal
    def test_kill_after_the_new_stack_starts_rerun_completes_the_publish(self):
        root, env, accept = self.prepare_compose_change()
        (root / "llm.env").write_text(R2_ENV)
        killed, output = self.deploy(root, dict(env, FAIL="kill-after-up"), accept)
        self.assertNotEqual(0, killed.returncode, output)
        state = self.state(root)
        self.assertEqual(NEXT, state["deploy_attempt"]["revision"])
        self.assertEqual(PREVIOUS, (root / "current-ref").read_text().strip(), "not published yet")
        attempt = state["deploy_attempt"]
        result, output = self.deploy_until_unlocked(root, dict(env, FAIL=""), accept)
        self.assertEqual(0, result.returncode, output)
        self.assertIn(f"Resuming the recorded deployment of {NEXT}: its stack runs; verifying", output)
        self.assertIn("the recorded attempt completed", output)
        state = self.state(root)
        self.assertEqual((NEXT, PREVIOUS), (state["current_ref"], state["previous_ref"]))
        self.assertEqual(attempt["previous_dump"], state["previous_dump"], "exactly the recorded tuple")
        self.assertEqual(attempt["previous_llm_env"], state["previous_llm_env"])
        self.assertEqual(R2_ENV, Path(state["previous_llm_env"]).read_text())
        self.assertNotIn("deploy_attempt", state)
        self.assertFalse((root / "deploy-attempt-model.json").exists())
        self.assertEqual([], state.get("pending_cleanup") or [])

    def test_kill_after_the_new_stack_starts_failed_verification_recovers_the_recorded_tuple(self):
        root, env, accept = self.prepare_compose_change()
        (root / "llm.env").write_text(R2_ENV)
        killed, output = self.deploy(root, dict(env, FAIL="kill-after-up"), accept)
        self.assertNotEqual(0, killed.returncode, output)
        dump = self.state(root)["deploy_attempt"]["previous_dump"]
        result, output = self.deploy_until_unlocked(root, dict(env, FAIL="internal-api"), accept)
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn("Previous stack restored", output)
        restores = [r for r in self.rows(root) if "dropdb" in r[-1]]
        self.assertTrue(restores, "the recorded quiesced dump was restored")
        self.assertEqual("sha256:old-image", (root / "events.running-image").read_text())
        state = self.state(root)
        self.assertNotIn("deploy_attempt", state)
        self.assertNotIn("previous_ref", state)
        self.assertEqual(PREVIOUS, (root / "current-ref").read_text().strip())
        self.assertTrue(Path(dump).exists())
        self.assert_no_key_copy(root, R2_KEY)

    def test_kill_before_the_new_stack_starts_rerun_recovers(self):
        root, env, accept = self.prepare_compose_change()
        (root / "llm.env").write_text(R2_ENV)
        killed, output = self.deploy(root, dict(env, FAIL="kill-before-up"), accept)
        self.assertNotEqual(0, killed.returncode, output)
        self.assertIn("deploy_attempt", self.state(root))
        result, output = self.deploy_until_unlocked(root, dict(env, FAIL=""), accept)
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn("its stack never came up; recovering the previous stack", output)
        self.assertIn("Previous stack restored", output)
        self.assertNotIn("deploy_attempt", self.state(root))
        self.assert_no_key_copy(root, R2_KEY)
        # a fresh deployment then goes through normally
        result, output = self.deploy_until_unlocked(root, dict(env, FAIL=""), accept)
        self.assertEqual(0, result.returncode, output)
        self.assertEqual(NEXT, self.state(root)["current_ref"])

    # ------------------------------------------------------------------ 9 cleanup journal
    def test_failed_deploy_snapshot_is_journalled_and_cleaned_by_the_next_lock_holder(self):
        root, env, accept = self.prepare_compose_change("migration")
        (root / "llm.env").write_text(R2_ENV)
        failed, output = self.deploy(root, env, accept)
        self.assertNotEqual(0, failed.returncode, output)
        self.assertIn("Previous stack restored", output)
        # the recovered attempt's snapshot (it holds the key) was journalled, then deleted
        self.assertFalse((root / f"llm.env.release-{PREVIOUS}").exists())
        self.assertEqual([], self.state(root).get("pending_cleanup") or [])
        self.assertNotIn("deploy_attempt", self.state(root))
        self.assert_no_key_copy(root, R2_KEY)

    def test_snapshot_of_a_deploy_failing_before_the_journal_is_cleaned_by_the_next_run(self):
        root, env, accept = self.prepare_compose_change("dump")  # fails before writers stop
        (root / "llm.env").write_text(R2_ENV)
        failed, output = self.deploy(root, env, accept)
        self.assertNotEqual(0, failed.returncode, output)
        snapshot = root / f"llm.env.release-{PREVIOUS}"
        self.assertIn(str(snapshot), self.state(root)["pending_cleanup"], "journalled when it was made")
        result, output = self.deploy(root, dict(env, FAIL=""), accept)  # the next lock holder
        self.assertEqual(0, result.returncode, output)
        state = self.state(root)
        self.assertEqual(str(snapshot), state["previous_llm_env"], "re-made and published as the target")
        self.assertEqual([], state.get("pending_cleanup") or [])

    def test_crash_between_the_state_commit_and_the_unlink_is_finished_by_cleanup(self):
        rs = _release_state()
        root, env = self.r3_deployed()
        old = Path(self.state(root)["previous_llm_env"])
        newer = root / f"llm.env.release-{NEXT}"
        newer.write_text(R3_ENV)
        crashed = rs.cleanup
        rs.cleanup = lambda directory: (_ for _ in ()).throw(
            KeyboardInterrupt()
        )  # killed right after the commit
        try:
            with self.assertRaises(KeyboardInterrupt):
                argv = ["publish", str(root), "--current", "c" * 40, "--previous", NEXT]
                rs.main([*argv, "--previous-dump", "/d", "--previous-llm-env", str(newer)])
        finally:
            rs.cleanup = crashed
        state = self.state(root)
        self.assertEqual(str(newer), state["previous_llm_env"])
        self.assertIn(str(old), state["pending_cleanup"], "journalled in the same write")
        self.assertTrue(old.exists(), "not yet deleted")
        # the next lock holder (any deploy, rollback or accept) finishes it, idempotently
        rs.main(["cleanup", str(root)])
        rs.main(["cleanup", str(root)])
        self.assertFalse(old.exists())
        self.assertEqual([], self.state(root)["pending_cleanup"])
        self.assertTrue(newer.exists(), "the live rollback target is kept")


if __name__ == "__main__":
    unittest.main()
