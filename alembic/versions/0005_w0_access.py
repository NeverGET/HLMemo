"""W0a access hardening (D-052, D-061): device expiry + the `device_minted` event kind.

Revision ID: 0005_w0_access
Revises: 0004_title_norm_fold (branch `phase0`)

Adds ``devices.expires_at timestamptz NULL``. A device whose ``expires_at`` has passed is treated
exactly like a revoked one, both in the middleware's pre-body gate and in the in-transaction
resolve (``auth/resolve.py``). ``events.kind`` gains ``device_minted`` (server-side minting via
``python -m hlmemo.ops``; CC-2 lists the kind, W0a is its first producer). Additive only: no
existing row is rewritten.

This revision carries the branch label ``main`` (CC-1). Every migrate invocation now runs
``alembic upgrade main@head``; ``main@head`` resolves through ``0004_title_norm_fold`` and the
``phase0`` chain, so a database at ``0001_phase0`` or ``0004_title_norm_fold`` upgrades with the
same command. ``phase0@head`` keeps resolving to the same head (the label marks the chain's base).
The deferred ``hnsw`` branch stays a separate base and is never applied by ``main@head``.
"""

from __future__ import annotations

from alembic import op

revision = "0005_w0_access"
down_revision = "0004_title_norm_fold"
branch_labels = ("main",)
depends_on = None

PHASE0_KINDS = (
    "'write','call_the_day','access','archive','restore','project_created',"
    "'device_registered','device_approved','device_revoked','grant_added','grant_revoked'"
)


def _kind_check(kinds: str) -> str:
    return f"""
DO $$
DECLARE c text;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
            WHERE conrelid = 'events'::regclass AND contype = 'c'
              AND pg_get_constraintdef(oid) LIKE '%kind%'
  LOOP
    EXECUTE format('ALTER TABLE events DROP CONSTRAINT %I', c);
  END LOOP;
END $$;
ALTER TABLE events ADD CONSTRAINT events_kind_check CHECK (kind IN ({kinds}));
"""


def upgrade() -> None:
    op.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS expires_at timestamptz")
    op.execute(_kind_check(PHASE0_KINDS + ",'device_minted'"))


def downgrade() -> None:
    # Refuses (CHECK violation) while device_minted events exist: they are authoritative (D-010).
    op.execute(_kind_check(PHASE0_KINDS))
    op.execute("ALTER TABLE devices DROP COLUMN IF EXISTS expires_at")
