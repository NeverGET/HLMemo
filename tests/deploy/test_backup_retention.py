"""Regression tests for independent pre-upgrade and calendar backup retention."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("retention", Path(__file__).resolve().parents[2] / "deploy/backup/retention.py")
retention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retention)


class RetentionTest(unittest.TestCase):
    def test_calendar_rotation_never_touches_same_day_deploys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for tier in ("daily", "weekly", "pre-upgrade"):
                (root / tier).mkdir()
            upgrades = [root / "pre-upgrade" / f"abc1234-2026-09-22T{hour}0000Z.dump" for hour in ("09", "10")]
            for path in upgrades:
                path.write_text(path.name)
            for week in range(1, 7):
                (root / "weekly" / f"hlmemo-2026-W{week:02d}.dump").write_text("old")
            for day in range(10, 23):
                (root / "daily" / f"hlmemo-2026-09-{day:02d}T090000Z.dump").write_text("old")
            latest = root / "daily/hlmemo-2026-09-22T100000Z.dump"
            latest.write_text("latest")
            weekly = retention.rotate(latest, root)
            self.assertTrue(all(path.exists() for path in upgrades))
            self.assertEqual(7, len(list((root / "daily").glob("*.dump"))))
            self.assertEqual(4, len(list((root / "weekly").glob("*.dump"))))
            self.assertFalse((root / "daily/hlmemo-2026-09-22T090000Z.dump").exists())
            self.assertEqual("latest", weekly.read_text())
            self.assertEqual("latest", latest.read_text())

    def test_explicit_prune_keeps_latest_five_and_supports_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pre-upgrade").mkdir()
            # Commit hashes do not sort chronologically; mtimes choose the newest.
            for index, name in enumerate("zyxwvuts"):
                path = root / "pre-upgrade" / f"{name}.dump"
                path.write_text(name)
                os.utime(path, (index + 1, index + 1))
            self.assertEqual(3, retention.prune(root, 5))
            self.assertEqual({"w.dump", "v.dump", "u.dump", "t.dump", "s.dump"}, {p.name for p in (root / "pre-upgrade").glob("*.dump")})
            self.assertEqual(3, retention.prune(root, 2))
            with self.assertRaises(ValueError):
                retention.prune(root, 0)

    def test_rotation_rejects_pre_upgrade_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                retention.rotate(root / "pre-upgrade/abc.dump", root)


if __name__ == "__main__":
    unittest.main()
