"""``llm_calls.outcome`` accepts ``billing_or_quota`` (D-212: a provider refusal for billing or quota).

Revision ID: 0010_billing_outcome
Revises: 0009_memory_map

Drops and re-adds the ``outcome`` CHECK with one more value; no data changes. Backward compatible:
code from before this revision never writes the new value, so it runs unchanged on the migrated
schema. The downgrade turns any ``billing_or_quota`` row back into ``http_error`` (what it was
before) and restores the old CHECK.
"""

from __future__ import annotations

from alembic import op

revision = "0010_billing_outcome"
down_revision = "0009_memory_map"
branch_labels = None
depends_on = None

LOCK_TIMEOUT = "3s"
OLD = "'ok','schema_retry_ok','schema_fail','http_error','timeout','budget_deferred','breaker_open'"
NEW = OLD + ",'billing_or_quota'"


def _swap(values: str) -> str:
    return (
        "ALTER TABLE llm_calls DROP CONSTRAINT IF EXISTS llm_calls_outcome_check;\n"
        f"ALTER TABLE llm_calls ADD CONSTRAINT llm_calls_outcome_check CHECK (outcome IN ({values}));"
    )


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    bind.exec_driver_sql(_swap(NEW))


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    bind.exec_driver_sql("UPDATE llm_calls SET outcome = 'http_error' WHERE outcome = 'billing_or_quota'")
    bind.exec_driver_sql(_swap(OLD))
