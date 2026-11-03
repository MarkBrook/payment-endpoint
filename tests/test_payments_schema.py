import pytest
from sqlalchemy.exc import IntegrityError


@pytest.mark.parametrize("first", ["pending", "succeeded"])
@pytest.mark.parametrize("second", ["pending", "succeeded"])
def test_cart_cannot_have_two_live_payments(make_cart, insert_payment, first, second):
    cart = make_cart()
    insert_payment(cart, key="first", status=first)

    with pytest.raises(IntegrityError, match="idx_payments_live_cart_id"):
        insert_payment(cart, key="second", status=second)


def test_failed_payments_do_not_block_a_new_payment(make_cart, insert_payment, payments_of):
    cart = make_cart()
    insert_payment(cart, key="first", status="failed")
    insert_payment(cart, key="second", status="failed")
    insert_payment(cart, key="third", status="pending")

    assert [row["status"] for row in payments_of(cart)] == ["failed", "failed", "pending"]


def test_idempotency_key_is_unique_per_cart(make_cart, insert_payment):
    cart, other_cart = make_cart(), make_cart()
    insert_payment(cart, key="same", status="failed")
    insert_payment(other_cart, key="same", status="failed")

    with pytest.raises(IntegrityError, match="payments_cart_id_idempotency_key_key"):
        insert_payment(cart, key="same", status="failed")


def test_provider_charge_settles_one_payment(make_cart, insert_payment):
    cart, other_cart = make_cart(), make_cart()
    insert_payment(cart, status="succeeded", provider_charge_id="ch_1")

    with pytest.raises(IntegrityError, match="payments_provider_charge_id_key"):
        insert_payment(other_cart, status="succeeded", provider_charge_id="ch_1")
