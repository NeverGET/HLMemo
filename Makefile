# HLMemo developer targets. Requires uv (https://docs.astral.sh/uv/) and Docker.
.PHONY: up down migrate test lint sync db

COMPOSE ?= docker compose
UV ?= uv

sync:            ## install locked deps into .venv
	$(UV) sync --frozen

db:              ## start only Postgres
	$(COMPOSE) up -d --wait db

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

lint:
	$(UV) run ruff check src tests alembic
	$(UV) run ruff format --check src tests alembic
