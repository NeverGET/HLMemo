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
import r4_fixtures  # noqa: E402
import test_deploy_recovery as harness  # noqa: E402  (module import: its tests are not re-collected)
import test_llm_env_release as d108  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
NEXT, PREVIOUS = harness.NEXT, harness.PREVIOUS
R2_ENV, R3_ENV, R2_KEY, R3_KEY = d108.R2_ENV, d108.R3_ENV, d108.R2_KEY, d108.R3_KEY
INSTALL_KEY = "fake-or-" + "IN" * 16
GEMINI_KEY = r4_fixtures.GEMINI_KEY


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
    def install(self, root, env, *extra, fail="", keys=None):
        """install_llm_env.sh from a workstation copy (r4_fixtures: the Gemini writer profiles) with
        a key file holding every key the R4 env needs (``keys`` replaces it)."""
        state = root / "install-state"
        state.mkdir(exist_ok=True)
        (state / "ssh_config").write_text("Host hlm-deploy\n  HostName 203.0.113.10\n")
        workstation = root / "workstation"
        if not workstation.exists():
            r4_fixtures.workstation_repo(workstation)
        key_file = root / "operator.env"
        if keys is None:
            keys = {"OPENROUTER_API_KEY": INSTALL_KEY, "GEMINI_API_KEY": GEMINI_KEY}
        key_file.write_text("".join(f"{k}={v}\n" for k, v in keys.items()))
        run_env = dict(
            env, FAIL=fail, HLM_REMOTE_DIR=str(root / "app"), HLM_REMOTE_ENV=str(root / "prod.env")
        )
        result = subprocess.run(
            [
                "bash",
                str(workstation / "deploy/scripts/install_llm_env.sh"),
                "--state",
                str(state),
                "--key-file",
                str(key_file),
                *extra,
            ],
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
        # review 79 T5: this checkout's template is the R4 env (its label, never a literal)
        self.assertIn("RESULT librarian PASS llm.env=present release=r4 manifest=r4", output)
        self.assertIn("switch complete", output)
        disk = (root / "llm.env").read_text()
        self.assertIn("HLM_ENV_RELEASE=r4\n", disk)
        self.assertIn("HLM_RESEARCH_ENABLED=true\n", disk)
        self.assertEqual(disk, self.running_env(root, "api"))
        self.assertEqual(disk, self.running_env(root, "librarian"))
        self.assertNotIn("env_switch", self.state(root))
        self.assertNotIn(INSTALL_KEY, output)
        self.assertNotIn(GEMINI_KEY, output)
        # R4 R-14: the writer probe ran once, in the API container, with the api's writer
        self.assertIn(
            "writer probe (api container): exit=0 ok=True profile=google-gemini38-flash-medium", output
        )
        self.assertEqual("google-gemini38-flash-medium\n", (root / "events.probe-writer").read_text())
        self.assertIn("writer=google-gemini38-flash-medium", output)
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

    def host_evaluate(self, root, env, release="r4"):
        """The RUNBOOK's standalone re-check on the (harness) host: collect in both containers, then
        evaluate --release rN --writer-probe api against the llm.env on disk."""
        script = (
            'cd "$HLM_REMOTE_DIR" && d=$(mktemp -d) && '
            "bash deploy/scripts/stack.sh exec -T librarian python - collect --service librarian --probe"
            ' --wait-heartbeat 45 < deploy/scripts/check_librarian.py > "$d/l.json" && '
            "bash deploy/scripts/stack.sh exec -T api python - collect --service api"
            ' < deploy/scripts/check_librarian.py > "$d/a.json" && '
            'python3 deploy/scripts/check_librarian.py evaluate --llm-env present --llm-env-file "$LLM_ENV"'
            f' --release {release} --writer-probe api --librarian "$d/l.json" --api "$d/a.json"'
        )
        result = subprocess.run(
            ["bash", "-c", script],
            env=dict(
                env,
                HLM_REMOTE_DIR=str(root / "app"),
                HLM_ENV_FILE=str(root / "prod.env"),
                LLM_ENV=str(root / "llm.env"),
            ),
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=120,
        )
        return result.returncode, result.stdout + result.stderr

    def test_install_fails_when_the_writer_probe_fails_and_never_prints_the_key(self):
        """R4 R-14: the env switch's check includes probe-writer in the api container; a refused
        probe fails it (the journal stays, so deploy/rollback refuse until a passing re-run), and the
        probe's stderr/extra fields (a leaky probe prints the key there) never reach the output."""
        root, env = self.r3_deployed()
        result, output = self.install(root, env, fail="probe-writer")
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn("the writer probe FAILED in the api container (exit 1, status 401)", output)
        self.assertNotIn(GEMINI_KEY, output)
        self.assertNotIn("HTTP 401 for key", output)
        self.assertIn("env_switch", self.state(root))
        result, output = self.install(root, env)
        self.assertEqual(0, result.returncode, output)
        self.assertNotIn("env_switch", self.state(root))

    def test_wrong_writer_key_in_the_api_only_fails_the_probe(self):
        """R4 R-14 (consult 89: the librarian-side probe could not see the api's credential): the
        librarian runs the right Gemini key, the api a wrong but non-empty one. key_set passes for
        both; the probe in the API container fails, and neither key is printed."""
        root, env = self.r3_deployed()
        result, output = self.install(root, env)
        self.assertEqual(0, result.returncode, output)
        wrong = "fake-gm-" + "W0" * 16
        right_env = self.running_env(root, "api")
        (root / "events.running-env.api").write_text(
            right_env.replace(f"GEMINI_API_KEY={GEMINI_KEY}", f"GEMINI_API_KEY={wrong}")
        )
        env = dict(env, GOOD_GEMINI_KEY=GEMINI_KEY)
        code, output = self.host_evaluate(root, env)
        self.assertEqual(1, code, output)
        self.assertIn("provider google-gemini38-flash-medium https://gemini.invalid/v1: key set", output)
        self.assertIn("the writer probe FAILED in the api container (exit 1, status 401)", output)
        self.assertNotIn(wrong, output)
        self.assertNotIn(GEMINI_KEY, output)
        (root / "events.running-env.api").write_text(right_env)
        code, output = self.host_evaluate(root, env)
        self.assertEqual(0, code, output)
        self.assertIn("RESULT librarian PASS llm.env=present release=r4", output)

    # ------------------------------------------------------------------ R-3 env switches R3 <-> R4
    def caps(self, root):
        env = r4_fixtures.dotenv((root / "llm.env").read_text())
        return tuple(env.get(f"HLM_LLM_BUDGET_{w}_USD") for w in ("HOUR", "DAY", "MONTH"))

    def test_r4_env_switch_with_reset_then_behaviour_only_rollback_to_the_r3_template(self):
        """R4 plan §4.3 and §6.2(a) (R-3), on a deployed host under the deploy lock:
        1. the R3 template installed on the R4 code (--release-template <R3 ref>): caps 1/2/10;
        2. the R4 switch WITH --reset-operator-values (the key file holds every key the R4 env
           needs): the template's caps 3/8/60 replace R3's, the keys are the key file's, r4 PASS;
        3. the behaviour-only rollback (--release-template <R3 ref>, a key file with ANOTHER
           OpenRouter value): the R3 caps 1/2/10 explicitly (MONTH 60 would fail the R3 manifest), the
           INSTALLED key kept, research off, evaluate --release r3 PASS, the env_switch journal empty.
        """
        root, env = self.r3_deployed()
        template = root / "r3-template.env.example"
        template.write_text(r4_fixtures.R3_TEMPLATE)
        env = dict(env, RELEASE_TEMPLATE=str(template))
        r3_ref = "805f4cd"
        prod_key = "fake-or-" + "PR" * 16
        # 1. an R3-installed env (the state the R4 switch starts from)
        result, output = self.install(
            root, env, "--release-template", r3_ref, keys={"OPENROUTER_API_KEY": prod_key}
        )
        self.assertEqual(0, result.returncode, output)
        self.assertIn("RESULT librarian PASS llm.env=present release=r3 manifest=r3", output)
        self.assertEqual(("1", "2", "10"), self.caps(root))
        # 2. the R4 switch: --reset-operator-values, every key in the key file
        keys = {"OPENROUTER_API_KEY": prod_key, "GEMINI_API_KEY": GEMINI_KEY}
        result, output = self.install(root, env, "--reset-operator-values", keys=keys)
        self.assertEqual(0, result.returncode, output)
        self.assertIn("RESULT librarian PASS llm.env=present release=r4 manifest=r4", output)
        disk = r4_fixtures.dotenv((root / "llm.env").read_text())
        self.assertEqual(("3", "8", "60"), self.caps(root), "the template's caps replace R3's 1/2/10")
        self.assertEqual((prod_key, GEMINI_KEY), (disk["OPENROUTER_API_KEY"], disk["GEMINI_API_KEY"]))
        self.assertNotIn("env_switch", self.state(root))
        # without the reset, the installed R3 caps would have won (the reason §4.3 uses it)
        # 3. the behaviour-only rollback to the R3 template, R4 caps (MONTH 60) installed
        other = "fake-or-" + "XX" * 16
        result, output = self.install(
            root, env, "--release-template", r3_ref, keys={"OPENROUTER_API_KEY": other}
        )
        self.assertEqual(0, result.returncode, output)
        self.assertIn(f"release=r3 (template {r3_ref}; its caps, the installed keys kept)", output)
        self.assertIn("RESULT librarian PASS llm.env=present release=r3 manifest=r3", output)
        self.assertIn("switch complete", output)
        self.assertNotIn("env_switch", self.state(root))
        disk_text = (root / "llm.env").read_text()
        disk = r4_fixtures.dotenv(disk_text)
        self.assertEqual(("1", "2", "10"), self.caps(root))
        self.assertEqual("r3", disk["HLM_ENV_RELEASE"])
        self.assertEqual(prod_key, disk["OPENROUTER_API_KEY"], "the installed key is kept")
        self.assertIn("kept the operator's OPENROUTER_API_KEY", output)
        self.assertNotIn("kept the operator's HLM_LLM_BUDGET", output)
        self.assertFalse(
            [k for k in disk if k.startswith(("HLM_RESEARCH_", "HLM_MAP_SUMMARY"))], "research off"
        )
        self.assertNotIn("GEMINI_API_KEY", disk, "the R3 template has no Gemini key")
        self.assertEqual(disk_text, self.running_env(root, "api"))
        self.assertEqual(disk_text, self.running_env(root, "librarian"))
        for key in (prod_key, other, GEMINI_KEY):
            self.assertNotIn(key, output)

    def test_r4_env_with_the_luna_writer_probes_the_research_primary(self):
        """R4 plan §6.2(b) with the decided contract: the R4 template without its writer line,
        installed with --release-template PATH over the Gemini env. The switch passes the r4
        manifest; probe-writer still runs in the api container and reports the research primary
        (HLM_PROFILE), and the installed Gemini key is kept in the file (the template keeps its line)."""
        root, env = self.r3_deployed()
        self.assertEqual(0, self.install(root, env, "--reset-operator-values")[0].returncode)
        workstation = root / "workstation"
        luna = root / "r4-luna.env.example"
        luna.write_text(
            "".join(
                line
                for line in (workstation / "deploy/llm.env.example").read_text().splitlines(keepends=True)
                if not line.startswith("HLM_RESEARCH_WRITER_PROFILE=")
            )
        )
        (root / "events.probe-writer").unlink()
        result, output = self.install(
            root, env, "--release-template", str(luna), keys={"OPENROUTER_API_KEY": INSTALL_KEY}
        )
        self.assertEqual(0, result.returncode, output)
        self.assertIn("writer: unset (the research primary openrouter-gpt6-luna writes; HLM_PROFILE)", output)
        self.assertIn("writer probe (api container): exit=0 ok=True profile=openrouter-gpt6-luna", output)
        self.assertIn("RESULT librarian PASS llm.env=present release=r4 manifest=r4", output)
        self.assertEqual("openrouter-gpt6-luna\n", (root / "events.probe-writer").read_text())
        disk = r4_fixtures.dotenv((root / "llm.env").read_text())
        self.assertNotIn("HLM_RESEARCH_WRITER_PROFILE", disk)
        self.assertEqual(GEMINI_KEY, disk["GEMINI_API_KEY"], "the installed key is kept")
        self.assertEqual(("3", "8", "60"), self.caps(root))
        self.assertNotIn("env_switch", self.state(root))
        self.assertNotIn(GEMINI_KEY, output)
        # a refused probe of the primary fails the switch too
        result, output = self.install(
            root,
            env,
            "--release-template",
            str(luna),
            fail="probe-writer",
            keys={"OPENROUTER_API_KEY": INSTALL_KEY},
        )
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn("the writer probe FAILED in the api container (exit 1, status 401)", output)

    def test_keep_mode_install_of_the_r4_template_keeps_the_installed_r3_caps(self):
        """Why §4.3 needs --reset-operator-values: a plain (keep) R4 install over the R3 env keeps the
        installed caps 1/2/10 (they satisfy the R4 manifest, so it passes, but the owner's 3/8/60 are
        not installed)."""
        root, env = self.r3_deployed()
        template = root / "r3-template.env.example"
        template.write_text(r4_fixtures.R3_TEMPLATE)
        env = dict(env, RELEASE_TEMPLATE=str(template))
        self.assertEqual(0, self.install(root, env, "--release-template", "805f4cd")[0].returncode)
        result, output = self.install(root, env)
        self.assertEqual(0, result.returncode, output)
        self.assertEqual(("1", "2", "10"), self.caps(root))
        self.assertIn("kept the operator's HLM_LLM_BUDGET_MONTH_USD", output)

    def test_release_template_refusals_send_nothing(self):
        """--release-template: an unknown ref or path, or a template whose caps violate its own
        release's manifest, stops the install before anything is sent (no journal, no file)."""
        root, env = self.r3_deployed()
        before = (root / "llm.env").read_text()
        result, output = self.install(root, env, "--release-template", "no-such-ref")
        self.assertEqual(64, result.returncode, output)
        self.assertIn("not a commit of this checkout", output)
        bad = root / "r3-bad.env.example"
        bad.write_text(
            r4_fixtures.R3_TEMPLATE.replace("HLM_LLM_BUDGET_MONTH_USD=10", "HLM_LLM_BUDGET_MONTH_USD=60")
        )
        result, output = self.install(root, env, "--release-template", str(bad))
        self.assertEqual(65, result.returncode, output)
        self.assertIn(
            "the template's spend guard violates the r3 manifest: HLM_LLM_BUDGET_MONTH_USD=60 (at most 10)",
            output,
        )
        self.assertEqual(before, (root / "llm.env").read_text())
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

    # ------------------------------------------------------------------ R-2 rollback safety dump
    def killed_destructive_rollback(self):
        """R3 over R2, the R3 env installed, a rollback killed inside its destructive phase."""
        root, env = self.r3_deployed()
        self.install_r3_env(root)
        killed, output, _ = self.remote(root, dict(env, FAIL="rollback-kill"), "--rollback")
        self.assertNotEqual(0, killed.returncode, output)
        state = self.state(root)
        self.assertTrue(state["rollback_destructive"], "the destructive phase began")
        safety = Path(state["rollback_safety"])
        # R-2: outside every rotated tier (daily/weekly/pre-upgrade)
        self.assertEqual((root / "backups/rollback").resolve(), safety.parent.resolve())
        return root, env, safety

    def backup_until_unlocked(self, root, env):
        """The daily backup timer's run (the harness's backup.sh), retried while the killed
        runner's last child still holds the operation lock."""
        for _ in range(40):
            result = subprocess.run(
                ["bash", str(root / "app/deploy/backup/backup.sh")],
                env=dict(env, HLM_ENV_FILE=str(root / "prod.env")),
                text=True,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=60,
            )
            if "Another backup/restore is active" not in result.stderr:
                return result
            time.sleep(0.25)
        self.fail("operation lock never released")

    def test_missing_safety_dump_after_the_destructive_phase_fails_closed(self):
        """R4 R-2 (review 77 / consult 89 (h)): a rollback_destructive=true journal whose safety dump
        is gone (an older release's rotation, a manual delete): the retry, with every service
        healthy, must NOT restore nothing, start the current release and close the journal. It stops
        FAIL CLOSED: writers stay stopped, nothing restarts, no end-rollback, the journal stays open."""
        root, env, safety = self.killed_destructive_rollback()
        safety.unlink()
        result, output, rows = self.rerun_until_unlocked(root, env)
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn("FAIL CLOSED: the destructive phase of the rollback to", output)
        self.assertIn(f"the saved database of {NEXT} ({safety}) is missing", output)
        self.assertNotIn("rollback aborted", output)
        self.assertNotIn("Rollback complete", output)
        self.assertFalse([r for r in rows if "up" in r], "nothing was started")
        self.assertFalse([r for r in rows if "dropdb" in r[-1]], "no database was touched")
        state = self.state(root)
        self.assertEqual(PREVIOUS, state["rollback_in_progress"], "the journal stays open")
        self.assertEqual(str(safety), state["rollback_safety"])
        self.assertTrue(state["rollback_destructive"])
        # and it stays closed on every re-run until the operator recovers by hand
        result, output, rows = self.rerun_until_unlocked(root, env)
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn("FAIL CLOSED", output)
        self.assertFalse([r for r in rows if "up" in r])

    def test_daily_backups_after_a_killed_rollback_keep_its_safety_dump(self):
        """R4 R-2: the backup timer runs (twice, the same day) after a killed destructive rollback;
        the safety dump survives (outside rotation), and the retry's recovery restores from it."""
        root, env, safety = self.killed_destructive_rollback()
        for _ in range(2):
            result = self.backup_until_unlocked(root, env)
            self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(safety.exists())
        result, output, _ = self.rerun_until_unlocked(root, dict(env, FAIL="rollback-up"))
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn(f"Current release {NEXT} restored (database from {safety})", output)
        self.assertNotIn("rollback_safety", self.state(root))
        self.assertTrue(safety.exists(), "kept (deleted by hand once settled)")

    def test_rollback_refuses_while_a_backup_or_restore_holds_the_operation_lock(self):
        """R4 R-2 (review 77): backup, restore and rollback share $HLM_BACKUP_DIR/.operation.flock;
        a rollback started while a backup or restore holds it refuses before anything stops, and
        the backup timer cannot start while a rollback holds it."""
        root, env = self.r3_deployed()
        self.install_r3_env(root)
        (root / "backups").mkdir(exist_ok=True)
        with open(root / "backups/.operation.flock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result, output, rows = self.remote(root, env, "--rollback")
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn("Rollback refused (nothing was stopped): a backup or restore is running", output)
        self.assertFalse([r for r in rows if "stop" in r])
        self.assertNotIn("rollback_in_progress", self.state(root))
        result, output, _ = self.remote(root, env, "--rollback")
        self.assertEqual(0, result.returncode, output)
        self.assertIn("Rollback complete", output)
        saved = [line for line in output.splitlines() if line.startswith("Saved the current database")]
        self.assertTrue(saved and "/backups/rollback/hlmemo-rollback-" in saved[0], saved)

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
