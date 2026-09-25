#!/usr/bin/env bash
# libcheck.sh : re-run the deploy runner's post-cutover librarian check (remote-deploy.sh "R2 ... Sol 48/49")
# on the VM against the RUNNING containers, with the deployed checkout's own check_librarian.py:
# collect (librarian, with provider probe + heartbeat wait) + collect (api) + evaluate. Prints the
# per-task fallbacks line and RESULT librarian PASS|FAIL. Read-only; no key or token is printed.
S=/Users/cemalkurt/Projects/HLMemo/deploy/.local/127.0.0.1-2223
exec /usr/bin/ssh -F $S/ssh_config hlm-deploy 'set -e; cd /opt/hlmemo/app; export HLM_ENV_FILE=/etc/hlmemo/prod.env
source deploy/scripts/common.sh
d=$(mktemp -d); trap "rm -rf $d" EXIT
state=absent; [ -f /etc/hlmemo/llm.env ] && state=present
echo "checkout $(git rev-parse --short=12 HEAD); api image revision $(docker inspect --format "{{index .Config.Labels \"org.opencontainers.image.revision\"}}" "$(docker ps -q --filter label=com.docker.compose.service=api)" | cut -c1-12); llm.env=$state"
dc exec -T librarian python - collect --service librarian --probe --wait-heartbeat 45 < deploy/scripts/check_librarian.py > $d/l.json || true
dc exec -T api python - collect --service api < deploy/scripts/check_librarian.py > $d/a.json || true
python3 deploy/scripts/check_librarian.py evaluate --llm-env $state --librarian $d/l.json --api $d/a.json' </dev/null
