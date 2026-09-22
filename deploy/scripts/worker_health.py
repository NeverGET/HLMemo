"""Fail when the worker has not completed a poll/heartbeat in three minutes."""
import time
from pathlib import Path

heartbeat = Path("/tmp/hlmemo-worker-heartbeat")
raise SystemExit(0 if heartbeat.exists() and time.time() - heartbeat.stat().st_mtime < 180 else 1)
