class PaymentError(Exception):
    """An expected reason the payment can't go ahead, reported to the client as `code`."""

    code: str
    message: str

    def __init__(self) -> None:
        super().__init__(self.message)


class Unauthenticated(PaymentError):
    code = "unauthenticated"
    message = "Send the paying user's id in the X-User-Id header."


class InvalidIdempotencyKey(PaymentError):
    code = "invalid_idempotency_key"
    message = "The Idempotency-Key header is missing, empty or too long."


class CartNotFound(PaymentError):
    code = "cart_not_found"
    message = "Cart not found."


class CartAlreadyPaid(PaymentError):
    code = "cart_already_paid"
    message = "This cart is already paid."


class CartAbandoned(PaymentError):
    code = "cart_abandoned"
    message = "This cart was abandoned and can't be paid."


class PaymentInProgress(PaymentError):
    code = "payment_in_progress"
    message = "Another payment for this cart is pending. Retry it with its own Idempotency-Key."


class CartEmpty(PaymentError):
    code = "cart_empty"
    message = "The cart has nothing to pay for."


class PaymentMethodMissing(PaymentError):
    code = "payment_method_missing"
    message = "Add a default card before paying."


class PaymentProviderUnavailable(PaymentError):
    """The provider did not answer, so the card may or may not have been charged."""

    code = "payment_provider_unavailable"
    message = "The payment outcome is unknown. Retry with the same Idempotency-Key."
