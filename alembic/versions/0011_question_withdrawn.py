"""``librarian_questions.status`` accepts ``withdrawn`` (``ops librarian withdraw``: an operator retracts
an open, approved or accepted_pending proposal; terminal, never applied).

Revision ID: 0011_question_withdrawn
Revises: 0010_billing_outcome

Widens the ``status`` CHECK by one value; no data changes. Code from before this revision never
writes the new value, and its apply paths select only ``approved`` and ``accepted_pending``
(``memory.answer`` only ``open``), so its apply logic ignores withdrawn rows. An older RELEASE still
does not run on this schema: its readiness check expects its own alembic head (0010). Rolling back to
it means restoring the pre-upgrade dump, which loses every write made after that dump.

Upgrade: the swap runs in short autocommit steps, as in ``0010_billing_outcome`` (R4.1 review Sol
F-2): add the replacement ``librarian_questions_status_check_v2`` ``NOT VALID`` (catalog only),
``VALIDATE`` it (``SHARE UPDATE EXCLUSIVE``: reads and writes go on), then drop the old constraint and
rename ``_v2`` to it in ONE short transaction. Safe to interrupt: a leftover staged constraint holds
the NEW values, a superset of the live ones, so it never rejects a write; a re-run drops and re-adds
it. The final name is ``librarian_questions_status_check`` either way.

Downgrade REFUSES while any question is ``withdrawn`` (as ``0008`` refuses while W2c statuses exist):
those rows are projections of authoritative ``librarian`` events (op ``withdraw``), and replaying those
events needs this schema. Review 107 #2: a staged OLD constraint committed on its own would reject
every future withdraw if a refusal's cleanup failed (lock timeout) while alembic still says 0011. So
the whole downgrade is ONE transaction, the migration's own (``transaction_per_migration``, with the
alembic version update): the first ``ALTER`` takes ``ACCESS EXCLUSIVE`` (no withdraw can commit
after it), then the refusal check, then the drop and the validated old constraint. A refusal or a
lock timeout rolls ALL of it back. The validation scan runs under that lock; ``librarian_questions``
is small (one row per proposal).
"""

from __future__ import annotations

from alembic import op

revision = "0011_question_withdrawn"
down_revision = "0010_billing_outcome"
branch_labels = None
depends_on = None

LOCK_TIMEOUT = "3s"
TABLE = "librarian_questions"
CHECK = "librarian_questions_status_check"
STAGED = "librarian_questions_status_check_v2"
OLD = (
    "'open','approved','rejected','superseded','answered','applied','expired','authority_lost',"
    "'accepted_pending'"
)
NEW = OLD + ",'withdrawn'"
# (no '%' format: the driver would read it as a parameter placeholder)
REFUSE_WITHDRAWN = f"""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM {TABLE} WHERE status = 'withdrawn') THEN
    RAISE EXCEPTION USING MESSAGE = 'downgrade refused: '
      || (SELECT count(*) FROM {TABLE} WHERE status = 'withdrawn')
      || ' question(s) are withdrawn (projections of withdraw events; replaying them needs this schema)';
  END IF;
END $$"""


DOWNGRADE = f"""
ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {STAGED};
{REFUSE_WITHDRAWN};
ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {CHECK};
ALTER TABLE {TABLE} ADD CONSTRAINT {STAGED} CHECK (status IN ({OLD}));
ALTER TABLE {TABLE} RENAME CONSTRAINT {STAGED} TO {CHECK};
"""


def upgrade() -> None:
    """The staged swap (module doc); every step is its own short transaction."""
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        bind.exec_driver_sql(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        try:
            bind.exec_driver_sql(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {STAGED}")
            bind.exec_driver_sql(
                f"ALTER TABLE {TABLE} ADD CONSTRAINT {STAGED} CHECK (status IN ({NEW})) NOT VALID"
            )
            bind.exec_driver_sql(f"ALTER TABLE {TABLE} VALIDATE CONSTRAINT {STAGED}")
            # one simple-query string = one implicit transaction: drop and rename land together
            bind.exec_driver_sql(
                f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {CHECK};\n"
                f"ALTER TABLE {TABLE} RENAME CONSTRAINT {STAGED} TO {CHECK};"
            )
        finally:
            bind.exec_driver_sql("RESET lock_timeout")


def downgrade() -> None:
    """ONE transaction (module doc): refused or timed out, nothing changes and alembic stays at 0011."""
    bind = op.get_bind()
    bind.exec_driver_sql(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    bind.exec_driver_sql(DOWNGRADE)
