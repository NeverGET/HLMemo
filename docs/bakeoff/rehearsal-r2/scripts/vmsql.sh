#!/usr/bin/env bash
# vmsql.sh < file.sql : run SQL (stdin) with psql inside the VM's db container (read-mostly diagnostics).
S=/Users/cemalkurt/Projects/HLMemo/deploy/.local/127.0.0.1-2223
exec /usr/bin/ssh -F $S/ssh_config hlm-deploy 'docker exec -i "$(docker ps -q --filter label=com.docker.compose.service=db)" sh -c '"'"'psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off'"'"
