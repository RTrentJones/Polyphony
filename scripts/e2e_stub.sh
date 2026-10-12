#!/usr/bin/env bash
# End-to-end run of the REAL app against the deterministic stub LLM
# (evals/stub_llm.py): upload -> extraction proposals -> commit -> voice
# indexing -> staged outline -> scene -> continuity, through the HTTP API, with
# no key and no quota. Used by CI (test.yml `e2e` job) and locally.
#
# Needs: a pgvector Postgres at $DATABASE_URL (empty DB), python deps installed.
# Writes eval-report-e2e-<book>.json and fails on the gate rules below.
set -euo pipefail
cd "$(dirname "$0")/.."

export LLM_PROVIDER=stub STUB_LLM_API_KEY=stub
export ADMIN_EMAIL=${ADMIN_EMAIL:-e2e-admin@example.com}
export ADMIN_PASSWORD=${ADMIN_PASSWORD:-e2e_admin_pw_12345}
export EVAL_ADMIN_EMAIL=$ADMIN_EMAIL EVAL_ADMIN_PASSWORD=$ADMIN_PASSWORD
export EVAL_BASE_URL=${EVAL_BASE_URL:-http://localhost:8000}
export EVAL_JUDGE_PROVIDER=stub EVAL_RPM=600 DEBUG=true
export EVAL_CACHE_DIR=$(mktemp -d)
PY=${PYTHON:-python}

$PY -m evals.stub_llm --port 8099 > e2e-stub.log 2>&1 &
STUB=$!
$PY -m app.migrate
$PY -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > e2e-app.log 2>&1 &
APP=$!
trap 'kill $APP $STUB 2>/dev/null || true' EXIT
for i in $(seq 1 60); do
  curl -fsS http://localhost:8000/health >/dev/null 2>&1 && break
  sleep 1
done

rc=0
$PY -m evals.run --book cel --steps extraction,ingestion,outline,prose \
  --out eval-report-e2e-cel.json || rc=1
$PY -m evals.gate eval-report-e2e-cel.json \
  "outline.cast_fidelity>=0.9" "extraction.score>0" \
  "ingestion.n_characters>0" "prose.words>0" || rc=1

$PY -m evals.run --book dracula --steps all --out eval-report-e2e-dracula.json || rc=1
$PY -m evals.gate eval-report-e2e-dracula.json \
  "extraction.score>0" "outline.n_nodes>0" "prose.words>0" \
  "attribution.generated>0" "continuity.n_injected>0" || rc=1
exit $rc
