"""R2 `install_llm_env.sh` under a fake ssh (no host): the provider key travels only on ssh stdin,
never in argv or output; llm.env is 0600, complete and idempotent; backups; --remove."""

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/scripts/install_llm_env.sh"
# Built at runtime: no secret-shaped literal in the repository (pre-commit / gitleaks).
KEY_A = "fake-or-" + "Q7" * 16
KEY_B = "fake-or-" + "Z3" * 16
OTHER = "unrelated-" + "S9" * 12

FAKE_SSH = r"""#!/usr/bin/env python3
import json, os, subprocess, sys
data = sys.stdin.buffer.read()
with open(os.environ["EVENTS"], "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "stdin_bytes": len(data)}) + "\n")
with open(os.environ["EVENTS"] + ".stdin", "ab") as f:
    f.write(data)
if os.environ.get("TRUNCATE") == "1":  # a transfer cut at a line boundary: the end marker is lost
    data = data.rstrip(b"\n").rsplit(b"\n", 1)[0] + b"\n"
sys.exit(subprocess.run(["bash", "-c", sys.argv[-1]], input=data).returncode)
"""


class InstallLlmEnvTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        (self.state / "ssh_config").write_text("Host hlm-deploy\n  HostName 203.0.113.10\n")
        self.etc = self.root / "etc-hlmemo"
        self.etc.mkdir(mode=0o750)
        self.target = self.etc / "llm.env"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "ssh").write_text(FAKE_SSH)
        (self.bin / "ssh").chmod(0o700)
        self.events = self.root / "events"
        self.key_file = self.root / "dot.env"
        self.write_key(KEY_A)
        self.env = dict(
            os.environ,
            PATH=f"{self.bin}:{os.environ['PATH']}",
            EVENTS=str(self.events),
            HLM_REMOTE_ENV=str(self.etc / "prod.env"),
        )

    def write_key(self, key):
        # A dotenv with other secrets: only the profiles' key variable may ever be read.
        self.key_file.write_text(f"OTHER_SECRET={OTHER}\nexport OPENROUTER_API_KEY='{key}'\n")

    def run_install(self, *extra, env=None):
        result = subprocess.run(
            ["bash", str(SCRIPT), "--state", str(self.state), "--key-file", str(self.key_file), *extra],
            env=env or self.env,
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        return result, result.stdout + result.stderr

    def calls(self):
        if not self.events.exists():
            return []
        return [json.loads(line) for line in self.events.read_text().splitlines()]

    def assert_never_exposed(self, output, *keys):
        argv = json.dumps([c["argv"] for c in self.calls()])
        for key in (*keys, OTHER):
            self.assertNotIn(key, output, "key in the script output")
            self.assertNotIn(key, argv, "key in an ssh argv")

    def backups(self):
        return sorted(self.etc.glob("llm.env.bak-*"))

    def test_install_sends_key_on_stdin_only_writes_0600_and_is_idempotent(self):
        result, output = self.run_install()
        self.assertEqual(0, result.returncode, output)
        content = self.target.read_text()
        self.assertEqual(0o600, stat.S_IMODE(self.target.stat().st_mode))
        self.assertEqual(os.getuid(), self.target.stat().st_uid)
        for line in (
            "HLM_LIBRARIAN_ENABLED=true",
            "HLM_LIBRARIAN_ROLE=observer",
            "HLM_PROFILE=openrouter-gpt6-luna",
            # D-094 production mapping, straight from the template
            "HLM_FALLBACK_PROFILE=openrouter-glm53-flash",
            "HLM_FALLBACK_PROFILE__SYNTHESIS=openrouter",
            "HLM_FALLBACK_PROFILE__QUERY_REWRITE=openrouter",
            "HLM_FALLBACK_PROFILE__RISK_JUDGE=openrouter-qwen38-27b-fast",
            f"OPENROUTER_API_KEY={KEY_A}",
            "HLM_LLM_MODE=live",
            "HLM_LLM_BUDGET_HOUR_USD=3",
            "HLM_LLM_BUDGET_DAY_USD=10",
            "HLM_LLM_BUDGET_MONTH_USD=60",
            "HLM_LLM_JOB_CALL_CAP=20",
            # D-111/D-116: the release marker (the post-cutover check expects the R3 manifest)
            "HLM_ENV_RELEASE=r3",
        ):
            self.assertIn(line + "\n", content)
        # D-116: R3 ships without the query rewrite and the per-source cap
        self.assertNotIn("\nHLM_QUERY_REWRITE=", content)
        self.assertNotIn("\nHLM_RETRIEVAL_SOURCE_CAP=", content)
        self.assertNotIn("HLM_LIBRARIAN_ENABLED=false", content)
        self.assertNotIn(OTHER, content, "only the profile's key variable is read")
        self.assertTrue(content.endswith("# END llm.env (install_llm_env.sh)\n"))
        self.assertIn(f"installed {self.target} (0600", output)
        self.assertIn("OPENROUTER_API_KEY=<set>", output)
        self.assertIn(KEY_A, (self.root / "events.stdin").read_text(), "the key travelled on ssh stdin")
        (call,) = self.calls()
        self.assertEqual(call["argv"][call["argv"].index("-F") + 1], str(self.state / "ssh_config"))
        self.assertIn("hlm-deploy", call["argv"])
        self.assert_never_exposed(output, KEY_A)
        self.assertEqual([], self.backups())
        # Idempotent: same key, same file, no backup.
        before = self.target.read_bytes()
        again, output = self.run_install()
        self.assertEqual(0, again.returncode, output)
        self.assertIn(f"unchanged {self.target}", output)
        self.assertEqual(before, self.target.read_bytes())
        self.assertEqual([], self.backups())
        self.assert_never_exposed(output, KEY_A)

    def test_changed_key_backs_up_previous_file_and_keeps_three(self):
        self.assertEqual(0, self.run_install()[0].returncode)
        self.write_key(KEY_B)
        result, output = self.run_install()
        self.assertEqual(0, result.returncode, output)
        self.assertIn(f"OPENROUTER_API_KEY={KEY_B}\n", self.target.read_text())
        (backup,) = self.backups()
        self.assertEqual(0o600, stat.S_IMODE(backup.stat().st_mode))
        self.assertIn(f"OPENROUTER_API_KEY={KEY_A}\n", backup.read_text())
        self.assertIn(f"backup {backup}", output)
        self.assert_never_exposed(output, KEY_A, KEY_B)
        for i in range(4):  # rotations: only the newest three backups stay
            self.write_key(f"fake-or-rotation{i}-" + "K5" * 10)
            self.assertEqual(0, self.run_install()[0].returncode)
        self.assertEqual(3, len(self.backups()))
        self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in self.backups()))

    def test_remove_deletes_file_and_backups(self):
        self.assertEqual(0, self.run_install()[0].returncode)
        self.write_key(KEY_B)
        self.assertEqual(0, self.run_install()[0].returncode)
        self.assertEqual(1, len(self.backups()))
        result = subprocess.run(
            ["bash", str(SCRIPT), "--state", str(self.state), "--remove"],
            env=self.env,
            text=True,
            capture_output=True,
            timeout=60,
        )
        output = result.stdout + result.stderr
        self.assertEqual(0, result.returncode, output)
        self.assertFalse(self.target.exists())
        self.assertEqual([], self.backups())
        self.assertIn("removed 2 file(s)", output)
        self.assertEqual(0, self.calls()[-1]["stdin_bytes"], "--remove sends nothing")
        self.assert_never_exposed(output, KEY_A, KEY_B)

    def test_missing_key_or_bad_input_sends_nothing(self):
        self.key_file.write_text(f"OTHER_SECRET={OTHER}\nOPENROUTER_API_KEY=\n")
        result, output = self.run_install()
        self.assertEqual(65, result.returncode, output)
        self.assertIn("OPENROUTER_API_KEY is missing or empty", output)
        self.assertEqual([], self.calls(), "ssh must not run without a key")
        self.key_file.write_text("OPENROUTER_API_KEY=has spaces and $dollar\n")
        result, output = self.run_install()
        self.assertEqual(65, result.returncode, output)
        self.assertIn("value not shown", output)
        self.assertNotIn("$dollar", output)
        self.assertEqual([], self.calls())
        for args in ((), ("--state", str(self.state), "--profile", "nope")):
            result = subprocess.run(
                ["bash", str(SCRIPT), *args], env=self.env, capture_output=True, timeout=30
            )
            self.assertEqual(64, result.returncode)
        self.assertFalse(self.target.exists())

    def test_fallback_flag_replaces_the_default_only_and_template_profiles_must_exist(self):
        """D-094: --fallback replaces HLM_FALLBACK_PROFILE; the per-task lines stay the template's.
        A template naming a profile that does not exist sends nothing."""
        result, output = self.run_install("--fallback", "openrouter")
        self.assertEqual(0, result.returncode, output)
        content = self.target.read_text()
        self.assertIn("HLM_FALLBACK_PROFILE=openrouter\n", content)
        self.assertIn("HLM_FALLBACK_PROFILE__RISK_JUDGE=openrouter-qwen38-27b-fast\n", content)
        self.assertEqual(1, content.count("HLM_FALLBACK_PROFILE="))
        self.assert_never_exposed(output, KEY_A)
        # a copy of the script next to a template with a mistyped per-task profile
        repo = self.root / "repo"
        (repo / "deploy/scripts").mkdir(parents=True)
        (repo / "deploy/scripts/install_llm_env.sh").write_text(SCRIPT.read_text())
        (repo / "profiles").symlink_to(ROOT / "profiles")
        template = (ROOT / "deploy/llm.env.example").read_text()
        (repo / "deploy/llm.env.example").write_text(
            template.replace("__RISK_JUDGE=openrouter-qwen38-27b-fast", "__RISK_JUDGE=no-such-profile")
        )
        before = len(self.calls())
        result = subprocess.run(
            ["bash", str(repo / "deploy/scripts/install_llm_env.sh"), "--state", str(self.state),
             "--key-file", str(self.key_file)],
            env=self.env, text=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=60,
        )  # fmt: skip
        output = result.stdout + result.stderr
        self.assertEqual(65, result.returncode, output)
        self.assertIn("unknown profile in llm.env: no-such-profile", output)
        self.assertEqual(before, len(self.calls()), "nothing is sent")
        self.assert_never_exposed(output, KEY_A)

    def test_truncated_transfer_is_refused_and_leaves_the_file(self):
        self.assertEqual(0, self.run_install()[0].returncode)
        before = self.target.read_bytes()
        self.write_key(KEY_B)
        result, output = self.run_install(env=dict(self.env, TRUNCATE="1"))
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn("incomplete llm.env on stdin", output)
        self.assertEqual(before, self.target.read_bytes())
        self.assertEqual([], self.backups())
        self.assertEqual([], list(self.etc.glob("llm.env.tmp-*")))
        self.assert_never_exposed(output, KEY_A, KEY_B)


if __name__ == "__main__":
    unittest.main()
