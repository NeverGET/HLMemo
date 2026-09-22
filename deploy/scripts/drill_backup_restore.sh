#!/usr/bin/env bash
# Deliberately destructive integration gate, restricted to a local disposable bake stack.
# PostgreSQL variables below expand inside the container, never in the host shell.
# shellcheck disable=SC2016
set -euo pipefail
umask 077
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=deploy/scripts/common.sh
source "$SCRIPT_DIR/common.sh"
export COMPOSE_PROJECT
if [[ ${HLM_ALLOW_DESTRUCTIVE_DRILL:-} != 1 || $COMPOSE_PROJECT != bake-* ]]; then
    echo 'Refusing: set HLM_ALLOW_DESTRUCTIVE_DRILL=1 and use a disposable BAKE_PROJECT=bake-*.' >&2
    exit 64
fi
dc config --format json | python3 -c '
import json, os, sys, urllib.parse
c = json.load(sys.stdin)
e = c["services"]["caddy"].get("environment", {})
u = os.environ.get("HLM_SMOKE_URL", "https://" + os.environ.get("HLM_DOMAIN", e.get("HLM_DOMAIN", "localhost")))
if urllib.parse.urlsplit(u).hostname not in {"localhost", "127.0.0.1", "::1"}:
    sys.exit("Refusing destructive drill: HTTPS endpoint must be localhost")
if c.get("name") != os.environ["COMPOSE_PROJECT"]:
    sys.exit("Refusing destructive drill: compose project mismatch")
'
# A remote Docker context could otherwise destroy a remote bake stack despite a local URL.
if [[ -n ${DOCKER_CONTEXT:-} ]]; then
    endpoint=$(docker context inspect "$DOCKER_CONTEXT" --format '{{.Endpoints.docker.Host}}')
elif [[ -n ${DOCKER_HOST:-} ]]; then
    endpoint=$DOCKER_HOST
else
    endpoint=$(docker context inspect --format '{{.Endpoints.docker.Host}}')
fi
if [[ $endpoint != unix://* ]]; then
    echo 'Refusing destructive drill: Docker must use a local Unix socket.' >&2
    exit 64
fi
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
dc config --format json | python3 "$SCRIPT_DIR/probe.py" write --state "$temporary/state.json"
dump=$("$DEPLOY_DIR/backup/backup.sh")
dc stop caddy api worker
dc exec -T db sh -eu -c '
    case "$POSTGRES_DB" in postgres|template0|template1|"") exit 64;; esac
    dropdb --username="$POSTGRES_USER" --force -- "$POSTGRES_DB"
    createdb --username="$POSTGRES_USER" --owner="$POSTGRES_USER" --template=template0 -- "$POSTGRES_DB"
    test "$(psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --tuples-only --no-align --command="SELECT count(*) FROM information_schema.tables WHERE table_schema = '\''public'\''")" = 0
'
echo 'Isolated database wiped; public table count is zero.'
"$DEPLOY_DIR/backup/restore.sh" "$dump" --yes
dc config --format json | python3 "$SCRIPT_DIR/probe.py" read --state "$temporary/state.json"
