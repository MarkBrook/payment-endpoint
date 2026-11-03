from decimal import Decimal
from http import HTTPStatus
from uuid import UUID

from flask import Blueprint, current_app, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shop.errors import (
    CartAbandoned,
    CartAlreadyPaid,
    CartEmpty,
    CartNotFound,
    InvalidIdempotencyKey,
    PaymentError,
    PaymentInProgress,
    PaymentMethodMissing,
    PaymentProviderUnavailable,
    Unauthenticated,
)
from shop.models import PaymentStatus
from shop.payments import pay_for_cart

MAX_IDEMPOTENCY_KEY_LENGTH = 255

HTTP_STATUS_BY_PAYMENT_STATUS = {
    PaymentStatus.SUCCEEDED: HTTPStatus.CREATED,
    PaymentStatus.FAILED: HTTPStatus.PAYMENT_REQUIRED,
}
HTTP_STATUS_BY_ERROR: dict[type[PaymentError], HTTPStatus] = {
    Unauthenticated: HTTPStatus.UNAUTHORIZED,
    InvalidIdempotencyKey: HTTPStatus.BAD_REQUEST,
    CartNotFound: HTTPStatus.NOT_FOUND,
    CartAlreadyPaid: HTTPStatus.CONFLICT,
    CartAbandoned: HTTPStatus.CONFLICT,
    PaymentInProgress: HTTPStatus.CONFLICT,
    CartEmpty: HTTPStatus.UNPROCESSABLE_CONTENT,
    PaymentMethodMissing: HTTPStatus.UNPROCESSABLE_CONTENT,
    PaymentProviderUnavailable: HTTPStatus.BAD_GATEWAY,
}

payments_api = Blueprint("payments_api", __name__)


class PaymentHeaders(BaseModel):
    """X-User-Id stands in for the identity an auth gateway would pass on."""

    user_id: UUID = Field(alias="X-User-Id")
    idempotency_key: str = Field(
        alias="Idempotency-Key", min_length=1, max_length=MAX_IDEMPOTENCY_KEY_LENGTH
    )


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cart_id: UUID
    status: PaymentStatus
    amount: Decimal
    currency: str
    failure_code: str | None


@payments_api.post("/carts/<uuid:cart_id>/payments")
def create_payment(cart_id: UUID) -> tuple[dict[str, object], HTTPStatus]:
    headers = _payment_headers()
    payment = pay_for_cart(
        current_app.extensions["db_sessions"],
        current_app.extensions["payment_provider"],
        cart_id=cart_id,
        user_id=headers.user_id,
        idempotency_key=headers.idempotency_key,
    )
    body = PaymentResponse.model_validate(payment).model_dump(mode="json")
    return body, HTTP_STATUS_BY_PAYMENT_STATUS[payment.status]


@payments_api.errorhandler(PaymentError)
def handle_payment_error(error: PaymentError) -> tuple[dict[str, str], HTTPStatus]:
    return {"code": error.code, "message": error.message}, HTTP_STATUS_BY_ERROR[type(error)]


def _payment_headers() -> PaymentHeaders:
    try:
        return PaymentHeaders.model_validate(dict(request.headers))
    except ValidationError as error:
        invalid_headers = {detail["loc"][0] for detail in error.errors()}
        if "X-User-Id" in invalid_headers:
            raise Unauthenticated() from error
        raise InvalidIdempotencyKey() from error
