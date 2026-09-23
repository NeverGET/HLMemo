"""Sol 36 M1: the rollback tuple is published atomically (release-state.json); partial writes."""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("release_state", ROOT / "deploy/scripts/release_state.py")
rs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rs)

OLD, NEW, NEWER = "a" * 40, "b" * 40, "c" * 40


class ReleaseStateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def publish(self, current, previous, dump):
        rs.main(
            [
                "publish",
                str(self.dir),
                "--current",
                current,
                "--previous",
                previous,
                "--previous-dump",
                dump,
                "--previous-image",
                f"hlmemo:{previous}",
                "--previous-image-id",
                "sha256:x",
                "--env-backup",
                "/etc/hlmemo/api.env=/etc/hlmemo/api.env.pre-w0-1",
            ]
        )

    def markers(self):
        return {m: (self.dir / m).read_text().strip() for m in rs.LEGACY if (self.dir / m).exists()}

    def test_publish_writes_state_and_derives_legacy_markers(self):
        self.publish(NEW, OLD, "/b/old.dump")
        state = json.loads((self.dir / rs.STATE).read_text())
        self.assertEqual(state["env_backups"], {"/etc/hlmemo/api.env": "/etc/hlmemo/api.env.pre-w0-1"})
        self.assertFalse(state["accepted"])
        self.assertEqual(
            self.markers(), {"current-ref": NEW, "previous-ref": OLD, "previous-dump": "/b/old.dump"}
        )
        self.assertEqual([], list(self.dir.glob(".*.*")), "no temporary files left")

    def test_failed_state_rename_leaves_the_old_tuple_intact(self):
        self.publish(NEW, OLD, "/b/old.dump")
        before_state, before_markers = (self.dir / rs.STATE).read_text(), self.markers()
        with (
            mock.patch.object(rs.os, "replace", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            self.publish(NEWER, NEW, "/b/new.dump")
        self.assertEqual(before_state, (self.dir / rs.STATE).read_text())
        self.assertEqual(before_markers, self.markers())
        self.assertEqual([], list(self.dir.glob(".*.*")), "the temporary file is removed")

    def test_failure_while_deriving_markers_keeps_a_consistent_state_and_derive_repairs(self):
        self.publish(NEW, OLD, "/b/old.dump")
        real_replace = os.replace
        calls = []

        def replace(src, dst):
            calls.append(Path(dst).name)
            if Path(dst).name == "previous-dump":
                raise OSError("crash between marker renames")
            return real_replace(src, dst)

        with mock.patch.object(rs.os, "replace", side_effect=replace), self.assertRaises(OSError):
            self.publish(NEWER, NEW, "/b/new.dump")
        self.assertEqual(calls[0], rs.STATE, "the state file is published first, in one rename")
        state = json.loads((self.dir / rs.STATE).read_text())
        self.assertEqual(
            (state["current_ref"], state["previous_ref"], state["previous_dump"]), (NEWER, NEW, "/b/new.dump")
        )
        self.assertEqual(self.markers()["previous-dump"], "/b/old.dump")  # legacy files are stale ...
        rs.main(["derive", str(self.dir)])  # ... and derived again from the one source of truth
        self.assertEqual(
            self.markers(), {"current-ref": NEWER, "previous-ref": NEW, "previous-dump": "/b/new.dump"}
        )

    def test_rolled_back_consumes_the_pair_and_accept_deletes_backups(self):
        backup = self.dir / "api.env.pre-w0-1"
        backup.write_text("HLM_REGISTRATION_SECRET=x\n")
        rs.main(
            [
                "publish",
                str(self.dir),
                "--current",
                NEW,
                "--previous",
                OLD,
                "--previous-dump",
                "/d",
                "--previous-image",
                f"hlmemo:{OLD}",
                "--previous-image-id",
                "sha256:x",
                "--env-backup",
                f"{self.dir / 'api.env'}={backup}",
            ]
        )
        rs.main(["accept", str(self.dir)])
        self.assertFalse(backup.exists())
        self.assertTrue(json.loads((self.dir / rs.STATE).read_text())["accepted"])
        rs.main(["begin-rollback", str(self.dir), OLD])
        self.assertEqual(OLD, json.loads((self.dir / rs.STATE).read_text())["rollback_in_progress"])
        rs.main(["rolled-back", str(self.dir)])
        state = json.loads((self.dir / rs.STATE).read_text())
        self.assertEqual((state["current_ref"], state["rolled_back_from"]), (OLD, NEW))
        self.assertNotIn("rollback_in_progress", state)
        self.assertEqual(self.markers(), {"current-ref": OLD})

    def test_backups_of_every_publication_stay_listed_until_acceptance(self):
        first, second = self.dir / "api.env.pre-w0-1", self.dir / "api.env.pre-w0-2"
        for backup, (current, previous) in ((first, (NEW, OLD)), (second, (NEWER, NEW))):
            backup.write_text("x\n")
            rs.main(
                ["publish", str(self.dir), "--current", current, "--previous", previous,
                 "--previous-dump", "/d", "--env-backup", f"{self.dir / 'api.env'}={backup}"]
            )  # fmt: skip
        state = json.loads((self.dir / rs.STATE).read_text())
        self.assertEqual(state["retired_backups"], sorted([str(first), str(second)]))
        rs.main(["accept", str(self.dir)])
        self.assertFalse(first.exists() or second.exists())

    def test_add_retired_records_before_publish_and_keeps_legacy_markers(self):
        """R1 finding: a backup made by a FAILED deployment is recorded at once, survives the next
        publish and is deleted by accept; add-retired never derives (legacy markers stay)."""
        (self.dir / "current-ref").write_text(OLD + "\n")  # legacy-only host, no state file yet
        failed = self.dir / "api.env.pre-w0-0"
        failed.write_text("HLM_ADMIN_TOKEN=x\n")
        rs.main(["add-retired", str(self.dir), str(failed)])
        rs.main(["add-retired", str(self.dir), str(failed)])  # idempotent
        state = json.loads((self.dir / rs.STATE).read_text())
        self.assertEqual(state, {"retired_backups": [str(failed)]})
        self.assertEqual(self.markers(), {"current-ref": OLD})
        self.publish(NEW, OLD, "/b/old.dump")
        state = json.loads((self.dir / rs.STATE).read_text())
        self.assertEqual(state["retired_backups"], sorted([str(failed), "/etc/hlmemo/api.env.pre-w0-1"]))
        rs.main(["accept", str(self.dir)])
        self.assertFalse(failed.exists())

    def test_accept_sweeps_unrecorded_backups_only_in_sweep_dirs(self):
        envdir = self.dir / "etc"
        envdir.mkdir()
        stray, other = envdir / "api.env.pre-w0-20260101T000000Z", envdir / "api.env"
        stray.write_text("HLM_REGISTRATION_SECRET=x\n")
        other.write_text("HLM_CURSOR_SECRET=y\n")
        rs.main(["accept", str(self.dir)])
        self.assertTrue(stray.exists(), "no --sweep-dir: unrecorded backups are left alone")
        rs.main(["accept", str(self.dir), "--sweep-dir", str(envdir)])
        self.assertFalse(stray.exists())
        self.assertTrue(other.exists())

    def test_crash_while_consuming_the_pair_is_repaired_by_derive(self):
        """D-065 item 5: state consumption + marker removal is one derive; a crash in between leaves
        a consistent state, and deriving again deletes the stale previous-ref/previous-dump."""
        self.publish(NEW, OLD, "/b/old.dump")
        with (
            mock.patch.object(rs.Path, "unlink", side_effect=OSError("crash while pruning markers")),
            self.assertRaises(OSError),
        ):
            rs.main(["rolled-back", str(self.dir)])
        state = json.loads((self.dir / rs.STATE).read_text())
        self.assertEqual((state["current_ref"], state.get("previous_ref")), (OLD, None))
        self.assertEqual(self.markers()["previous-ref"], OLD)  # stale until derived again
        rs.main(["derive", str(self.dir)])
        self.assertEqual(self.markers(), {"current-ref": OLD})


if __name__ == "__main__":
    unittest.main()
