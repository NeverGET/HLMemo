#!/usr/bin/env bash
# Local TLS transport + actual client-IP evidence, no new containers or ports.
set -euo pipefail
# shellcheck source=deploy/scripts/common.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
dc config --format json | python3 "$DEPLOY_DIR/scripts/check_edge.py"
