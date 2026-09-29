"""Regression tests for independent pre-upgrade and calendar backup retention."""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "retention", Path(__file__).resolve().parents[2] / "deploy/backup/retention.py"
)
retention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retention)


class RetentionTest(unittest.TestCase):
    def test_same_second_rotation_keeps_current_dump_over_random_suffix_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for tier in ("daily", "weekly"):
                (root / tier).mkdir()
            previous = root / "daily/hlmemo-2026-09-22T100000Z-zzzzzz.dump"
            latest = root / "daily/hlmemo-2026-09-22T100000Z-aaaaaa.dump"
            previous.write_text("first")
            latest.write_text("second")
            weekly = retention.rotate(latest, root)
            self.assertEqual(list((root / "daily").glob("*.dump")), [latest])
            self.assertEqual(latest.read_text(), "second")
            self.assertEqual(weekly.read_text(), "second")

    def test_calendar_rotation_never_touches_same_day_deploys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for tier in ("daily", "weekly", "pre-upgrade"):
                (root / tier).mkdir()
            upgrades = [
                root / "pre-upgrade" / f"abc1234-2026-09-22T{hour}0000Z.dump" for hour in ("09", "10")
            ]
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
            self.assertEqual(
                {"w.dump", "v.dump", "u.dump", "t.dump", "s.dump"},
                {p.name for p in (root / "pre-upgrade").glob("*.dump")},
            )
            self.assertEqual(3, retention.prune(root, 2))
            with self.assertRaises(ValueError):
                retention.prune(root, 0)

    def test_rotation_and_prune_never_delete_a_dump_the_release_state_references(self):
        """R4 R-2 (review 77): an open rollback's safety dump journalled in daily/ (an older runner's
        path), the rollback pair's pre-upgrade dump and a deploy attempt's dump survive rotation and
        pruning; without the state (the old behaviour) the same rotation deletes the safety dump."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for tier in ("daily", "weekly", "pre-upgrade"):
                (root / tier).mkdir()
            safety = root / "daily/hlmemo-2026-09-22T090000Z-safety.dump"
            safety.write_text("the current database, saved before the rollback replaced it")
            upgrades = []
            for index, name in enumerate("abcdefg"):
                path = root / "pre-upgrade" / f"{name}.dump"
                path.write_text(name)
                os.utime(path, (index + 1, index + 1))
                upgrades.append(path)
            state = root / "release-state.json"
            state.write_text(
                json.dumps(
                    {
                        "rollback_in_progress": "a" * 40,
                        "rollback_safety": str(safety),
                        "rollback_destructive": True,
                        "previous_dump": str(upgrades[0]),  # the oldest: prune would take it first
                        "deploy_attempt": {"previous_dump": str(upgrades[1])},
                    }
                )
            )
            protected = retention.protected_dumps(state)
            later = root / "daily/hlmemo-2026-09-22T100000Z-later.dump"  # the same day's timer backup
            later.write_text("later")
            retention.rotate(later, root, protected)
            self.assertTrue(safety.exists(), "the open rollback's safety dump is kept")
            self.assertTrue(later.exists())
            self.assertEqual(3, retention.prune(root, 2, protected))
            self.assertEqual(
                {"a.dump", "b.dump", "f.dump", "g.dump"},
                {p.name for p in (root / "pre-upgrade").glob("*.dump")},
            )
            # without the release state: the same-day rotation deletes it (review 77's trigger)
            newest = root / "daily/hlmemo-2026-09-22T110000Z-newest.dump"
            newest.write_text("newest")
            retention.rotate(newest, root)
            self.assertFalse(safety.exists())
            # no state file: nothing protected; an unreadable one stops (nothing is guessed away)
            self.assertEqual(frozenset(), retention.protected_dumps(root / "missing.json"))
            state.write_text("{not json")
            with self.assertRaises(SystemExit):
                retention.protected_dumps(state)

    def test_rotation_rejects_pre_upgrade_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                retention.rotate(root / "pre-upgrade/abc.dump", root)


if __name__ == "__main__":
    unittest.main()
