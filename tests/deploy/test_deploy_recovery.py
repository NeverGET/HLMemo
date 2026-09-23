"""Exercise real deploy/backup shell control flow with isolated transport/runtime doubles."""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = "a" * 40
NEXT = "b" * 40

DOCKER = r"""#!/usr/bin/env python3
import json, os, signal, sys, time
from pathlib import Path
args=sys.argv[1:]
with open(os.environ["EVENTS"], "a") as f: f.write(json.dumps(["docker", *args])+"\n")
fail=os.environ.get("FAIL", "")
state_path=Path(os.environ["EVENTS"]+".images")
images=json.loads(state_path.read_text()) if state_path.exists() else {}
labels_path=Path(os.environ["EVENTS"]+".labels")
labels=json.loads(labels_path.read_text()) if labels_path.exists() else {}
env_file=Path(os.environ["HLM_REMOTE_ENV"])
settings={}
if env_file.exists():
    settings=dict(line.split("=", 1) for line in env_file.read_text().splitlines() if "=" in line)
selected=os.environ.get("HLM_IMAGE") or settings.get("HLM_IMAGE", "hlmemo:prod")
if args[:2] == ["image", "inspect"]:
    if args[-1] not in images: sys.exit(1)
    if "--format" in args and "org.opencontainers.image.revision" in args[args.index("--format")+1]:
        print(labels.get(args[-1], ""))
    else:
        print(images[args[-1]])
    sys.exit()
if args[:2] == ["image", "tag"]:
    images[args[-1]]=args[-2]
    state_path.write_text(json.dumps(images))
    sys.exit()
if "build" in args:
    images[selected]="sha256:new-image"
    state_path.write_text(json.dumps(images))
    assert os.environ["HLM_IMAGE_REVISION"] == "b"*40
    if fail != "build-missing-label":
        labels[selected] = "a"*40 if fail == "build-wrong-label" else os.environ["HLM_IMAGE_REVISION"]
    labels_path.write_text(json.dumps(labels))
if "run" in args and "migrate" in args:
    Path(os.environ["EVENTS"]+".migration-image").write_text(images.get(selected, selected))
    if fail == "retag-during-migration":
        labels[selected] = "a"*40
        labels_path.write_text(json.dumps(labels))
if "up" in args and "api" in args:
    Path(os.environ["EVENTS"]+".running-image").write_text(images.get(selected, selected))
    # The api container's org.opencontainers.image.revision label (rollback.sh checks it).
    tag = selected.rsplit(":", 1)[-1]
    revision = tag if len(tag) == 40 else labels.get(selected, "")
    Path(os.environ["EVENTS"]+".running-revision").write_text(revision)
if "-f" in args and ".rollback-compose." in args[args.index("-f")+1]:
    captured=json.loads(Path(args[args.index("-f")+1]).read_text())
    assert captured["services"]["api"]["environment"]["TOKEN"] == "literal$$VAR"
if args[0] == "ps":
    if fail != "missing-baseline": print("old-container")
elif args[0] == "inspect" and "image.revision" in " ".join(args):
    marker = Path(os.environ["EVENTS"]+".running-revision")
    print(marker.read_text() if marker.exists() else "a"*40)
elif args[0] == "inspect": print("sha256:old-image")
elif ("exec" in args and "dropdb" in args[-1] and fail == "rollback-kill"
      and ".rollback-compose." in " ".join(args)
      and not Path(os.environ["EVENTS"]+".killed").exists()):
    Path(os.environ["EVENTS"]+".killed").touch()
    os.kill(os.getppid(), signal.SIGKILL)  # an uncatchable interruption mid-rollback
elif "config" in args and "-f" in args and ".rollback-compose." in args[args.index("-f")+1]:
    print(Path(args[args.index("-f")+1]).read_text())  # the captured rollback model, as rendered
elif "config" in args:
    rendered = {"name":"bake-astra", "services":{
        s:{"image":"mutable:prod", "environment":{"TOKEN":"literal$$VAR"}}
        for s in ("api","worker","db","caddy")}}
    if "-f" in args and ".compose-previous." in args[args.index("-f")+1]:
        Path(os.environ["EVENTS"]+".previous-model").write_text(Path(args[args.index("-f")+1]).read_text())
        # Like Compose: the api service's env_file (HLM_API_ENV_FILE) lands in its environment.
        api_env = Path(os.environ.get("HLM_API_ENV_FILE") or "/nonexistent")
        if api_env.is_file():
            for line in api_env.read_text().splitlines():
                key, sep, value = line.removeprefix("export ").partition("=")
                if sep and not key.startswith("#"):
                    rendered["services"]["api"]["environment"][key.strip()] = value
    print(json.dumps(rendered))
elif "ps" in args:
    if os.environ.get("INITIAL") != "1": print("db-container")
elif "exec" in args and "hlmemo.ops" in args:
    # W0a: server-side minting; the token only ever travels on stdout.
    assert sys.stdin.read() == "", "hlmemo.ops inherited input"
    if "mint" in args:
        print(os.environ.get("ROUTES_TOKEN", "hlm_" + "r" * 43))
    elif "list" in args:
        print("   2  g7-mac   personal trusted  expires=- grants=gates-g7:write")
elif "exec" in args and "--routes" in args:
    assert "def check_routes" in sys.stdin.read(), "route checker not fed on stdin"
    if fail == "routes-internal": sys.exit(14)
elif "exec" in args:
    if "pg_dump" in args[-1]:
        assert sys.stdin.read() == "", "pg_dump inherited input"
        if fail == "dump": sys.exit(7)
        print("valid-snapshot")
        if fail == "retag-during-snapshot":
            labels[selected] = "a"*40
            labels_path.write_text(json.dumps(labels))
    elif "dropdb" in args[-1]:
        assert sys.stdin.read() in ("", "valid-snapshot\n")
    elif "pg_restore" in args: sys.stdin.read()
    elif fail == "internal-api" and "api" in args: sys.exit(12)
    elif fail == "internal-caddy" and "caddy" in args: sys.exit(13)
elif "stop" in args and fail == "term":
    marker=Path(os.environ["EVENTS"]+".signalled")
    if not marker.exists():
        marker.touch()
        os.kill(os.getppid(), signal.SIGTERM)
elif "stop" in args and fail in ("stop", "stop-always"):
    if fail == "stop-always": sys.exit(8)
    marker=Path(os.environ["EVENTS"]+".stopped")
    if not marker.exists(): marker.touch(); sys.exit(8)
elif "run" in args:
    assert sys.stdin.read() == "", "migrate inherited input"
    if fail in ("slow", "observer"):
        Path(os.environ["EVENTS"]+".migrating").touch()
        time.sleep(2)
    if fail == "term-migration": os.kill(os.getppid(), signal.SIGTERM)
    if fail == "migration": sys.exit(9)
elif "up" in args and "api" in args and fail == "health" and ".rollback-compose." not in " ".join(args):
    sys.exit(10)
elif "up" in args and "api" in args and fail == "rollback-up" and ".rollback-compose." in " ".join(args):
    sys.exit(10)
"""

GIT = r"""#!/usr/bin/env python3
import json, os, sys
args=sys.argv[1:]
with open(os.environ["EVENTS"], "a") as f: f.write(json.dumps(["git", *args])+"\n")
if args[0] == "show":
    from pathlib import Path
    if args[-1].endswith(":deploy/compose.prod.yaml"):
        name = "compose.previous.yaml" if args[-1].startswith("a"*40) else "compose.prod.yaml"
        path = Path(os.environ["HLM_REMOTE_DIR"])/"deploy"/name
        if not path.exists(): path = Path(os.environ["HLM_REMOTE_DIR"])/"deploy/compose.prod.yaml"
        sys.stdout.write(path.read_text())
    elif ":deploy/scripts/" in args[-1] and not args[-1].endswith("remote-deploy.sh"):
        sys.stdout.write((Path(os.environ["HLM_REMOTE_DIR"])/args[-1].split(":", 1)[1]).read_text())
    else:
        print((Path(os.environ["HLM_REMOTE_DIR"])/"deploy/scripts/remote-deploy.sh").read_text())
    sys.exit()
changed = os.environ.get("FAIL") == "compose-change" or os.environ.get("COMPOSE_CHANGE") == "1"
if args[:2] == ["cat-file", "-e"]:
    # PREW0_TARGET=1: the previous release (a*40) predates W0a (no 0005 migration, no ops package).
    sys.exit(1 if os.environ.get("PREW0_TARGET") == "1" and args[2].startswith("a"*40) else 0)
if args[0] == "diff" and changed: sys.exit(1)
if args[:2] == ["checkout", "--detach"] and args[-1] == "b"*40 and os.environ.get("FAIL") == "legacy":
    from pathlib import Path
    import shutil
    shutil.copyfile(os.environ["NEW_COMMON"], Path.cwd()/"deploy/scripts/common.sh")
if args[:2] == ["remote", "get-url"]: print("https://example.invalid/repo.git")
elif args[0] == "rev-parse": print("b"*40 if "FETCH_HEAD^{commit}" in args else "a"*40)
"""

COMMON = r"""set -euo pipefail
DEPLOY_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
backup_dir() { echo "$HLM_BACKUP_DIR"; }
backup_path() { echo "$1"; }
COMPOSE_PROJECT=bake-astra
export COMPOSE_PROJECT
dc() { docker compose -p "$COMPOSE_PROJECT" -f "$DEPLOY_DIR/compose.prod.yaml" "$@"; }
env_value() { if [[ $1 == HLM_DOMAIN ]]; then echo localhost; fi; }
backup_value() { echo ""; }
backup_env() { [[ ${FAIL:-} != upload ]] || echo S3_BUCKET=simulated-upload; }
"""


# W0a: the public route checker runs as `python3 deploy/scripts/check_edge.py --routes` on the
# host. Intercept only that invocation (it would open real sockets); everything else runs the real
# interpreter. The shim records argv and whether the token arrived through the environment.
PYTHON3 = """#!/bin/sh
case "$1" in
  */check_edge.py|deploy/scripts/check_edge.py)
    printf '%s\\n' "$*" >> "$EVENTS.routes-argv"
    printf '%s\\n' "${HLM_ROUTES_TOKEN:-<none>}" >> "$EVENTS.routes-token"
    [ "$FAIL" != routes-public ] || exit 1
    echo 'RESULT routes PASS (harness)'
    exit 0 ;;
esac
exec REAL_PYTHON "$@"
"""


class DeployRecoveryTest(unittest.TestCase):
    def prepare_deploy(self, failure, initial=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        app = root / "app"
        (app / ".git").mkdir(parents=True)
        shutil.copytree(ROOT / "deploy", app / "deploy", ignore=shutil.ignore_patterns("data", ".terraform"))
        (app / "deploy/scripts/common.sh").write_text(COMMON)
        if not initial:
            (root / "current-ref").write_text(PREVIOUS + "\n")
        (root / "previous-ref").write_text("c" * 40 + "\n")
        (root / "previous-dump").write_text("older-marker.dump\n")
        (root / "prod.env").write_text("HLM_DOMAIN=localhost\n")
        binary = root / "bin"
        binary.mkdir()
        programs = {
            "docker": DOCKER,
            "git": GIT,
            "ssh": (
                "#!/usr/bin/env bash\n"
                "if [[ $FAIL == observer && -f $EVENTS.migrating ]]; then exit 255; fi\n"
                'exec bash -c "${@: -1}"\n'
            ),
            "curl": "#!/usr/bin/env bash\n[[ $FAIL != external ]] || exit 22\necho ready\n",
            "setsid": (
                "#!/usr/bin/env python3\nimport os,sys\nos.setsid()\nos.execvp(sys.argv[1],sys.argv[1:])\n"
            ),
            "aws": "#!/usr/bin/env bash\necho simulated-upload-failure >&2\nexit 42\n",
            "python3": PYTHON3.replace("REAL_PYTHON", shutil.which("python3") or sys.executable),
        }
        for name, content in programs.items():
            path = binary / name
            path.write_text(content)
            path.chmod(0o700)
        events = root / "events"
        env = dict(
            os.environ,
            PATH=f"{binary}:{os.environ['PATH']}",
            FAIL=failure,
            EVENTS=str(events),
            HLM_REMOTE_DIR=str(app),
            HLM_REMOTE_ENV=str(root / "prod.env"),
            HLM_BACKUP_DIR=str(root / "backups"),
            INITIAL="1" if initial else "0",
            HLM_DEPLOY_POLL_SECONDS="0.05",
            NEW_COMMON=str(root / "new-common.sh"),
        )
        if failure == "legacy":
            (root / "new-common.sh").write_text(COMMON)
            (app / "deploy/scripts/common.sh").write_text("echo legacy-layout-cannot-render >&2; exit 99\n")
        if initial:
            (root / ".deploy-managed").touch()
        return root, env

    def run_deploy(self, failure, initial=False):
        root, env = self.prepare_deploy(failure, initial)
        events = root / "events"
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "deploy/scripts/deploy.sh"),
                "local",
                "next",
                "https://example.invalid/repo.git",
            ],
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        result.stderr += result.stdout
        rows = [json.loads(line) for line in events.read_text().splitlines()]
        return root, result, rows

    def test_failed_build_release_then_restore_uses_previous_image_and_alembic(self):
        for failure in ("dump", "migration", "health"):
            with self.subTest(failure=failure):
                root, env = self.prepare_deploy(failure)
                result = subprocess.run(
                    [
                        "bash",
                        str(ROOT / "deploy/scripts/deploy.sh"),
                        "local",
                        "next",
                        "https://example.invalid/repo.git",
                    ],
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=30,
                )
                self.assertNotEqual(0, result.returncode, result.stdout)
                images = json.loads((root / "events.images").read_text())
                self.assertEqual("sha256:new-image", images[f"hlmemo:{NEXT}"])
                self.assertEqual("sha256:old-image", images[f"hlmemo:{PREVIOUS}"])
                self.assertIn(f"HLM_IMAGE=hlmemo:{PREVIOUS}\n", (root / "prod.env").read_text())
                dump = root / "restore.dump"
                dump.write_text("valid-snapshot\n")
                env.update(FAIL="", HLM_ENV_FILE=str(root / "prod.env"))
                restored = subprocess.run(
                    ["bash", str(root / "app/deploy/backup/restore.sh"), str(dump), "--yes"],
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=15,
                )
                self.assertEqual(0, restored.returncode, restored.stderr)
                self.assertEqual("sha256:old-image", (root / "events.migration-image").read_text())
                self.assertEqual("sha256:old-image", (root / "events.running-image").read_text())

    def test_compose_change_refused_before_build_backup_or_stop(self):
        root, result, rows = self.run_deploy("compose-change")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Compose model changed", result.stdout)
        self.assertFalse(any("build" in row or "stop" in row or "pg_dump" in row[-1] for row in rows))
        self.assertEqual(PREVIOUS, (root / "current-ref").read_text().strip())

    def test_existing_release_image_is_reused_without_rebuild(self):
        root, env = self.prepare_deploy("")
        (root / "events.images").write_text(json.dumps({f"hlmemo:{NEXT}": "sha256:built-release"}))
        (root / "events.labels").write_text(json.dumps({f"hlmemo:{NEXT}": NEXT}))
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "deploy/scripts/deploy.sh"),
                "local",
                "next",
                "https://example.invalid/repo.git",
            ],
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout)
        rows = [json.loads(line) for line in (root / "events").read_text().splitlines()]
        self.assertFalse(any("build" in row for row in rows))
        self.assertEqual("sha256:built-release", (root / "events.running-image").read_text())

    def test_existing_release_with_missing_or_wrong_revision_fails_before_build_or_stop(self):
        for label in (None, PREVIOUS):
            with self.subTest(label=label):
                root, env = self.prepare_deploy("")
                (root / "events.images").write_text(json.dumps({f"hlmemo:{NEXT}": "sha256:wrong"}))
                labels = {} if label is None else {f"hlmemo:{NEXT}": label}
                (root / "events.labels").write_text(json.dumps(labels))
                result = subprocess.run(
                    [
                        "bash",
                        str(ROOT / "deploy/scripts/deploy.sh"),
                        "local",
                        "next",
                        "https://example.invalid/repo.git",
                    ],
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=30,
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("Release image revision mismatch", result.stdout)
                rows = [json.loads(line) for line in (root / "events").read_text().splitlines()]
                self.assertFalse(any("build" in row or "stop" in row or "pg_dump" in row[-1] for row in rows))
                self.assertEqual(PREVIOUS, (root / "current-ref").read_text().strip())

    def test_new_build_with_missing_or_wrong_revision_fails_before_backup_or_stop(self):
        for failure in ("build-missing-label", "build-wrong-label"):
            with self.subTest(failure=failure):
                root, result, rows = self.run_deploy(failure)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("Release image revision mismatch", result.stdout)
                self.assertTrue(any("build" in row for row in rows))
                self.assertFalse(any("stop" in row or "pg_dump" in row[-1] for row in rows))
                self.assertEqual(PREVIOUS, (root / "current-ref").read_text().strip())

    def test_revision_is_rechecked_before_migration_and_service_start(self):
        for failure, migrated in (("retag-during-snapshot", False), ("retag-during-migration", True)):
            with self.subTest(failure=failure):
                root, result, rows = self.run_deploy(failure)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("Release image revision mismatch", result.stdout)
                self.assertIn("Previous stack restored", result.stdout)
                self.assertEqual(migrated, any("run" in row and "migrate" in row for row in rows))
                self.assertFalse(
                    any(
                        "up" in row and "api" in row and ".rollback-compose." not in " ".join(row)
                        for row in rows
                    )
                )
                self.assertEqual(PREVIOUS, (root / "current-ref").read_text().strip())

    def test_conflicting_previous_release_tag_fails_without_retag_or_build(self):
        root, env = self.prepare_deploy("")
        (root / "events.images").write_text(json.dumps({f"hlmemo:{PREVIOUS}": "sha256:conflict"}))
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "deploy/scripts/deploy.sh"),
                "local",
                "next",
                "https://example.invalid/repo.git",
            ],
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("refusing to retag immutable release", result.stdout)
        rows = [json.loads(line) for line in (root / "events").read_text().splitlines()]
        self.assertFalse(any("build" in row or "stop" in row or "tag" in row for row in rows))
        self.assertEqual(
            "sha256:conflict", json.loads((root / "events.images").read_text())[f"hlmemo:{PREVIOUS}"]
        )

    def test_image_publication_failure_recovers_previous_release(self):
        root, env = self.prepare_deploy("")
        helper = root / "app/deploy/scripts/release_env.py"
        helper.write_text(
            f"import sys\nif sys.argv[2].endswith({NEXT!r}): sys.exit(17)\n" + helper.read_text()
        )
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "deploy/scripts/deploy.sh"),
                "local",
                "next",
                "https://example.invalid/repo.git",
            ],
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(17, result.returncode, result.stdout)
        self.assertIn("Previous stack restored", result.stdout)
        self.assertIn(f"HLM_IMAGE=hlmemo:{PREVIOUS}\n", (root / "prod.env").read_text())
        self.assertEqual(PREVIOUS, (root / "current-ref").read_text().strip())

    def test_success_publishes_immutable_image(self):
        root, result, _ = self.run_deploy("")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f"HLM_IMAGE=hlmemo:{NEXT}\n", (root / "prod.env").read_text())

    def test_stdin_reading_children_reach_deployment_ready(self):
        for initial in (True, False):
            with self.subTest(initial=initial):
                root, result, rows = self.run_deploy("", initial=initial)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn("Deployment ready", result.stdout)
                self.assertTrue(any("run" in row and "migrate" in row for row in rows))
                self.assertTrue(any("up" in row and "api" in row for row in rows))
                self.assertEqual("0", next(root.glob(".deploy-runs/*/status")).read_text().strip())
                self.assertFalse(list(root.glob(".rollback-compose.*")))

    def test_external_failure_keeps_new_stack_and_never_restores_database(self):
        root, result, rows = self.run_deploy("external")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("internally healthy new stack left running", result.stdout)
        self.assertEqual(NEXT, (root / "current-ref").read_text().strip())
        self.assertFalse(any("dropdb" in row[-1] for row in rows))
        self.assertEqual(1, sum("stop" in row for row in rows))
        self.assertFalse(list(root.glob(".rollback-compose.*")))

    def test_missing_rollback_container_fails_before_stopping_writers(self):
        root, result, rows = self.run_deploy("missing-baseline")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Cannot capture rollback", result.stdout)
        self.assertFalse(any("stop" in row for row in rows))
        self.assertFalse(list(root.glob(".rollback-compose.*")))

    def test_rollback_config_render_follows_target_checkout(self):
        _, result, rows = self.run_deploy("legacy")
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn("Deployment ready", result.stdout)
        self.assertNotIn("legacy-layout-cannot-render", result.stdout)
        checkout = next(i for i, row in enumerate(rows) if row[:3] == ["git", "checkout", "--detach"])
        render = next(i for i, row in enumerate(rows) if "config" in row)
        self.assertLess(checkout, render)

    def test_success_prunes_pre_upgrade_dumps_but_failure_does_not(self):
        for failure, keep in (("", 5), ("migration", 8), ("external", 8)):
            with self.subTest(failure=failure):
                root, env = self.prepare_deploy(failure)
                snapshots = root / "backups/pre-upgrade"
                snapshots.mkdir(parents=True)
                for index in range(7):
                    dump = snapshots / f"old-{index}.dump"
                    dump.write_text("old")
                    os.utime(dump, (index + 1, index + 1))
                result = subprocess.run(
                    [
                        "bash",
                        str(ROOT / "deploy/scripts/deploy.sh"),
                        "local",
                        "next",
                        "https://example.invalid/repo.git",
                    ],
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=30,
                )
                self.assertEqual(keep, len(list(snapshots.glob("*.dump"))), result.stdout)
                if not failure:
                    self.assertEqual(0, result.returncode, result.stdout)
                    self.assertTrue(Path((root / "previous-dump").read_text().strip()).exists())

    def test_ssh_observer_failure_reports_guidance_and_remote_completes(self):
        root, result, _ = self.run_deploy("observer")
        self.assertEqual(255, result.returncode)
        self.assertIn("deployment continues", result.stderr)
        deadline = time.monotonic() + 10
        while not list(root.glob(".deploy-runs/*/status")) and time.monotonic() < deadline:
            time.sleep(0.05)
        status = next(root.glob(".deploy-runs/*/status"))
        self.assertEqual("0", status.read_text().strip())
        self.assertIn("Deployment ready", status.with_name("log").read_text())
        self.assertFalse(list(root.glob(".rollback-compose.*")))

    def test_early_setup_failure_still_publishes_final_status(self):
        root, env = self.prepare_deploy("")
        (root / "prod.env").unlink()
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "deploy/scripts/deploy.sh"),
                "local",
                "next",
                "https://example.invalid/repo.git",
            ],
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("Missing readable env file", result.stdout)
        self.assertEqual("1", next(root.glob(".deploy-runs/*/status")).read_text().strip())
        self.assertFalse(list(root.glob(".rollback-compose.*")))

    def test_killing_client_process_group_does_not_stop_remote_migration(self):
        root, env = self.prepare_deploy("slow")
        command = [
            "bash",
            str(ROOT / "deploy/scripts/deploy.sh"),
            "local",
            "next",
            "https://example.invalid/repo.git",
        ]
        client = subprocess.Popen(
            command,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 15
            while not (root / "events.migrating").exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue((root / "events.migrating").exists())
            os.killpg(client.pid, signal.SIGKILL)
            client.communicate(timeout=5)
            while not list(root.glob(".deploy-runs/*/status")) and time.monotonic() < deadline:
                time.sleep(0.05)
            status = next(root.glob(".deploy-runs/*/status"))
            self.assertEqual("0", status.read_text().strip())
            self.assertIn("Deployment ready", status.with_name("log").read_text())
            self.assertFalse(list(root.glob(".rollback-compose.*")))
            self.assertEqual(NEXT, (root / "current-ref").read_text().strip())
        finally:
            if client.poll() is None:
                os.killpg(client.pid, signal.SIGKILL)
                client.communicate(timeout=5)

    def test_real_compose_rollback_roundtrip_preserves_dollar_literals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / "service.env"
            env_file.write_text("TOKEN='x$FOO'\n")
            compose_file = root / "compose.json"
            compose_file.write_text(
                json.dumps(
                    {
                        "name": "bake-astra",
                        "services": {
                            name: {
                                "image": "busybox",
                                "env_file": [str(env_file)],
                                "healthcheck": {"test": ["CMD-SHELL", "echo $$HOME"]},
                            }
                            for name in ("db", "api", "worker", "caddy")
                        },
                    }
                )
            )
            command = ["docker", "compose", "-f", str(compose_file), "config", "--format", "json"]
            first = json.loads(subprocess.check_output(command))
            self.assertEqual("x$$FOO", first["services"]["db"]["environment"]["TOKEN"])
            self.assertEqual(["CMD-SHELL", "echo $$HOME"], first["services"]["db"]["healthcheck"]["test"])
            compose_file.write_text(json.dumps(first))
            # Run the actual deployment snapshot transformation, mocking only the
            # running-container lookup; Compose parsing above/below is real.
            script = (ROOT / "deploy/scripts/remote-deploy.sh").read_text()
            transform = script.split("<<'PYCONFIG'\n", 1)[1].split("\nPYCONFIG", 1)[0]
            with (
                patch("sys.argv", ["snapshot", str(compose_file)]),
                patch("subprocess.check_output", return_value="old-image"),
            ):
                exec(compile(transform, "deploy-rollback-snapshot", "exec"), {})
            second = json.loads(subprocess.check_output(command))
            for service in second["services"].values():
                service["image"] = "busybox"
            self.assertEqual(first, second)

    def test_upload_failure_is_nonfatal_and_dump_precedes_stop(self):
        root, result, rows = self.run_deploy("upload")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("WARNING: pre-upgrade S3 upload failed", result.stderr)
        self.assertEqual(NEXT, (root / "current-ref").read_text().strip())
        self.assertEqual(PREVIOUS, (root / "previous-ref").read_text().strip())
        dump = Path((root / "previous-dump").read_text().strip())
        self.assertEqual("pre-upgrade", dump.parent.name)
        self.assertEqual("valid-snapshot\n", dump.read_text())
        snapshot = next(i for i, row in enumerate(rows) if "pg_dump" in row[-1])
        stop = next(i for i, row in enumerate(rows) if "stop" in row)
        self.assertLess(snapshot, stop)

    def test_dump_failure_never_stops_writers(self):
        root, result, rows = self.run_deploy("dump")
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(any("stop" in row for row in rows))
        self.assertEqual(PREVIOUS, (root / "current-ref").read_text().strip())
        self.assertIn("previous stack remains running", result.stderr)

    def test_migration_and_health_failure_restore_dump_then_previous_stack(self):
        for failure in ("migration", "health", "term-migration", "internal-api", "internal-caddy"):
            with self.subTest(failure=failure):
                root, result, rows = self.run_deploy(failure)
                self.assertNotEqual(0, result.returncode)
                if failure == "term-migration":
                    self.assertEqual(143, result.returncode)
                self.assertIn("Previous stack restored", result.stderr)
                self.assertEqual(PREVIOUS, (root / "current-ref").read_text().strip())
                restores = [i for i, row in enumerate(rows) if "dropdb" in row[-1]]
                self.assertEqual(1, len(restores))
                restarts = [
                    i
                    for i, row in enumerate(rows)
                    if "up" in row
                    and "--no-deps" in row
                    and "api" in row
                    and ".rollback-compose." in " ".join(row)
                ]
                self.assertEqual(1, len(restarts))
                self.assertLess(restores[0], restarts[0])
                self.assertEqual(1, sum("run" in row and "migrate" in row for row in rows))
                self.assertFalse(list(root.glob(".rollback-compose.*")))
                self.assertEqual("c" * 40, (root / "previous-ref").read_text().strip())
                self.assertEqual("older-marker.dump", (root / "previous-dump").read_text().strip())

    def test_librarian_joins_writer_lifecycle_and_is_removed_on_recovery_to_older_model(self):
        """W2a: the librarian stops/starts with the writers and its heartbeat is checked; a recovery
        to a previous model without the service (the harness renders api/worker/db/caddy only)
        removes the new librarian container and never asks the rollback model to start it."""
        _, result, rows = self.run_deploy("")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(
            any(row[-4:] == ["caddy", "api", "worker", "librarian"] and "stop" in row for row in rows)
        )
        self.assertTrue(any("up" in row and "--wait" in row and "librarian" in row for row in rows))
        self.assertTrue(any("exec" in row and "hlmemo.librarian.health" in row for row in rows))
        _, result, rows = self.run_deploy("migration")
        self.assertIn("Previous stack restored", result.stderr)
        self.assertTrue(any("rm" in row and row[-1] == "librarian" for row in rows))
        restarts = [row for row in rows if "up" in row and ".rollback-compose." in " ".join(row)]
        self.assertTrue(restarts)
        self.assertFalse(any("librarian" in row for row in restarts))

    def test_sigterm_after_stop_recovers_previous_stack(self):
        root, result, rows = self.run_deploy("term")
        self.assertEqual(143, result.returncode, result.stderr)
        self.assertIn("Previous stack restored", result.stderr)
        self.assertTrue(any("up" in row and "--no-deps" in row and "api" in row for row in rows))
        self.assertFalse(any("dropdb" in row[-1] for row in rows))
        self.assertEqual(PREVIOUS, (root / "current-ref").read_text().strip())

    def test_partial_stop_failure_restarts_without_database_restore(self):
        for failure in ("stop", "stop-always"):
            with self.subTest(failure=failure):
                root, result, rows = self.run_deploy(failure)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("Previous stack restored", result.stderr)
                self.assertFalse(any("dropdb" in row[-1] for row in rows))
                self.assertTrue(any("up" in row and "--no-deps" in row and "api" in row for row in rows))


if __name__ == "__main__":
    unittest.main()
