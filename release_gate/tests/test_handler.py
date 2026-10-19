"""Tests for the release gate, using a fake HTTP layer (no network, no AWS)."""

import itertools
import random

import handler as gate
from handler import GateConfig, run_checks

CFG = GateConfig(test_url="http://test", test_header="x-test", test_token="t", checks=10)


class FakeService:
    """Pretends to be the green service. `error_rate` simulates a bad release."""

    def __init__(self, error_rate=0.0, version="v9.9.9", broken_idempotency=False, seed=1):
        self.error_rate = error_rate
        self.version = version
        self.broken_idempotency = broken_idempotency
        self.payments = {}
        self.keys = {}
        self.ids = itertools.count()
        self.rng = random.Random(seed)

    def __call__(self, cfg, method, path, body=None, headers=None):
        assert headers is None or "Idempotency-Key" in headers or method == "GET"
        if self.rng.random() < self.error_rate:
            return 500, {}, {}
        hdr = {"x-app-version": self.version}
        if method == "POST":
            key = headers["Idempotency-Key"]
            if key in self.keys and not self.broken_idempotency:
                return 200, hdr, self.payments[self.keys[key]]
            pid = f"p-{next(self.ids)}"
            self.payments[pid] = {"payment_id": pid, **body}
            self.keys[key] = pid
            return 201, hdr, self.payments[pid]
        pid = path.rsplit("/", 1)[-1]
        if pid in self.payments:
            return 200, hdr, self.payments[pid]
        return 404, hdr, {}


def test_healthy_release_passes():
    result = run_checks(CFG, request=FakeService())
    assert result.passed
    assert result.ok == 10
    assert result.versions == {"v9.9.9"}


def test_release_with_errors_fails():
    result = run_checks(CFG, request=FakeService(error_rate=0.3))
    assert not result.passed
    assert result.failures


def test_broken_idempotency_fails():
    result = run_checks(CFG, request=FakeService(broken_idempotency=True))
    assert not result.passed
    assert any("idempotency" in f for f in result.failures)


def test_unreachable_service_fails():
    def boom(*args, **kwargs):
        raise TimeoutError("timed out")

    result = run_checks(CFG, request=boom)
    assert not result.passed
    assert len(result.failures) == 10


def test_handler_returns_hook_status(monkeypatch):
    monkeypatch.setenv("TEST_URL", "http://test/")
    monkeypatch.setenv("TEST_HEADER", "x-test")
    monkeypatch.setenv("TEST_TOKEN", "t")
    monkeypatch.setenv("GATE_CHECKS", "5")

    monkeypatch.setattr(gate, "run_checks", lambda cfg: run_checks(cfg, FakeService()))
    assert gate.handler({"lifecycleStage": "POST_TEST_TRAFFIC_SHIFT"}, None) == {
        "hookStatus": "SUCCEEDED"
    }

    monkeypatch.setattr(gate, "run_checks", lambda cfg: run_checks(cfg, FakeService(1.0)))
    assert gate.handler({"lifecycleStage": "POST_TEST_TRAFFIC_SHIFT"}, None) == {
        "hookStatus": "FAILED"
    }


def test_config_strips_trailing_slash(monkeypatch):
    monkeypatch.setenv("TEST_URL", "http://alb:9000/")
    monkeypatch.setenv("TEST_HEADER", "x-test")
    monkeypatch.setenv("TEST_TOKEN", "t")
    assert GateConfig.from_env().test_url == "http://alb:9000"


def test_real_http_layer_against_local_server():
    """Exercise _request over a real socket: header sent, JSON parsed, errors mapped."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            seen["token"] = self.headers.get("x-test")
            seen["key"] = self.headers.get("Idempotency-Key")
            self.send_response(201)
            self.send_header("x-app-version", "v1.0.0")
            self.end_headers()
            self.wfile.write(json.dumps({"payment_id": "p-1"}).encode())

        def do_GET(self):  # noqa: N802
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cfg = GateConfig(
        test_url=f"http://127.0.0.1:{server.server_port}", test_header="x-test", test_token="s3"
    )
    try:
        status, headers, body = gate._request(
            cfg, "POST", "/payments", {"a": 1}, {"Idempotency-Key": "k"}
        )
        assert (status, body, seen) == (201, {"payment_id": "p-1"}, {"token": "s3", "key": "k"})
        assert headers["x-app-version"] == "v1.0.0"
        status, _, body = gate._request(cfg, "GET", "/payments/nope")
        assert (status, body) == (404, {})
    finally:
        server.shutdown()
