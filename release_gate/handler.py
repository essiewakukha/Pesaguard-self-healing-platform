"""Release gate: an ECS deployment lifecycle hook.

ECS calls this Lambda at POST_TEST_TRAFFIC_SHIFT: the new version (green) is
running and receives TEST traffic only. No customer has touched it yet.

The gate sends a handful of real payment requests to green through the test
listener and answers ECS with:
    SUCCEEDED -> carry on and start shifting production traffic
    FAILED    -> roll back; customers never saw this version

What the gate checks is correctness ("does a payment work at all?"). Performance
under real traffic is the job of the CloudWatch alarms during the canary bake.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field

log = logging.getLogger()
log.setLevel(logging.INFO)


@dataclass
class GateConfig:
    test_url: str
    test_header: str
    test_token: str
    checks: int = 20
    # Generous on purpose: the gate is a correctness check. A slow-but-working
    # release should pass here and be caught by the latency alarm instead.
    timeout_s: float = 5.0

    @classmethod
    def from_env(cls) -> GateConfig:
        return cls(
            test_url=os.environ["TEST_URL"].rstrip("/"),
            test_header=os.environ["TEST_HEADER"],
            test_token=os.environ["TEST_TOKEN"],
            checks=int(os.environ.get("GATE_CHECKS", "20")),
            timeout_s=float(os.environ.get("GATE_TIMEOUT_S", "5")),
        )


@dataclass
class Result:
    ok: int = 0
    failures: list[str] = field(default_factory=list)
    latencies_ms: list[float] = field(default_factory=list)
    versions: set[str] = field(default_factory=set)

    @property
    def passed(self) -> bool:
        return not self.failures and self.ok > 0


def _request(
    cfg: GateConfig, method: str, path: str, body: dict | None = None, headers: dict | None = None
) -> tuple[int, dict, dict]:
    """Make one HTTP call to green through the test listener."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(  # noqa: S310 - URL comes from our own stack
        f"{cfg.test_url}{path}", data=data, method=method
    )
    req.add_header(cfg.test_header, cfg.test_token)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout_s) as resp:  # noqa: S310
            return resp.status, dict(resp.headers), json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers or {}), {}


def run_checks(cfg: GateConfig, request=_request) -> Result:
    """Send `cfg.checks` payment round-trips plus an idempotency check."""
    result = Result()
    run_id = uuid.uuid4().hex[:8]

    for i in range(cfg.checks):
        key = f"gate-{run_id}-{i:03d}"
        body = {
            "amount": 10,
            "currency": "KES",
            "phone_number": "254700000000",
            "reference": f"RELEASE-GATE-{run_id}",
        }
        start = time.perf_counter()
        try:
            status, headers, payload = request(
                cfg, "POST", "/payments", body, {"Idempotency-Key": key}
            )
        except Exception as err:  # timeout, connection refused, bad JSON...
            result.failures.append(f"payment {i}: {type(err).__name__}: {err}")
            continue
        result.latencies_ms.append((time.perf_counter() - start) * 1000)

        if status != 201:
            result.failures.append(f"payment {i}: expected 201, got {status}")
            continue
        version = headers.get("x-app-version") or headers.get("X-App-Version")
        if version:
            result.versions.add(version)

        # Read it back: a payment we cannot fetch is a broken payment.
        status, _, fetched = request(cfg, "GET", f"/payments/{payload['payment_id']}")
        if status != 200 or fetched.get("payment_id") != payload["payment_id"]:
            result.failures.append(f"payment {i}: read-back failed with {status}")
            continue

        # The most important rule: a retry must never charge twice.
        if i == 0:
            status, _, replay = request(cfg, "POST", "/payments", body, {"Idempotency-Key": key})
            if status != 200 or replay.get("payment_id") != payload["payment_id"]:
                result.failures.append(f"idempotency broken: retry returned {status}")
                continue

        result.ok += 1
    return result


def handler(event, context):
    """Entry point ECS invokes. Must return {"hookStatus": ...}."""
    stage = event.get("lifecycleStage")
    revision = event.get("executionDetails", {}).get("targetServiceRevisionArn", "?")
    log.info(json.dumps({"msg": "gate start", "stage": stage, "revision": revision}))

    cfg = GateConfig.from_env()
    result = run_checks(cfg)

    latencies = sorted(result.latencies_ms)
    summary = {
        "msg": "gate result",
        "passed": result.passed,
        "ok": result.ok,
        "checks": cfg.checks,
        "failures": result.failures[:10],
        "versions_seen": sorted(result.versions),
        "p50_ms": round(latencies[len(latencies) // 2], 1) if latencies else None,
        "max_ms": round(latencies[-1], 1) if latencies else None,
    }
    log.info(json.dumps(summary))

    return {"hookStatus": "SUCCEEDED" if result.passed else "FAILED"}
