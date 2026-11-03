"""Create the payments table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-03

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
-- Status values:
--   pending   - the provider has not confirmed the outcome yet.
--   succeeded - the provider charged the card.
--   failed    - the provider declined the card.
CREATE TABLE payments (
    id                 UUID           PRIMARY KEY DEFAULT gen_random_uuid(),
    cart_id            UUID           NOT NULL REFERENCES carts(id),
    payment_method_id  UUID           NOT NULL REFERENCES user_payment_methods(id),
    idempotency_key    VARCHAR(255)   NOT NULL,
    amount             NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    currency           CHAR(3)        NOT NULL,
    status             TEXT           NOT NULL DEFAULT 'pending'
                                      CHECK (status IN ('pending', 'succeeded', 'failed')),
    provider_charge_id TEXT           UNIQUE,
    failure_code       TEXT,
    created_at         TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ    NOT NULL DEFAULT NOW(),

    CONSTRAINT payments_cart_id_idempotency_key_key UNIQUE (cart_id, idempotency_key)
)
""")
    op.execute("""
-- At most one payment per cart can be in flight or successful.
-- Failed attempts don't count, so the user can pay again after a decline.
CREATE UNIQUE INDEX idx_payments_live_cart_id
    ON payments(cart_id)
    WHERE status IN ('pending', 'succeeded')
""")


def downgrade() -> None:
    op.execute("DROP TABLE payments")
