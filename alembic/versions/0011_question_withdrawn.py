"""``librarian_questions.status`` accepts ``withdrawn`` (``ops librarian withdraw``: an operator retracts
an open, approved or accepted_pending proposal; terminal, never applied).

Revision ID: 0011_question_withdrawn
Revises: 0010_billing_outcome

Widens the ``status`` CHECK by one value; no data changes. Backward compatible: code from before this
revision never writes the new value, and every apply path of that code selects only ``approved`` and
``accepted_pending`` (``memory.answer`` only ``open``), so it runs unchanged on the migrated schema and
never applies a withdrawn question.

The swap runs in short autocommit steps, as in ``0010_billing_outcome`` (R4.1 review Sol F-2): add the
replacement ``librarian_questions_status_check_v2`` ``NOT VALID`` (catalog only), ``VALIDATE`` it
(``SHARE UPDATE EXCLUSIVE``: reads and writes go on), then drop the old constraint and rename ``_v2`` to
it in ONE short transaction. Every step can be re-run; the final name is
``librarian_questions_status_check`` either way.

Downgrade REFUSES while any question is ``withdrawn`` (as ``0008`` refuses while W2c statuses exist):
those rows are projections of authoritative ``librarian`` events (op ``withdraw``), and replaying those
events needs this schema. The staged old-value constraint is added ``NOT VALID`` first, so no new
``withdrawn`` row can be written while the check runs; the check, the drop and the rename then land in
ONE transaction. On a refusal the staged constraint is dropped again and nothing else changed.
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


def _swap(values: str, *, refuse_withdrawn: bool) -> None:
    """Replace the ``status`` CHECK by one over ``values`` without a long ``ACCESS EXCLUSIVE`` lock
    (module doc). ``refuse_withdrawn``: the downgrade's guard, in the same transaction as the swap."""
    with op.get_context().autocommit_block():  # every step below is its own short transaction
        bind = op.get_bind()
        bind.exec_driver_sql(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        try:
            bind.exec_driver_sql(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {STAGED}")
            bind.exec_driver_sql(
                f"ALTER TABLE {TABLE} ADD CONSTRAINT {STAGED} CHECK (status IN ({values})) NOT VALID"
            )
            if not refuse_withdrawn:
                bind.exec_driver_sql(f"ALTER TABLE {TABLE} VALIDATE CONSTRAINT {STAGED}")
            # one simple-query string = one implicit transaction: guard, drop and rename land together
            try:
                bind.exec_driver_sql(
                    (f"{REFUSE_WITHDRAWN};\n" if refuse_withdrawn else "")
                    + f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {CHECK};\n"
                    f"ALTER TABLE {TABLE} RENAME CONSTRAINT {STAGED} TO {CHECK};"
                )
            except Exception:
                # nothing swapped: drop the staged constraint so the current schema stays as it was
                bind.exec_driver_sql(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {STAGED}")
                raise
            if refuse_withdrawn:
                bind.exec_driver_sql(f"ALTER TABLE {TABLE} VALIDATE CONSTRAINT {CHECK}")
        finally:
            bind.exec_driver_sql("RESET lock_timeout")


def upgrade() -> None:
    _swap(NEW, refuse_withdrawn=False)


def downgrade() -> None:
    _swap(OLD, refuse_withdrawn=True)
