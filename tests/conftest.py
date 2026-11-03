import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from flask import Flask
from sqlalchemy import URL, Engine, RowMapping, create_engine, make_url, text
from werkzeug.test import TestResponse

from shop import create_app
from shop.payment_provider import MockPaymentProvider

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


@dataclass(frozen=True)
class CartRef:
    id: UUID
    user_id: UUID


@pytest.fixture(scope="session")
def database_url() -> URL:
    url = make_url(os.environ["TEST_DATABASE_URL"])
    server = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)')
        connection.exec_driver_sql(f'CREATE DATABASE "{url.database}"')
    server.dispose()

    alembic_config = Config(str(ALEMBIC_INI))
    alembic_config.attributes["database_url"] = url.render_as_string(hide_password=False)
    command.upgrade(alembic_config, "head")
    return url


@pytest.fixture(scope="session")
def db(database_url: URL) -> Iterator[Engine]:
    engine = create_engine(database_url)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def empty_tables(db: Engine) -> None:
    with db.begin() as connection:
        connection.exec_driver_sql(
            "TRUNCATE payments, user_payment_methods, cart_items, carts, products, users"
        )


@pytest.fixture(scope="session")
def app(database_url: URL) -> Iterator[Flask]:
    app = create_app(
        {"DATABASE_URL": database_url.render_as_string(hide_password=False), "TESTING": True}
    )
    yield app
    app.extensions["db_sessions"].kw["bind"].dispose()


@pytest.fixture
def provider(app: Flask) -> MockPaymentProvider:
    provider = MockPaymentProvider()
    provider.charge = Mock(wraps=provider.charge)
    app.extensions["payment_provider"] = provider
    return provider


@pytest.fixture
def pay(app: Flask, provider: MockPaymentProvider) -> Callable[..., TestResponse]:
    """POST a payment for the cart as its owner, or as `as_user`. A key of None omits the header."""

    def post(
        cart: CartRef, idempotency_key: str | None = "key-1", *, as_user: UUID | None = None
    ) -> TestResponse:
        headers = {"X-User-Id": str(as_user or cart.user_id)}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        # A client per request, so concurrent tests can call this from several threads.
        return app.test_client().post(f"/carts/{cart.id}/payments", headers=headers)

    return post


@pytest.fixture
def add_card(db: Engine) -> Callable[[UUID, str], None]:
    """Save a default card for the user."""

    def add(user_id: UUID, token: str) -> None:
        with db.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO user_payment_methods"
                    " (user_id, provider_token, last_four, is_default)"
                    " VALUES (:user_id, :token, '4242', TRUE)"
                ),
                {"user_id": user_id, "token": token},
            )

    return add


@pytest.fixture
def make_cart(db: Engine, add_card: Callable[[UUID, str], None]) -> Callable[..., CartRef]:
    """Create a user with a cart and a default card. Default items cost 45.00 x1 + 12.50 x2."""

    def make(
        *,
        items: tuple[tuple[str, int], ...] = (("45.00", 1), ("12.50", 2)),
        card_token: str | None = "tok_test_visa",
        status: str = "active",
    ) -> CartRef:
        with db.begin() as connection:
            user_id = connection.scalar(
                text("INSERT INTO users (email, name) VALUES (:email, 'Test user') RETURNING id"),
                {"email": f"{uuid4()}@example.com"},
            )
            cart_id = connection.scalar(
                text("INSERT INTO carts (user_id, status) VALUES (:user_id, :status) RETURNING id"),
                {"user_id": user_id, "status": status},
            )
            for price, quantity in items:
                product_id = connection.scalar(
                    text(
                        "INSERT INTO products (name, price) VALUES ('Test product', :price)"
                        " RETURNING id"
                    ),
                    {"price": Decimal(price)},
                )
                connection.execute(
                    text(
                        "INSERT INTO cart_items (cart_id, product_id, quantity, unit_price)"
                        " VALUES (:cart_id, :product_id, :quantity, :price)"
                    ),
                    {
                        "cart_id": cart_id,
                        "product_id": product_id,
                        "quantity": quantity,
                        "price": Decimal(price),
                    },
                )
        if card_token is not None:
            add_card(user_id, card_token)
        return CartRef(id=cart_id, user_id=user_id)

    return make


@pytest.fixture
def insert_payment(db: Engine) -> Callable[..., None]:
    """Insert a payment row directly, bypassing the endpoint, paid with the cart owner's card."""

    def insert(
        cart: CartRef,
        *,
        key: str = "key-1",
        status: str = "pending",
        provider_charge_id: str | None = None,
    ) -> None:
        with db.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO payments (cart_id, payment_method_id, idempotency_key, amount,"
                    " currency, status, provider_charge_id)"
                    " SELECT :cart_id, id, :key, 70.00, 'USD', :status, :provider_charge_id"
                    " FROM user_payment_methods WHERE user_id = :user_id"
                ),
                {
                    "cart_id": cart.id,
                    "user_id": cart.user_id,
                    "key": key,
                    "status": status,
                    "provider_charge_id": provider_charge_id,
                },
            )

    return insert


@pytest.fixture
def payments_of(db: Engine) -> Callable[[CartRef], list[RowMapping]]:
    def query(cart: CartRef) -> list[RowMapping]:
        with db.connect() as connection:
            rows = connection.execute(
                text("SELECT * FROM payments WHERE cart_id = :cart_id ORDER BY created_at"),
                {"cart_id": cart.id},
            )
            return list(rows.mappings())

    return query


@pytest.fixture
def cart_status(db: Engine) -> Callable[[CartRef], str]:
    def query(cart: CartRef) -> str:
        with db.connect() as connection:
            return connection.scalar(
                text("SELECT status FROM carts WHERE id = :cart_id"), {"cart_id": cart.id}
            )

    return query
