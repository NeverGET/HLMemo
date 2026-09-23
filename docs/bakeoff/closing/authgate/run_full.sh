#!/bin/bash
# usage: run_full.sh <n>
cd /Users/cemalkurt/Projects/HLMemo-bake/fix-auth
LOG=/Users/cemalkurt/Projects/HLMemo/docs/bakeoff/closing/authgate/A2-full-run$1.log
IGN="--ignore=tests/integration/test_g2_budget.py --ignore=tests/integration/test_g2_wire.py --ignore=tests/integration/test_g3_recall.py --ignore=tests/integration/test_g4_latency.py --ignore=tests/integration/test_g7_clients.py --ignore=tests/integration/test_g7_server.py --ignore=tests/integration/test_worker_restart.py"
echo "\$ HLM_MODELS_DIR=.../HLMemo/models HLM_TEST_DSN=.../hlm_authv uv run --frozen pytest tests/unit tests/fixtures tests/integration -q -p no:cacheprovider --durations=15 $IGN   (fix-auth @ $(git rev-parse --short HEAD))" > $LOG
echo "start $(date +%T)" >> $LOG
S=$(date +%s)
HLM_MODELS_DIR=/Users/cemalkurt/Projects/HLMemo/models HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_authv uv run --frozen pytest tests/unit tests/fixtures tests/integration -q -p no:cacheprovider --durations=15 $IGN >> $LOG 2>&1
rc=$?
echo "rc=$rc end $(date +%T) wall=$(( $(date +%s)-S ))s" >> $LOG
