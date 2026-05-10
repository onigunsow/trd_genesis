FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

# uv installer (Astral)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv \
    && mv /root/.local/bin/uvx /usr/local/bin/uvx

WORKDIR /app

COPY pyproject.toml uv.lock* README.md /app/
RUN uv sync --no-install-project

COPY src/ /app/src/

# Install the project itself so [project.scripts] (e.g. `trading`) is on PATH.
RUN uv sync

# @MX:NOTE: SPEC-TRADING-016 REQ-016-1-2 — bake the git commit hash into the image.
# Healthcheck (`check_build_commit`) compares /app/.build_commit against the
# HOST_BUILD_COMMIT env var injected by compose at runtime; mismatch => fail + Telegram alert.
ARG BUILD_COMMIT=unknown
ENV BUILD_COMMIT=${BUILD_COMMIT}
RUN echo "${BUILD_COMMIT}" > /app/.build_commit

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH=/app/src

# Non-root user (uid:gid match host onigunsow 1000:1000)
RUN groupadd -g 1000 trading \
    && useradd -u 1000 -g 1000 -s /bin/bash -m trading \
    && mkdir -p /app/data /app/logs \
    && chown -R 1000:1000 /app /opt/venv

USER 1000:1000

CMD ["python", "-m", "trading.healthcheck"]
