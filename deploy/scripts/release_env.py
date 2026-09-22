#!/usr/bin/env python3
"""Atomically publish the verified release image without interpreting dotenv code."""

import os
import re
import stat
import sys
import tempfile
from pathlib import Path


def publish(path: Path, image: str) -> None:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]*:(?:[a-f0-9]{40}|[a-f0-9]{64})", image):
        raise ValueError("Expected an immutable repository:commit image reference")
    metadata = path.stat()
    lines = [
        line
        for line in path.read_text().splitlines()
        if not re.match(r"^\s*(?:export\s+)?HLM_IMAGE\s*=", line)
    ]
    fd, temporary = tempfile.mkstemp(prefix=".release-env.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
            stream.write("\n".join([*lines, f"HLM_IMAGE={image}", ""]))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    publish(Path(sys.argv[1]), sys.argv[2])
