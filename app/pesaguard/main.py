"""PesaGuard payments API.

Run locally:  uvicorn pesaguard.main:app --reload   (from the app/ folder)
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .config import Settings, get_settings
from .faults import FaultConfig, InjectedFault, parse_fault_mode, should_fail
from .logs import configure_logging
from .models import (
    HealthResponse,
    Payment,
    PaymentRequest,
    PaymentStatus,
    ReadyResponse,
)
from .repository import (
    DynamoDBRepository,
    IdempotencyConflict,
    PaymentRepository,
    RepositoryUnavailable,
    fingerprint,
)

log = logging.getLogger("pesaguard")


def create_app(
    settings: Settings | None = None, repository: PaymentRepository | None = None
) -> FastAPI:
    """Build the app. Tests pass their own settings and an in-memory repository."""
    settings = settings or get_settings()
    # Parse now so a bad FAULT_MODE stops the container at startup.
    faults: FaultConfig = parse_fault_mode(settings.fault_mode)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        if app.state.repo is None:
            repo = DynamoDBRepository(
                settings.table_name, settings.aws_region, settings.dynamodb_endpoint
            )
            if settings.auto_create_table:
                repo.ensure_table()
            app.state.repo = repo
        log.info(
            "starting",
            extra={
                "version": settings.app_version,
                "color": settings.deploy_color,
                "fault": settings.fault_mode,
            },
        )
        yield

    app = FastAPI(title="PesaGuard", version=settings.app_version, lifespan=lifespan)
    app.state.repo = repository
    app.state.settings = settings

    def get_repo(request: Request) -> PaymentRepository:
        return request.app.state.repo

    # ---- middleware: request id + one structured log line per request -------
    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        response.headers["x-request-id"] = request_id
        response.headers["x-app-version"] = settings.app_version
        log.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "version": settings.app_version,
                "color": settings.deploy_color,
            },
        )
        return response

    @app.exception_handler(InjectedFault)
    async def injected_fault_handler(request: Request, exc: InjectedFault):
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    @app.exception_handler(RepositoryUnavailable)
    async def repo_unavailable_handler(request: Request, exc: RepositoryUnavailable):
        log.error("database unavailable", extra={"request_id": request.state.request_id})
        return JSONResponse(status_code=503, content={"detail": "temporarily unavailable"})

    def apply_faults() -> None:
        """Dependency on payment routes only. See faults.py."""
        if faults.latency_ms:
            time.sleep(faults.latency_ms / 1000)
        if should_fail(faults):
            raise InjectedFault()

    # ---- health ---------------------------------------------------------------
    @app.get("/health", response_model=HealthResponse)
    def health():
        """Liveness: the process is running. Never touches the database."""
        return HealthResponse(
            status="ok", version=settings.app_version, color=settings.deploy_color
        )

    @app.get("/ready", response_model=ReadyResponse)
    def ready(response: Response, repo: PaymentRepository = Depends(get_repo)):
        """Readiness: the app can do real work (database reachable)."""
        try:
            repo.ping()
            checks, status = {"database": "ok"}, "ready"
        except RepositoryUnavailable:
            checks, status = {"database": "unreachable"}, "not_ready"
            response.status_code = 503
        return ReadyResponse(status=status, version=settings.app_version, checks=checks)

    # ---- payments -------------------------------------------------------------
    @app.post(
        "/payments",
        response_model=Payment,
        status_code=201,
        dependencies=[Depends(apply_faults)],
    )
    def create_payment(
        body: PaymentRequest,
        response: Response,
        idempotency_key: str = Header(min_length=8, max_length=128),
        repo: PaymentRepository = Depends(get_repo),
    ):
        if body.amount > settings.max_amount:
            raise HTTPException(422, f"amount exceeds limit of {settings.max_amount}")

        payment = Payment(
            payment_id=str(uuid.uuid4()),
            amount=body.amount,
            currency=body.currency,
            phone_number=body.phone_number,
            reference=body.reference,
            status=PaymentStatus.COMPLETED,  # a real provider call goes here later
            created_at=datetime.now(UTC),
            processed_by_version=settings.app_version,
        )
        try:
            stored, created = repo.save_if_new(idempotency_key, fingerprint(body), payment)
        except IdempotencyConflict:
            raise HTTPException(
                409, "Idempotency-Key was already used with a different request"
            ) from None

        if not created:
            response.status_code = 200
            response.headers["Idempotent-Replayed"] = "true"
        return stored

    @app.get(
        "/payments/{payment_id}",
        response_model=Payment,
        dependencies=[Depends(apply_faults)],
    )
    def get_payment(payment_id: str, repo: PaymentRepository = Depends(get_repo)):
        payment = repo.get(payment_id)
        if payment is None:
            raise HTTPException(404, "payment not found")
        return payment

    return app


app = create_app()
