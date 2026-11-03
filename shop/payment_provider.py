import threading
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import uuid4

from shop.errors import PaymentProviderUnavailable


@dataclass(frozen=True)
class Charge:
    id: str
    amount: Decimal
    currency: str
    decline_code: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.decline_code is None


class PaymentProvider(Protocol):
    def charge(
        self, *, card_token: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> Charge:
        """Charge the card at most once per idempotency key.

        Raises PaymentProviderUnavailable when the outcome is unknown, for example on a timeout.
        """
        ...


class MockPaymentProvider:
    """In-memory provider. Like real providers, it returns the stored charge for a repeated key.

    The card token picks the outcome:
    - "tok_test_declined": the card is declined.
    - "tok_test_timeout": the card is charged, but the response to the first attempt is lost.
      A retry with the same idempotency key gets the charge.
    - any other token: the card is charged.
    """

    DECLINED_TOKEN = "tok_test_declined"
    TIMEOUT_TOKEN = "tok_test_timeout"

    def __init__(self) -> None:
        self.charges: dict[str, Charge] = {}
        self._lock = threading.Lock()

    def charge(
        self, *, card_token: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> Charge:
        with self._lock:
            first_attempt = idempotency_key not in self.charges
            if first_attempt:
                self.charges[idempotency_key] = Charge(
                    id=f"ch_{uuid4().hex}",
                    amount=amount,
                    currency=currency,
                    decline_code="card_declined" if card_token == self.DECLINED_TOKEN else None,
                )
            charge = self.charges[idempotency_key]
        if first_attempt and card_token == self.TIMEOUT_TOKEN:
            raise PaymentProviderUnavailable()
        return charge
