# HLMemo developer targets. Requires uv (https://docs.astral.sh/uv/) and Docker.
.PHONY: up down down-v migrate models test test-compose test-o2 test-g7 test-g8 smoke lint sync db

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

# O2: worker crash/restart (codex review 09 §4). Opt-in and slow (~LEASE_SECONDS + warm-up): it
# brings up an ISOLATED compose project (`hlmemo-o2`, own db + volume on a free loopback port),
# lets the single worker kill itself mid-commit and asserts Docker restarts it and it reclaims the
# job. Never touches the dev stack or `HLM_TEST_DSN`.
test-o2:
	HLM_O2_RESTART=1 $(UV) run --frozen pytest -q -s -p no:cacheprovider tests/integration/test_worker_restart.py

# CC-5 release-blocking live gate (G-LIVE-A): every LLM task fixture against the real provider for
# PROFILE and again for FALLBACK, REPS reps, aborted as FAIL above MAX_USD (a runaway guard).
PROFILE ?= openrouter
FALLBACK ?= openrouter-luna
REPS ?= 3
MAX_USD ?= 5
.PHONY: gate-live
gate-live:
	$(UV) run --frozen python eval/live/run.py --profile $(PROFILE) --fallback $(FALLBACK) --reps $(REPS) --max-usd $(MAX_USD)

# Release gate (D-063): G3 recall, G4 latency and G-L3 (queries timed DURING 100 writes through the
# real api process, librarian process stalled/503) on a DISPOSABLE clone of the loaded G3 world.
# Release-blocking at R2 and before Phase 5 bulk imports. G-L3 writes into the clone: drop it after.
.PHONY: gate-release
gate-release:
	@test -n "$(HLM_TEST_DSN)" || { echo 'Set HLM_TEST_DSN to a disposable clone of hlm_retr (never hlm/hlm_test)' >&2; exit 1; }
	HLM_GL3=1 $(UV) run --frozen pytest -q -s -p no:cacheprovider tests/integration/test_g3_recall.py tests/integration/test_g4_latency.py tests/integration/test_gl3_llm_down.py

# G8: gitleaks over the history + secret paths untracked (host only, no DB)
test-g8:
	$(UV) run --frozen pytest -q -p no:cacheprovider tests/integration/test_g8_secrets.py

lint:
	$(UV) run ruff check src tests alembic
	$(UV) run ruff format --check src tests alembic

# Production tooling. DEPLOY_ENV selects prod.env with sibling app/api/db/backup.env files.
# Keep all real env files outside Git; BAKE_* isolates local drills.
DEPLOY_ENV ?= $(CURDIR)/deploy/.env.prod
DEPLOY = HLM_ENV_FILE="$(DEPLOY_ENV)" bash deploy/scripts/stack.sh
.PHONY: deploy-config deploy-build deploy-up deploy-down deploy-status deploy-logs deploy-smoke deploy-backup deploy-restore deploy-drill deploy-remote deploy-tf-validate deploy-lint

deploy-config:
	$(DEPLOY) config -q

deploy-build:
	$(DEPLOY) build api

deploy-up:
	$(DEPLOY) up -d --build --wait --wait-timeout 300

deploy-down:
	$(DEPLOY) down

deploy-status:
	$(DEPLOY) ps

deploy-logs:
	$(DEPLOY) logs --tail 100

deploy-smoke:
	HLM_ENV_FILE="$(DEPLOY_ENV)" bash deploy/scripts/smoke_mcp.sh

deploy-backup:
	HLM_ENV_FILE="$(DEPLOY_ENV)" bash deploy/backup/backup.sh

deploy-restore:
	@test -n "$(DUMP)" || (echo 'Set DUMP=/absolute/path/file.dump' >&2; exit 1)
	HLM_ENV_FILE="$(DEPLOY_ENV)" bash deploy/backup/restore.sh "$(DUMP)" --yes

deploy-drill:
	HLM_ENV_FILE="$(DEPLOY_ENV)" bash deploy/scripts/drill_backup_restore.sh

deploy-remote:
	@test -n "$(HOST)" -a -n "$(REF)" || (echo 'Set HOST=hlmdeploy@server REF=git-ref [REPO=git-url]' >&2; exit 1)
	bash deploy/scripts/deploy.sh "$(HOST)" "$(REF)" $(if $(REPO),"$(REPO)")

deploy-tf-validate:
	terraform -chdir=deploy/terraform/hetzner init -backend=false
	terraform -chdir=deploy/terraform/hetzner validate
	terraform fmt -check -recursive deploy/terraform

deploy-lint:
	shellcheck deploy/backup/*.sh deploy/scripts/*.sh
	gitleaks dir deploy --no-banner
