"""Deliberate faults, used to build "bad releases" on purpose.

FAULT_MODE examples:
    none               normal behaviour (default)
    error_rate=0.3     30% of payment requests fail with HTTP 500
    latency_ms=1500    every payment request is delayed by 1.5 seconds

Faults only affect the /payments endpoints, never /health. That is deliberate:
a real buggy release usually still passes a basic health check, which is
exactly why PesaGuard watches business metrics, not just "is it up?".
"""

from __future__ import annotations

import random
from dataclasses import dataclass


class InjectedFault(Exception):
    """Raised to simulate a bug in the release."""


@dataclass(frozen=True)
class FaultConfig:
    error_rate: float = 0.0
    latency_ms: int = 0

    @property
    def active(self) -> bool:
        return self.error_rate > 0 or self.latency_ms > 0


def parse_fault_mode(value: str | None) -> FaultConfig:
    """Parse FAULT_MODE. Invalid values raise ValueError so a typo fails the
    container at startup instead of silently shipping without the fault."""
    if not value or value.strip().lower() == "none":
        return FaultConfig()

    error_rate, latency_ms = 0.0, 0
    for part in value.split(","):
        name, sep, raw = part.strip().partition("=")
        if not sep:
            raise ValueError(f"FAULT_MODE part '{part}' must look like name=value")
        if name == "error_rate":
            error_rate = float(raw)
            if not 0.0 <= error_rate <= 1.0:
                raise ValueError("error_rate must be between 0 and 1")
        elif name == "latency_ms":
            latency_ms = int(raw)
            if latency_ms < 0:
                raise ValueError("latency_ms must be 0 or more")
        else:
            raise ValueError(f"unknown fault '{name}'")
    return FaultConfig(error_rate=error_rate, latency_ms=latency_ms)


def should_fail(config: FaultConfig, rng: random.Random | None = None) -> bool:
    if config.error_rate <= 0:
        return False
    return (rng or random).random() < config.error_rate