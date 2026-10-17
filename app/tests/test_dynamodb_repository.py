"""Tests the real DynamoDB code against moto, a local fake of AWS.

These prove the transaction + condition logic works, without an AWS account.
"""

from datetime import UTC, datetime

import pytest
from moto import mock_aws

from pesaguard.models import Payment, PaymentRequest, PaymentStatus
from pesaguard.repository import DynamoDBRepository, IdempotencyConflict, fingerprint


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")


@pytest.fixture
def ddb():
    with mock_aws():
        repo = DynamoDBRepository("payments-test", "us-east-1")
        repo.ensure_table()
        repo.ensure_table()  # second call is a no-op
        yield repo


def make_payment(payment_id="p-1", amount=1500):
    return Payment(
        payment_id=payment_id,
        amount=amount,
        currency="KES",
        phone_number="254712345678",
        reference="ORDER-1",
        status=PaymentStatus.COMPLETED,
        created_at=datetime.now(UTC),
        processed_by_version="v9.9.9-test",
    )


def req(amount=1500):
    return PaymentRequest(amount=amount, phone_number="254712345678", reference="ORDER-1")


def test_save_and_get(ddb):
    saved, created = ddb.save_if_new("key-0001", fingerprint(req()), make_payment())
    assert created
    assert ddb.get("p-1") == saved


def test_get_missing_returns_none(ddb):
    assert ddb.get("nope") is None


def test_duplicate_key_returns_original(ddb):
    ddb.save_if_new("key-0001", fingerprint(req()), make_payment("p-1"))
    again, created = ddb.save_if_new("key-0001", fingerprint(req()), make_payment("p-2"))
    assert not created
    assert again.payment_id == "p-1"
    assert ddb.get("p-2") is None  # the duplicate was never stored


def test_duplicate_key_different_body_conflicts(ddb):
    ddb.save_if_new("key-0001", fingerprint(req(1500)), make_payment("p-1"))
    with pytest.raises(IdempotencyConflict):
        ddb.save_if_new("key-0001", fingerprint(req(9999)), make_payment("p-2", 9999))


def test_ping_ok(ddb):
    ddb.ping()
