# Shared Dockerfile for every Python service.
#
# One file rather than eight near-identical ones: the services differ only in which
# package is installed and which module is run, both passed as build arguments. A drift in
# base image, user, or health check across services is a class of bug this removes.
#
# Build from the repository root so the shared library is in the build context:
#   docker build -f infra/docker/service.Dockerfile \
#     --build-arg SERVICE_PATH=services/auth \
#     --build-arg SERVICE_MODULE=marsool_auth.main:app \
#     -t marsool/auth:dev .

ARG PYTHON_VERSION=3.12

# ----------------------------------------------------------------------------- builder
FROM python:${PYTHON_VERSION}-slim AS builder

ARG SERVICE_PATH

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# build-essential is needed to compile any wheel without a manylinux build; it stays in
# the builder stage and never reaches the runtime image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Dependency manifests are copied before sources so that a code-only change reuses the
# cached dependency layer.
COPY libs/core/pyproject.toml libs/core/pyproject.toml
COPY ${SERVICE_PATH}/pyproject.toml ${SERVICE_PATH}/pyproject.toml

COPY libs/core libs/core
COPY ${SERVICE_PATH} ${SERVICE_PATH}

RUN pip install --no-cache-dir ./libs/core \
    && pip install --no-cache-dir ./${SERVICE_PATH}

# ----------------------------------------------------------------------------- runtime
FROM python:${PYTHON_VERSION}-slim AS runtime

ARG SERVICE_PATH
ARG SERVICE_MODULE
ARG SERVICE_PORT=8000

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SERVICE_MODULE=${SERVICE_MODULE} \
    SERVICE_PORT=${SERVICE_PORT}

# curl is present solely for the container health check.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 marsool \
    && useradd --system --uid 1001 --gid marsool --create-home marsool

COPY --from=builder /opt/venv /opt/venv
# Migrations are not importable package data, so they are copied explicitly; the container
# doubles as the migration runner in deployment jobs.
COPY --from=builder /build/${SERVICE_PATH}/alembic.ini /app/alembic.ini
COPY --from=builder /build/${SERVICE_PATH}/migrations /app/migrations

WORKDIR /app
USER marsool
EXPOSE ${SERVICE_PORT}

# Readiness, not liveness: the check should fail while a dependency is unreachable.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${SERVICE_PORT}/health/ready" || exit 1

# One worker per container: replicas are scaled by the orchestrator, which gives finer
# control and clearer per-instance metrics than in-process workers.
CMD ["sh", "-c", "exec uvicorn \"$SERVICE_MODULE\" --host 0.0.0.0 --port \"$SERVICE_PORT\" --no-access-log --proxy-headers"]
