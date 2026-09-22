# syntax=docker/dockerfile:1.7
# HLMemo runtime image (PHASE0-SPEC §6). Multi-stage, non-root, uv-locked.
# NOTE: embedding model files (models.lock revision) are NOT baked yet — a later task adds that layer.

FROM python:3.12.14-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.12.14-slim-bookworm AS runtime
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HLM_PROFILES_DIR=/app/profiles
RUN groupadd --system --gid 10001 hlm && useradd --system --uid 10001 --gid hlm --home /app --shell /usr/sbin/nologin hlm
WORKDIR /app
COPY --from=builder --chown=hlm:hlm /app/.venv /app/.venv
COPY --chown=hlm:hlm alembic.ini ./
COPY --chown=hlm:hlm alembic ./alembic
COPY --chown=hlm:hlm profiles ./profiles
COPY --chown=hlm:hlm hlm.example.toml ./
COPY --chown=hlm:hlm tests ./tests
USER hlm
EXPOSE 8765
CMD ["python", "-m", "hlmemo.server.app"]
