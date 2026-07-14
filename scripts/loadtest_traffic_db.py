#!/usr/bin/env python
"""Tier 1 load test — traffic-metering DB hot path.

Exercises just the two DB calls the proxy adds on every request:
  1. ledger.record_agent_traffic(agent_id, path_kind)
  2. ledger.get_agent_traffic_mode_cached(agent_id, path_kind)

No HTTP layer, no upstream forwarding — answers the narrow question:
"can the DB handle the per-request UPSERT + mode lookup at production
rates?" If this can't sustain target throughput, no amount of proxy
tuning helps.

Workload defaults match the realistic traffic scenario:
100 synthetic agents, 1:10 chat:embedding ratio. We ramp
worker concurrency through fixed phases and report:
  - ops/sec achieved (closed-loop, so this is the ceiling at that
    worker count)
  - latency p50 / p95 / p99 / max per phase
  - error count per phase

Usage:
    python scripts/loadtest_traffic_db.py            # all phases, 5s each
    python scripts/loadtest_traffic_db.py --duration 10
    python scripts/loadtest_traffic_db.py --workers 1,8,32,128
    DATABASE_URL=postgresql://... python scripts/loadtest_traffic_db.py

DO NOT run this against production. Run against a representative staging
DB or the local Postgres. The script appends to agent_traffic_meters and
agent_traffic_mode_history — easy to clean up but noisy.
"""

from __future__ import annotations

import argparse
import os
import random
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Resolve DATABASE_URL before importing kyde — the pool is built on
# first use and reads the env var once.
if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = (
        "postgresql://witness:witness-dev-only@localhost:5432/witness"
    )

# Path hack so the script works when invoked from the repo root without
# installing the package.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

from kyde import ledger  # noqa: E402

# Workload shape — the realistic 1:10 chat:embedding scenario.
N_AGENTS_DEFAULT = 100
# Probability of picking 'chat' on each op; the rest is split across the
# non-chat path_kinds. 1/11 ≈ 9% chat / 91% non-chat → roughly the 1:10
# chat:embedding ratio when embedding dominates the non-chat bucket.
P_CHAT_DEFAULT = 1.0 / 11.0
NON_CHAT_KINDS = (
    "embedding",
    "embedding",
    "embedding",
    "embedding",
    "moderation",
    "models_list",
    "audio_transcription",
    "image_generation",
)
DEFAULT_PHASES = (1, 4, 8, 16, 32, 64, 128)


def _pick_kind(p_chat: float) -> str:
    if random.random() < p_chat:
        return "chat"
    return random.choice(NON_CHAT_KINDS)


def _one_op(agent_id: str, kind: str) -> float:
    """Run the two DB calls the proxy adds per request. Returns elapsed
    milliseconds; raises on error so the caller can count failures."""
    t0 = time.perf_counter()
    ledger.record_agent_traffic(agent_id, kind)
    ledger.get_agent_traffic_mode_cached(agent_id, kind)
    return (time.perf_counter() - t0) * 1000.0


def _phase(
    *,
    workers: int,
    duration_s: float,
    agents: list[str],
    p_chat: float,
) -> dict:
    """Run a closed-loop phase: `workers` threads each loop calling
    _one_op as fast as they can for `duration_s`. Returns aggregated
    stats."""
    samples: list[float] = []
    errors: list[Exception] = []
    samples_lock = threading.Lock()
    errors_lock = threading.Lock()
    stop_at = time.time() + duration_s

    def loop():
        local_samples: list[float] = []
        local_errors: list[Exception] = []
        while time.time() < stop_at:
            agent = random.choice(agents)
            kind = _pick_kind(p_chat)
            try:
                local_samples.append(_one_op(agent, kind))
            except Exception as exc:
                local_errors.append(exc)
        # Bulk-merge under the lock so we don't contend on every op.
        with samples_lock:
            samples.extend(local_samples)
        with errors_lock:
            errors.extend(local_errors)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(loop) for _ in range(workers)]
        for f in as_completed(futures):
            f.result()  # surface any threadpool-level errors
    wall = time.perf_counter() - t0
    n = len(samples)

    if n == 0:
        return {
            "workers": workers,
            "n_ops": 0,
            "wall_s": wall,
            "ops_per_sec": 0.0,
            "errors": len(errors),
            "first_error": (str(errors[0]) if errors else None),
        }

    s_sorted = sorted(samples)

    def pct(p: float) -> float:
        idx = max(0, min(len(s_sorted) - 1, int(round(p * (len(s_sorted) - 1)))))
        return s_sorted[idx]

    return {
        "workers": workers,
        "n_ops": n,
        "wall_s": wall,
        "ops_per_sec": n / wall,
        "p50_ms": pct(0.50),
        "p95_ms": pct(0.95),
        "p99_ms": pct(0.99),
        "max_ms": s_sorted[-1],
        "mean_ms": statistics.fmean(s_sorted),
        "errors": len(errors),
        "first_error": (str(errors[0]) if errors else None),
    }


def _print_row(stats: dict) -> None:
    print(
        f"  workers={stats['workers']:>4}  "
        f"ops={stats['n_ops']:>7}  "
        f"wall={stats['wall_s']:>5.2f}s  "
        f"rate={stats['ops_per_sec']:>8.1f}/s  "
        f"p50={stats.get('p50_ms', 0):>5.2f}ms  "
        f"p95={stats.get('p95_ms', 0):>5.2f}ms  "
        f"p99={stats.get('p99_ms', 0):>5.2f}ms  "
        f"max={stats.get('max_ms', 0):>6.2f}ms  "
        f"err={stats['errors']}",
        flush=True,
    )
    if stats["errors"] and stats.get("first_error"):
        print(f"    first error: {stats['first_error']}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--duration",
        type=float,
        default=5.0,
        help="Seconds per phase (default: 5)",
    )
    parser.add_argument(
        "--workers",
        type=str,
        default=",".join(str(w) for w in DEFAULT_PHASES),
        help=(
            "Comma-separated worker counts to ramp through "
            f"(default: {','.join(str(w) for w in DEFAULT_PHASES)})"
        ),
    )
    parser.add_argument(
        "--agents",
        type=int,
        default=N_AGENTS_DEFAULT,
        help=f"Number of synthetic agents (default: {N_AGENTS_DEFAULT})",
    )
    parser.add_argument(
        "--p-chat",
        type=float,
        default=P_CHAT_DEFAULT,
        help=(
            "Probability of picking path_kind='chat' on each op "
            f"(default: {P_CHAT_DEFAULT:.3f}, ≈ 1:10 chat:embedding)"
        ),
    )
    parser.add_argument(
        "--warmup",
        type=float,
        default=1.0,
        help="Seconds of warmup before each phase (default: 1)",
    )
    args = parser.parse_args()

    phases = [int(w) for w in args.workers.split(",") if w.strip()]
    agents = [f"agent:load-{i:04x}" for i in range(args.agents)]

    db_url = ledger._database_url()
    print(f"DATABASE_URL: {db_url}")
    print(
        f"workload: {len(agents)} agents, "
        f"p(chat)={args.p_chat:.3f}, "
        f"phases={phases}, "
        f"duration={args.duration}s, warmup={args.warmup}s"
    )

    # Touch the pool once so the first phase doesn't pay schema-init
    # overhead.
    ledger._get_pool()
    print("Pool warm — beginning phases.")
    print()
    print(
        "phase  |  workers   ops     wall   rate          "
        "p50      p95      p99     max       err"
    )
    print("-" * 100)

    for workers in phases:
        # Warm up — same shape, half the workers, ignored.
        _phase(
            workers=max(1, workers // 2),
            duration_s=args.warmup,
            agents=agents,
            p_chat=args.p_chat,
        )
        stats = _phase(
            workers=workers,
            duration_s=args.duration,
            agents=agents,
            p_chat=args.p_chat,
        )
        _print_row(stats)

    print()
    print(
        "tip: throughput should rise with workers until DB connection pool "
        "or PK contention caps it. If p99 climbs faster than rate, you've "
        "hit the cliff."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
