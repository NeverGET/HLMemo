#!/bin/bash
# Missing-models readiness: recreate api with an empty tmpfs over /app/models, check /ready and restarts.
SP=/private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/3aacf803-644a-4ddb-83b0-6cf474ae4519/scratchpad; E=$SP/judge-auth-env
D=/Users/cemalkurt/Projects/HLMemo/docs/bakeoff/closing/authgate
cd /Users/cemalkurt/Projects/HLMemo-bake/fix-auth || exit 1
export HLM_APP_ENV_FILE=$E/app.env HLM_API_ENV_FILE=$E/api.env HLM_DB_ENV_FILE=$E/db.env HLM_BACKUP_ENV_FILE=$E/backup.env
DC=(docker compose -p judge-auth -f deploy/compose.prod.yaml -f "$D/B8-nomodels.override.yaml" --env-file "$E/prod.env")
ready() { docker exec judge-auth-api-1 python -c "import urllib.request,urllib.error
try:
  r=urllib.request.urlopen('http://127.0.0.1:8765/ready',timeout=8); print(r.status, r.read()[:300].decode())
except urllib.error.HTTPError as e: print(e.code, e.read()[:1200].decode())"; }
echo "\$ docker compose -p judge-auth -f deploy/compose.prod.yaml -f B8-nomodels.override.yaml --env-file <env> up -d --no-deps --force-recreate api"
"${DC[@]}" up -d --no-deps --force-recreate api 2>&1 | tail -3
T=$(date +%s)
echo "## $(date -u +%T) /app/models entries in container: $(docker exec judge-auth-api-1 ls -A /app/models | wc -l)"
sleep 15; echo "## +15s internal /ready:"; ready
echo "## edge /ready: $(curl -sk -o /dev/null -w '%{http_code}' https://localhost:28443/ready) ; edge /health: $(curl -sk https://localhost:28443/health)"
sleep 50
echo "## +$(( $(date +%s)-T ))s inspect: $(docker inspect judge-auth-api-1 --format 'status={{.State.Status}} RestartCount={{.RestartCount}} OOMKilled={{.State.OOMKilled}} health={{.State.Health.Status}} StartedAt={{.State.StartedAt}}')"
echo "## internal /ready again:"; ready
echo "## api log (non-ready lines):"; docker logs judge-auth-api-1 2>&1 | grep -v '/ready HTTP' | cut -c1-250 | tail -15
echo "## restore: recreate api without override"
docker compose -p judge-auth -f deploy/compose.prod.yaml --env-file "$E/prod.env" up -d --no-deps --force-recreate --wait api 2>&1 | tail -2
echo "## after restore: $(docker inspect judge-auth-api-1 --format 'RestartCount={{.RestartCount}} health={{.State.Health.Status}}') edge /ready=$(curl -sk -o /dev/null -w '%{http_code}' https://localhost:28443/ready)"
