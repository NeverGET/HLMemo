# HLMemo developer targets. Requires uv (https://docs.astral.sh/uv/) and Docker.
.PHONY: up down down-v migrate models test test-compose test-g7 test-g8 smoke lint sync db

COMPOSE ?= docker compose
UV ?= uv

sync:            ## install locked deps into .venv
	$(UV) sync --frozen

db:              ## start only Postgres
	$(COMPOSE) up -d --wait db

# Optional host model download. Compose bakes assets by default; HLM_BAKE_MODELS=0 requires
# an explicit development bind-mount override (see compose.yaml).
models:          ## download the pinned embedding model into ./models and verify it against models.lock
	$(UV) run --frozen python -c "from hlmemo.core.embedder import download_model; print(download_model())"
	$(UV) run --frozen hlm doctor --models

up:              ## full local stack (db -> migrate -> api, worker)
	$(COMPOSE) up -d --build --wait db migrate api worker

down:            ## stop stack, keep pgdata volume (use `down-v` to wipe)
	$(COMPOSE) down --remove-orphans

down-v:
	$(COMPOSE) down --remove-orphans --volumes

migrate: db      ## apply phase0 migrations from the host against the compose db
	HLM_DB_DSN=$${HLM_DB_DSN:-postgresql://hlm:hlm@127.0.0.1:$${HLM_DB_PORT:-5432}/hlm} $(UV) run alembic upgrade phase0@head

test:            ## integration tests (starts compose db if needed)
	$(UV) run pytest -q tests/integration

test-compose:    ## integration tests inside the `test` image against the compose stack
	$(COMPOSE) --profile test run --rm --build test

# G7: real coding CLIs against the running stack. Needs HLM_DEVICE_TOKEN (trusted device, write on
# HLM_PROJECT, default g7-smoke) — see tests/smoke/README.md. `smoke` runs the scripts directly.
test-g7:
	HLM_PROJECT=$${HLM_PROJECT:-g7-smoke} $(UV) run --frozen pytest -q -s -p no:cacheprovider tests/integration/test_g7_clients.py

smoke:
	HLM_PROJECT=$${HLM_PROJECT:-g7-smoke} bash tests/smoke/claude.sh
	HLM_PROJECT=$${HLM_PROJECT:-g7-smoke} bash tests/smoke/codex.sh
	HLM_PROJECT=$${HLM_PROJECT:-g7-smoke} bash tests/smoke/agy.sh

# G8: gitleaks over the history + secret paths untracked (host only, no DB)
test-g8:
	$(UV) run --frozen pytest -q -p no:cacheprovider tests/integration/test_g8_secrets.py

lint:
	$(UV) run ruff check src tests alembic
	$(UV) run ruff format --check src tests alembic
