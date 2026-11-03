# Payment endpoint

A Flask service that pays for an online shop cart with the user's saved card. It adds a `payments` table to the provided base schema and one endpoint:

```
POST /carts/{cart_id}/payments
```

The endpoint charges a cart at most once. Retries with the same `Idempotency-Key` are safe - double clicks or concurrent requests can't double charge. The payment provider is never called inside a database transaction.

Stack: Python 3.13, Flask, Pydantic, SQLAlchemy 2.1, Alembic, PostgreSQL 17, pytest, Docker Compose.

## Quick start

You need Docker with Compose.

```sh
docker compose up --build -d
```

This starts PostgreSQL and the app on http://localhost:8000. On start the app runs `alembic upgrade head`, which creates the base schema with its sample data and the `payments` table.

### Pay for Alice's sample cart

The `X-User-Id` header says who is paying (see [Assumptions](#assumptions)).

```sh
KEY=$(uuidgen)

curl -i -X POST http://localhost:8000/carts/c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1/payments \
  -H "X-User-Id: 11111111-1111-1111-1111-111111111111" \
  -H "Idempotency-Key: $KEY"
```

```
HTTP/1.1 201 CREATED
Content-Type: application/json

{"id":"2dd7840b-de2b-4cfe-92df-77ccce3548b6","cart_id":"c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1","status":"succeeded","amount":"70.00","currency":"USD","failure_code":null}
```

Run the same command again with the same `$KEY`: you get the same payment back and the card is not charged again. Run it with a new key: you get `409` with `"code":"cart_already_paid"`.

### Try the failure paths

Give Bob a cart with one Wool Blanket and a card that the mock provider declines:

```sh
docker compose exec -T db psql -U shop -d shop <<'SQL'
INSERT INTO carts (id, user_id)
VALUES ('b0b0b0b0-b0b0-b0b0-b0b0-b0b0b0b0b0b0', '22222222-2222-2222-2222-222222222222');
INSERT INTO cart_items (cart_id, product_id, quantity, unit_price)
VALUES ('b0b0b0b0-b0b0-b0b0-b0b0-b0b0b0b0b0b0', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 1, 89.99);
INSERT INTO user_payment_methods (user_id, provider_token, last_four, is_default)
VALUES ('22222222-2222-2222-2222-222222222222', 'tok_test_declined', '0002', TRUE);
SQL

BOB=22222222-2222-2222-2222-222222222222
BOB_CART=http://localhost:8000/carts/b0b0b0b0-b0b0-b0b0-b0b0-b0b0b0b0b0b0/payments

curl -i -X POST $BOB_CART -H "X-User-Id: $BOB" -H "Idempotency-Key: $(uuidgen)"
```

The answer is `402` with `"status":"failed"` and `"failure_code":"card_declined"`. The cart stays active, so Bob can pay again with another card and a new key.

Now switch Bob to a card whose first charge response gets lost on the way back:

```sh
docker compose exec -T db psql -U shop -d shop -c \
  "UPDATE user_payment_methods SET provider_token = 'tok_test_timeout' WHERE user_id = '$BOB'"

KEY=$(uuidgen)
curl -i -X POST $BOB_CART -H "X-User-Id: $BOB" -H "Idempotency-Key: $KEY"
curl -i -X POST $BOB_CART -H "X-User-Id: $BOB" -H "Idempotency-Key: $(uuidgen)"
curl -i -X POST $BOB_CART -H "X-User-Id: $BOB" -H "Idempotency-Key: $KEY"
```

1. The first call gets `502 payment_provider_unavailable`. The card was charged, but the app could not know that, so the payment stays `pending`.
2. The call with a new key gets `409 payment_in_progress`: a second payment for this cart is not allowed while the first one is unresolved.
3. The retry with the first key charges again with the same provider idempotency key. The provider returns the original charge, so the answer is `201` and the card is charged once.

Stop everything and delete the data with `docker compose down -v`.

## Tests and checks

The tests run against a real PostgreSQL, because they check row locks, unique indexes and concurrent requests:

```sh
docker compose run --rm --build app pytest
```

The session fixture creates a `shop_test` database and migrates it with Alembic. Each test starts from empty tables.

Lint, format and the SQL of the payments migration:

```sh
docker compose run --rm app ruff check .
docker compose run --rm app ruff format --check .
docker compose run --rm app alembic upgrade 0001:head --sql
```

### Without Docker

You need Python 3.13 and a PostgreSQL 13 or later server.

```sh
python3.13 -m venv .venv
.venv/bin/pip install --upgrade pip  # dependency groups need pip 25.1 or later
.venv/bin/pip install -e . --group dev

export DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/shop
export TEST_DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/shop_test

.venv/bin/alembic upgrade head
.venv/bin/flask --app shop run
.venv/bin/pytest
```

The test database user needs the right to create databases: the tests drop and recreate the database in `TEST_DATABASE_URL`.

## API

`POST /carts/{cart_id}/payments` charges the cart owner's default card for the cart total. It takes no body.

| Header | Value |
|---|---|
| `X-User-Id` | Id of the paying user |
| `Idempotency-Key` | 1 to 255 characters, one fresh UUID per checkout attempt |

`PaymentHeaders`, a Pydantic model in `shop/api.py`, validates both headers. If both are invalid, the answer is 401.

A payment response looks like the example in the quick start and is defined by `PaymentResponse` in the same file. The amount is a decimal string. Errors have a stable `code` and a human readable `message`:

```json
{"code":"cart_already_paid","message":"This cart is already paid."}
```

| Situation | Status | `code` |
|---|---|---|
| Card charged | 201 | |
| Card declined | 402, body is the failed payment | |
| Same key as a finished payment | Same status and body as the first time | |
| Same key as a pending payment | The charge is retried: 201, 402 or 502 | |
| Missing or malformed `X-User-Id` | 401 | `unauthenticated` |
| Missing, empty or too long `Idempotency-Key` | 400 | `invalid_idempotency_key` |
| Unknown cart or another user's cart | 404 | `cart_not_found` |
| Cart already paid (new key) | 409 | `cart_already_paid` |
| Cart abandoned | 409 | `cart_abandoned` |
| Another payment for the cart is pending (new key) | 409 | `payment_in_progress` |
| Nothing to pay | 422 | `cart_empty` |
| No default card | 422 | `payment_method_missing` |
| Provider did not answer, outcome unknown | 502 | `payment_provider_unavailable` |

A cart id that is not a UUID doesn't match the route, so Flask answers with its default 404 page.

## Schema

The migrations live in `migrations/versions/`. `0001` is the provided base schema, unchanged and with its sample data. `0002` adds the `payments` table, one row per payment attempt:

| Column | Notes |
|---|---|
| `cart_id`, `payment_method_id` | The cart paid for and the card used. A retried charge reuses this card. |
| `idempotency_key` | The client's key. Unique per cart. |
| `amount`, `currency` | Taken from the totals service when the payment starts. `amount > 0`. |
| `status` | `pending`, `succeeded` or `failed`. |
| `provider_charge_id` | The provider's charge. Unique, so one charge can't settle two payments. |
| `failure_code` | Why the card was declined, for example `card_declined`. |

Two constraints carry the duplicate protection:

- `UNIQUE (cart_id, idempotency_key)` makes a retry find its payment instead of creating a second one.
- A partial unique index on `cart_id` for `pending` and `succeeded` rows lets a cart have at most one payment that is in flight or done. Failed attempts don't count, so the user can pay again after a decline.

The foreign keys have no `ON DELETE CASCADE`: a cart or card that has payments can't be deleted, so payment records are never lost with it.

## How it works

### Three steps, no provider call inside a transaction

`shop/payments.py` handles every request in three steps:

1. **Find or start (transaction 1).** Lock the cart row (`SELECT ... FOR UPDATE`) and look up the payment by `(cart_id, Idempotency-Key)`. If there is none, check the cart, compute the total, pick the default card and insert a `pending` payment. Commit.
2. **Charge.** Call the provider with no transaction open. The provider's idempotency key is the payment id.
3. **Record (transaction 2).** Lock the cart row again. On success, mark the payment `succeeded` and the cart `checked_out`. On decline, mark the payment `failed`.

Calling the provider inside a transaction would hold the cart lock and a database connection for as long as the provider takes. Worse, a rollback after a real charge would erase the only record that the card was charged. Here the `pending` row is committed before any money moves, so the app always knows a charge may exist.

### Idempotency

- The key is scoped to the cart, which is part of the URL. The request has no body, so a reused key can't come with a different payload.
- The response is rebuilt from the payment row, so no response cache is needed. A retry of a finished payment gets the same status and body without calling the provider.
- The key lookup happens before the cart checks. That is why a retry of a paid request gets its payment back instead of `409 cart_already_paid`.
- Rejected requests (for example a missing card) store nothing, so the client can fix the cause and retry with the same key.
- A retry of a `pending` payment charges it again with the same provider idempotency key. Providers such as Stripe and Adyen return the original charge for a repeated key, so the card is charged once. The provider key is the payment id, which is unique across the shop, while client keys are only unique per cart.

### Race conditions

| Race | Handling |
|---|---|
| Double click or two tabs, different keys | The cart row lock serializes them. The first creates the payment, the others see it and get 409. The partial unique index enforces the same rule in the database. |
| Same key sent twice at once | Both find the same payment and both may call the provider with the same key. The provider returns one charge, both record the same outcome and both get 201 with the same payment. |
| Lost provider response or crash after step 1 | The payment stays `pending`. A retry with the same key finishes it with one charge. |
| Check-then-insert | Every check runs after the cart lock is taken. Under PostgreSQL's default READ COMMITTED isolation, each statement sees what the previous lock holder committed. |

`tests/test_payment_concurrency.py` covers these with real threads and real commits. The tests sync threads with barriers and events instead of sleeps, and their assertions hold for every interleaving.

### Failure modes

| Failure | State afterwards | What the client sees and does |
|---|---|---|
| Card declined | Payment `failed`, cart `active` | 402. Change the card, then pay with a new key. |
| Provider timeout | Payment `pending` | 502. Retry with the same key: 201 or 402, one charge. |
| App crash after step 1 | Payment `pending` | Retry with the same key finishes it. |
| Database error in step 3 after a charge | Step 3 rolled back, payment `pending` | 500. Retry with the same key: the provider returns the stored charge, 201. |

### Mock payment provider

`MockPaymentProvider` in `shop/payment_provider.py` keeps charges in memory and, like a real provider, returns the stored charge for a repeated idempotency key. The card token decides the outcome:

| Token | Outcome |
|---|---|
| `tok_test_declined` | Declined with `card_declined` |
| `tok_test_timeout` | Charged, but the response to the first attempt is lost. A retry with the same key gets the charge. |
| Anything else | Charged |

Because the mock lives in process memory, gunicorn runs a single worker process (with threads).

## Assumptions

- **Auth:** an auth gateway in front of the service authenticates the user and passes their id in `X-User-Id`. The endpoint trusts that header and returns 404 for carts of other users.
- **Card:** the user pays with their default card. If several cards are marked default, the newest one wins.
- **Totals and currency:** the shop sells in one currency (USD). `shop/totals.py` stands in for the existing totals service: it sums `quantity * unit_price` over the cart items.
- **Provider idempotency:** the provider honors idempotency keys, also for concurrent requests, and keeps them at least as long as a payment can stay `pending`.
- **Stock:** the payment does not reserve or decrement stock. Inventory belongs to order fulfillment.
- **Cart service:** it refuses item changes and status changes while the cart has a `pending` or `succeeded` payment. Otherwise a user could add items after the amount was fixed. The payment endpoint can't enforce this on its own.
- **Clients:** they send one UUID per checkout attempt, reuse it for retries and send a new one after a decline.
- **Key retention:** keys are kept as long as their payment row, so a finished payment can be replayed at any time.
- **Card deletion:** cards with payments can't be deleted (foreign key).
- **Card data:** only the provider token is stored. It is never logged or returned, so card data stays with the provider.
- **Isolation:** the database keeps PostgreSQL's default READ COMMITTED isolation.

## Project layout

```
shop/__init__.py          create_app(): settings, database engine, provider, routes
shop/api.py               the endpoint and its Pydantic models for headers and responses
shop/payments.py          the three-step payment flow
shop/payment_provider.py  provider interface and the mock provider
shop/models.py            SQLAlchemy mappings for the columns the service uses
shop/totals.py            stand-in for the existing totals service
shop/errors.py            the errors the endpoint reports
migrations/               Alembic environment and the two revisions
tests/                    endpoint, concurrency and schema tests
```
