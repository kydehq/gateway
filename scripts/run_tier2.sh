#!/usr/bin/env bash
# Tier 2 end-to-end run: mock upstream + proxy + load client.
#
# Brings up mock_upstream.py and a proxy instance, runs the realistic-
# mix and chat-only scenarios, then tears everything down. Captures all
# output to scripts/loadtest_traffic_proxy.results.txt.
#
# Prereqs on the server you run this on:
#   - Postgres reachable at DATABASE_URL (default: docker-compose's PG)
#   - The kyde package installed in PYTHON's venv (pip install -e .)
#   - Ports 8000 (proxy) and 9000 (mock) free
#
# Usage:
#   DATABASE_URL=postgresql://... ./scripts/run_tier2.sh
#   PYTHON=.venv/bin/python ./scripts/run_tier2.sh
#   KYDE_DB_POOL_MAX=50 ./scripts/run_tier2.sh   # test a larger pool

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="${PYTHON:-python}"
PROXY_PORT="${PROXY_PORT:-8000}"
MOCK_PORT="${MOCK_PORT:-9000}"
DURATION="${DURATION:-5}"
WARMUP="${WARMUP:-1}"
WORKERS="${WORKERS:-1,4,16,64,128}"
OUT="${OUT:-scripts/loadtest_traffic_proxy.results.txt}"
LOGDIR="${LOGDIR:-scripts/.tier2-logs}"

mkdir -p "$LOGDIR"
MOCK_LOG="$LOGDIR/mock_upstream.log"
PROXY_LOG="$LOGDIR/proxy.log"
MOCK_PID=""
PROXY_PID=""

cleanup() {
    set +e
    if [ -n "${PROXY_PID:-}" ]; then
        echo "[run_tier2] stopping proxy pid=$PROXY_PID" | tee -a "$OUT"
        kill "$PROXY_PID" 2>/dev/null
        wait "$PROXY_PID" 2>/dev/null
    fi
    if [ -n "${MOCK_PID:-}" ]; then
        echo "[run_tier2] stopping mock pid=$MOCK_PID" | tee -a "$OUT"
        kill "$MOCK_PID" 2>/dev/null
        wait "$MOCK_PID" 2>/dev/null
    fi
}
trap cleanup EXIT

# ---- Sanity: are the ports free? ----
for port in "$PROXY_PORT" "$MOCK_PORT"; do
    if ss -ltn "( sport = :$port )" 2>/dev/null | grep -q LISTEN; then
        echo "[run_tier2] FATAL: port $port already in use" >&2
        exit 1
    fi
done

# ---- Header ----
echo "=== Tier 2 run: $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" | tee "$OUT"
echo "PYTHON: $PYTHON" | tee -a "$OUT"
echo "DATABASE_URL: ${DATABASE_URL:-<unset>}" | tee -a "$OUT"
echo "KYDE_DB_POOL_MIN/MAX: ${KYDE_DB_POOL_MIN:-<default>} / ${KYDE_DB_POOL_MAX:-<default>}" | tee -a "$OUT"
echo "PROXY_PORT=$PROXY_PORT  MOCK_PORT=$MOCK_PORT" | tee -a "$OUT"
echo "DURATION=${DURATION}s  WARMUP=${WARMUP}s  WORKERS=$WORKERS" | tee -a "$OUT"
echo "" | tee -a "$OUT"

# ---- Start mock upstream ----
echo "[run_tier2] starting mock_upstream on :$MOCK_PORT" | tee -a "$OUT"
"$PYTHON" scripts/mock_upstream.py --port "$MOCK_PORT" \
    > "$MOCK_LOG" 2>&1 &
MOCK_PID=$!
sleep 1

# ---- Start proxy ----
echo "[run_tier2] starting proxy on :$PROXY_PORT" | tee -a "$OUT"
KYDE_CONFIG=scripts/loadtest_config.yaml \
    "$PYTHON" -m uvicorn kyde.server:app \
    --host 127.0.0.1 --port "$PROXY_PORT" \
    --log-level warning \
    > "$PROXY_LOG" 2>&1 &
PROXY_PID=$!

# Wait for proxy to bind. Up to 30 seconds — startup pays migration cost
# on a fresh DB.
echo "[run_tier2] waiting for proxy /healthz ..." | tee -a "$OUT"
for i in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:$PROXY_PORT/healthz" >/dev/null 2>&1; then
        echo "[run_tier2] proxy ready after ${i}s" | tee -a "$OUT"
        break
    fi
    sleep 1
done

if ! curl -sf "http://127.0.0.1:$PROXY_PORT/healthz" >/dev/null 2>&1; then
    echo "[run_tier2] FATAL: proxy never became ready. Tail of proxy log:" >&2
    tail -30 "$PROXY_LOG" >&2
    exit 1
fi
echo "" | tee -a "$OUT"

# ---- Scenario A: realistic mix (1:10 chat:embedding) ----
echo "### Scenario A — realistic mix (1:10 chat:embedding) ###" | tee -a "$OUT"
"$PYTHON" scripts/loadtest_traffic_proxy.py \
    --proxy-url "http://127.0.0.1:$PROXY_PORT" \
    --duration "$DURATION" --warmup "$WARMUP" --workers "$WORKERS" \
    2>&1 | tee -a "$OUT"
echo "" | tee -a "$OUT"

# ---- Scenario B: chat-only (metering overhead baseline) ----
echo "### Scenario B — chat-only baseline ###" | tee -a "$OUT"
"$PYTHON" scripts/loadtest_traffic_proxy.py \
    --proxy-url "http://127.0.0.1:$PROXY_PORT" \
    --duration "$DURATION" --warmup "$WARMUP" --workers "$WORKERS" \
    --chat-only \
    2>&1 | tee -a "$OUT"
echo "" | tee -a "$OUT"

echo "=== Tier 2 complete. Results in $OUT ===" | tee -a "$OUT"
echo "Mock log: $MOCK_LOG"
echo "Proxy log: $PROXY_LOG"
