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
        rs.main(["rolled-back", str(self.dir)])
        state = json.loads((self.dir / rs.STATE).read_text())
        self.assertEqual((state["current_ref"], state["rolled_back_from"]), (OLD, NEW))
        self.assertEqual(self.markers(), {"current-ref": OLD})


if __name__ == "__main__":
    unittest.main()
