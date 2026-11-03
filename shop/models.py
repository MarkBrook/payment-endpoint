"""ORM mappings for the columns this service reads or writes.

The Alembic migrations are the source of truth for the full schema.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CartStatus(StrEnum):
    ACTIVE = "active"
    CHECKED_OUT = "checked_out"
    ABANDONED = "abandoned"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Cart(Base):
    __tablename__ = "carts"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID]
    status: Mapped[str]
    updated_at: Mapped[datetime] = mapped_column(onupdate=func.now())


class CartItem(Base):
    __tablename__ = "cart_items"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    cart_id: Mapped[UUID]
    quantity: Mapped[int]
    unit_price: Mapped[Decimal]


class PaymentMethod(Base):
    __tablename__ = "user_payment_methods"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID]
    provider_token: Mapped[str]
    is_default: Mapped[bool]
    created_at: Mapped[datetime]


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    cart_id: Mapped[UUID]
    payment_method_id: Mapped[UUID]
    idempotency_key: Mapped[str]
    amount: Mapped[Decimal]
    currency: Mapped[str]
    status: Mapped[str]
    provider_charge_id: Mapped[str | None]
    failure_code: Mapped[str | None]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
