"""PLACEHOLDER — outbox worker (lease/fence loop) is implemented in a later Phase-0 task."""

from __future__ import annotations

import logging
import sys


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("hlmemo.worker").warning("hlmemo worker: not implemented yet; exiting 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
