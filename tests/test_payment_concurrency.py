import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from werkzeug.test import TestResponse

from shop.payment_provider import Charge, MockPaymentProvider


def run_together(count: int, request: Callable[[int], TestResponse]) -> list[TestResponse]:
    """Send `count` requests from separate threads, released at the same moment."""
    barrier = threading.Barrier(count)

    def run(index: int) -> TestResponse:
        barrier.wait(timeout=5)
        return request(index)

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(run, range(count), timeout=10))


class GatedProvider(MockPaymentProvider):
    """Holds the first charge until the test sets `release`."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def charge(self, **kwargs) -> Charge:
        self.entered.set()
        self.release.wait(timeout=5)
        return super().charge(**kwargs)


def test_cart_is_not_locked_while_provider_charges(app, pay, make_cart, payments_of):
    provider = GatedProvider()
    app.extensions["payment_provider"] = provider
    cart = make_cart()

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(pay, cart, "key-1")
        assert provider.entered.wait(timeout=5)
        # This would wait for the cart lock if the provider call ran inside a transaction.
        other_key = pay(cart, "key-2")
        provider.release.set()
        assert first.result(timeout=5).status_code == 201

    assert (other_key.status_code, other_key.json["code"]) == (409, "payment_in_progress")
    assert len(payments_of(cart)) == 1
    assert len(provider.charges) == 1


def test_same_key_sent_concurrently_charges_once(pay, make_cart, payments_of, provider):
    cart = make_cart()

    responses = run_together(8, lambda _: pay(cart, "same-key"))

    assert [response.status_code for response in responses] == [201] * 8
    assert len({response.json["id"] for response in responses}) == 1
    assert len(payments_of(cart)) == 1
    assert len(provider.charges) == 1


def test_different_keys_sent_concurrently_charge_once(pay, make_cart, payments_of, provider):
    cart = make_cart()

    responses = run_together(8, lambda index: pay(cart, f"key-{index}"))

    assert sorted(response.status_code for response in responses) == [201] + [409] * 7
    assert {response.json["code"] for response in responses if response.status_code == 409} <= {
        "payment_in_progress",
        "cart_already_paid",
    }
    assert len(payments_of(cart)) == 1
    assert len(provider.charges) == 1
