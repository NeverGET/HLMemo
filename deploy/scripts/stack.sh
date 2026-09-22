#!/usr/bin/env bash
# Safe Compose wrapper; refuses the existing hlmemo development project.
set -euo pipefail
# shellcheck source=deploy/scripts/common.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
dc "$@"
