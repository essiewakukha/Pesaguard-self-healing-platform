"""Storage for payments, with idempotency built in.

Idempotency in one sentence: if a client sends the same request twice with the
same Idempotency-Key (for example because the network dropped the first
response), the customer is charged once and both calls get the same answer.

Two implementations share one interface:
  - InMemoryRepository: for unit tests, no database needed.
  - DynamoDBRepository: for docker compose (DynamoDB Local) and AWS.
"""

from __future__ import annotations

import abc
import hashlib
import json
import threading

import boto3
from botocore.exceptions import ClientError

from .models import Payment, PaymentRequest


class IdempotencyConflict(Exception):
    """Same Idempotency-Key reused with a different request body."""


class RepositoryUnavailable(Exception):
    """The database could not be reached."""


def fingerprint(request: PaymentRequest) -> str:
    """A stable hash of the request body, used to detect key reuse."""
    canonical = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


class PaymentRepository(abc.ABC):
    @abc.abstractmethod
    def save_if_new(
        self, idempotency_key: str, request_fingerprint: str, payment: Payment
    ) -> tuple[Payment, bool]:
        """Store the payment unless the key was already used.

        Returns (payment, created). When the key was used before with the same
        body, returns the ORIGINAL payment and created=False.
        Raises IdempotencyConflict when the key was used with a different body.
        """

    @abc.abstractmethod
    def get(self, payment_id: str) -> Payment | None: ...

    @abc.abstractmethod
    def ping(self) -> None:
        """Raise RepositoryUnavailable if storage cannot be reached."""


class InMemoryRepository(PaymentRepository):
    def __init__(self) -> None:
        self._payments: dict[str, Payment] = {}
        self._keys: dict[str, tuple[str, str]] = {}  # key -> (fingerprint, payment_id)
        self._lock = threading.Lock()

    def save_if_new(self, idempotency_key, request_fingerprint, payment):
        with self._lock:
            if idempotency_key in self._keys:
                stored_fp, payment_id = self._keys[idempotency_key]
                if stored_fp != request_fingerprint:
                    raise IdempotencyConflict(idempotency_key)
                return self._payments[payment_id], False
            self._keys[idempotency_key] = (request_fingerprint, payment.payment_id)
            self._payments[payment.payment_id] = payment
            return payment, True

    def get(self, payment_id):
        return self._payments.get(payment_id)

    def ping(self):
        return None


class DynamoDBRepository(PaymentRepository):
    """Single-table design with two kinds of item:

    pk = "PAY#<payment_id>"   the payment itself
    pk = "IDEM#<key>"         the idempotency record pointing at the payment

    Both are written in ONE transaction, each with "only if it does not exist
    yet". So even two identical requests arriving at the same millisecond on
    two different containers cannot create two payments.
    """

    def __init__(self, table_name: str, region: str, endpoint_url: str | None = None):
        self.table_name = table_name
        self.client = boto3.client("dynamodb", region_name=region, endpoint_url=endpoint_url)

    def ensure_table(self) -> None:
        """Local development only. On AWS, CDK owns the table."""
        try:
            self.client.describe_table(TableName=self.table_name)
        except self.client.exceptions.ResourceNotFoundException:
            self.client.create_table(
                TableName=self.table_name,
                AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
                BillingMode="PAY_PER_REQUEST",
            )
            self.client.get_waiter("table_exists").wait(TableName=self.table_name)

    def save_if_new(self, idempotency_key, request_fingerprint, payment):
        payment_json = payment.model_dump_json()
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": {
                                "pk": {"S": f"IDEM#{idempotency_key}"},
                                "fingerprint": {"S": request_fingerprint},
                                "payment_id": {"S": payment.payment_id},
                            },
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": {
                                "pk": {"S": f"PAY#{payment.payment_id}"},
                                "payment": {"S": payment_json},
                            },
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                ]
            )
            return payment, True
        except ClientError as err:
            code = err.response["Error"]["Code"]
            if code != "TransactionCanceledException":
                raise RepositoryUnavailable(str(err)) from err

        # The key already exists: look up what it points to.
        existing = self._get_item(f"IDEM#{idempotency_key}")
        if existing is None:  # pragma: no cover - only under a very rare race
            raise RepositoryUnavailable("idempotency record vanished")
        if existing["fingerprint"]["S"] != request_fingerprint:
            raise IdempotencyConflict(idempotency_key)
        original = self.get(existing["payment_id"]["S"])
        if original is None:  # pragma: no cover
            raise RepositoryUnavailable("payment record missing")
        return original, False

    def get(self, payment_id):
        item = self._get_item(f"PAY#{payment_id}")
        if item is None:
            return None
        return Payment.model_validate_json(item["payment"]["S"])

    def ping(self):
        try:
            self.client.describe_table(TableName=self.table_name)
        except Exception as err:  # any failure means "not ready"
            raise RepositoryUnavailable(str(err)) from err

    def _get_item(self, pk: str) -> dict | None:
        try:
            resp = self.client.get_item(
                TableName=self.table_name, Key={"pk": {"S": pk}}, ConsistentRead=True
            )
        except ClientError as err:
            raise RepositoryUnavailable(str(err)) from err
        return resp.get("Item")