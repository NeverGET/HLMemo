"""Test-image entrypoint: provision only hlm_verify, then run the requested test command.

Never drops or truncates a database. The normal pytest fixtures migrate and clean hlm_verify.
The production image does not contain this file or use this entrypoint.
"""

from __future__ import annotations

import os
import sys

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def ensure_test_database(dsn: str) -> None:
    name = conninfo_to_dict(dsn).get("dbname")
    if name != "hlm_verify":
        raise ValueError("test image requires HLM_TEST_DSN to name hlm_verify")
    admin_dsn = make_conninfo(dsn, dbname="postgres")
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if not exists:
            try:
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            except psycopg.errors.DuplicateDatabase:
                pass  # Another test container provisioned it after our existence check.


def main() -> None:
    dsn = os.environ.get("HLM_TEST_DSN", "")
    if not dsn:
        raise ValueError("test image requires an explicit HLM_TEST_DSN")
    ensure_test_database(dsn)
    command = sys.argv[1:] or ["pytest", "-q", "tests/integration"]
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
