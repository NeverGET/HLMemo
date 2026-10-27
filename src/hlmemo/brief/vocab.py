"""The brief's token meter vocabulary (tiktoken o200k_base), kept where macOS does not purge it, and never
downloaded inside the SessionStart hook.

tiktoken caches its 3.6 MB vocabulary in ``$TMPDIR/data-gym-cache`` unless ``TIKTOKEN_CACHE_DIR`` is set.
macOS purges that folder, and then every hook run downloaded the vocabulary in the middle of assembling
the brief, outside the fetch deadline: the watchdog ended the hook at 8 s and the session got "brief
unavailable" (2026-10-18 .. 10-25, D-267). Now:

* the vocabulary lives in ``~/.cache/hlm/tiktoken`` (``XDG_CACHE_HOME`` honoured; an explicit
  ``TIKTOKEN_CACHE_DIR`` wins), seeded from tiktoken's temp cache when that copy still exists;
* ``load_offline()`` never opens a socket: with no local copy it raises, the brief counts with its
  pessimistic estimate, and ``warm_in_background()`` starts ``python -m hlmemo.brief.vocab`` detached, so
  the next session has the vocabulary.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

#: tiktoken_ext.openai_public.o200k_base reads this blob; tiktoken names its cache file sha1(url)
VOCAB_URL = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
VOCAB_KEY = hashlib.sha1(VOCAB_URL.encode()).hexdigest()
CACHE_ENV = "TIKTOKEN_CACHE_DIR"
WARM_MARK = ".warming"
WARM_EVERY_S = 600  # at most one background download attempt per 10 minutes
DOWNLOAD_TIMEOUT_S = 60  # per socket operation in the background download


class VocabUnavailable(LookupError):
    """No local copy of the vocabulary; the caller estimates instead."""


def cache_dir() -> Path:
    explicit = os.environ.get(CACHE_ENV)
    if explicit:
        return Path(explicit).expanduser()
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base).expanduser() / "hlm" / "tiktoken"


def _temp_copy() -> Path:
    return Path(tempfile.gettempdir()) / "data-gym-cache" / VOCAB_KEY


def ensure_local() -> bool:
    """Point tiktoken at ``cache_dir()`` and make sure the vocabulary file is there (copying tiktoken's temp
    cache when only that one exists). True when a local copy exists. Never raises, never downloads."""
    try:
        d = cache_dir()
        os.environ[CACHE_ENV] = str(d)
        target = d / VOCAB_KEY
        if target.is_file():
            return True
        src = _temp_copy()
        if not src.is_file():
            return False
        d.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f"{VOCAB_KEY}.{os.getpid()}.tmp")
        shutil.copyfile(src, tmp)
        os.replace(tmp, target)
        return True
    except Exception:  # noqa: BLE001
        return False


def load_offline(name: str) -> Any:
    """The tiktoken encoding from the local copy only. Raises ``VocabUnavailable`` instead of downloading
    (also when the local copy fails tiktoken's hash check, which would otherwise trigger a re-download)."""
    if not ensure_local():
        raise VocabUnavailable(name)
    import tiktoken
    import tiktoken.load as tl

    def no_network(blobpath: str) -> bytes:
        raise VocabUnavailable(blobpath)

    real = tl.read_file
    tl.read_file = no_network
    try:
        return tiktoken.get_encoding(name)
    finally:
        tl.read_file = real


_warm_started = False


def warm_in_background() -> bool:
    """Start one detached ``python -m hlmemo.brief.vocab`` (at most once per process and per
    ``WARM_EVERY_S``). True when started. Never raises, never waits."""
    global _warm_started
    if _warm_started:
        return False
    _warm_started = True
    try:
        d = cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        mark = d / WARM_MARK
        if mark.exists() and time.time() - mark.stat().st_mtime < WARM_EVERY_S:
            return False
        mark.touch()
        subprocess.Popen(  # noqa: S603 - our own interpreter and module, no shell
            [sys.executable, "-P", "-m", "hlmemo.brief.vocab"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env={**os.environ, CACHE_ENV: str(d)},
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    """Download (or verify) the vocabulary into ``cache_dir()``. Outside any hook deadline."""
    os.environ[CACHE_ENV] = str(cache_dir())
    import socket

    socket.setdefaulttimeout(DOWNLOAD_TIMEOUT_S)  # tiktoken's requests.get has none (review 125)
    try:
        import tiktoken

        from hlmemo.core import METER_VERSION

        tiktoken.get_encoding(METER_VERSION)
        (cache_dir() / WARM_MARK).unlink(missing_ok=True)
        return 0
    except Exception:  # noqa: BLE001
        return 1


if __name__ == "__main__":
    sys.exit(main())
