import os
from collections.abc import Mapping
from typing import Any

from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shop.api import payments_api
from shop.payment_provider import MockPaymentProvider


def create_app(config: Mapping[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.json.sort_keys = False
    app.config["DATABASE_URL"] = os.environ.get("DATABASE_URL")
    app.config.from_mapping(config or {})
    if not app.config["DATABASE_URL"]:
        raise RuntimeError("Set the DATABASE_URL environment variable.")

    engine = create_engine(app.config["DATABASE_URL"])
    app.extensions["db_sessions"] = sessionmaker(engine, expire_on_commit=False)
    app.extensions["payment_provider"] = MockPaymentProvider()

    app.register_blueprint(payments_api)
    return app
