# Migration runner.
#
# Installs every service so one job can bring the whole database to head. Deployed as a
# Kubernetes Job (or a compose one-shot) that must complete before any service starts, so a
# service never boots against a schema it does not expect.
#
#   docker build -f infra/docker/migrate.Dockerfile -t marsool/migrate:dev .

ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv /opt/venv

WORKDIR /app

COPY libs/core libs/core
COPY services services
COPY scripts/migrate_all.py scripts/migrate_all.py

RUN pip install --no-cache-dir ./libs/core \
    && pip install --no-cache-dir \
        ./services/auth \
        ./services/user \
        ./services/merchant \
        ./services/catalog \
    && apt-get purge -y build-essential && apt-get autoremove -y

RUN groupadd --system --gid 1001 marsool \
    && useradd --system --uid 1001 --gid marsool marsool \
    && chown -R marsool:marsool /app
USER marsool

CMD ["python", "scripts/migrate_all.py"]
