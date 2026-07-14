# Load testing — `scripts/`

Standalone scripts that exercise the per-agent traffic metering hot
path. Run on a server, share the result files back. None of this is
wired into CI.

## What's here

| File | Purpose |
|---|---|
| `loadtest_traffic_db.py` | Tier 1 — DB-only stress (UPSERT + cached mode lookup, no HTTP). |
| `loadtest_traffic_db_sweep.sh` | Runs Tier 1 at pool sizes `{10, 25, 50, 100}` to find the connection-pool ceiling. |
| `loadtest_traffic_db.results.md` | Tier 1 baseline run + analysis (pool max=10). |
| `mock_upstream.py` | Fast local FastAPI server returning canned OpenAI-shaped responses. Tier 2 target. |
| `loadtest_traffic_proxy.py` | Tier 2 client — async httpx hitting the proxy at a configurable mix. |
| `loadtest_config.yaml` | Proxy config that points every upstream at `mock_upstream.py`. |
| `run_tier2.sh` | Orchestrates Tier 2: brings up mock + proxy, runs the realistic-mix and chat-only scenarios, tears down. |

## Prereqs on the execution host

1. Postgres reachable. The scripts default to
   `postgresql://witness:witness-dev-only@localhost:5432/witness`. Set
   `DATABASE_URL` to override.
2. The kyde package installed in a venv:
   ```
   python -m venv .venv
   .venv/bin/pip install -e .
   ```
   `PYTHON=.venv/bin/python` in the env makes the shell scripts pick it up.
3. Ports `8000` (proxy) and `9000` (mock) free for Tier 2.

## Tier 1 — DB hot path

The cheap, isolation-friendly test. Run this first.

```bash
# Single run against the test DB (no risk of polluting the dev DB)
DATABASE_URL='postgresql://witness:witness-dev-only@localhost:5432/witness_test' \
    .venv/bin/python scripts/loadtest_traffic_db.py --duration 5
```

Look for:
- **Throughput plateau** — where ops/sec stops scaling with workers.
  That's the pool ceiling.
- **p99 latency cliff** — if p99 climbs linearly with worker count
  while throughput is flat, you're connection-pool-bound, not
  PK-contended.
- **Errors should be 0**. If they aren't, something else is wrong.

### Pool-size sweep

The default `max_size=10` connection pool is the first ceiling we found.
Use the sweep runner to test 10 / 25 / 50 / 100 and pick a value:

```bash
DATABASE_URL='postgresql://witness:witness-dev-only@localhost:5432/witness_test' \
PYTHON=.venv/bin/python \
    ./scripts/loadtest_traffic_db_sweep.sh
```

Output is written to `scripts/loadtest_traffic_db_sweep.results.txt`.

Apply the chosen value via env on the proxy:
```
KYDE_DB_POOL_MIN=10 KYDE_DB_POOL_MAX=50 .venv/bin/python -m uvicorn kyde.server:app
```

## Tier 2 — full proxy round-trip

End-to-end with a mock upstream. Answers "what latency does the proxy
itself add per request, at scale, with the metering on?"

```bash
DATABASE_URL='postgresql://witness:witness-dev-only@localhost:5432/witness' \
PYTHON=.venv/bin/python \
    ./scripts/run_tier2.sh
```

The script:
1. Starts `mock_upstream.py` on :9000.
2. Starts the proxy on :8000 with `KYDE_CONFIG=scripts/loadtest_config.yaml`
   so every upstream points at the mock.
3. Waits for `/healthz`.
4. Runs **Scenario A** — realistic mix (1:10 chat:embedding, 100 agents).
5. Runs **Scenario B** — chat-only (baseline for metering overhead).
6. Stops both processes.

Output → `scripts/loadtest_traffic_proxy.results.txt`. Per-process
logs → `scripts/.tier2-logs/`.

### Interpreting Tier 2

- **Scenario B (chat-only)** is the apples-to-apples comparison with
  Phase A behavior — every request was already being logged, this just
  adds the meter UPSERT + cache lookup. The latency delta vs. mock-
  upstream baseline IS the cost of metering.
- **Scenario A (mixed)** stresses the non-chat path (which today writes
  no ledger row by default but still increments meters). Compare its
  numbers to Scenario B to see how much extra cost comes from the
  embedding/non-chat traffic flowing through.
- The mock has ~0ms baseline latency. Real upstreams add 1–30 s on chat
  and 50–300 ms on embeddings — so any proxy overhead in the
  single-digit-ms range disappears in production.

### Tuning the pool for Tier 2

Re-run with a larger pool to verify the fix carries through to the
proxy:
```
KYDE_DB_POOL_MAX=50 ./scripts/run_tier2.sh
```

### Cleanup

`run_tier2.sh` registers a trap that kills the child processes on exit.
If it crashes uncleanly:
```bash
pkill -f mock_upstream.py
pkill -f "uvicorn kyde.server"
```

## What we're trying to learn

From `BACKLOG.md`:

> **Proxy hot-path perf**: counter UPSERT on every request + mode
> cache lookup adds DB load proportional to traffic. Untested at
> scale.

The Tier 1 baseline (in `loadtest_traffic_db.results.md`) already
showed:
- UPSERT itself is ~1.5 ms — not the bottleneck.
- The 10-slot connection pool is the ceiling.

These scripts let you (a) verify a larger pool fixes the ceiling on
your hardware, and (b) measure the actual end-to-end latency overhead
the metering layer adds in real proxy traffic. Once you have numbers
from Scenario B on production-sized hardware, you have the
defensible "this is the cost" answer to anyone asking.
