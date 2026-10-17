import itertools

import pytest
from fastapi.testclient import TestClient

from pesaguard.config import Settings
from pesaguard.main import create_app
from pesaguard.repository import InMemoryRepository

_counter = itertools.count()


def make_settings(**overrides) -> Settings:
    defaults = dict(
        app_version="v9.9.9-test",
        deploy_color="blue",
        fault_mode="none",
        table_name="unused",
        aws_region="us-east-1",
        dynamodb_endpoint=None,
        auto_create_table=False,
        max_amount=150000,
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture
def repo():
    return InMemoryRepository()


@pytest.fixture
def client(repo):
    app = create_app(make_settings(), repository=repo)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def payment_body():
    return {
        "amount": 1500,
        "currency": "KES",
        "phone_number": "254712345678",
        "reference": "ORDER-1001",
    }


@pytest.fixture
def new_key():
    """Returns a function that gives a fresh, valid Idempotency-Key each call."""
    return lambda: f"test-key-{next(_counter):06d}"