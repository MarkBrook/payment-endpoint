"""Stand-in for the shop's existing payment total calculation service."""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shop.models import CartItem

SHOP_CURRENCY = "USD"


@dataclass(frozen=True)
class CartTotal:
    amount: Decimal
    currency: str


def calculate_cart_total(session: Session, cart_id: UUID) -> CartTotal:
    amount = session.scalar(
        select(func.coalesce(func.sum(CartItem.quantity * CartItem.unit_price), 0)).where(
            CartItem.cart_id == cart_id
        )
    )
    return CartTotal(amount=amount, currency=SHOP_CURRENCY)
