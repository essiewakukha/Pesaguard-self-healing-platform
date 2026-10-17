import random
import time

import pytest
from fastapi.testclient import TestClient

from pesaguard.faults import FaultConfig, parse_fault_mode, should_fail
from pesaguard.main import create_app
from pesaguard.repository import InMemoryRepository

from .conftest import make_settings


@pytest.mark.parametrize("value", [None, "", "none", "NONE"])
def test_no_fault(value):
    assert parse_fault_mode(value) == FaultConfig()
    assert not parse_fault_mode(value).active


def test_parse_combined():
    cfg = parse_fault_mode("error_rate=0.3,latency_ms=1500")
    assert cfg == FaultConfig(error_rate=0.3, latency_ms=1500)


@pytest.mark.parametrize("value", ["error_rate", "error_rate=2", "latency_ms=-1", "explode=yes"])
def test_invalid_fault_mode_rejected(value):
    with pytest.raises(ValueError):
        parse_fault_mode(value)


def test_invalid_fault_mode_stops_app_at_startup():
    with pytest.raises(ValueError):
        create_app(make_settings(fault_mode="error_rate=oops"), repository=InMemoryRepository())


def test_error_rate_is_roughly_respected():
    rng = random.Random(42)
    cfg = FaultConfig(error_rate=0.3)
    failures = sum(should_fail(cfg, rng) for _ in range(10_000))
    assert 2700 < failures < 3300


def test_full_error_rate_fails_payments_but_not_health(payment_body):
    app = create_app(make_settings(fault_mode="error_rate=1.0"), repository=InMemoryRepository())
    with TestClient(app) as c:
        resp = c.post("/payments", json=payment_body, headers={"Idempotency-Key": "abcdefgh"})
        assert resp.status_code == 500
        assert c.get("/health").status_code == 200


def test_latency_fault_slows_payments_only(payment_body):
    app = create_app(make_settings(fault_mode="latency_ms=300"), repository=InMemoryRepository())
    with TestClient(app) as c:
        start = time.perf_counter()
        c.get("/health")
        health_s = time.perf_counter() - start

        start = time.perf_counter()
        c.post("/payments", json=payment_body, headers={"Idempotency-Key": "abcdefgh"})
        pay_s = time.perf_counter() - start

    assert health_s < 0.2
    assert pay_s >= 0.3
