"""R2 (librarian ON, observer) deploy checks, offline.

* remote-deploy.sh's post-cutover librarian check under the fake ssh/docker harness (D-037b):
  with llm.env present the api settings, the librarian settings and the heartbeat must all be
  enabled/live/observer and the api risk judge must load non-empty (Sol 48); an unreachable
  provider is only reported; a failure leaves the new stack running (no database rollback).
  R3 (D-111 #6): a missing llm.env fails the cutover; R3 mode (the api runs HLM_ENV_RELEASE=r3, or
  ``evaluate --release r3``) requires the rewrite ON; the R2 env on the R3 image is the D-108
  interim and says so. R4 (review 79 T5): this checkout is CODE_RELEASE r4; an R3 env on it is
  checked against the R3 manifest (memory.ask and the map summaries OFF), an R4 env against the R4
  manifest (both ON, their fallbacks pinned, the per-question limits bounded).
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
    "HLM_FALLBACK_PROFILE__QUERY_REWRITE": "openrouter",
    "HLM_FALLBACK_PROFILE__RISK_JUDGE": "openrouter-qwen38-27b-fast",
    "HLM_QUERY_REWRITE": None,
    "HLM_RETRIEVAL_SOURCE_CAP": None,
    # D-121: the spend guard ON, month <= $10, day and hour <= month
    "HLM_LLM_BUDGET_HOUR_USD": "1",
    "HLM_LLM_BUDGET_DAY_USD": "2",
    "HLM_LLM_BUDGET_MONTH_USD": "10",
    "HLM_LLM_BUDGET_DISABLED": "false",
}
#: R4 plan §1.3 (D-198): the prod writer
WRITER = "google-gemini38-flash-medium"
#: D-136 / review 79 T5 / R4 plan §1.5: what the R4 template adds (install_llm_env.sh)
R4_MANIFEST_ENV = {
    **R3_MANIFEST_ENV,
    "HLM_RESEARCH_ENABLED": "true",
    "HLM_MAP_SUMMARY_ENABLED": "false",
    "HLM_FALLBACK_PROFILE__RESEARCH": "openrouter",
    "HLM_FALLBACK_PROFILE__MAP_SUMMARY": "openrouter-glm53-flash",
    "HLM_RESEARCH_MAX_USD": "0.12",
    "HLM_RESEARCH_MAX_TOKENS": "100000",
    "HLM_RESEARCH_ANSWER_MODE": "prose",
    "HLM_RESEARCH_ATTRIBUTION": "llm",
    "HLM_RESEARCH_RERANK": "llm",
    "HLM_RESEARCH_WRITER_PROFILE": WRITER,
    "HLM_RESEARCH_WRITER_TIMEOUT_S": "120",
    "HLM_RESEARCH_HTTP_TIMEOUT_S": "150",
    "HLM_DETACHED_HOLD_MAX_S": "180",
    "HLM_RESEARCH_TIMEOUT_S": "170",
    "HLM_RESEARCH_PROSE_MAX_TOKENS": "16000",
    "HLM_LLM_TIMEOUT_S": "180",
    # R4 plan §1.5 / D-198: the owner's caps for the test month (_BUDGETS_R4)
    "HLM_LLM_BUDGET_HOUR_USD": "3",
    "HLM_LLM_BUDGET_DAY_USD": "8",
    "HLM_LLM_BUDGET_MONTH_USD": "60",
}
#: ... and an R2 llm.env (no marker, no per-task fallbacks)
R2_MANIFEST_ENV = {
    **dict.fromkeys(R3_MANIFEST_ENV),
    "HLM_PROFILE": "openrouter-gpt6-luna",
    "HLM_FALLBACK_PROFILE": "openrouter",
}


def _manifest_env(label):
    if label == "r4":
        return R4_MANIFEST_ENV
    return R3_MANIFEST_ENV if label else R2_MANIFEST_ENV


def r4_research(writer=WRITER, *, key=True, until="2099-12-31", trace=False, **extra):
    """R4 collect's effective research state: the writer profile as the container loads it (key
    SET or not, its price_valid_until) and whether the research tracer is on."""
    return {
        "enabled": True,
        "map_summary": False,
        "max_usd": 0.12,
        "max_tokens": 100000,
        "answer_mode": "prose",
        "writer": None if writer is None else {"profile": writer, "key_set": key, "price_valid_until": until},
        "trace_dir_set": trace,
        **extra,
    }


def report(service, *, enabled=True, role="observer", mode="live", hb_enabled=True, hb_role="observer", **kw):
    manifest_env = {**_manifest_env(kw.get("env_release", "r3")), **kw.get("manifest", {})}
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
        "manifest_env": manifest_env,
    }
    writer = manifest_env.get("HLM_RESEARCH_WRITER_PROFILE")
    if kw.get("env_release") == "r4" and "research" not in kw:  # R4 collect: the research state
        kw["research"] = r4_research(writer, key=kw.get("writer_key", True))
    if writer:  # R4 B5: the writer is a profile of the chain (key_set covers its key)
        out["profiles"].append(
            {"name": writer, "base_url": "https://gemini.invalid/v1", "key_set": kw.get("writer_key", True)}
        )
    if "research" in kw:  # review 79 T5: the effective research state (collect)
        out["research"] = kw["research"]
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


#: the spend caps (hour, day, month)
CAP_KEYS = ("HLM_LLM_BUDGET_HOUR_USD", "HLM_LLM_BUDGET_DAY_USD", "HLM_LLM_BUDGET_MONTH_USD")
#: R4 R-14: a captured probe-writer result (evaluate --writer-probe FILE): exit status + stdout
PROBE_OK = {"exit": 0, "stdout": json.dumps({"ok": True, "profile": WRITER, "status": 200, "latency_ms": 41})}


def env_for(report_json):
    """An llm.env whose manifest keys and marker are what ``report_json`` says the service runs."""
    try:
        rep = json.loads(report_json or "{}")
    except ValueError:
        rep = {}
    lines = ["HLM_LIBRARIAN_ENABLED=true"]
    lines += [f"{k}={v}" for k, v in (rep.get("manifest_env") or {}).items() if v is not None]
    if rep.get("env_release"):
        lines.append(f"HLM_ENV_RELEASE={rep['env_release']}")
    return "\n".join(lines) + "\n"


class R2DeployCheckTest(unittest.TestCase):
    prepare_deploy = harness.DeployRecoveryTest.prepare_deploy
    prepare_split = w0.W0DeployTest.prepare_split
    deploy = w0.W0DeployTest.deploy

    def run_r2(self, llm_env, librarian=None, api=None, env_text=None):
        """``llm_env``: the file on disk is ``env_text``, by default exactly what the api report
        says it runs (D-116: the check compares the file with both services)."""
        root, env = self.prepare_split("")
        if llm_env:
            (root / "llm.env").write_text(env_text if env_text is not None else env_for(api or librarian))
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
            "R4 release: llm.env is required (the r4 manifest, D-111/D-116; install_llm_env.sh)",
        )

    def test_missing_llm_env_fails_even_when_everything_else_passes(self):
        """Review 72 (sol) trigger: an R3 cutover with llm.env missing PASSed (R1-style)."""
        self.assert_fails_after_cutover(
            False,
            report("librarian", env_release=None),
            report("api", env_release=None),
            "RESULT librarian FAIL R4 release: llm.env is required",
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
                "api llm.env differs from the r3 manifest (the D-094 mapping):"
                " HLM_FALLBACK_PROFILE__RISK_JUDGE=- (expected openrouter-qwen38-27b-fast)",
            ),
            "synthesis fallback empty": (
                report("librarian", manifest={"HLM_FALLBACK_PROFILE__SYNTHESIS": ""}),
                report("api"),
                "librarian llm.env differs from the r3 manifest (the D-094 mapping):"
                " HLM_FALLBACK_PROFILE__SYNTHESIS=- (expected openrouter)",
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
        self.assertIn("llm.env manifest: env_release=r3 (code r4, check mode r3)", output)
        self.assertIn(" HLM_QUERY_REWRITE=- ", output)
        self.assertIn(" HLM_RETRIEVAL_SOURCE_CAP=-", output)
        self.assert_fails_after_cutover(
            True,
            report("librarian"),
            report("api", manifest={"HLM_QUERY_REWRITE": "true"}),
            "api runs HLM_QUERY_REWRITE=true",
        )

    def test_the_template_satisfies_the_current_release_manifest(self):
        """deploy/llm.env.example (what install_llm_env.sh writes) carries the marker (R4), the D-094
        keys, no query rewrite / per-source cap switched on, and (review 79 T5, R4 plan §1.5)
        memory.ask ON, the map summaries OFF, the pinned fallbacks and bounded per-question limits."""
        spec = importlib.util.spec_from_file_location("check_librarian", CHECK)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        env = {}
        for line in (ROOT / "deploy/llm.env.example").read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not line.startswith("#"):
                env[key.strip()] = value.strip()
        self.assertEqual("r4", mod.CODE_RELEASE)
        manifest = mod.RELEASE_MANIFESTS[mod.CODE_RELEASE]
        self.assertEqual(mod.CODE_RELEASE, env.get(mod.ENV_RELEASE_KEY))
        for key, value in manifest["exact"].items():
            self.assertEqual(value, env.get(key), key)
        self.assertEqual([], mod._budget_problems(env, manifest["budgets"]))  # D-121 / D-198
        self.assertEqual(
            ("3", "8", "60"), tuple(env[k] for k in manifest["budgets"]["keys"]), "the owner's R4 caps"
        )
        self.assertIs(mod._BUDGETS_R4, manifest["budgets"])
        # R-3: R3's budgets are untouched (month <= 10): the R4 caps would fail an R3 env
        self.assertIs(mod._BUDGETS, mod.RELEASE_MANIFESTS["r3"]["budgets"])
        self.assertEqual(10.0, mod._BUDGETS["month_max_usd"])
        self.assertNotIn("max_usd", mod._BUDGETS)
        self.assertTrue(mod._budget_problems(env, mod._BUDGETS))
        # the writer is one the manifest accepts, the tracked limits are set, the tracer is absent
        for key, allowed in manifest["one_of"].items():
            self.assertIn(env.get(key) or None, allowed, key)
        self.assertEqual("google-gemini38-flash-medium", env["HLM_RESEARCH_WRITER_PROFILE"])
        for key in manifest["tracked"]:
            self.assertTrue(env.get(key), key)
        self.assertNotIn(mod.TRACE_KEY, env)
        self.assertEqual("", env["GEMINI_API_KEY"], "the key's line, empty in the template")
        for key in manifest["off"]:
            self.assertIn(env.get(key, "false").lower(), ("", "0", "false", "no", "off"), key)
        self.assertEqual(("HLM_RESEARCH_ENABLED",), tuple(manifest["on"]))
        self.assertIn("HLM_MAP_SUMMARY_ENABLED", manifest["off"])
        self.assertEqual("false", env.get("HLM_MAP_SUMMARY_ENABLED"))
        for key in manifest["on"]:
            self.assertEqual("true", env.get(key), key)
        self.assertEqual([], mod._limit_problems(env, manifest["limits"]))
        self.assertEqual("0.12", env["HLM_RESEARCH_MAX_USD"])
        # the R3 manifest keeps memory.ask and the summaries OFF (an R3 env on this image)
        self.assertTrue(set(manifest["on"]) <= set(mod.RELEASE_MANIFESTS["r3"]["off"]))
        self.assertIn("HLM_MAP_SUMMARY_ENABLED", mod.RELEASE_MANIFESTS["r3"]["off"])

    def test_r4_manifest_pins_research_map_summary_and_their_fallbacks(self):
        """Review 79 T5 / R4 plan §1.5: an R4 env is checked against the R4 manifest: memory.ask ON,
        the summaries OFF, the research and map-summary fallbacks exact, the per-question limits
        present and bounded; an R3 env with memory.ask switched on fails the R3 manifest; the
        effective state is reported."""
        research = r4_research()
        lib = report("librarian", env_release="r4", research=research)
        api = report("api", env_release="r4", research=research)
        code, output = self.evaluate(lib, api)
        self.assertEqual(0, code, output)
        self.assertIn("llm.env manifest: env_release=r4 (code r4, check mode r4)", output)
        self.assertIn("RESULT librarian PASS llm.env=present release=r4 manifest=r4", output)
        self.assertIn("api research: memory.ask enabled=true map_summary=false max_usd=0.12", output)
        code, output = self.evaluate(lib, api, "--release", "r4")
        self.assertEqual(0, code, output)
        cases = {
            "research off": (
                {"HLM_RESEARCH_ENABLED": "false"},
                "runs HLM_RESEARCH_ENABLED=false (r4 manifest: true, review 79 T5",
            ),
            "map summary on": (
                {"HLM_MAP_SUMMARY_ENABLED": "true"},
                "runs HLM_MAP_SUMMARY_ENABLED=true (r4 manifest: absent or false",
            ),
            "research fallback other": (
                {"HLM_FALLBACK_PROFILE__RESEARCH": "openrouter-glm53-flash"},
                "HLM_FALLBACK_PROFILE__RESEARCH=openrouter-glm53-flash (expected openrouter)",
            ),
            "map summary fallback missing": (
                {"HLM_FALLBACK_PROFILE__MAP_SUMMARY": None},
                "HLM_FALLBACK_PROFILE__MAP_SUMMARY=- (expected openrouter-glm53-flash)",
            ),
            "question cap raised": (
                {"HLM_RESEARCH_MAX_USD": "0.13"},
                "per-question limits violate the r4 manifest: HLM_RESEARCH_MAX_USD=0.13"
                " (positive, at most 0.12)",
            ),
            "token cap missing": (
                {"HLM_RESEARCH_MAX_TOKENS": None},
                "HLM_RESEARCH_MAX_TOKENS=- (required, at most 100000)",
            ),
        }
        for name, (manifest, message) in cases.items():
            with self.subTest(name):
                code, output = self.evaluate(
                    report("librarian", env_release="r4", manifest=manifest),
                    report("api", env_release="r4", manifest=manifest),
                )
                self.assertEqual(1, code, output)
                self.assertIn(message, output)
        # an R3 env keeps memory.ask OFF: switched on, it fails the R3 manifest
        on = {"HLM_RESEARCH_ENABLED": "true"}
        code, output = self.evaluate(report("librarian", manifest=on), report("api", manifest=on))
        self.assertEqual(1, code, output)
        self.assertIn("api runs HLM_RESEARCH_ENABLED=true (r3 manifest: absent or false", output)
        # ... and so does an unlabelled (interim) env
        code, output = self.evaluate(
            report("librarian", env_release=None, manifest={"HLM_MAP_SUMMARY_ENABLED": "1"}),
            report("api", env_release=None, manifest={"HLM_MAP_SUMMARY_ENABLED": "1"}),
        )
        self.assertEqual(1, code, output)
        self.assertIn("api runs HLM_MAP_SUMMARY_ENABLED=1 (r2-env interim manifest: absent or false", output)
        # api and librarian must run the same research switches (the manifest keys include them)
        code, output = self.evaluate(
            report("librarian", env_release="r4", manifest={"HLM_RESEARCH_ENABLED": "yes"}),
            report("api", env_release="r4", manifest={"HLM_RESEARCH_ENABLED": "false"}),
        )
        self.assertEqual(1, code, output)
        self.assertIn(
            "api and librarian run different llm.env values for HLM_RESEARCH_ENABLED"
            " (api=false, librarian=yes)",
            output,
        )

    def r4(self, *, probe=PROBE_OK, extra=(), api_kw=None, lib_kw=None, **kw):
        """evaluate an R4 env (both services run it; ``kw`` applies to both, ``api_kw``/``lib_kw``
        to one) in release mode r4."""
        lib = report("librarian", env_release="r4", **{**kw, **(lib_kw or {})})
        api = report("api", env_release="r4", **{**kw, **(api_kw or {})})
        return self.evaluate(lib, api, "--release", "r4", *extra, probe=probe)

    def test_r4_manifest_pins_the_answer_contract_and_accepts_the_two_gemini_writers(self):
        """R4 plan §1.5 (B6): prose / attribution llm / rerank llm are pinned (the code defaults are
        claims / sources / off); the writer is unset or one of the two Gemini profiles; the tracked
        limits must match across api and librarian."""
        code, output = self.r4()
        self.assertEqual(0, code, output)
        self.assertIn("RESULT librarian PASS llm.env=present release=r4 manifest=r4", output)
        self.assertIn("writer=google-gemini38-flash-medium", output)
        self.assertIn("answer_mode=prose", output)
        high = "google-gemini38-flash-high"
        probe_high = {
            "exit": 0,
            "stdout": json.dumps({"ok": True, "profile": high, "status": 200, "latency_ms": 9}),
        }
        code, output = self.r4(manifest={"HLM_RESEARCH_WRITER_PROFILE": high}, probe=probe_high)
        self.assertEqual(0, code, output)
        cases = {
            "claims": (
                {"HLM_RESEARCH_ANSWER_MODE": "claims"},
                "HLM_RESEARCH_ANSWER_MODE=claims (expected prose)",
            ),
            "attribution default": (
                {"HLM_RESEARCH_ATTRIBUTION": None},
                "HLM_RESEARCH_ATTRIBUTION=- (expected llm)",
            ),
            "rerank off": ({"HLM_RESEARCH_RERANK": "off"}, "HLM_RESEARCH_RERANK=off (expected llm)"),
            "another writer": (
                {"HLM_RESEARCH_WRITER_PROFILE": "openrouter-glm5"},
                "runs HLM_RESEARCH_WRITER_PROFILE=openrouter-glm5 (r4 manifest: one of unset,"
                " google-gemini38-flash-medium, google-gemini38-flash-high)",
            ),
        }
        for name, (manifest, message) in cases.items():
            with self.subTest(name):
                code, output = self.r4(manifest=manifest)
                self.assertEqual(1, code, output)
                self.assertIn(message, output)
        code, output = self.r4(api_kw={"manifest": {"HLM_RESEARCH_HTTP_TIMEOUT_S": "20"}})
        self.assertEqual(1, code, output)
        self.assertIn(
            "api and librarian run different llm.env values for HLM_RESEARCH_HTTP_TIMEOUT_S"
            " (api=20, librarian=150)",
            output,
        )

    def test_r4_accepts_an_unset_writer_without_a_gemini_key(self):
        """R4 plan §6.2(b) (the REVERT option "R4 env with the luna writer"): an UNSET writer passes
        the r4 manifest; no Gemini profile is in the chain, so key_set needs no GEMINI_API_KEY, and
        probe-writer and the price date do not apply."""
        unset = {"HLM_RESEARCH_WRITER_PROFILE": None}
        for extra in ((), ("--today", "2031-01-01")):
            with self.subTest(extra=extra):
                code, output = self.r4(manifest=unset, research=r4_research(None), probe=None, extra=extra)
                self.assertEqual(0, code, output)
                self.assertIn("writer: unset (the research profile writes)", output)
                self.assertIn("writer=unset(research profile)", output)
                self.assertNotIn("gemini", output.lower())
                self.assertNotIn("writer probe", output)

    def test_r4_missing_writer_key_fails(self):
        """R4 B5: key_set covers the writer's key: a writer without GEMINI_API_KEY in the api (or the
        librarian) fails the check."""
        code, output = self.r4(api_kw={"writer_key": False})
        self.assertEqual(1, code, output)
        self.assertIn(
            "api: provider key missing for google-gemini38-flash-medium (install_llm_env.sh)", output
        )
        self.assertIn("writer: google-gemini38-flash-medium key=MISSING", output)

    def test_r4_budgets_are_the_owners_and_r3_budgets_are_unchanged(self):
        """R4 §1.5 / D-198 (R-3): _BUDGETS_R4 = HOUR 3 / DAY 8 / MONTH 60, each an upper bound; lower
        caps pass; R3's _BUDGETS (month <= 10) is separate, so an R3 env with MONTH 60 still fails."""
        for caps in (("3", "8", "60"), ("1", "2", "10"), ("0.5", "8", "8")):
            with self.subTest(caps=caps):
                manifest = dict(zip(CAP_KEYS, caps, strict=True))
                code, output = self.r4(manifest=manifest)
                self.assertEqual(0, code, output)
        cases = {
            "hour": (("4", "8", "60"), "HLM_LLM_BUDGET_HOUR_USD=4 (at most 3)"),
            "day": (("3", "9", "60"), "HLM_LLM_BUDGET_DAY_USD=9 (at most 8)"),
            "month": (("3", "8", "61"), "HLM_LLM_BUDGET_MONTH_USD=61 (at most 60)"),
            "guard off": (None, "HLM_LLM_BUDGET_DISABLED=true (must be false)"),
        }
        for name, (caps, message) in cases.items():
            with self.subTest(name):
                manifest = (
                    {"HLM_LLM_BUDGET_DISABLED": "true"}
                    if caps is None
                    else dict(zip(CAP_KEYS, caps, strict=True))
                )
                code, output = self.r4(manifest=manifest)
                self.assertEqual(1, code, output)
                self.assertIn("spend guard violates the r4 manifest (D-121)", output)
                self.assertIn(message, output)
        r3_with_r4_caps = dict(zip(CAP_KEYS, ("3", "8", "60"), strict=True))
        code, output = self.evaluate(
            report("librarian", manifest=r3_with_r4_caps),
            report("api", manifest=r3_with_r4_caps),
            "--release",
            "r3",
        )
        self.assertEqual(1, code, output)
        self.assertIn(
            "spend guard violates the r3 manifest (D-121): HLM_LLM_BUDGET_MONTH_USD=60 (at most 10)", output
        )

    def test_r4_trace_dir_in_the_api_fails(self):
        """R4 R-11: a stale HLM_RESEARCH_TRACE_DIR (=/tmp/traces) in the api's env (llm.env, app.env
        or hlm.toml: the effective setting) fails the R4 check; a report without the research state
        cannot prove it is off and fails too."""
        code, output = self.r4(api_kw={"research": r4_research(trace=True)})
        self.assertEqual(1, code, output)
        self.assertIn("the api runs with HLM_RESEARCH_TRACE_DIR set (R-11", output)
        self.assertIn("trace_dir_set=True", output)
        api = json.loads(report("api", env_release="r4"))
        del api["research"]
        code, output = self.evaluate(
            report("librarian", env_release="r4"), json.dumps(api), "--release", "r4"
        )
        self.assertEqual(1, code, output)
        self.assertIn("the api report has no research state (collect)", output)
        # the R3 manifest has no tracer rule (memory.ask is off there)
        code, output = self.evaluate(
            report("librarian", research=r4_research(trace=True)),
            report("api", research=r4_research(trace=True)),
            "--release",
            "r3",
        )
        self.assertEqual(0, code, output)

    def test_r4_writer_price_date_fails_when_passed_and_warns_ahead(self):
        """R4 R-5: the writer profile's price_valid_until: passed or missing -> FAIL (the guard would
        book at stale prices); within 14 days -> WARNING and PASS; later -> PASS without a warning."""
        today = ("--today", "2026-09-29")
        for until, code_expected, message in (
            ("2026-09-28", 1, "price_valid_until 2026-09-28 has passed (today 2026-09-29)"),
            (None, 1, "price_valid_until=- is missing or not YYYY-MM-DD (R-5"),
            ("31.12.2026", 1, "price_valid_until=31.12.2026 is missing or not YYYY-MM-DD"),
            (
                "2026-09-29",
                0,
                "WARNING writer profile google-gemini38-flash-medium: price_valid_until 2026-09-29",
            ),
            ("2026-10-13", 0, "is within 14 days (today 2026-09-29)"),
            ("2026-10-14", 0, "price_valid_until=2026-10-14"),
        ):
            with self.subTest(until=until):
                code, output = self.r4(research=r4_research(until=until), extra=today)
                self.assertEqual(code_expected, code, output)
                self.assertIn(message, output)
                if until == "2026-10-14":
                    self.assertNotIn("WARNING", output)

    def test_r4_writer_probe_must_pass_in_the_api_container(self):
        """R4 R-14: the probe (probe-writer in the api container) is required in r4 mode with a
        writer; a non-zero exit, ok!=true or another profile than the api's writer FAILS; only the
        probe's ok/profile/status/latency_ms fields are ever printed (a leaky probe's key never)."""
        code, output = self.r4(probe=None)
        self.assertEqual(1, code, output)
        self.assertIn("the writer probe did not run (evaluate --writer-probe api; R-14)", output)
        sentinel = "sentinel-" + "K7" * 12
        leaky = {
            "ok": False,
            "profile": WRITER,
            "status": 401,
            "latency_ms": 5,
            "key": sentinel,
            "body": sentinel,
        }
        cases = {
            "refused": (
                {"exit": 3, "stdout": f"{sentinel}\n" + json.dumps(leaky)},
                "the writer probe FAILED in the api container (exit 3, HTTP 401)",
            ),
            "not ok": (
                {"exit": 0, "stdout": json.dumps({**leaky, "status": 200})},
                "the writer probe did not report ok (ok=False; R-14)",
            ),
            "other profile": (
                {"exit": 0, "stdout": json.dumps({"ok": True, "profile": "google-gemini38-flash-high"})},
                "the writer probe used profile google-gemini38-flash-high, the api runs"
                " google-gemini38-flash-medium",
            ),
            "garbage": (
                {"exit": 0, "stdout": f"Traceback {sentinel}\n"},
                "the writer probe did not report ok (ok=None; R-14)",
            ),
            "profile field injection": (
                {"exit": 0, "stdout": json.dumps({"ok": True, "profile": f"x {sentinel}", "status": 200})},
                "the writer probe used profile -, the api runs google-gemini38-flash-medium",
            ),
            "unreadable capture": ("not json", "the writer probe could not run in the api container"),
        }
        for name, (probe, message) in cases.items():
            with self.subTest(name):
                if isinstance(probe, str):
                    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
                        fh.write(probe)
                    self.addCleanup(Path(fh.name).unlink)
                    code, output = self.r4(probe=None, extra=("--writer-probe", fh.name))
                else:
                    code, output = self.r4(probe=probe)
                self.assertEqual(1, code, output)
                self.assertIn(message, output)
                self.assertNotIn(sentinel, output)
        # an R3 env never runs the probe (research is off there)
        code, output = self.evaluate(report("librarian"), report("api"), "--release", "r3", probe=None)
        self.assertEqual(0, code, output)
        self.assertNotIn("writer probe", output)

    def test_no_llm_env_fails_whatever_runs(self):
        """D-111 #6: without llm.env an R3 cutover fails, idle or active."""
        for lib, api in (
            (report("librarian", enabled=False, hb_enabled=False), report("api")),
            (report("librarian", enabled=False, hb_enabled=True), report("api", enabled=False)),
        ):
            self.assert_fails_after_cutover(False, lib, api, "R4 release: llm.env is required")

    def test_r3_env_marker_puts_the_check_in_r3_mode(self):
        """D-111 #6 / D-116: an api running HLM_ENV_RELEASE=r3 is checked in R3 mode: the full R3
        manifest (the D-094 mapping present) is required."""
        self.assert_fails_after_cutover(
            True,
            report("librarian"),
            report("api", manifest={"HLM_FALLBACK_PROFILE__SYNTHESIS": None}),
            "check mode r3",
            "api llm.env differs from the r3 manifest (the D-094 mapping): HLM_FALLBACK_PROFILE__SYNTHESIS=-",
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
            "RESULT librarian PASS llm.env=present release=r2-env (D-108 interim: install the R4 llm.env,"
            " recreate librarian api, then evaluate --release r4)",
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
            True,
            lib,
            report("api"),
            "api and librarian run different llm.env values for HLM_ENV_RELEASE (api=r3, librarian=-)",
        )

    def evaluate(self, lib, api, *extra, probe=PROBE_OK):
        """``probe``: the captured probe-writer result passed as --writer-probe FILE (None: none)."""
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name, rep in (("librarian", lib), ("api", api)):
                path = Path(tmp) / f"{name}.json"
                path.write_text(rep + "\n")
                paths.append(str(path))
            if probe is not None:
                (Path(tmp) / "probe.json").write_text(json.dumps(probe))
                extra = ("--writer-probe", str(Path(tmp) / "probe.json"), *extra)
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
                    "api llm.env differs from the r3 manifest (the D-094 mapping):",
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

    def test_r3_manifest_values_are_exact_and_both_services_match(self):
        """D-116 #7 (review 75): a wrong but valid D-094 profile fails R3 mode; api and librarian
        must match on EVERY manifest key (not only the release label)."""
        code, output = self.evaluate(
            report("librarian", manifest={"HLM_FALLBACK_PROFILE": "openrouter"}),
            report("api", manifest={"HLM_FALLBACK_PROFILE": "openrouter"}),
        )
        self.assertEqual(1, code, output)
        self.assertIn("HLM_FALLBACK_PROFILE=openrouter (expected openrouter-glm53-flash)", output)
        code, output = self.evaluate(
            report("librarian", manifest={"HLM_FALLBACK_PROFILE__RISK_JUDGE": "openrouter"}), report("api")
        )
        self.assertEqual(1, code, output)
        self.assertIn(
            "api and librarian run different llm.env values for HLM_FALLBACK_PROFILE__RISK_JUDGE"
            " (api=openrouter-qwen38-27b-fast, librarian=openrouter)",
            output,
        )

    def test_the_llm_env_on_disk_must_be_what_both_services_run(self):
        """D-116 #7: an llm.env edited (or installed) on disk without recreating the services fails."""
        with tempfile.TemporaryDirectory() as tmp:
            disk = Path(tmp) / "llm.env"
            disk.write_text(env_for(report("api")))
            code, output = self.evaluate(report("librarian"), report("api"), "--llm-env-file", str(disk))
            self.assertEqual(0, code, output)
            disk.write_text(
                env_for(report("api")).replace("HLM_LLM_BUDGET_MONTH_USD=10", "HLM_LLM_BUDGET_MONTH_USD=8")
            )
            code, output = self.evaluate(report("librarian"), report("api"), "--llm-env-file", str(disk))
        self.assertEqual(1, code, output)
        self.assertIn(
            "llm.env on disk differs from what the api runs: HLM_LLM_BUDGET_MONTH_USD disk=8 running=10",
            output,
        )

    def test_only_an_unlabelled_env_is_the_interim(self):
        """D-116 #8: an unknown label (a later release, a typo) FAILS instead of passing as R2."""
        for label in ("r5", "R4", "r3 "):
            with self.subTest(label=label):
                code, output = self.evaluate(
                    report("librarian", env_release=label), report("api", env_release=label)
                )
                self.assertEqual(1, code, output)
                self.assertIn(f"unknown llm.env release label {label}", output)
        code, output = self.evaluate(report("librarian", env_release=None), report("api", env_release=None))
        self.assertEqual(0, code, output)
        self.assertIn("release=r2-env (D-108 interim", output)

    def test_r3_manifest_budget_guard(self):
        """D-121: budgets present and ON, month <= $10, day and hour <= month; operator values that
        stay inside those bounds pass."""
        ok = {
            "HLM_LLM_BUDGET_HOUR_USD": "0.5",
            "HLM_LLM_BUDGET_DAY_USD": "1",
            "HLM_LLM_BUDGET_MONTH_USD": "7",
        }
        code, output = self.evaluate(report("librarian", manifest=ok), report("api", manifest=ok))
        self.assertEqual(0, code, output)
        cases = {
            "month above the target": (
                {"HLM_LLM_BUDGET_MONTH_USD": "60"},
                "HLM_LLM_BUDGET_MONTH_USD=60 (at most 10)",
            ),
            "guard disabled": (
                {"HLM_LLM_BUDGET_DISABLED": "true"},
                "HLM_LLM_BUDGET_DISABLED=true (must be false)",
            ),
            "guard key missing": (
                {"HLM_LLM_BUDGET_DISABLED": None},
                "HLM_LLM_BUDGET_DISABLED=- (must be false)",
            ),
            "day above month": (
                {"HLM_LLM_BUDGET_DAY_USD": "12", "HLM_LLM_BUDGET_MONTH_USD": "10"},
                "HLM_LLM_BUDGET_DAY_USD=12 (at most the month cap 10)",
            ),
            "hour missing": (
                {"HLM_LLM_BUDGET_HOUR_USD": None},
                "HLM_LLM_BUDGET_HOUR_USD=- (a positive amount",
            ),
        }
        for name, (manifest, message) in cases.items():
            with self.subTest(name):
                code, output = self.evaluate(
                    report("librarian", manifest=manifest), report("api", manifest=manifest)
                )
                self.assertEqual(1, code, output)
                self.assertIn("spend guard violates the r3 manifest (D-121)", output)
                self.assertIn(message, output)

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
