# Load testing the per-agent traffic metering

Execution runbook. Procedure-only — for background on *what* we're
testing and why, read `scripts/README.md` and
`scripts/loadtest_traffic_db.results.md`.

Time budget: ~10 min of setup + ~10 min of runs. Output files end up in
`scripts/` and are safe to share back.

---

## 0. Before you start

- You're on the branch with the load-test scripts. Confirm:
  ```bash
  git log --oneline | grep -E "tier 1|tier 2|env-configurable DB pool" | head -3
  ```
  You should see `f439643`, `3763f61`, `ad78ce0`.
- Host has Python 3.12+, Docker (or a local Postgres 16), git.

## 1. Set up the environment

```bash
# 1a. Clone / pull
git fetch origin feature/ui-messaging
git checkout feature/ui-messaging
git pull

# 1b. Python venv with the package installed editable
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .

# 1c. Bring up Postgres (skip if you already have one)
docker compose up -d postgres
# … or any Postgres 16 reachable at the DATABASE_URL below.
```

Pick one `DATABASE_URL` and export it for every run below:

```bash
# The dev DB shipped by docker-compose:
export DATABASE_URL='postgresql://witness:witness-dev-only@localhost:5432/witness'
# OR — if you'd rather not pollute the dev DB, use the test DB:
export DATABASE_URL='postgresql://witness:witness-dev-only@localhost:5432/witness_test'
```

> The scripts append rows to `agent_traffic_meters`,
> `agent_traffic_mode_history`, and (for Tier 2) `ledger`. They never
> drop or truncate. Cleanup commands at the bottom of this file.

## 2. Run 1 — Tier 1 baseline (≈ 1 min)

DB-only stress at the default 10-slot pool. Establishes "is the
UPSERT+lookup itself the bottleneck?"

```bash
.venv/bin/python scripts/loadtest_traffic_db.py --duration 5
```

Output goes to stdout. Look for the table — workers / ops/sec /
p50 / p95 / p99. You should see throughput plateau around 4 workers and
p99 climb linearly with worker count above 10. That's connection-pool
starvation — the documented baseline finding.

**Capture:** redirect stdout to a file you'll share back:

```bash
.venv/bin/python scripts/loadtest_traffic_db.py --duration 5 \
  | tee scripts/run1_tier1_baseline.$(hostname).txt
```

## 3. Run 2 — Tier 1 pool sweep (≈ 3 min)

Re-runs Tier 1 at pool sizes {10, 25, 50, 100}. Tells us where the
*next* ceiling is and which pool value to pick for production.

```bash
PYTHON=.venv/bin/python ./scripts/loadtest_traffic_db_sweep.sh
```

The wrapper writes its own results file at
`scripts/loadtest_traffic_db_sweep.results.txt`. It will be overwritten
on re-runs — copy it aside if you want to keep history:

```bash
cp scripts/loadtest_traffic_db_sweep.results.txt \
   scripts/run2_pool_sweep.$(hostname).txt
```

Look for:
- The pool size at which throughput stops scaling. That's your
  recommended `KYDE_DB_POOL_MAX`.
- p99 should stay flat at that pool size as workers climb — if it
  doesn't, you're now hitting PK or Postgres CPU rather than the pool.

## 4. Run 3 — Tier 2 with the default pool (≈ 3 min)

End-to-end through the proxy with a local mock upstream. The number
that answers "how much latency does the proxy add per request, with
metering on?"

Free ports 8000 and 9000 first if anything else uses them.

```bash
PYTHON=.venv/bin/python ./scripts/run_tier2.sh
```

The script orchestrates everything (mock + proxy startup, both
scenarios, teardown via `trap`). It writes:

- `scripts/loadtest_traffic_proxy.results.txt` — the result table for
  both scenarios.
- `scripts/.tier2-logs/mock_upstream.log` — mock stderr.
- `scripts/.tier2-logs/proxy.log` — proxy stderr.

**Capture** before the next run overwrites:
```bash
cp scripts/loadtest_traffic_proxy.results.txt \
   scripts/run3_tier2_default.$(hostname).txt
cp scripts/.tier2-logs/proxy.log scripts/run3_proxy.$(hostname).log
```

Two scenarios run back-to-back:
- **Scenario A — mixed (1:10 chat:embedding).** What real traffic looks
  like.
- **Scenario B — chat-only.** The Phase-A baseline. The latency delta
  between A and B (per worker level) is roughly the cost the non-chat
  metering path adds.

## 5. Run 4 — Tier 2 with the chosen pool size (≈ 3 min)

Use the pool value Run 2 pointed at. Confirms the fix carries through
to the full round-trip.

```bash
KYDE_DB_POOL_MIN=10 KYDE_DB_POOL_MAX=50 \
PYTHON=.venv/bin/python ./scripts/run_tier2.sh
```

(`50` is an example — pick whatever Run 2's table recommends.)

Capture:
```bash
cp scripts/loadtest_traffic_proxy.results.txt \
   scripts/run4_tier2_tuned.$(hostname).txt
cp scripts/.tier2-logs/proxy.log scripts/run4_proxy.$(hostname).log
```

## 6. Share back

The minimum useful bundle is these files:

| File | Contents |
|---|---|
| `scripts/run1_tier1_baseline.<host>.txt` | Tier 1 at pool=10 |
| `scripts/run2_pool_sweep.<host>.txt` | Tier 1 at pool ∈ {10, 25, 50, 100} |
| `scripts/run3_tier2_default.<host>.txt` | Tier 2 (Scenarios A + B), pool=10 |
| `scripts/run4_tier2_tuned.<host>.txt` | Tier 2 (Scenarios A + B), tuned pool |
| `scripts/run3_proxy.<host>.log` | Proxy log from Run 3 |
| `scripts/run4_proxy.<host>.log` | Proxy log from Run 4 |

Plus the host description (so the numbers are interpretable):

```bash
{ echo "=== system ===";  uname -a; \
  echo "=== cpu ===";     lscpu | head -20; \
  echo "=== memory ===";  free -h; \
  echo "=== postgres ==="; .venv/bin/python -c "import psycopg, os; \
     c=psycopg.connect(os.environ['DATABASE_URL']); cur=c.cursor(); \
     cur.execute('SHOW server_version'); print('pg version:', cur.fetchone()[0])"; \
} | tee scripts/host_info.$(hostname).txt
```

A `tar -czf loadtest-results-$(hostname).tar.gz scripts/run*.txt scripts/run*.log scripts/host_info*.txt`
is a fine way to bundle them.

## 7. Cleanup (optional)

The load tests leave rows in `agent_traffic_meters`,
`agent_traffic_mode_history`, and (Run 3+) `ledger`. To clean up:

```bash
.venv/bin/python - <<'PY'
import os, psycopg
with psycopg.connect(os.environ["DATABASE_URL"]) as c, c.cursor() as cur:
    cur.execute("DELETE FROM agent_traffic_meters WHERE agent_id LIKE 'agent:load-%'")
    cur.execute("DELETE FROM agent_traffic_mode_history WHERE agent_id LIKE 'agent:load-%'")
    cur.execute("DELETE FROM ledger WHERE user_agent = 'loadtest-traffic-proxy/1.0'")
    c.commit()
    print("cleaned.")
PY
```

If a previous Tier 2 run crashed and you can't start a new one because
the ports are taken:

```bash
pkill -f mock_upstream.py
pkill -f "uvicorn kyde.server"
```

## Troubleshooting

**"proxy unreachable at http://localhost:8000"** — the proxy didn't
start. Check `scripts/.tier2-logs/proxy.log` for the actual error.
Usually one of: DB not reachable, migrations failed, port taken.

**`psycopg.OperationalError: connection ... failed`** — `DATABASE_URL`
isn't pointing at a live Postgres. Try
`.venv/bin/python -c "import os, psycopg; psycopg.connect(os.environ['DATABASE_URL'])"`
in isolation to surface the real error.

**Throughput is much lower than the documented baseline** — first
check whether you're CPU-bound on the proxy process (`top -p $(pgrep
-f uvicorn)`). Single-worker uvicorn will pin one core; raise
`--workers` on the proxy if you want a fair comparison to a
multi-worker deploy. Also confirm `mock_upstream.py` isn't the
bottleneck (it shouldn't be at <10k req/s but worth verifying:
`top -p $(pgrep -f mock_upstream)`).

**Numbers swing wildly between runs** — the host is probably under
other load. Run during a quiet window or use `taskset` to pin the
proxy to specific cores.
