#!/usr/bin/env python3
"""Watch a release from the customer's side and record the evidence.

Sends one real payment per interval to the public URL, and records the time,
HTTP status, latency and which version answered. Stop with Ctrl+C to get a
summary: when each version was first and last seen, and how many requests
failed. The CSV in evidence/ is the proof behind the numbers in the README.

    python3 scripts/watch_release.py http://my-alb.amazonaws.com
    python3 scripts/watch_release.py http://my-alb.amazonaws.com --interval 0.5

Standard library only, so it runs anywhere Python 3.11+ is installed.
"""

from __future__ import annotations

import argparse
import csv
import json
import signal
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class Sample:
    at: datetime
    status: int
    version: str
    latency_ms: float


@dataclass
class Summary:
    samples: list[Sample] = field(default_factory=list)

    def add(self, sample: Sample) -> None:
        self.samples.append(sample)

    def versions(self) -> dict[str, dict]:
        """Per version: first seen, last seen, requests, failures."""
        out: dict[str, dict] = {}
        for s in self.samples:
            v = out.setdefault(
                s.version, {"first": s.at, "last": s.at, "requests": 0, "failures": 0}
            )
            v["last"] = s.at
            v["requests"] += 1
            if s.status >= 500 or s.status == 0:
                v["failures"] += 1
        return out

    def report(self) -> str:
        if not self.samples:
            return "No requests recorded."
        lines = [
            "",
            f"Requests: {len(self.samples)}   "
            f"Failed: {sum(1 for s in self.samples if s.status >= 500 or s.status == 0)}",
            "",
            f"{'version':<20}{'first seen':<12}{'last seen':<12}{'requests':>9}{'failed':>8}",
        ]
        for name, v in self.versions().items():
            lines.append(
                f"{name:<20}{v['first']:%H:%M:%S}    {v['last']:%H:%M:%S}    "
                f"{v['requests']:>9}{v['failures']:>8}"
            )
        return "\n".join(lines)


def pay(base_url: str, timeout: float) -> Sample:
    body = json.dumps(
        {
            "amount": 50,
            "currency": "KES",
            "phone_number": "254711111111",
            "reference": "RELEASE-WATCH",
        }
    ).encode()
    req = urllib.request.Request(f"{base_url}/payments", data=body, method="POST")  # noqa: S310
    req.add_header("Content-Type", "application/json")
    req.add_header("Idempotency-Key", f"watch-{uuid.uuid4().hex}")
    start = time.perf_counter()
    at = datetime.now(UTC)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            status, version = resp.status, resp.headers.get("x-app-version", "?")
    except urllib.error.HTTPError as err:
        status, version = err.code, (err.headers or {}).get("x-app-version", "?")
    except Exception:  # timeout, connection reset: count as failure
        status, version = 0, "no-response"
    return Sample(at, status, version, (time.perf_counter() - start) * 1000)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", help="public API address, e.g. from `make url`")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between requests")
    parser.add_argument("--timeout", type=float, default=5.0, help="request timeout, seconds")
    parser.add_argument("--out", default="evidence", help="folder for the CSV")
    args = parser.parse_args(argv)

    base = args.url.rstrip("/")
    out_dir = Path(args.out)
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"release-{datetime.now(UTC):%Y%m%d-%H%M%S}.csv"

    summary = Summary()
    stop = False

    def on_stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, on_stop)

    print(f"Watching {base} every {args.interval}s. Ctrl+C to stop. Writing {path}")
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["utc_time", "status", "version", "latency_ms"])
        while not stop:
            s = pay(base, args.timeout)
            summary.add(s)
            writer.writerow(
                [
                    s.at.isoformat(timespec="milliseconds"),
                    s.status,
                    s.version,
                    f"{s.latency_ms:.0f}",
                ]
            )
            fh.flush()
            flag = "  <-- FAILED" if s.status >= 500 or s.status == 0 else ""
            print(f"{s.at:%H:%M:%S} {s.status:>3} {s.version:<16} {s.latency_ms:>6.0f} ms{flag}")
            time.sleep(max(0.0, args.interval - s.latency_ms / 1000))

    print(summary.report())
    print(f"\nEvidence saved to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
