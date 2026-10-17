# ---- Stage 1: install dependencies -------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Stage 2: small runtime image ---------------------------------------------
FROM python:3.12-slim

# Baked in at build time so every image knows exactly which release it is.
#   docker build --build-arg APP_VERSION=v1.1.0 -t pesaguard:v1.1.0 .
# A deliberately bad release for Phase 5:
#   docker build --build-arg APP_VERSION=v1.2.0 --build-arg FAULT_MODE=error_rate=0.3 .
ARG APP_VERSION=v0.0.0-dev
ARG FAULT_MODE=none

ENV APP_VERSION=${APP_VERSION} \
    FAULT_MODE=${FAULT_MODE} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Run as an unprivileged user, never root.
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home app

WORKDIR /srv
COPY --from=builder /install /usr/local
COPY pesaguard ./pesaguard

USER 10001
EXPOSE 8000

# Container-level liveness check (ECS and docker compose both use it).
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "pesaguard.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]