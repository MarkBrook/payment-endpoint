from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest


def test_pays_cart_with_default_card(pay, make_cart, payments_of, cart_status, provider):
    cart = make_cart()

    response = pay(cart)

    assert response.status_code == 201
    payment_id = response.json["id"]
    assert response.json == {
        "id": payment_id,
        "cart_id": str(cart.id),
        "status": "succeeded",
        "amount": "70.00",
        "currency": "USD",
        "failure_code": None,
    }
    provider.charge.assert_called_once_with(
        card_token="tok_test_visa",
        amount=Decimal("70.00"),
        currency="USD",
        idempotency_key=payment_id,
    )
    [payment] = payments_of(cart)
    assert str(payment["id"]) == payment_id
    assert payment["status"] == "succeeded"
    assert payment["amount"] == Decimal("70.00")
    assert payment["provider_charge_id"] == provider.charges[payment_id].id
    assert cart_status(cart) == "checked_out"


@pytest.mark.parametrize("user_id", [None, "not-a-uuid"], ids=["missing", "malformed"])
def test_rejects_request_without_valid_user_id(app, make_cart, payments_of, user_id):
    cart = make_cart()
    headers = {"Idempotency-Key": "key-1"}
    if user_id is not None:
        headers["X-User-Id"] = user_id

    response = app.test_client().post(f"/carts/{cart.id}/payments", headers=headers)

    assert response.status_code == 401
    assert response.json["code"] == "unauthenticated"
    assert payments_of(cart) == []


@pytest.mark.parametrize(
    "idempotency_key", [None, "", "k" * 256], ids=["missing", "empty", "too-long"]
)
def test_rejects_missing_or_oversized_idempotency_key(pay, make_cart, payments_of, idempotency_key):
    cart = make_cart()

    response = pay(cart, idempotency_key)

    assert response.status_code == 400
    assert response.json["code"] == "invalid_idempotency_key"
    assert payments_of(cart) == []


def test_accepts_idempotency_key_of_255_characters(pay, make_cart):
    response = pay(make_cart(), "k" * 255)

    assert response.status_code == 201


def test_user_id_is_checked_before_idempotency_key(app, make_cart):
    cart = make_cart()

    response = app.test_client().post(f"/carts/{cart.id}/payments")

    assert response.status_code == 401
    assert response.json["code"] == "unauthenticated"


def test_unknown_cart_is_not_found(pay, make_cart, provider):
    cart = make_cart()

    response = pay(replace(cart, id=uuid4()))

    assert response.status_code == 404
    assert response.json["code"] == "cart_not_found"
    provider.charge.assert_not_called()


def test_another_users_cart_is_not_found(pay, make_cart, payments_of, cart_status, provider):
    cart, other_users_cart = make_cart(), make_cart()

    response = pay(cart, as_user=other_users_cart.user_id)

    assert response.status_code == 404
    assert response.json["code"] == "cart_not_found"
    assert payments_of(cart) == []
    assert cart_status(cart) == "active"
    provider.charge.assert_not_called()


@pytest.mark.parametrize(
    ("cart_options", "status_code", "code"),
    [
        ({"status": "checked_out"}, 409, "cart_already_paid"),
        ({"status": "abandoned"}, 409, "cart_abandoned"),
        ({"items": ()}, 422, "cart_empty"),
        ({"card_token": None}, 422, "payment_method_missing"),
    ],
    ids=["checked-out", "abandoned", "empty", "no-card"],
)
def test_unpayable_cart_is_rejected_without_charge(
    pay, make_cart, payments_of, provider, cart_options, status_code, code
):
    cart = make_cart(**cart_options)

    response = pay(cart)

    assert response.status_code == status_code
    assert response.json["code"] == code
    assert payments_of(cart) == []
    provider.charge.assert_not_called()


def test_same_key_returns_same_payment_without_new_charge(pay, make_cart, payments_of, provider):
    cart = make_cart()
    first = pay(cart, "key-1")

    retry = pay(cart, "key-1")

    assert retry.status_code == 201
    assert retry.json == first.json
    assert len(payments_of(cart)) == 1
    provider.charge.assert_called_once()


def test_new_key_for_paid_cart_is_rejected(pay, make_cart, payments_of, provider):
    cart = make_cart()
    pay(cart, "key-1")

    response = pay(cart, "key-2")

    assert response.status_code == 409
    assert response.json["code"] == "cart_already_paid"
    assert len(payments_of(cart)) == 1
    provider.charge.assert_called_once()


def test_rejected_request_does_not_use_up_the_key(pay, make_cart, add_card):
    cart = make_cart(card_token=None)
    assert pay(cart, "key-1").status_code == 422
    add_card(cart.user_id, "tok_test_visa")

    response = pay(cart, "key-1")

    assert response.status_code == 201


def test_newest_default_card_is_charged(pay, make_cart, add_card):
    cart = make_cart(card_token="tok_test_declined")
    add_card(cart.user_id, "tok_test_visa")

    response = pay(cart)

    assert response.status_code == 201


def test_declined_card_fails_payment_and_keeps_cart_active(
    pay, make_cart, payments_of, cart_status
):
    cart = make_cart(card_token="tok_test_declined")

    response = pay(cart)

    assert response.status_code == 402
    assert response.json["status"] == "failed"
    assert response.json["failure_code"] == "card_declined"
    [payment] = payments_of(cart)
    assert payment["status"] == "failed"
    assert payment["failure_code"] == "card_declined"
    assert cart_status(cart) == "active"


def test_same_key_after_decline_returns_same_decline(pay, make_cart, provider):
    cart = make_cart(card_token="tok_test_declined")
    first = pay(cart, "key-1")

    retry = pay(cart, "key-1")

    assert retry.status_code == 402
    assert retry.json == first.json
    provider.charge.assert_called_once()


def test_new_key_after_decline_charges_new_card(pay, make_cart, add_card, payments_of, cart_status):
    cart = make_cart(card_token="tok_test_declined")
    pay(cart, "key-1")
    add_card(cart.user_id, "tok_test_visa")

    response = pay(cart, "key-2")

    assert response.status_code == 201
    assert [row["status"] for row in payments_of(cart)] == ["failed", "succeeded"]
    assert cart_status(cart) == "checked_out"


def test_lost_provider_response_leaves_payment_pending(pay, make_cart, payments_of, cart_status):
    cart = make_cart(card_token="tok_test_timeout")

    response = pay(cart)

    assert response.status_code == 502
    assert response.json["code"] == "payment_provider_unavailable"
    assert [row["status"] for row in payments_of(cart)] == ["pending"]
    assert cart_status(cart) == "active"


def test_new_key_while_payment_is_pending_is_rejected(pay, make_cart, payments_of, provider):
    cart = make_cart(card_token="tok_test_timeout")
    pay(cart, "key-1")

    response = pay(cart, "key-2")

    assert response.status_code == 409
    assert response.json["code"] == "payment_in_progress"
    assert len(payments_of(cart)) == 1
    provider.charge.assert_called_once()


def test_same_key_resumes_payment_after_lost_response(pay, make_cart, cart_status, provider):
    cart = make_cart(card_token="tok_test_timeout")
    lost = pay(cart, "key-1")
    assert lost.status_code == 502

    retry = pay(cart, "key-1")

    assert retry.status_code == 201
    assert retry.json["status"] == "succeeded"
    assert len(provider.charges) == 1
    assert cart_status(cart) == "checked_out"
