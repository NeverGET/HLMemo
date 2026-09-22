#!/usr/bin/env bash
# G-D2: healthy services, external TLS routing and absence of database port publication.
set -euo pipefail
# shellcheck source=deploy/scripts/common.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
dc config --format json | python3 "$DEPLOY_DIR/scripts/check_tls.py"
