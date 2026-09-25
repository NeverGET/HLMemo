"""R2 (librarian ON, observer) deploy checks, offline.

* remote-deploy.sh's post-cutover librarian check under the fake ssh/docker harness (D-037b):
  with llm.env present the api settings, the librarian settings and the heartbeat must all be
  enabled/live/observer and the api risk judge must load non-empty (Sol 48); an unreachable
  provider is only reported; a failure leaves the new stack running (no database rollback).
  R3 (D-111 #6): a missing llm.env fails the cutover; R3 mode (the api runs HLM_ENV_RELEASE=r3, or
  ``evaluate --release r3``) requires the rewrite ON; the R2 env on the R3 image is the D-108
  interim and says so.
* check_librarian.py observer-gate (remote_gates.sh --librarian) on crafted job/audit reports.
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_deploy_recovery as harness  # noqa: E402  (module import: its tests are not re-collected)
import test_w0_access as w0  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "deploy/scripts/check_librarian.py"
NEXT = harness.NEXT


def profiles(reachable=True, key=True):
    probe = {"reachable": True, "status": 200} if reachable else {"reachable": False, "error": "URLError"}
    return [
        {"name": name, "base_url": "https://llm.invalid/api/v1", "key_set": key, "probe": dict(probe)}
        for name in ("openrouter-gpt6-luna", "openrouter")
    ]


#: D-116: the manifest keys an llm.env of the R3 template carries (install_llm_env.sh)
R3_MANIFEST_ENV = {
    "HLM_PROFILE": "openrouter-gpt6-luna",
    "HLM_FALLBACK_PROFILE": "openrouter-glm53-flash",
    "HLM_FALLBACK_PROFILE__SYNTHESIS": "openrouter",
    "HLM_FALLBACK_PROFILE__RISK_JUDGE": "openrouter-qwen38-27b-fast",
    "HLM_QUERY_REWRITE": None,
    "HLM_RETRIEVAL_SOURCE_CAP": None,
}
#: ... and an R2 llm.env (no marker, no per-task fallbacks)
R2_MANIFEST_ENV = {
    **dict.fromkeys(R3_MANIFEST_ENV),
    "HLM_PROFILE": "openrouter-gpt6-luna",
    "HLM_FALLBACK_PROFILE": "openrouter",
}


def report(service, *, enabled=True, role="observer", mode="live", hb_enabled=True, hb_role="observer", **kw):
    out = {
        "service": service,
        "enabled": enabled,
        "role": role,
        "llm_mode": mode,
        "profile": "openrouter-gpt6-luna",
        "fallback": "openrouter",
        "profiles": profiles(kw.get("reachable", True), kw.get("key", True)),
        "env_release": kw.get("env_release", "r3"),  # D-111: the llm.env marker the container runs
        # D-116: the manifest keys the container runs with (R2 env when it carries no marker)
        "manifest_env": {
            **(R3_MANIFEST_ENV if kw.get("env_release", "r3") else R2_MANIFEST_ENV),
            **kw.get("manifest", {}),
        },
    }
    if service == "librarian":
        out["heartbeat"] = {
            "enabled": hb_enabled,
            "role": hb_role,
            "breaker_state": "closed",
            "age_s": kw.get("hb_age", 3.2),
            "ready": 0,
            "spend_hour_usd": 0.0,
            "spend_today_usd": 0.01,
            "reserved_usd": 0.0,
        }
        if kw.get("hb_missing"):
            out["heartbeat"] = {"error": "FileNotFoundError"}
        out.update(
            heartbeat_interval_s=10.0, heartbeat_max_age_s=30.0, heartbeat_waited_s=kw.get("waited", 0.0)
        )
    else:
        out["risk_judge"] = kw.get("risk_judge", ["openrouter-gpt6-luna"])
        if "risk_judge_error" in kw:
            out.pop("risk_judge")
            out["risk_judge_error"] = kw["risk_judge_error"]
    return json.dumps(out)


class R2DeployCheckTest(unittest.TestCase):
    prepare_deploy = harness.DeployRecoveryTest.prepare_deploy
    prepare_split = w0.W0DeployTest.prepare_split
    deploy = w0.W0DeployTest.deploy

    def run_r2(self, llm_env, librarian=None, api=None):
        root, env = self.prepare_split("")
        if llm_env:
            (root / "llm.env").write_text("HLM_LIBRARIAN_ENABLED=true\n")
        else:
            (root / "llm.env").unlink()  # the harness host has the R3 env by default
        if librarian is not None:
            env["LIBRARIAN_REPORT_LIBRARIAN"] = librarian
        if api is not None:
            env["LIBRARIAN_REPORT_API"] = api
        result, output = self.deploy(root, env)
        rows = [json.loads(line) for line in (root / "events").read_text().splitlines()]
        return root, result, output, rows

    def test_missing_llm_env_fails_the_r3_cutover(self):
        """D-111 #6 / D-116: the R1-style idle pass is gone for R3; its llm.env is release state."""
        idle_lib = report("librarian", enabled=False, hb_enabled=False, env_release=None)
        idle_api = report("api", enabled=False, env_release=None)
        self.assert_fails_after_cutover(
            False,
            idle_lib,
            idle_api,
            "R3 release: llm.env is required (the r3 manifest, D-111/D-116; install_llm_env.sh)",
        )

    def test_missing_llm_env_fails_even_when_everything_else_passes(self):
        """Review 72 (sol) trigger: an R3 cutover with llm.env missing PASSed (R1-style)."""
        self.assert_fails_after_cutover(
            False,
            report("librarian", env_release=None),
            report("api", env_release=None),
            "RESULT librarian FAIL R3 release: llm.env is required",
        )

    def test_llm_env_enabled_observer_passes(self):
        """Happy path: api settings, librarian settings and heartbeat all enabled/live/observer."""
        _, result, output, rows = self.run_r2(True, report("librarian"), report("api"))
        self.assertEqual(0, result.returncode, output)
        self.assertIn("librarian heartbeat: enabled=True role=observer breaker_state=closed", output)
        self.assertIn("api: enabled=true role=observer mode=live risk_judge=openrouter-gpt6-luna", output)
        self.assertIn(
            "RESULT librarian PASS llm.env=present release=r3 manifest=r3 enabled=true mode=live"
            " role=observer risk_judge=openrouter-gpt6-luna",
            output,
        )
        self.assertIn("key set, reachable (HTTP 200)", output)
        self.assertIn("Deployment ready", output)
        collects = [r for r in rows if "collect" in r]
        self.assertEqual(
            [("librarian", True), ("api", False)],
            [(r[r.index("--service") + 1], "--probe" in r) for r in collects],
        )
        # Sol 49: the librarian report re-reads a missing/stale heartbeat for up to 45 s.
        self.assertEqual("45", collects[0][collects[0].index("--wait-heartbeat") + 1])
        cutover = next(i for i, r in enumerate(rows) if "up" in r and "api" in r)
        self.assertTrue(all(rows.index(r) > cutover for r in collects), "checked after cutover")

    def test_unreachable_provider_is_reported_not_fatal(self):
        lib, api = report("librarian", reachable=False), report("api", reachable=False)
        _, result, output, _ = self.run_r2(True, lib, api)
        self.assertEqual(0, result.returncode, output)
        self.assertIn("UNREACHABLE (URLError); reported only", output)
        self.assertIn("RESULT librarian PASS", output)

    def assert_fails_after_cutover(self, llm_env, lib, api, *messages):
        root, result, output, rows = self.run_r2(llm_env, lib, api)
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn("RESULT librarian FAIL", output)
        for message in messages:
            self.assertIn(message, output)
        self.assertIn("new stack left running (no database rollback)", output)
        self.assertEqual(NEXT, (root / "current-ref").read_text().strip(), "cutover kept")
        self.assertFalse(any("dropdb" in r[-1] for r in rows))
        self.assertNotIn("Deployment ready", output)

    def test_r2_fails_unless_every_source_is_enabled_live_observer(self):
        """Sol 48 High: with llm.env present, OFF, a non-live mode or a role above observer in the
        api settings, the librarian settings or the heartbeat is a failed R2 release."""
        cases = {
            "off": (
                report("librarian", enabled=False, hb_enabled=False),
                report("api", enabled=False),
                (
                    "api settings: HLM_LIBRARIAN_ENABLED=False (R2: true)",
                    "librarian settings: HLM_LIBRARIAN_ENABLED=False (R2: true)",
                    "heartbeat enabled=False (R2: True)",
                ),
            ),
            "mode off": (
                report("librarian", mode="off", hb_enabled=False),
                report("api", mode="off"),
                (
                    "api settings: HLM_LLM_MODE=off (R2: live)",
                    "librarian settings: HLM_LLM_MODE=off (R2: live)",
                ),
            ),
            "wrong role everywhere": (
                report("librarian", role="assistant", hb_role="assistant"),
                report("api", role="assistant"),
                (
                    "api settings: HLM_LIBRARIAN_ROLE=assistant (R2: observer)",
                    "librarian settings: HLM_LIBRARIAN_ROLE=assistant (R2: observer)",
                    "heartbeat role=assistant (R2: observer)",
                ),
            ),
            "heartbeat role only": (
                report("librarian", hb_role="autonomous"),
                report("api"),
                ("heartbeat role=autonomous (R2: observer)",),
            ),
            "heartbeat disabled only": (
                report("librarian", hb_enabled=False),
                report("api"),
                ("heartbeat enabled=False (R2: True)",),
            ),
            "keys missing": (
                report("librarian", key=False),
                report("api", key=False),
                ("provider key missing for openrouter-gpt6-luna,openrouter",),
            ),
        }
        for name, (lib, api, messages) in cases.items():
            with self.subTest(name):
                self.assert_fails_after_cutover(True, lib, api, *messages)

    def test_heartbeat_freshness(self):
        """Sol 49: with llm.env present a fresh heartbeat passes; one older than 3 intervals (30 s)
        or none at all after the post-cutover wait fails the R2 check."""
        _, result, output, _ = self.run_r2(True, report("librarian", hb_age=29.9), report("api"))
        self.assertEqual(0, result.returncode, output)
        self.assertIn("age_s=29.9 ready=0 (max age 30s, waited 0.0s)", output)
        self.assert_fails_after_cutover(
            True,
            report("librarian", hb_age=95.0, waited=45.0),
            report("api"),
            "heartbeat stale: 95.0s old > 30s (3 x the 10s interval; waited 45.0s after cutover)",
        )
        self.assert_fails_after_cutover(
            True,
            report("librarian", hb_missing=True, waited=45.0),
            report("api"),
            "no librarian heartbeat (FileNotFoundError; waited 45.0s after cutover)",
        )

    def test_api_librarian_mismatch_fails(self):
        cases = {
            "api off": (report("api", enabled=False), "api settings: HLM_LIBRARIAN_ENABLED=False (R2: true)"),
            "api role": (report("api", role="assistant"), "api settings: HLM_LIBRARIAN_ROLE=assistant"),
            "api mode": (report("api", mode="replay"), "api settings: HLM_LLM_MODE=replay (R2: live)"),
        }
        for name, (api, message) in cases.items():
            with self.subTest(name):
                self.assert_fails_after_cutover(
                    True, report("librarian"), api, "api and librarian settings differ", message
                )

    def test_risk_judge_configuration_error_or_empty_chain_fails(self):
        """Sol 48 Medium: the api's judge must load with a non-empty chain when llm.env is present."""
        self.assert_fails_after_cutover(
            True,
            report("librarian"),
            report("api", risk_judge_error="LlmConfigError"),
            "api: enabled=true role=observer mode=live risk_judge=ERROR LlmConfigError",
            "api risk-judge configuration error (LlmConfigError)",
        )
        self.assert_fails_after_cutover(
            True, report("librarian"), report("api", risk_judge=[]), "api risk-judge chain is empty"
        )

    def test_r3_manifest_rewrite_and_cap_off_and_the_d094_mapping_present(self):
        """D-116: the R3 manifest, not a hard-coded flag: HLM_QUERY_REWRITE and
        HLM_RETRIEVAL_SOURCE_CAP absent or false in the api's AND the librarian's env; the D-094
        fallback keys present."""
        for off in (None, "false", "0", "FALSE", "no", ""):
            with self.subTest(off=off):
                manifest = {"HLM_QUERY_REWRITE": off, "HLM_RETRIEVAL_SOURCE_CAP": off}
                code, output = self.evaluate(
                    report("librarian", manifest=manifest), report("api", manifest=manifest)
                )
                self.assertEqual(0, code, output)
                self.assertIn("RESULT librarian PASS llm.env=present release=r3 manifest=r3", output)
        cases = {
            "rewrite on (api)": (
                report("librarian"),
                report("api", manifest={"HLM_QUERY_REWRITE": "true"}),
                "api runs HLM_QUERY_REWRITE=true (r3 manifest: absent or false, D-116",
            ),
            "cap on (librarian)": (
                report("librarian", manifest={"HLM_RETRIEVAL_SOURCE_CAP": "1"}),
                report("api"),
                "librarian runs HLM_RETRIEVAL_SOURCE_CAP=1 (r3 manifest: absent or false",
            ),
            "risk-judge fallback missing": (
                report("librarian"),
                report("api", manifest={"HLM_FALLBACK_PROFILE__RISK_JUDGE": None}),
                "api llm.env lacks HLM_FALLBACK_PROFILE__RISK_JUDGE (r3 manifest: the D-094 fallback mapping",
            ),
            "synthesis fallback empty": (
                report("librarian", manifest={"HLM_FALLBACK_PROFILE__SYNTHESIS": ""}),
                report("api"),
                "librarian llm.env lacks HLM_FALLBACK_PROFILE__SYNTHESIS",
            ),
        }
        for name, (lib, api, message) in cases.items():
            with self.subTest(name):
                code, output = self.evaluate(lib, api)
                self.assertEqual(1, code, output)
                self.assertIn(message, output)
        # end to end through the deploy runner: the manifest line and a failing cutover
        _, result, output, _ = self.run_r2(True, report("librarian"), report("api"))
        self.assertEqual(0, result.returncode, output)
        self.assertIn("llm.env manifest: env_release=r3 (code r3, check mode r3)", output)
        self.assertIn("HLM_QUERY_REWRITE=- HLM_RETRIEVAL_SOURCE_CAP=-", output)
        self.assert_fails_after_cutover(
            True,
            report("librarian"),
            report("api", manifest={"HLM_QUERY_REWRITE": "true"}),
            "api runs HLM_QUERY_REWRITE=true",
        )

    def test_the_r3_template_satisfies_the_r3_manifest(self):
        """deploy/llm.env.example (what install_llm_env.sh writes) carries the marker, the D-094
        keys and no query rewrite / per-source cap switched on."""
        spec = importlib.util.spec_from_file_location("check_librarian", CHECK)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        env = {}
        for line in (ROOT / "deploy/llm.env.example").read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not line.startswith("#"):
                env[key.strip()] = value.strip()
        manifest = mod.RELEASE_MANIFESTS[mod.CODE_RELEASE]
        self.assertEqual(mod.CODE_RELEASE, env.get(mod.ENV_RELEASE_KEY))
        for key in manifest["present"]:
            self.assertTrue(env.get(key), key)
        for key in manifest["off"]:
            self.assertIn(env.get(key, "false").lower(), ("", "0", "false", "no", "off"), key)

    def test_no_llm_env_fails_whatever_runs(self):
        """D-111 #6: without llm.env an R3 cutover fails, idle or active."""
        for lib, api in (
            (report("librarian", enabled=False, hb_enabled=False), report("api")),
            (report("librarian", enabled=False, hb_enabled=True), report("api", enabled=False)),
        ):
            self.assert_fails_after_cutover(False, lib, api, "R3 release: llm.env is required")

    def test_r3_env_marker_puts_the_check_in_r3_mode(self):
        """D-111 #6 / D-116: an api running HLM_ENV_RELEASE=r3 is checked in R3 mode: the full R3
        manifest (the D-094 mapping present) is required."""
        self.assert_fails_after_cutover(
            True,
            report("librarian"),
            report("api", manifest={"HLM_FALLBACK_PROFILE__SYNTHESIS": None}),
            "check mode r3",
            "api llm.env lacks HLM_FALLBACK_PROFILE__SYNTHESIS",
        )

    def test_r2_env_interim_passes_and_says_so(self):
        """D-108 Order B steps 1-2: the R3 image with the R2 llm.env (no marker, no per-task
        fallbacks) passes the R2 checks and the manifest's "off" keys; the result line names the
        interim and the next step."""
        lib = report("librarian", env_release=None)
        api = report("api", env_release=None)
        _, result, output, _ = self.run_r2(True, lib, api)
        self.assertEqual(0, result.returncode, output)
        self.assertIn("check mode r2-env interim", output)
        self.assertIn(
            "RESULT librarian PASS llm.env=present release=r2-env (D-108 interim: install the R3 llm.env,"
            " recreate librarian api, then evaluate --release r3)",
            output,
        )
        # the manifest's "off" keys hold in the interim too, and api and librarian run one env
        self.assert_fails_after_cutover(
            True,
            lib,
            report("api", env_release=None, manifest={"HLM_RETRIEVAL_SOURCE_CAP": "true"}),
            "api runs HLM_RETRIEVAL_SOURCE_CAP=true",
        )
        self.assert_fails_after_cutover(
            True, lib, report("api"), "api and librarian run different llm.env releases (r3 vs None)"
        )

    def evaluate(self, lib, api, *extra):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name, rep in (("librarian", lib), ("api", api)):
                path = Path(tmp) / f"{name}.json"
                path.write_text(rep + "\n")
                paths.append(str(path))
            result = subprocess.run(
                [sys.executable, str(CHECK), "evaluate", "--llm-env", "present",
                 "--librarian", paths[0], "--api", paths[1], *extra],
                text=True, capture_output=True, timeout=60,
            )  # fmt: skip
        return result.returncode, result.stdout + result.stderr

    def test_release_r3_flag_fails_on_the_r2_env(self):
        """D-111 #6: ``evaluate --release r3`` (D-108 step 4, after the env switch) fails while the
        api still runs the R2 env, even when everything else would pass the interim."""
        lib = report("librarian", env_release=None)
        code, output = self.evaluate(lib, report("api", env_release=None))
        self.assertEqual(0, code, output)  # without the flag: the interim
        for api, messages in (
            (
                report("api", env_release=None),
                (
                    "api runs llm.env release=- (HLM_ENV_RELEASE; r3 manifest: r3)",
                    "api llm.env lacks HLM_FALLBACK_PROFILE__SYNTHESIS,HLM_FALLBACK_PROFILE__RISK_JUDGE",
                ),
            ),
        ):
            code, output = self.evaluate(lib, api, "--release", "r3")
            self.assertEqual(1, code, output)
            for message in messages:
                self.assertIn(message, output)
        code, output = self.evaluate(report("librarian"), report("api"), "--release", "r3")
        self.assertEqual(0, code, output)
        self.assertIn("RESULT librarian PASS llm.env=present release=r3", output)

    def test_no_report_fails(self):
        _, result, output, _ = self.run_r2(True, "", report("api"))
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn("RESULT librarian FAIL no usable report from librarian", output)


class HeartbeatWaitTest(unittest.TestCase):
    """check_librarian.wait_for_heartbeat (collect --wait-heartbeat): the first heartbeat after
    cutover may arrive during the wait; a stale one is re-read until the wait runs out."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("check_librarian", CHECK)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)  # module level imports nothing from hlmemo

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "hb.json"

    def write(self, age_s, delay_s=0.0):
        def run():
            time.sleep(delay_s)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"enabled": True, "role": "observer", "ts": time.time() - age_s}))
            tmp.replace(self.path)

        if not delay_s:
            return run()
        thread = threading.Thread(target=run)
        thread.start()
        self.addCleanup(thread.join)

    def test_fresh_heartbeat_returns_at_once(self):
        self.write(age_s=2)
        hb, waited = self.mod.wait_for_heartbeat(self.path, 30.0, 45.0)
        self.assertLess(hb["age_s"], 30)
        self.assertEqual((True, "observer"), (hb["enabled"], hb["role"]))
        self.assertLess(waited, 1.0)

    def test_first_heartbeat_arrives_within_the_wait(self):
        self.write(age_s=0, delay_s=1.5)  # no file yet: the librarian has not written one
        hb, waited = self.mod.wait_for_heartbeat(self.path, 30.0, 10.0)
        self.assertNotIn("error", hb)
        self.assertLess(hb["age_s"], 30)
        self.assertGreaterEqual(waited, 1.0)
        self.assertLess(waited, 5.0)

    def test_stale_heartbeat_refreshed_within_the_wait(self):
        self.write(age_s=120)
        self.write(age_s=0, delay_s=1.5)
        hb, waited = self.mod.wait_for_heartbeat(self.path, 30.0, 10.0)
        self.assertLess(hb["age_s"], 30)
        self.assertLess(waited, 5.0)

    def test_stale_or_missing_after_the_wait_is_reported_as_is(self):
        hb, waited = self.mod.wait_for_heartbeat(self.path, 30.0, 1.2)
        self.assertEqual({"error": "FileNotFoundError"}, hb)
        self.assertGreaterEqual(waited, 1.2)
        self.write(age_s=100)
        hb, waited = self.mod.wait_for_heartbeat(self.path, 30.0, 1.2)
        self.assertGreater(hb["age_s"], 30)
        self.assertGreaterEqual(waited, 1.2)
        self.assertLess(waited, 3.0)


class ObserverGateTest(unittest.TestCase):
    """check_librarian.py observer-gate: the --librarian verdict of remote_gates.sh."""

    EVENT = 4242

    def gate(self, job, audit):
        with tempfile.TemporaryDirectory() as tmp:
            jpath, apath = Path(tmp) / "job.json", Path(tmp) / "audit.json"
            jpath.write_text(json.dumps(job) + "\n")
            apath.write_text(audit if isinstance(audit, str) else json.dumps(audit))
            result = subprocess.run(
                [
                    sys.executable,
                    str(CHECK),
                    "observer-gate",
                    "--job",
                    str(jpath),
                    "--audit",
                    str(apath),
                    "--version-id",
                    "77",
                ],
                text=True,
                capture_output=True,
                timeout=30,
            )
        return result.returncode, result.stdout.strip().splitlines()[-1]

    def job(self, **kw):
        key = f"librarian_write:{self.EVENT}"
        out = {
            "version_id": 77,
            "source_event_id": self.EVENT,
            "waited_s": 12.0,
            "jobs": [{"key": key, "status": "done", "attempts": 1, "last_error": None}],
            "events": [
                {
                    "job": key,
                    "event_id": 5000,
                    "role": "observer",
                    "outcome": "proposed",
                    "mutations": {"signal_upsert": 1},
                    "questions": 1,
                }
            ],
            "version_signals": True,
        }
        out.update(kw)
        return out

    def audit(self, status="open", applied_at=None):
        return {
            "project": "gates-probe",
            "proposals": [
                {
                    "question_id": "q1",
                    "batch_id": "b1",
                    "job": f"librarian_write:{self.EVENT}",
                    "status": status,
                },
                {"question_id": "q0", "batch_id": "b0", "job": "librarian_write:1", "status": "applied"},
            ],
            "batches": [{"batch_id": "b1", "applied_at": applied_at}, {"batch_id": "b0", "applied_at": "x"}],
        }

    def test_observer_processing_passes(self):
        rc, line = self.gate(self.job(), self.audit())
        self.assertEqual(0, rc, line)
        self.assertTrue(line.startswith("RESULT librarian PASS v77 1 job(s) done"), line)
        self.assertIn("signal_upsert=1 links/closes=0", line)
        self.assertIn("audit proposals=1 (open), applied=0", line)

    def test_violations_fail(self):
        event = self.job()["events"][0]
        cases = [
            (self.job(jobs=[]), self.audit(), "no librarian_write job for event 4242"),
            (
                self.job(
                    jobs=[
                        {
                            "key": "librarian_write:4242",
                            "status": "queued",
                            "attempts": 2,
                            "last_error": "E_X",
                        }
                    ]
                ),
                self.audit(),
                "not done",
            ),
            (self.job(events=[{**event, "role": "assistant"}]), self.audit(), "expected observer"),
            (
                self.job(
                    events=[
                        {**event, "mutations": {"signal_upsert": 1, "link_insert": 2, "version_close": 1}}
                    ]
                ),
                self.audit(),
                "observer applied links/invalidations",
            ),
            (self.job(version_signals=False), self.audit(), "no version_signals row for v77; signal_upsert"),
            (self.job(version_signals=None), self.audit(), "no version_signals table"),
            (self.job(), self.audit(status="applied"), "applied proposals ['q1']"),
            (self.job(), self.audit(applied_at="2026-09-24T00:00:00Z"), "batches ['b1']"),
            (self.job(), "not json", "is not JSON"),
        ]
        for job, audit, message in cases:
            with self.subTest(message=message):
                rc, line = self.gate(job, audit)
                self.assertEqual(1, rc, line)
                self.assertTrue(line.startswith("RESULT librarian FAIL"), line)
                self.assertIn(message, line)


if __name__ == "__main__":
    unittest.main()
