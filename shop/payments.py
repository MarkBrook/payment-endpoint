"""Paying for a cart in three steps, so the provider is never called inside a transaction:

1. Lock the cart row, then find the payment for the idempotency key or create a pending one.
2. Charge the card through the provider, keyed by the payment id.
3. Lock the cart row again and record the outcome.

Every transaction locks the cart row first, so requests for one cart run one at a time.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from shop.errors import (
    CartAbandoned,
    CartAlreadyPaid,
    CartEmpty,
    CartNotFound,
    PaymentInProgress,
    PaymentMethodMissing,
)
from shop.models import Cart, CartStatus, Payment, PaymentMethod, PaymentStatus
from shop.payment_provider import Charge, PaymentProvider
from shop.totals import calculate_cart_total


def pay_for_cart(
    sessions: sessionmaker[Session],
    provider: PaymentProvider,
    *,
    cart_id: UUID,
    user_id: UUID,
    idempotency_key: str,
) -> Payment:
    """Charge the user's default card for the cart, at most once.

    A retry with the same idempotency key returns the payment it created. If that payment is still
    pending, for example because the provider's answer was lost, the retry charges it again with
    the same provider idempotency key and the provider returns the original charge.
    """
    with sessions.begin() as session:
        payment = _find_or_start_payment(session, cart_id, user_id, idempotency_key)
        if payment.status != PaymentStatus.PENDING:
            return payment
        card_token = session.get_one(PaymentMethod, payment.payment_method_id).provider_token

    # No transaction is open here, so a slow provider doesn't hold the cart lock.
    charge = provider.charge(
        card_token=card_token,
        amount=payment.amount,
        currency=payment.currency,
        idempotency_key=str(payment.id),
    )

    with sessions.begin() as session:
        return _record_charge(session, payment.id, payment.cart_id, charge)


def _find_or_start_payment(
    session: Session, cart_id: UUID, user_id: UUID, idempotency_key: str
) -> Payment:
    cart = session.get(Cart, cart_id, with_for_update=True)
    if cart is None or cart.user_id != user_id:
        raise CartNotFound()

    payment = session.scalar(
        select(Payment).where(
            Payment.cart_id == cart_id, Payment.idempotency_key == idempotency_key
        )
    )
    return payment or _start_payment(session, cart, idempotency_key)


def _start_payment(session: Session, cart: Cart, idempotency_key: str) -> Payment:
    _ensure_cart_is_payable(session, cart)
    total = calculate_cart_total(session, cart.id)
    if total.amount <= 0:
        raise CartEmpty()
    card = _default_card(session, cart.user_id)

    payment = Payment(
        cart_id=cart.id,
        payment_method_id=card.id,
        idempotency_key=idempotency_key,
        amount=total.amount,
        currency=total.currency,
        status=PaymentStatus.PENDING,
    )
    session.add(payment)
    return payment


def _ensure_cart_is_payable(session: Session, cart: Cart) -> None:
    if cart.status == CartStatus.CHECKED_OUT:
        raise CartAlreadyPaid()
    if cart.status == CartStatus.ABANDONED:
        raise CartAbandoned()
    pending = select(Payment.id).where(
        Payment.cart_id == cart.id, Payment.status == PaymentStatus.PENDING
    )
    if session.scalar(pending) is not None:
        raise PaymentInProgress()


def _default_card(session: Session, user_id: UUID) -> PaymentMethod:
    card = session.scalar(
        select(PaymentMethod)
        .where(PaymentMethod.user_id == user_id, PaymentMethod.is_default)
        .order_by(PaymentMethod.created_at.desc())
    )
    if card is None:
        raise PaymentMethodMissing()
    return card


def _record_charge(session: Session, payment_id: UUID, cart_id: UUID, charge: Charge) -> Payment:
    # Concurrent retries of one request may all get here. The provider gives each of them the
    # same charge, so they all write the same outcome.
    cart = session.get_one(Cart, cart_id, with_for_update=True)
    payment = session.get_one(Payment, payment_id)
    payment.provider_charge_id = charge.id
    if charge.succeeded:
        payment.status = PaymentStatus.SUCCEEDED
        cart.status = CartStatus.CHECKED_OUT
    else:
        payment.status = PaymentStatus.FAILED
        payment.failure_code = charge.decline_code
    return payment
