#!/usr/bin/env bash
# Tier 1 sweep — run loadtest_traffic_db.py at increasing pool sizes to
# find the next throughput ceiling beyond the current 10-slot default.
#
# Usage:
#   DATABASE_URL=postgresql://... ./scripts/loadtest_traffic_db_sweep.sh
#   PYTHON=.venv/bin/python ./scripts/loadtest_traffic_db_sweep.sh
#
# Output: one report file with all phases for all pool sizes appended.
# Each section is prefixed with the WITNESS_DB_POOL_MAX in effect.
#
# The script does not change persistent state — pool size is just an
# env var honoured by ledger.py:_get_pool(). No app restart needed
# between runs (each script invocation creates a fresh pool).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="${PYTHON:-python}"
DURATION="${DURATION:-5}"
WARMUP="${WARMUP:-1}"
# Worker ramp covers below, at, and above each pool ceiling so the
# saturation point is visible in the numbers.
WORKERS="${WORKERS:-1,4,8,16,32,64,128,256}"
POOL_SIZES="${POOL_SIZES:-10 25 50 100}"

OUT="${OUT:-scripts/loadtest_traffic_db_sweep.results.txt}"

echo "=== Tier 1 sweep run: $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" | tee "$OUT"
echo "DATABASE_URL: ${DATABASE_URL:-<unset — using ledger.py default>}" | tee -a "$OUT"
echo "PYTHON: $PYTHON" | tee -a "$OUT"
echo "DURATION: ${DURATION}s, WARMUP: ${WARMUP}s, WORKERS: $WORKERS" | tee -a "$OUT"
echo "POOL_SIZES: $POOL_SIZES" | tee -a "$OUT"
echo "" | tee -a "$OUT"

for max_pool in $POOL_SIZES; do
    # min_size scales with max_size so the warmup phase doesn't pay
    # connection-establishment cost mid-run.
    min_pool=$(( max_pool / 4 ))
    if [ "$min_pool" -lt 2 ]; then min_pool=2; fi

    echo "### WITNESS_DB_POOL_MIN=$min_pool  WITNESS_DB_POOL_MAX=$max_pool ###" | tee -a "$OUT"
    WITNESS_DB_POOL_MIN="$min_pool" \
    WITNESS_DB_POOL_MAX="$max_pool" \
        "$PYTHON" scripts/loadtest_traffic_db.py \
            --duration "$DURATION" \
            --warmup "$WARMUP" \
            --workers "$WORKERS" \
        2>&1 | tee -a "$OUT"
    echo "" | tee -a "$OUT"
done

echo "=== Sweep complete. Results in $OUT ===" | tee -a "$OUT"
