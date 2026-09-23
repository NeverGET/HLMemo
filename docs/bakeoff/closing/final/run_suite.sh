#!/bin/bash
# usage: run_suite.sh <n>   (neutral verifier; exact suite from the brief)
cd /Users/cemalkurt/Projects/HLMemo-bake/fix-auth
L=/Users/cemalkurt/Projects/HLMemo/docs/bakeoff/closing/final/A1-full-run$1.log
IGN="--ignore=tests/integration/test_g3_recall.py --ignore=tests/integration/test_g4_latency.py --ignore=tests/integration/test_g2_budget.py --ignore=tests/integration/test_g7_clients.py --ignore=tests/integration/test_worker_restart.py"
echo "\$ HLM_MODELS_DIR=.../HLMemo/models HLM_TEST_DSN=.../hlm_fin uv run --frozen pytest tests/unit tests/fixtures tests/integration -q -p no:cacheprovider $IGN   (fix-auth @ $(git rev-parse --short HEAD))" > $L
echo "start $(date +%T)" >> $L; S=$(date +%s)
HLM_MODELS_DIR=/Users/cemalkurt/Projects/HLMemo/models HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_fin uv run --frozen pytest tests/unit tests/fixtures tests/integration -q -p no:cacheprovider $IGN >> $L 2>&1
rc=$?
echo "rc=$rc end $(date +%T) wall=$(( $(date +%s)-S ))s" >> $L
