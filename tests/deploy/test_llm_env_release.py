"""D-108/D-111 #7: llm.env is part of the release state, under the fake ssh/docker harness (D-037b).

The D-108 scenario: the host runs R2 with the R2 llm.env; R3 is deployed with that env still
installed (Order B) and the runner snapshots it for the pair; the R3 env is installed afterwards;
`deploy.sh --rollback` must put the R2 env back BEFORE the R2 image starts (the R2 code cannot
load the R3-only profiles), render the R2 model with it (Compose inlines env_file contents), put
the R3 env back if a rollback step fails, keep the recorded newer env across an interrupted re-run,
and leave no copy of either env (they hold the provider key) behind.
"""

import importlib.util
import json
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_deploy_recovery as harness  # noqa: E402  (module import: its tests are not re-collected)
import test_r2_librarian as r2  # noqa: E402
import test_w0_access as w0  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
NEXT, PREVIOUS = harness.NEXT, harness.PREVIOUS
R2_KEY, R3_KEY = "harness-dummy-r2-value", "harness-dummy-r3-value"
#: production's R2 env: no release marker, R2 profiles only
R2_ENV = (
    "HLM_LIBRARIAN_ENABLED=true\n"
    "HLM_LIBRARIAN_ROLE=observer\n"
    "HLM_PROFILE=openrouter-gpt6-luna\n"
    "HLM_FALLBACK_PROFILE=openrouter\n"
    f"OPENROUTER_API_KEY={R2_KEY}\n"
)
#: the D-094 R3 env (D-116 manifest): the marker and a profile only the R3 image ships (the R2
#: librarian cannot load it)
R3_ENV = harness.R3_LLM_ENV
assert "HLM_FALLBACK_PROFILE__RISK_JUDGE=openrouter-qwen38-27b-fast" in R3_ENV
assert R3_KEY in R3_ENV

spec = importlib.util.spec_from_file_location("llm_env_release", ROOT / "deploy/scripts/llm_env_release.py")
ler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ler)


class D108RollbackTest(unittest.TestCase):
    prepare_deploy = harness.DeployRecoveryTest.prepare_deploy
    prepare_split = w0.W0DeployTest.prepare_split
    prepare_compose_change = w0.W0DeployTest.prepare_compose_change
    deploy = w0.W0DeployTest.deploy
    remote = w0.W0DeployTest.remote
    rows = staticmethod(w0.W0DeployTest.rows)
    state = staticmethod(w0.W0DeployTest.state)

    def r3_over_the_r2_env(self, r2_env=R2_ENV):
        """R2 host with its llm.env -> deploy R3 (Order B, the check reports the D-108 interim)."""
        root, env, accept = self.prepare_compose_change()
        llm = root / "llm.env"
        if r2_env is None:
            llm.unlink()
        else:
            llm.write_text(r2_env)
            llm.chmod(0o600)
        env["LIBRARIAN_REPORT_LIBRARIAN"] = r2.report("librarian", env_release=None)
        env["LIBRARIAN_REPORT_API"] = r2.report("api", env_release=None)
        result, output = self.deploy(root, env, accept)
        env = {k: v for k, v in env.items() if k != "COMPOSE_CHANGE"}
        return root, env, result, output

    def install_r3_env(self, root):
        """D-108 step 3 as install_llm_env.sh does it (D-116 #3): the file, then BOTH services
        recreated with it (the fake docker records what each container was created with)."""
        (root / "llm.env").write_text(R3_ENV)
        (root / "llm.env").chmod(0o600)
        for service in ("api", "librarian"):
            (root / f"events.running-env.{service}").write_text(R3_ENV)

    @staticmethod
    def ups(root, since=0):
        path = root / "events.llm-env-at-up"
        return [json.loads(line) for line in path.read_text().splitlines()][since:] if path.exists() else []

    def rerun_until_unlocked(self, root, env):
        for _ in range(20):  # the killed runner's last child may still hold the deploy lock briefly
            result, output, rows = self.remote(root, env, "--rollback")
            if "Another deployment is running" not in output:
                return result, output, rows
            time.sleep(0.25)
        self.fail("deploy lock never released")

    def assert_no_key_copy(self, root, *keys):
        """No file but llm.env itself holds a provider key (the harness's own event log aside)."""
        for path in root.rglob("*"):
            if path.is_file() and not path.is_symlink() and not path.name.startswith("events"):
                if path == root / "llm.env":
                    continue
                data = path.read_bytes()
                for key in keys:
                    self.assertNotIn(key.encode(), data, f"a copy of an llm.env is left in {path}")

    def test_d108_rollback_restores_the_r2_env_before_the_r2_image_starts(self):
        root, env, result, output = self.r3_over_the_r2_env()
        self.assertEqual(0, result.returncode, output)
        self.assertIn("RESULT librarian PASS llm.env=present release=r2-env (D-108 interim", output)
        state = self.state(root)
        snapshot = Path(state["previous_llm_env"])
        self.assertEqual(root / f"llm.env.release-{PREVIOUS}", snapshot)
        self.assertEqual(R2_ENV, snapshot.read_text(), "the env the previous release runs with")
        self.assertEqual(0o600, stat.S_IMODE(snapshot.stat().st_mode))
        self.assertIn(f"llm.env of {PREVIOUS[:12]} recorded for rollback: {snapshot}", output)
        self.install_r3_env(root)  # D-108 step 3
        before = len(self.ups(root))
        result, output, _ = self.remote(root, env, "--rollback")
        self.assertEqual(0, result.returncode, output)
        self.assertIn("Rollback complete", output)
        self.assertEqual(R2_ENV, (root / "llm.env").read_text(), "the R2 env is back")
        self.assertEqual(0o600, stat.S_IMODE((root / "llm.env").stat().st_mode))
        started = [u for u in self.ups(root, before) if u[0] == "rollback"]
        self.assertTrue(started, "the previous stack was started")
        for _model, file_content, model_release in started:
            self.assertEqual(R2_ENV, file_content, "llm.env was restored BEFORE the R2 image started")
            self.assertIsNone(model_release, "the R2 model was rendered with the R2 env, not the R3 one")
        self.assertLess(output.index(f"from llm.env.release-{PREVIOUS}"), output.index("Rollback complete"))
        state = self.state(root)
        self.assertNotIn("previous_llm_env", state)
        self.assertNotIn("rollback_llm_env", state)
        self.assertEqual([], sorted(p.name for p in root.glob("llm.env.*")), "spent copies deleted")
        self.assert_no_key_copy(root, R2_KEY, R3_KEY)

    def test_failed_rollback_step_puts_the_r3_env_back_before_r3_restarts(self):
        root, env, result, output = self.r3_over_the_r2_env()
        self.assertEqual(0, result.returncode, output)
        self.install_r3_env(root)
        before = len(self.ups(root))
        result, output, _ = self.remote(root, dict(env, FAIL="rollback-up"), "--rollback")
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn(f"Current release {NEXT} restored", output)
        self.assertEqual(R3_ENV, (root / "llm.env").read_text(), "the newer env is back")
        ups = self.ups(root, before)
        self.assertEqual("rollback", ups[0][0])
        self.assertEqual(R2_ENV, ups[0][1], "the failed step ran the R2 image with the R2 env")
        self.assertEqual(("current", R3_ENV), tuple(ups[-1][:2]), "R3 restarted with the R3 env")
        state = self.state(root)
        self.assertEqual((state["current_ref"], state["previous_ref"]), (NEXT, PREVIOUS), "pair kept")
        self.assertEqual(R2_ENV, Path(state["previous_llm_env"]).read_text(), "snapshot kept for a retry")
        self.assertNotIn("rollback_llm_env", state)
        self.assertFalse((root / f"llm.env.release-{NEXT}").exists(), "the spent newer copy is deleted")
        self.assert_no_key_copy(root, R3_KEY)

    def test_interrupted_rollback_rerun_keeps_the_recorded_newer_env(self):
        root, env, result, output = self.r3_over_the_r2_env()
        self.assertEqual(0, result.returncode, output)
        self.install_r3_env(root)
        killed, output, _ = self.remote(root, dict(env, FAIL="rollback-kill"), "--rollback")
        self.assertNotEqual(0, killed.returncode)
        state = self.state(root)
        self.assertEqual(PREVIOUS, state["rollback_in_progress"])
        newer = Path(state["rollback_llm_env"])
        self.assertEqual(R3_ENV, newer.read_text())
        self.assertEqual(R2_ENV, (root / "llm.env").read_text(), "killed after the env restore")
        # a re-run whose step fails: the recovery puts back the RECORDED newer env (the re-run did
        # not snapshot the already-restored R2 env as "newer")
        result, output, _ = self.rerun_until_unlocked(root, dict(env, FAIL="rollback-up"))
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn(f"Current release {NEXT} restored", output)
        self.assertEqual(R3_ENV, (root / "llm.env").read_text())
        # and a clean re-run completes with the R2 env
        result, output, _ = self.rerun_until_unlocked(root, env)
        self.assertEqual(0, result.returncode, output)
        self.assertEqual(R2_ENV, (root / "llm.env").read_text())
        self.assertEqual(PREVIOUS, self.state(root)["current_ref"])
        self.assert_no_key_copy(root, R2_KEY, R3_KEY)

    def test_previous_release_without_llm_env_rolls_back_to_none(self):
        root, env, result, output = self.r3_over_the_r2_env(r2_env=None)
        self.assertNotEqual(0, result.returncode, output)  # D-111 #6: R3 needs llm.env ...
        self.assertIn("R3 release: llm.env is required", output)
        self.assertEqual(NEXT, self.state(root)["current_ref"], "... but the cutover stands")
        self.assertEqual("absent", self.state(root)["previous_llm_env"])
        self.install_r3_env(root)
        before = len(self.ups(root))
        result, output, _ = self.remote(root, env, "--rollback")
        self.assertEqual(0, result.returncode, output)
        self.assertFalse((root / "llm.env").exists(), "the previous release ran without one")
        started = [u for u in self.ups(root, before) if u[0] == "rollback"]
        self.assertTrue(started)
        self.assertTrue(all(u[1] is None and u[2] is None for u in started), started)
        self.assert_no_key_copy(root, R3_KEY)

    def test_state_of_an_older_runner_leaves_llm_env_untouched_with_a_warning(self):
        root, env, result, output = self.r3_over_the_r2_env()
        self.assertEqual(0, result.returncode, output)
        state = self.state(root)
        Path(state.pop("previous_llm_env")).unlink()
        (root / "release-state.json").write_text(json.dumps(state))
        self.install_r3_env(root)
        result, output, _ = self.remote(root, env, "--rollback")
        self.assertEqual(0, result.returncode, output)
        self.assertIn(f"WARNING: release-state.json records no llm.env for {PREVIOUS}", output)
        self.assertEqual(R3_ENV, (root / "llm.env").read_text(), "legacy: llm.env left as it is")

    def test_publish_of_the_next_release_deletes_the_superseded_snapshot(self):
        root, env, result, output = self.r3_over_the_r2_env()
        self.assertEqual(0, result.returncode, output)
        old = Path(self.state(root)["previous_llm_env"])
        self.assertTrue(old.exists())
        # the next publication (a later release over NEXT) records its own snapshot
        spec = importlib.util.spec_from_file_location("rs", ROOT / "deploy/scripts/release_state.py")
        rs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rs)
        newer = ler.snapshot(root / "llm.env", NEXT)
        rs.main(["publish", str(root), "--current", "c" * 40, "--previous", NEXT, "--previous-dump", "/d",
                 "--previous-llm-env", newer])  # fmt: skip
        self.assertFalse(old.exists(), "the superseded snapshot (it holds the key) is deleted")
        self.assertEqual(newer, self.state(root)["previous_llm_env"])


class LlmEnvHelperTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.target = self.dir / "llm.env"

    def test_snapshot_and_restore_are_atomic_0600_copies(self):
        self.assertEqual("absent", ler.snapshot(self.target, PREVIOUS))
        self.target.write_text(R2_ENV)
        copy = Path(ler.snapshot(self.target, PREVIOUS))
        self.assertEqual((copy.name, copy.read_text()), (f"llm.env.release-{PREVIOUS}", R2_ENV))
        self.assertEqual(0o600, stat.S_IMODE(copy.stat().st_mode))
        self.target.write_text(R3_ENV)
        message = ler.restore(str(copy), self.target)
        self.assertNotIn(R2_KEY, message, "never prints content")
        self.assertEqual(R2_ENV, self.target.read_text())
        self.assertEqual(0o600, stat.S_IMODE(self.target.stat().st_mode))
        ler.restore("absent", self.target)
        self.assertFalse(self.target.exists())
        ler.restore("absent", self.target)  # idempotent
        self.assertEqual([copy.name], sorted(p.name for p in self.dir.iterdir()), "no temporary file left")
        with self.assertRaises(SystemExit):
            ler.restore(str(self.dir / "missing"), self.target)
        with self.assertRaises(SystemExit):
            ler.snapshot(self.target, "not-a-ref")


if __name__ == "__main__":
    unittest.main()
