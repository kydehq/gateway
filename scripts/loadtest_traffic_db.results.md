# Tier 1 load test — traffic-metering DB hot path

Captures the run + analysis for the BACKLOG follow-up *"Proxy hot-path
perf — counter UPSERT on every request + mode cache lookup adds DB
load proportional to traffic. Untested at scale."*

## Run

```
$ DATABASE_URL='postgresql://witness:witness-dev-only@localhost:5432/witness_test' \
    python scripts/loadtest_traffic_db.py --duration 5 --warmup 1
```

Local Postgres 16 in Docker, single proxy process, ConnectionPool
`min_size=2 max_size=10` (the current ledger.py default).

## Numbers

| workers | ops/sec | p50 | p95 | p99 | max |
|---:|---:|---:|---:|---:|---:|
| 1   |  770 |  1.22 ms |  1.65 ms |  2.20 ms |  5.18 ms |
| 4   | 2251 |  1.67 ms |  2.24 ms |  4.80 ms | 11.77 ms |
| 8   | 1941 |  3.80 ms |  6.76 ms |  9.17 ms | 19.99 ms |
| 16  | 1549 |  9.50 ms | 17.31 ms | 21.55 ms | 37.75 ms |
| 32  | 1649 | 17.84 ms | 32.59 ms | 38.32 ms | 56.97 ms |
| 64  | 1702 | 34.87 ms | 65.28 ms | 74.37 ms | 90.11 ms |
| 128 | 1652 | 70.50 ms |137.58 ms |148.93 ms |168.67 ms |

Zero errors across all phases.

## Reading the numbers

- **Throughput plateaus at ~2.2k ops/sec around 4 concurrent workers**,
  then *degrades* slightly and holds flat at ~1.6k/sec.
- **Per-op cost when uncontended: ~1.2–1.7 ms**. That's the
  end-to-end cost of `record_agent_traffic` UPSERT + cached mode
  lookup, including round-trip to Postgres.
- **Latency tracks worker count above 10**: p50 doubles each time
  workers double (8 → 16 → 32 → 64 → 128 give p50s of 3.8, 9.5, 17.8,
  34.9, 70.5 ms). That's classic connection-pool starvation — each
  extra worker spends ~half its time waiting for a pool slot.
- **No PK contention**: throughput at 128 workers (1652/sec) is barely
  worse than at 8 workers (1941/sec). If we were lock-contending on
  the agent_traffic_meters PK, total throughput would crater. The PK
  is fine; the connection pool is the ceiling.

## Diagnosis

The cliff isn't the UPSERT, it's the pool. `psycopg_pool.ConnectionPool`
is built with `max_size=10` (ledger.py:160). Beyond 10 concurrent
operations, threads queue waiting for a connection — which is the
behavior the numbers above show: latency rises linearly with workers
while throughput is flat at the rate 10 connections can sustain
(~1.7k ops/sec ÷ 10 = ~170 ops/sec per connection ≈ 6 ms serialized
per op, matching the observed p50 at high concurrency).

## What this means for the proxy

At realistic single-proxy traffic (say 100–500 req/sec on the chat path
that always logs, with bursty embeddings on top), the metering layer
adds ~1.5–4 ms of latency per request from a saturated pool, on the
order of 1‰ of a typical chat completion (~1–30 s upstream). The
metering itself is not the bottleneck.

The bottleneck we'd hit first is the pool ceiling — and the proxy
already shares this pool with `_log_entry`, `record_request_network`,
`session_turns`, etc. Each request consumes ~3–5 pool slots in
sequence. With pool max=10, the practical throughput ceiling per proxy
instance is well below 1k req/sec total, not 1.7k.

## Recommendation

**Raise `max_size`** before considering anything more invasive (no
in-process batching, no `LISTEN/NOTIFY` pub/sub). The cheapest, most
honest fix.

Two concrete next steps:

1. Make `min_size` / `max_size` configurable via env vars
   (`KYDE_DB_POOL_MIN` / `KYDE_DB_POOL_MAX`) with current values
   as defaults. Re-run this test at `max_size=50` and `max_size=100` to
   see where the next ceiling is.
2. Defer Tier 2 (full-proxy load test) until #1 is done — the DB
   layer's behavior dominates the result and we already know what's
   capping it.

If under expected production load #1 doesn't give enough headroom, the
next move is in-process batching of `record_agent_traffic` with a
periodic flush. That adds proxy state the Rust port must replicate,
so it's a deliberate trade-off.
