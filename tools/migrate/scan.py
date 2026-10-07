#!/usr/bin/env python3
"""Personal-data and credential scan of a migration's curated tree (PLAYBOOK §10). Run it with the HLMemo
venv:

    .venv/bin/python tools/migrate/scan.py --spec migration.toml [--known-values FILE] [--json]

Rules: email, hex40 (legacy device UDID; a full git sha matches too), udid, ipv4 / ipv4-private, ipv6 /
ipv6-private, tr-national-id (checksum-validated), key-path (~/.ssh/…, *.p8, *.p12, *.pfx, *.pem, *.keystore,
*.jks), secret-<rule> (the strong secret shapes of the write path), the spec's `[[scan.patterns]]`, and
`known` (the literal values of --known-values, a 0600 file; or HLM_SCAN_KNOWN_VALUES).
Every line printed is `<rule>  <path>:<line>  <masked line>`: values are masked (<EMAIL>, <HEX40>, <IP>,
<KNOWN>…). Accept a hit with `[scan].allow = ["<rule>:<path>:<line>"]`, or a value everywhere with
`[scan].allow_values`.
Exit 0 clean · 1 findings · 64 bad spec, arguments or a known-values file that others can read.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from hlmemo.migrate import scan as scanmod
    from hlmemo.migrate.spec import SpecError, load_spec
except ImportError:  # run from a checkout without the package installed: use its src/
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from hlmemo.migrate import scan as scanmod
    from hlmemo.migrate.spec import SpecError, load_spec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", required=True, help="the migration.toml of this migration")
    ap.add_argument("--known-values", help="a 0600 file of literal values that must not appear")
    ap.add_argument("--json", action="store_true", help="print JSON")
    a = ap.parse_args()
    try:
        spec = load_spec(a.spec)
    except SpecError as exc:
        print(f"spec: {exc}", file=sys.stderr)
        return 64
    kv_path = Path(a.known_values).expanduser() if a.known_values else scanmod.default_known_values()
    try:
        known = scanmod.load_known_values(kv_path) if kv_path else ()
    except (OSError, PermissionError) as exc:
        print(f"known values: {exc}", file=sys.stderr)
        return 64
    hits, stats = scanmod.scan(spec, known)
    if a.json:
        print(
            json.dumps(
                {
                    "stats": stats,
                    "known_values": len(known),
                    "hits": [
                        {"rule": h.rule, "path": h.path, "line": h.line, "context": h.context} for h in hits
                    ],
                },
                ensure_ascii=False,
                indent=1,
            )
        )
    else:
        for h in hits:
            print(f"{h.rule:16} {h.path}:{h.line}  {h.context}")
        print(
            f"scan: {len(hits)} finding(s) in {stats['files']} file(s), {stats['lines']} line(s); "
            f"{stats['allowed']} allowed; {len(known)} known value(s) checked"
        )
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
