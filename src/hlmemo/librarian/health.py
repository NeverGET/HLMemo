"""Container healthcheck for the librarian service: the heartbeat file is fresh.

``python -m hlmemo.librarian.health [max_age_s]`` exits 0 when ``HLM_LIBRARIAN_HEARTBEAT_FILE``
(default ``/tmp/hlm-librarian-heartbeat.json``) was written within ``max_age_s`` (default 120 s).
It checks liveness of the loop only; it never contacts the provider or the api.
"""

from __future__ import annotations

import json
import os
import sys
import time


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    max_age = float(args[0]) if args else 120.0
    path = os.environ.get("HLM_LIBRARIAN_HEARTBEAT_FILE", "/tmp/hlm-librarian-heartbeat.json")
    try:
        with open(path, encoding="utf-8") as fh:
            hb = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"unhealthy: {exc}", file=sys.stderr)
        return 1
    age = time.time() - float(hb.get("ts", 0))
    if age > max_age:
        print(f"unhealthy: heartbeat {age:.0f}s old", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
