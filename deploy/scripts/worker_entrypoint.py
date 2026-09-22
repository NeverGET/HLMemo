"""Record successful existing worker heartbeats without changing application sources."""

import logging
from pathlib import Path

from hlmemo.worker.main import main


class HeartbeatFile(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.name == "hlmemo.worker" and record.getMessage().startswith("worker heartbeat:"):
            Path("/tmp/hlmemo-worker-heartbeat").touch()


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger().addHandler(HeartbeatFile())
raise SystemExit(main())
