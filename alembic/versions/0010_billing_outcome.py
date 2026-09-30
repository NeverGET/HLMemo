"""``llm_calls.outcome`` accepts ``billing_or_quota`` (D-212: a provider refusal for billing or quota).

Revision ID: 0010_billing_outcome
Revises: 0009_memory_map

Widens the ``outcome`` CHECK by one value; no data changes. Backward compatible: code from before this
revision never writes the new value, so it runs unchanged on the migrated schema. The downgrade turns
any ``billing_or_quota`` row back into ``http_error`` (what it was before) and restores the old CHECK.

Why in steps (R4.1 review Sol F-2): ``ADD CONSTRAINT ... CHECK`` validates every existing row while it
holds ``ACCESS EXCLUSIVE`` on ``llm_calls``, and ``lock_timeout`` only bounds the WAIT for that lock,
not the scan. On a large ledger that is an unbounded outage. So the swap runs as separate autocommit
steps, each short under ``lock_timeout``:

  1. add the replacement ``llm_calls_outcome_check_v2`` as ``NOT VALID`` (catalog only, no scan);
  2. ``VALIDATE CONSTRAINT`` it (the scan runs under ``SHARE UPDATE EXCLUSIVE``: reads and writes go on);
  3. in ONE short transaction: drop the old ``llm_calls_outcome_check`` and rename ``_v2`` to it.

Downgrade (R4.1 review round 2 N-2): the ``billing_or_quota`` -> ``http_error`` data fix runs INSIDE the
final short transaction, right before the drop/rename, and the staged old-value constraint is added
``NOT VALID`` first (so no new ``billing_or_quota`` row can be written once it exists). The scan
(``VALIDATE``) then runs after the swap under ``SHARE UPDATE EXCLUSIVE``. An interruption or a concurrent
writer therefore cannot leave rows the restored constraint rejects, and every step can be re-run.

Every step is re-runnable (a failed or interrupted run leaves at most the ``_v2`` constraint, which the
next run drops and re-adds), and the final constraint name is ``llm_calls_outcome_check`` either way.
"""

from __future__ import annotations

from alembic import op

revision = "0010_billing_outcome"
down_revision = "0009_memory_map"
branch_labels = None
depends_on = None

LOCK_TIMEOUT = "3s"
CHECK = "llm_calls_outcome_check"
STAGED = "llm_calls_outcome_check_v2"
OLD = "'ok','schema_retry_ok','schema_fail','http_error','timeout','budget_deferred','breaker_open'"
NEW = OLD + ",'billing_or_quota'"


def _swap(values: str, fix_sql: str | None = None) -> None:
    """Replace the ``outcome`` CHECK by one over ``values`` without a long ``ACCESS EXCLUSIVE`` lock.

    Upgrade (``fix_sql`` None): add ``NOT VALID``, ``VALIDATE``, then drop + rename in one transaction.
    Downgrade (``fix_sql`` given): add ``NOT VALID``, then ``fix_sql`` + drop + rename in ONE transaction
    (the data fix and the swap land together), then ``VALIDATE`` the renamed constraint."""
    with op.get_context().autocommit_block():  # every step below is its own short transaction
        bind = op.get_bind()
        bind.exec_driver_sql(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        try:
            bind.exec_driver_sql(f"ALTER TABLE llm_calls DROP CONSTRAINT IF EXISTS {STAGED}")
            bind.exec_driver_sql(
                f"ALTER TABLE llm_calls ADD CONSTRAINT {STAGED} CHECK (outcome IN ({values})) NOT VALID"
            )
            if fix_sql is None:
                bind.exec_driver_sql(f"ALTER TABLE llm_calls VALIDATE CONSTRAINT {STAGED}")
            # one simple-query string = one implicit transaction: fix, drop and rename land together
            bind.exec_driver_sql(
                (f"{fix_sql};\n" if fix_sql else "")
                + f"ALTER TABLE llm_calls DROP CONSTRAINT IF EXISTS {CHECK};\n"
                f"ALTER TABLE llm_calls RENAME CONSTRAINT {STAGED} TO {CHECK};"
            )
            if fix_sql is not None:
                bind.exec_driver_sql(f"ALTER TABLE llm_calls VALIDATE CONSTRAINT {CHECK}")
        finally:
            bind.exec_driver_sql("RESET lock_timeout")


def upgrade() -> None:
    _swap(NEW)


def downgrade() -> None:
    _swap(OLD, "UPDATE llm_calls SET outcome = 'http_error' WHERE outcome = 'billing_or_quota'")
