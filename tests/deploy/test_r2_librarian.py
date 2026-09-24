"""R2 (librarian ON, observer) deploy checks, offline.

* remote-deploy.sh's post-cutover librarian check under the fake ssh/docker harness (D-037b):
  a missing llm.env idles and deploys; llm.env enabling the librarian needs an enabled heartbeat
  in the configured role and an api that sees the same switch; an unreachable provider is only
  reported; a failure leaves the new stack running (no database rollback).
* check_librarian.py observer-gate (remote_gates.sh --librarian) on crafted job/audit reports.
"""

import json
import subprocess
import sys
import tempfile
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


def report(service, *, enabled=True, role="observer", hb_enabled=True, hb_role="observer", **kw):
    out = {
        "service": service,
        "enabled": enabled,
        "role": role,
        "llm_mode": "live",
        "profile": "openrouter-gpt6-luna",
        "fallback": "openrouter",
        "profiles": profiles(kw.get("reachable", True), kw.get("key", True)),
    }
    if service == "librarian":
        out["heartbeat"] = {
            "enabled": hb_enabled,
            "role": hb_role,
            "breaker_state": "closed",
            "age_s": 3.2,
            "ready": 0,
            "spend_hour_usd": 0.0,
            "spend_today_usd": 0.01,
            "reserved_usd": 0.0,
        }
    else:
        out["risk_judge"] = ["openrouter-gpt6-luna"]
    return json.dumps(out)


class R2DeployCheckTest(unittest.TestCase):
    prepare_deploy = harness.DeployRecoveryTest.prepare_deploy
    prepare_split = w0.W0DeployTest.prepare_split
    deploy = w0.W0DeployTest.deploy

    def run_r2(self, llm_env, librarian=None, api=None):
        root, env = self.prepare_split("")
        if llm_env:
            (root / "llm.env").write_text("HLM_LIBRARIAN_ENABLED=true\n")
        if librarian is not None:
            env["LIBRARIAN_REPORT_LIBRARIAN"] = librarian
        if api is not None:
            env["LIBRARIAN_REPORT_API"] = api
        result, output = self.deploy(root, env)
        rows = [json.loads(line) for line in (root / "events").read_text().splitlines()]
        return root, result, output, rows

    def test_missing_llm_env_idles_and_deploys(self):
        root, result, output, rows = self.run_r2(llm_env=False)
        self.assertEqual(0, result.returncode, output)
        self.assertIn("librarian: idle (no llm.env)", output)
        self.assertIn("RESULT librarian PASS llm.env=absent enabled=false", output)
        collects = [r for r in rows if "collect" in r]
        self.assertEqual(
            [("librarian", True), ("api", False)],
            [(r[r.index("--service") + 1], "--probe" in r) for r in collects],
        )
        cutover = next(i for i, r in enumerate(rows) if "up" in r and "api" in r)
        self.assertTrue(all(rows.index(r) > cutover for r in collects), "checked after cutover")
        self.assertIn("Deployment ready", output)

    def test_llm_env_enabled_observer_passes(self):
        _, result, output, _ = self.run_r2(True, report("librarian"), report("api"))
        self.assertEqual(0, result.returncode, output)
        self.assertIn("librarian heartbeat: enabled=True role=observer breaker_state=closed", output)
        self.assertIn("api: enabled=true mode=live risk_judge=openrouter-gpt6-luna", output)
        self.assertIn("RESULT librarian PASS llm.env=present enabled=true role=observer", output)
        self.assertIn("key set, reachable (HTTP 200)", output)

    def test_unreachable_provider_is_reported_not_fatal(self):
        lib, api = report("librarian", reachable=False), report("api", reachable=False)
        _, result, output, _ = self.run_r2(True, lib, api)
        self.assertEqual(0, result.returncode, output)
        self.assertIn("UNREACHABLE (URLError); reported only", output)
        self.assertIn("RESULT librarian PASS", output)

    def test_mismatches_fail_after_cutover_without_database_rollback(self):
        cases = {
            "heartbeat enabled=False although llm.env enables": (
                report("librarian", hb_enabled=False),
                report("api"),
            ),
            "heartbeat role=assistant, configured observer": (
                report("librarian", hb_role="assistant"),
                report("api"),
            ),
            "api and librarian see different": (report("librarian"), report("api", enabled=False)),
            "provider key missing for openrouter-gpt6-luna,openrouter": (
                report("librarian", key=False),
                report("api", key=False),
            ),
        }
        for message, (lib, api) in cases.items():
            with self.subTest(message=message):
                root, result, output, rows = self.run_r2(True, lib, api)
                self.assertNotEqual(0, result.returncode, output)
                self.assertIn("RESULT librarian FAIL", output)
                self.assertIn(message, output)
                self.assertIn("new stack left running (no database rollback)", output)
                self.assertEqual(NEXT, (root / "current-ref").read_text().strip(), "cutover kept")
                self.assertFalse(any("dropdb" in r[-1] for r in rows))
                self.assertNotIn("Deployment ready", output)

    def test_no_report_fails(self):
        _, result, output, _ = self.run_r2(True, "", report("api"))
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn("RESULT librarian FAIL no usable report from librarian", output)


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
