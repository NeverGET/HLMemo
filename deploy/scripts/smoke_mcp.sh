#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=deploy/scripts/common.sh
source "$SCRIPT_DIR/common.sh"
dc config --format json | python3 "$SCRIPT_DIR/probe.py" smoke
