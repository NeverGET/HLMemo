"""R4 cross-contract tests: the REAL code-side pieces (profiles/*.toml, ``hlmemo.config``, ``python -m
hlmemo.ops probe-writer``) against the REAL deploy-side pieces (deploy/llm.env.example,
install_llm_env.sh, check_librarian.py collect/evaluate). r4-code and r4-deploy were built in parallel
against a written contract; r4-deploy's own tests use stand-in profiles (r4_fixtures.py). These tests
use none: every profile is read from this checkout's profiles/.

(a) the R4 template, installed by install_llm_env.sh and read by the real collect (api + librarian),
    passes evaluate --release r4 with the real medium and high writer profiles; the raw template passes
    the manifest and writer predicates too.
(b) install_llm_env.sh resolves GEMINI_API_KEY from the real medium profile's env:NAME reference.
(c) the real ``python -m hlmemo.ops probe-writer`` with a stubbed provider transport: one JSON line that
    evaluate's parser accepts (200) or rejects (401); exit 0 or 1; the key is never printed.
(d) the real profiles' price_valid_until, as collect reads it, drives evaluate's R-5 check with an
    injected today: FAIL once passed, WARN within 14 days, PASS otherwise.
(e) writer unset, HLM_PROFILE = luna: the profile evaluate expects the probe to report is the one
    probe-writer reports.

No network (the probe's transport is an httpx.MockTransport installed by a sitecustomize shim that
exits the process if it cannot install it; a dead proxy backs it up), no database, no real key: the
key values are dummies built at runtime. The probe runs on the real clock: after a real profile's
price_valid_until it reports price_expired (R-5) and the success cases fail until the profile
carries the new prices and date, which is intended.
"""

import datetime
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import r4_fixtures  # noqa: E402
import test_llm_env  # noqa: E402  (module import: its tests are not re-collected)

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
PROFILES = ROOT / "profiles"
TEMPLATE = ROOT / "deploy/llm.env.example"
CHECK = ROOT / "deploy/scripts/check_librarian.py"
INSTALL = ROOT / "deploy/scripts/install_llm_env.sh"
MEDIUM, HIGH = r4_fixtures.GEMINI_WRITERS
LUNA = "openrouter-gpt6-luna"
WRITER_KEY = "HLM_RESEARCH_WRITER_PROFILE"
#: dummy key values, built at runtime (install_llm_env.sh wants 16+ characters); never a real key
GEMINI_DUMMY = "test-not-a-key" + "-gemini-0001"
OPENROUTER_DUMMY = "test-not-a-key" + "-openrouter-0001"
DUMMIES = (GEMINI_DUMMY, OPENROUTER_DUMMY)
#: nothing listens there: if the stub were ever bypassed, a provider call fails instead of leaving
DEAD_PROXY = "http://127.0.0.1:9"
#: probe-writer's provider transport, stubbed (r4-code's own tests set hlmemo.ops.probe.TRANSPORT to
#: an httpx.MockTransport; this shim does the same in the probe's process, before the CLI runs)
SHIM = r'''
"""Test shim (tests/deploy/test_r4_cross_contract.py): probe-writer's transport, stubbed."""
import json
import os

try:
    import httpx
    from hlmemo.ops import probe

    def _handler(request):
        body = json.loads(request.content)
        key = os.environ.get(os.environ["STUB_KEY_VAR"], "")
        seen = {
            "url": str(request.url),
            "model": body.get("model"),
            "reasoning_effort": body.get("reasoning_effort"),
            "max_tokens": body.get("max_tokens"),
            "auth_ok": bool(key) and request.headers.get("authorization") == "Bearer " + key,
        }
        with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as fh:
            fh.write(json.dumps(seen) + "\n")
        if os.environ["STUB_MODE"] == "401":  # a provider that echoes the wrong key back
            return httpx.Response(401, json={"error": {"code": 401, "message": "API key not valid: " + key}})
        message = {"role": "assistant", "content": '{"ok"'}  # 16 tokens may end mid-object
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "length"}]})

    probe.TRANSPORT = httpx.MockTransport(_handler)
except BaseException:  # never let the probe run without the stub
    os._exit(97)
'''


def _needs_code_deps() -> bool:
    return all(importlib.util.find_spec(m) for m in ("httpx", "pydantic_settings", "psycopg"))


def _check_module():
    spec = importlib.util.spec_from_file_location("check_librarian_cc", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _real_profile(name: str) -> dict:
    with (PROFILES / f"{name}.toml").open("rb") as fh:
        return tomllib.load(fh)


@unittest.skipUnless(_needs_code_deps(), "the code side needs the project venv (httpx, pydantic-settings)")
class R4CrossContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.check = _check_module()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.shim = self.tmp / "shim"
        self.shim.mkdir()
        (self.shim / "sitecustomize.py").write_text(SHIM)
        self.outputs: list[str] = []  # every captured stdout/stderr: no dummy key may appear in any

    def tearDown(self):
        for text in self.outputs:
            for key in DUMMIES:
                self.assertNotIn(key, text, "a key value was printed")

    # ------------------------------------------------------------------ the pieces
    def install(self, key_lines: str | None = None) -> tuple[subprocess.CompletedProcess, Path]:
        """The real install_llm_env.sh of this checkout (the real template and profiles) under
        test_llm_env's fake ssh; returns (result, the installed llm.env)."""
        state, etc, bin_dir = self.tmp / "state", self.tmp / "etc-hlmemo", self.tmp / "bin"
        for d in (state, etc, bin_dir):
            d.mkdir(exist_ok=True)
        (state / "ssh_config").write_text("Host hlm-deploy\n  HostName 203.0.113.10\n")
        (bin_dir / "ssh").write_text(test_llm_env.FAKE_SSH)
        (bin_dir / "ssh").chmod(0o700)
        key_file = self.tmp / "dot.env"
        if key_lines is None:
            key_lines = f"OPENROUTER_API_KEY={OPENROUTER_DUMMY}\nGEMINI_API_KEY={GEMINI_DUMMY}\n"
        key_file.write_text(key_lines)
        result = subprocess.run(
            ["bash", str(INSTALL), "--state", str(state), "--key-file", str(key_file)],
            env={
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "EVENTS": str(self.tmp / "events"),
                "HLM_REMOTE_ENV": str(etc / "prod.env"),
                "HLM_REMOTE_DIR": str(self.tmp / "no-deployed-app"),
            },
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        self.outputs += [result.stdout, result.stderr]
        if (self.tmp / "events").exists():
            self.outputs.append(
                json.dumps([json.loads(x)["argv"] for x in (self.tmp / "events").read_text().splitlines()])
            )
        return result, etc / "llm.env"

    def code_env(self, llm_env: dict[str, str], **extra: str) -> dict[str, str]:
        """A code-side process environment: the llm.env's variables (as compose's env_file passes
        them), this checkout's src and profiles, an empty HOME and cwd (no hlm.toml), no proxy use."""
        return {
            "PATH": os.environ["PATH"],
            "HOME": str(self.home),
            "PYTHONPATH": str(SRC),
            "PYTHONDONTWRITEBYTECODE": "1",
            "HLM_PROFILES_DIR": str(PROFILES),
            "HTTPS_PROXY": DEAD_PROXY,
            "HTTP_PROXY": DEAD_PROXY,
            "ALL_PROXY": DEAD_PROXY,
            **llm_env,
            **extra,
        }

    def collect(self, service: str, llm_env: dict[str, str]) -> dict:
        """The real ``collect`` as the host feeds it (``python - collect --service S <
        check_librarian.py``), with the real hlmemo settings and profiles."""
        extra = {}
        if service == "librarian":
            heartbeat = self.tmp / "heartbeat.json"
            heartbeat.write_text(
                json.dumps(
                    {
                        "ts": time.time(),
                        "enabled": True,
                        "role": "observer",
                        "breaker_state": "closed",
                        "ready": 0,
                        "in_flight": 0,
                        "failed_24h": 0,
                        "spend_today_usd": 0.0,
                        "spend_hour_usd": 0.0,
                        "reserved_usd": 0.0,
                    }
                )
            )
            extra["HLM_LIBRARIAN_HEARTBEAT_FILE"] = str(heartbeat)
        result = subprocess.run(
            [sys.executable, "-", "collect", "--service", service],
            input=CHECK.read_text(),
            env=self.code_env(llm_env, **extra),
            cwd=self.home,
            text=True,
            capture_output=True,
            timeout=120,
        )
        self.outputs += [result.stdout, result.stderr]
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertNotIn("error", report, result.stdout)
        self.assertNotIn("profiles_error", report, result.stdout)
        return report

    def probe_writer(
        self, llm_env: dict[str, str], mode: str, key_var: str
    ) -> tuple[subprocess.CompletedProcess, list]:
        """The real ``python -m hlmemo.ops probe-writer`` with the provider transport stubbed."""
        log = self.tmp / f"stub-{mode}.jsonl"
        log.unlink(missing_ok=True)
        env = self.code_env(
            llm_env,
            PYTHONPATH=f"{self.shim}{os.pathsep}{SRC}",
            STUB_MODE=mode,
            STUB_LOG=str(log),
            STUB_KEY_VAR=key_var,
        )
        result = subprocess.run(
            [sys.executable, "-m", "hlmemo.ops", "probe-writer"],
            env=env,
            cwd=self.home,
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=120,
        )
        self.outputs += [result.stdout, result.stderr]
        self.assertNotEqual(97, result.returncode, "the transport stub was not installed")
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls

    def capture(self, result: subprocess.CompletedProcess, name: str) -> Path:
        """What evaluate --writer-probe FILE reads: the probe's exit status and its stdout."""
        path = self.tmp / f"probe-{name}.json"
        path.write_text(json.dumps({"exit": result.returncode, "stdout": result.stdout}))
        return path

    def evaluate(
        self, lib: dict, api: dict, llm_env_file: Path, probe_file: Path, today: str
    ) -> tuple[int, str]:
        """The real host-side evaluate --release r4 (stdlib only: no hlmemo on its path)."""
        paths = []
        for name, report in (("l", lib), ("a", api)):
            path = self.tmp / f"{name}.json"
            path.write_text(json.dumps(report) + "\n")
            paths.append(path)
        result = subprocess.run(
            [
                sys.executable,
                str(CHECK),
                "evaluate",
                "--llm-env",
                "present",
                "--librarian",
                str(paths[0]),
                "--api",
                str(paths[1]),
                "--release",
                "r4",
                "--llm-env-file",
                str(llm_env_file),
                "--writer-probe",
                str(probe_file),
                "--today",
                today,
            ],
            env={"PATH": os.environ["PATH"], "HOME": str(self.home)},
            text=True,
            capture_output=True,
            timeout=60,
        )
        output = result.stdout + result.stderr
        self.outputs.append(output)
        return result.returncode, output

    def installed(self) -> tuple[Path, str]:
        result, target = self.install()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return target, target.read_text()

    def with_writer(self, text: str, writer: str) -> str:
        lines = [
            f"{WRITER_KEY}={writer}" if line.startswith(f"{WRITER_KEY}=") else line
            for line in text.splitlines()
        ]
        self.assertIn(f"{WRITER_KEY}={writer}", lines)
        return "\n".join(lines) + "\n"

    def full_check(self, env_text: str, *, key_var: str, today: str = "2026-10-01"):
        """The installed env (``env_text``) through collect (both services), probe-writer (200) and
        evaluate. Returns (exit, output, api report, probe result, stub calls)."""
        env_file = self.tmp / "llm.env.checked"
        env_file.write_text(env_text)
        env = r4_fixtures.dotenv(env_text)
        lib, api = self.collect("librarian", env), self.collect("api", env)
        result, calls = self.probe_writer(env, "ok", key_var)
        code, output = self.evaluate(lib, api, env_file, self.capture(result, "ok"), today)
        return code, output, api, result, calls

    # ------------------------------------------------------------------ (a) + (b)
    def test_a_r4_template_and_real_writer_profiles_pass_the_r4_manifest(self):
        target, text = self.installed()
        for writer in (MEDIUM, HIGH):
            with self.subTest(writer=writer):
                env_text = text if writer == MEDIUM else self.with_writer(text, writer)
                self.assertIn(f"{WRITER_KEY}={writer}", env_text.splitlines())
                code, output, api, result, calls = self.full_check(env_text, key_var="GEMINI_API_KEY")
                self.assertEqual(0, code, output)
                self.assertIn(
                    "RESULT librarian PASS llm.env=present release=r4 manifest=r4 enabled=true mode=live"
                    " role=observer risk_judge=",
                    output,
                )
                self.assertTrue(output.rstrip().endswith(f"writer={writer}"), output)
                real = _real_profile(writer)
                self.assertIn(
                    f"writer: {writer} key=set price_valid_until={real['price_valid_until']}", output
                )
                self.assertIn(
                    f"writer probe (api container): exit=0 ok=True profile={writer} status=200", output
                )
                # the api's collect loads the real writer profile (key SET, never its value)
                self.assertEqual(
                    {"profile": writer, "key_set": True, "price_valid_until": str(real["price_valid_until"])},
                    api["research"]["writer"],
                )
                self.assertIn(
                    {"name": writer, "base_url": real["HLM_LLM_BASE_URL"], "key_set": True}, api["profiles"]
                )
                # the probe called the real profile's endpoint/model/effort with the GEMINI key
                (call,) = calls
                self.assertEqual(real["HLM_LLM_BASE_URL"] + "/chat/completions", call["url"])
                self.assertEqual(real["HLM_LLM_MODEL"], call["model"])
                self.assertEqual(real["extra"]["reasoning_effort"], call["reasoning_effort"])
                self.assertEqual(16, call["max_tokens"])
                self.assertTrue(call["auth_ok"])

    def test_a_raw_template_passes_the_manifest_and_writer_predicates(self):
        """The template itself (its HLM_LIBRARIAN_ENABLED=false aside, which the installer turns on):
        _manifest_failures against the file on disk and _writer_failures with the real probe."""
        env = {
            **r4_fixtures.dotenv(TEMPLATE.read_text()),
            "OPENROUTER_API_KEY": OPENROUTER_DUMMY,
            "GEMINI_API_KEY": GEMINI_DUMMY,
        }
        self.assertEqual(MEDIUM, env[WRITER_KEY])
        lib, api = self.collect("librarian", env), self.collect("api", env)
        self.assertEqual(
            [], self.check._manifest_failures(lib, api, "r4", self.check._disk_env(str(TEMPLATE)))
        )
        result, _calls = self.probe_writer(env, "ok", "GEMINI_API_KEY")
        failures, warnings = self.check._writer_failures(
            api, str(self.capture(result, "raw")), datetime.date(2026, 10, 1)
        )
        self.assertEqual(([], []), (failures, warnings))

    def test_b_installer_resolves_gemini_key_from_the_real_medium_profile(self):
        self.assertEqual("env:GEMINI_API_KEY", _real_profile(MEDIUM)["HLM_LLM_API_KEY"])
        self.assertEqual("env:GEMINI_API_KEY", _real_profile(HIGH)["HLM_LLM_API_KEY"])
        self.assertIn(f"{WRITER_KEY}={MEDIUM}", TEMPLATE.read_text().splitlines())
        target, text = self.installed()
        values = r4_fixtures.dotenv(text)
        self.assertEqual(GEMINI_DUMMY, values["GEMINI_API_KEY"])
        self.assertEqual(OPENROUTER_DUMMY, values["OPENROUTER_API_KEY"])
        self.assertEqual(MEDIUM, values[WRITER_KEY])
        # without the variable the real profile names, nothing is sent
        result, _target = self.install(key_lines=f"OPENROUTER_API_KEY={OPENROUTER_DUMMY}\n")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("GEMINI_API_KEY is missing or empty", result.stderr)
        self.assertEqual(text, target.read_text())

    # ------------------------------------------------------------------ (c)
    def test_c_real_probe_writer_output_is_what_evaluate_parses(self):
        target, text = self.installed()
        env = r4_fixtures.dotenv(text)
        lib, api = self.collect("librarian", env), self.collect("api", env)
        for mode, exit_code in (("ok", 0), ("401", 1)):
            with self.subTest(mode=mode):
                result, calls = self.probe_writer(env, mode, "GEMINI_API_KEY")
                self.assertEqual(exit_code, result.returncode, result.stderr)
                self.assertEqual(1, len(calls))
                self.assertTrue(calls[0]["auth_ok"])
                lines = result.stdout.strip().splitlines()
                self.assertEqual(1, len(lines), result.stdout)  # ONE JSON line, nothing else
                shown = json.loads(lines[0])
                self.assertEqual({"ok", "profile", "status", "latency_ms"}, set(shown))
                parsed = self.check.run_writer_probe(str(self.capture(result, mode)))
                self.assertEqual(exit_code, parsed["exit"])
                self.assertEqual(
                    {"ok": mode == "ok", "profile": MEDIUM, "status": 200 if mode == "ok" else 401},
                    {k: parsed["result"][k] for k in ("ok", "profile", "status")},
                )
                self.assertIsInstance(parsed["result"]["latency_ms"], int)
                code, output = self.evaluate(lib, api, target, self.capture(result, mode), "2026-10-01")
                if mode == "ok":
                    self.assertEqual(0, code, output)
                    self.assertIn("RESULT librarian PASS", output)
                else:
                    self.assertEqual(1, code, output)
                    self.assertIn("the writer probe FAILED in the api container (exit 1, status 401)", output)

    # ------------------------------------------------------------------ (d)
    def test_d_real_price_valid_until_drives_the_r5_check(self):
        target, text = self.installed()
        for writer in (MEDIUM, HIGH):
            until = datetime.date.fromisoformat(str(_real_profile(writer)["price_valid_until"]))
            env_text = text if writer == MEDIUM else self.with_writer(text, writer)
            env_file = self.tmp / f"llm.env.{writer}"
            env_file.write_text(env_text)
            env = r4_fixtures.dotenv(env_text)
            lib, api = self.collect("librarian", env), self.collect("api", env)
            self.assertEqual(str(until), api["research"]["writer"]["price_valid_until"])  # read, not assumed
            result, _calls = self.probe_writer(env, "ok", "GEMINI_API_KEY")
            probe_file = self.capture(result, writer)
            warn = f"WARNING writer profile {writer}: price_valid_until {until} is within 14 days"
            cases = (
                ("passed", until + datetime.timedelta(days=1), 1, f"price_valid_until {until} has passed"),
                ("14 days ahead", until - datetime.timedelta(days=14), 0, warn),
                ("the last day", until, 0, warn),
                ("15 days ahead", until - datetime.timedelta(days=15), 0, None),
            )
            for name, today, exit_code, message in cases:
                with self.subTest(writer=writer, case=name):
                    code, output = self.evaluate(lib, api, env_file, probe_file, today.isoformat())
                    self.assertEqual(exit_code, code, output)
                    if message is None:
                        self.assertNotIn("WARNING", output)
                        self.assertIn("RESULT librarian PASS", output)
                    else:
                        self.assertIn(message, output)

    # ------------------------------------------------------------------ (e)
    def test_e_unset_writer_expected_probe_profile_is_what_probe_writer_reports(self):
        target, text = self.installed()
        env_text = self.with_writer(text, "")
        self.assertEqual(LUNA, r4_fixtures.dotenv(env_text)["HLM_PROFILE"])
        code, output, api, result, calls = self.full_check(env_text, key_var="OPENROUTER_API_KEY")
        reported = json.loads(result.stdout.strip().splitlines()[-1])["profile"]
        self.assertEqual(LUNA, reported)
        self.assertIsNone(api["research"]["writer"])
        self.assertEqual(LUNA, api["research"]["primary"]["profile"])
        self.assertFalse(api["research"]["primary_research_disabled"])
        self.assertIn(f"writer: unset (the research primary {reported} writes; HLM_PROFILE)", output)
        self.assertEqual(0, code, output)
        self.assertTrue(output.rstrip().endswith(f"writer=unset(research primary {LUNA})"), output)
        (call,) = calls
        self.assertEqual(_real_profile(LUNA)["HLM_LLM_MODEL"], call["model"])
        self.assertTrue(call["auth_ok"])  # the OpenRouter key (luna's env:NAME), not the Gemini one
        # the equality is enforced: a probe of another profile fails the unset-writer env
        env_file = self.tmp / "llm.env.checked"
        lib = self.collect("librarian", r4_fixtures.dotenv(env_text))
        other = self.tmp / "probe-other.json"
        other.write_text(json.dumps({"exit": 0, "stdout": result.stdout.replace(LUNA, MEDIUM)}))
        code, output = self.evaluate(lib, api, env_file, other, "2026-10-01")
        self.assertEqual(1, code, output)
        self.assertIn(f"the writer probe used profile {MEDIUM}, the api runs {LUNA}", output)


if __name__ == "__main__":
    unittest.main()
