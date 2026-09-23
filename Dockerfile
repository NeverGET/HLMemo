# syntax=docker/dockerfile:1.7
# HLMemo runtime image (PHASE0-SPEC §6). Multi-stage, non-root, uv-locked.
#
# Stages (build a specific one with `--target`; compose pins `target:` per service because the
# LAST stage — `test` — is what a bare `docker build .` produces):
#   builder       locked runtime deps + the project (`uv sync --no-dev`)
#   builder-test  the same venv plus the locked dev group (pytest, ruff) — never shipped
#   models        `/app/models`: pinned e5 model downloaded and hash-verified at build time;
#                 opt-out with BAKE_MODELS=0 requires an explicit development bind mount
#   runtime       api / worker / migrate image (`hlmemo:dev`)
#   test          runtime + dev deps + tests (`hlmemo:test`, compose profile `test`)
#
# Bind-mount vs bake: `/ready` (server/app.py) verifies the model files against models.lock either
# way, so a container whose models are missing or wrong is never "healthy".

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

# Cache the budget tokenizer at build time; runtime has no network dependency.
ENV TIKTOKEN_CACHE_DIR=/app/tiktoken-cache
RUN /app/.venv/bin/python -c "from hlmemo.core.budget import Meter; Meter().count_text('ready')"

# Locked dev dependencies (pytest, ruff) for the `test` stage only.
FROM builder AS builder-test
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable

# Model assets. BAKE_MODELS=1 downloads intfloat/multilingual-e5-small at the models.lock revision
# (network at build time) and fails the build if any hashed file differs from models.lock.
FROM builder AS models
ARG BAKE_MODELS=1
ENV HF_HUB_DISABLE_TELEMETRY=1 HF_HOME=/root/.cache/huggingface
COPY models.lock ./
RUN --mount=type=cache,target=/root/.cache/huggingface <<'SH'
set -eu
mkdir -p /app/models
if [ "$BAKE_MODELS" = "1" ]; then
  /app/.venv/bin/python - <<'PY'
from pathlib import Path
from hlmemo.core.embedder import download_model, model_hashes
d = download_model("/app/models/multilingual-e5-small")
lock = {
    line.split(":", 1)[0].strip(): line.split("sha256:", 1)[1].split()[0]
    for line in Path("models.lock").read_text().splitlines()
    if ": sha256:" in line
}
bad = {k: v for k, v in model_hashes(d).items() if lock.get(k) != v}
if bad:
    raise SystemExit(f"baked model differs from models.lock: {bad}")
print(f"baked {d}: hashes match models.lock")
PY
  rm -rf /app/models/*/.cache
fi
SH

FROM python:3.12.14-slim-bookworm AS runtime
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HLM_PROFILES_DIR=/app/profiles HLM_MODELS_DIR=/app/models TIKTOKEN_CACHE_DIR=/app/tiktoken-cache \
    ORT_DISABLE_TELEMETRY=1
RUN groupadd --system --gid 10001 hlm && useradd --system --uid 10001 --gid hlm --home /app --shell /usr/sbin/nologin hlm
WORKDIR /app
COPY --from=builder --chown=hlm:hlm /app/.venv /app/.venv
COPY --from=builder --chown=hlm:hlm /app/tiktoken-cache /app/tiktoken-cache
COPY --from=models --chown=hlm:hlm /app/models /app/models
COPY --chown=hlm:hlm alembic.ini models.lock hlm.example.toml ./
COPY --chown=hlm:hlm alembic ./alembic
COPY --chown=hlm:hlm profiles ./profiles
USER hlm
EXPOSE 8765
CMD ["python", "-m", "hlmemo.server.app"]

# Integration tests against the compose stack (`docker compose --profile test run --rm test`).
FROM runtime AS test
USER root
COPY --from=builder-test --chown=hlm:hlm /app/.venv /app/.venv
COPY --chown=hlm:hlm pyproject.toml ./
COPY --chown=hlm:hlm tests ./tests
USER hlm
ENTRYPOINT ["python", "/app/tests/bootstrap_test_db.py"]
CMD ["pytest", "-q", "tests/integration"]
