"""The most important rule in the service: never charge a customer twice."""

from concurrent.futures import ThreadPoolExecutor


def test_retry_with_same_key_returns_original_payment(client, payment_body, new_key):
    key = new_key()
    first = client.post("/payments", json=payment_body, headers={"Idempotency-Key": key})
    second = client.post("/payments", json=payment_body, headers={"Idempotency-Key": key})

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json()["payment_id"] == first.json()["payment_id"]


def test_retry_creates_only_one_payment(client, repo, payment_body, new_key):
    key = new_key()
    for _ in range(5):
        client.post("/payments", json=payment_body, headers={"Idempotency-Key": key})
    assert len(repo._payments) == 1


def test_same_key_different_body_is_conflict(client, payment_body, new_key):
    key = new_key()
    client.post("/payments", json=payment_body, headers={"Idempotency-Key": key})
    payment_body["amount"] = 9999
    resp = client.post("/payments", json=payment_body, headers={"Idempotency-Key": key})
    assert resp.status_code == 409


def test_different_keys_create_different_payments(client, payment_body, new_key):
    a = client.post("/payments", json=payment_body, headers={"Idempotency-Key": new_key()})
    b = client.post("/payments", json=payment_body, headers={"Idempotency-Key": new_key()})
    assert a.json()["payment_id"] != b.json()["payment_id"]


def test_concurrent_duplicates_create_one_payment(client, repo, payment_body, new_key):
    key = new_key()

    def send(_):
        return client.post("/payments", json=payment_body, headers={"Idempotency-Key": key})

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(send, range(10)))

    ids = {r.json()["payment_id"] for r in results}
    assert len(ids) == 1
    assert len(repo._payments) == 1
    assert sorted(r.status_code for r in results) == [200] * 9 + [201]
