#!/usr/bin/env bash
# libcheck.sh [EXTRA_EVALUATE_ARGS...] : re-run the deploy runner's post-cutover librarian check on the
# VM against the RUNNING containers, with the deployed checkout's own check_librarian.py:
# collect (librarian, with provider probe + heartbeat wait) + collect (api) + evaluate.
#   R2 checkout:  libcheck.sh                       (R2 evaluate)
#   R3 checkout:  libcheck.sh --llm-env-file /etc/hlmemo/llm.env [--release r3]
#                 (the R3 cutover check / the install_llm_env.sh post-switch check, D-108 step 4)
# Prints the per-task fallbacks line and RESULT librarian PASS|FAIL. Read-only; no key or token is printed.
S=${HLM_STATE_DIR:-/Users/cemalkurt/Projects/HLMemo/deploy/.local/127.0.0.1-2223}   # HLM_STATE_DIR selects another host (prod R3 deploy)
extra=""; [ $# -gt 0 ] && extra=$(printf ' %q' "$@")
exec /usr/bin/ssh -F $S/ssh_config hlm-deploy 'set -e; cd /opt/hlmemo/app; export HLM_ENV_FILE=/etc/hlmemo/prod.env
source deploy/scripts/common.sh
d=$(mktemp -d); trap "rm -rf $d" EXIT
state=absent; [ -f /etc/hlmemo/llm.env ] && state=present
echo "checkout $(git rev-parse --short=12 HEAD); api image revision $(docker inspect --format "{{index .Config.Labels \"org.opencontainers.image.revision\"}}" "$(docker ps -q --filter label=com.docker.compose.service=api)" | cut -c1-12); llm.env=$state; release marker: $(grep -h "^HLM_ENV_RELEASE=" /etc/hlmemo/llm.env 2>/dev/null || echo none)"
dc exec -T librarian python - collect --service librarian --probe --wait-heartbeat 45 < deploy/scripts/check_librarian.py > $d/l.json || true
dc exec -T api python - collect --service api < deploy/scripts/check_librarian.py > $d/a.json || true
python3 deploy/scripts/check_librarian.py evaluate --llm-env $state --librarian $d/l.json --api $d/a.json'"$extra" </dev/null
