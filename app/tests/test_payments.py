import pytest
from fastapi.testclient import TestClient

from pesaguard.main import create_app
from pesaguard.repository import InMemoryRepository, RepositoryUnavailable

from .conftest import make_settings


def test_create_payment(client, payment_body, new_key):
    resp = client.post("/payments", json=payment_body, headers={"Idempotency-Key": new_key()})
    assert resp.status_code == 201
    data = resp.json()
    assert data["amount"] == 1500
    assert data["status"] == "completed"
    assert data["processed_by_version"] == "v9.9.9-test"
    assert "Idempotent-Replayed" not in resp.headers


def test_get_payment_round_trip(client, payment_body, new_key):
    created = client.post(
        "/payments", json=payment_body, headers={"Idempotency-Key": new_key()}
    ).json()
    resp = client.get(f"/payments/{created['payment_id']}")
    assert resp.status_code == 200
    assert resp.json() == created


def test_unknown_payment_is_404(client):
    assert client.get("/payments/does-not-exist").status_code == 404


def test_idempotency_key_is_required(client, payment_body):
    assert client.post("/payments", json=payment_body).status_code == 422


def test_idempotency_key_too_short(client, payment_body):
    resp = client.post("/payments", json=payment_body, headers={"Idempotency-Key": "short"})
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "field, value",
    [
        ("amount", 0),
        ("amount", -50),
        ("currency", "USD"),
        ("phone_number", "0712345678"),  # local format, not international
        ("phone_number", "254812345678"),  # not a mobile prefix
        ("reference", ""),
    ],
)
def test_invalid_requests_rejected(client, payment_body, new_key, field, value):
    payment_body[field] = value
    resp = client.post("/payments", json=payment_body, headers={"Idempotency-Key": new_key()})
    assert resp.status_code == 422


def test_amount_over_limit_rejected(client, payment_body, new_key):
    payment_body["amount"] = 150001
    resp = client.post("/payments", json=payment_body, headers={"Idempotency-Key": new_key()})
    assert resp.status_code == 422


class DownRepo(InMemoryRepository):
    def save_if_new(self, *args):
        raise RepositoryUnavailable("timeout")


def test_database_outage_returns_503_not_500(payment_body, new_key):
    app = create_app(make_settings(), repository=DownRepo())
    with TestClient(app) as c:
        resp = c.post("/payments", json=payment_body, headers={"Idempotency-Key": new_key()})
        assert resp.status_code == 503