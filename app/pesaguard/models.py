"""Request and response shapes for the payments API."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class PaymentStatus(StrEnum):
    COMPLETED = "completed"


class PaymentRequest(BaseModel):
    # Whole shillings only. Mobile money works in whole units, and integers
    # avoid floating-point rounding errors with money.
    amount: int = Field(gt=0, description="Amount in whole Kenyan shillings")
    currency: Literal["KES"] = "KES"
    # Kenyan mobile number in international format, e.g. 254712345678.
    phone_number: str = Field(pattern=r"^254(7|1)\d{8}$")
    reference: str = Field(min_length=1, max_length=64, description="Merchant's own reference")


class Payment(BaseModel):
    payment_id: str
    amount: int
    currency: str
    phone_number: str
    reference: str
    status: PaymentStatus
    created_at: datetime
    # The version that processed the payment. Very useful during a canary:
    # you can see exactly which release handled a failed request.
    processed_by_version: str


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    color: str


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    version: str
    checks: dict[str, str]
