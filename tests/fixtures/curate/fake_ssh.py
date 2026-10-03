#!/usr/bin/env python3
"""A FAKE ``ssh`` for the ``hlm curate --apply`` script test: no network, the "prod" is a directory
(``FAKE_SSH_DIR``). Usage as the script calls it: ``ssh -F <config> <host> '<remote command>'``.
``FAKE_SSH_PREVIEW_DELTA=1`` makes the preview change the counts (a broken preview)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    args = sys.argv[1:]
    assert args[0] == "-F" and Path(args[1]).is_file(), args
    cmd = args[3]
    state = Path(os.environ["FAKE_SSH_DIR"])
    counts = state / "counts.json"
    c = json.loads(counts.read_text()) if counts.exists() else {"events": 100, "links": 50, "backfill": 7}
    remote = state / "remote.jsonl"
    (state / "log.txt").open("a").write(cmd + "\n")
    if "exec -T db sh -s" in cmd:
        sql = sys.stdin.read()
        assert "p.slug = 'demo'" in sql, sql
        print(f"{c['events']}|{c['links']}|{c['backfill']}")
    elif "cat > /tmp/" in cmd:
        remote.write_text(sys.stdin.read())
    elif "rm -f /tmp/" in cmd:
        remote.unlink(missing_ok=True)
    elif "hlm links backfill" in cmd:
        recs = [json.loads(x) for x in remote.read_text().splitlines() if x.strip()]
        links = [{"src_vid": r["src_vid"], "dst_vid": r["dst_vid"], "scope": r["scope"]} for r in recs]
        if "--dry-run" in cmd:
            if os.environ.get("FAKE_SSH_PREVIEW_DELTA"):
                c["events"] += 1
            print(json.dumps({"preview": True, "applied": 0, "selected": len(recs), "links": links}))
        else:
            c["events"] += 1
            c["links"] += len(recs)
            c["backfill"] += len(recs)
            print(json.dumps({"preview": False, "applied": len(recs), "event_id": 4242, "links": links}))
    else:
        print(f"fake ssh: unexpected command {cmd!r}", file=sys.stderr)
        return 255
    counts.write_text(json.dumps(c))
    return 0


if __name__ == "__main__":
    sys.exit(main())
