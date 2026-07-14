#!/usr/bin/env python
"""Tier 2 load test — full proxy round-trip.

Hits a running proxy with N synthetic agents and a configurable mix of
chat / embedding requests. Measures end-to-end latency, throughput, and
errors at increasing client concurrency.

The proxy must be configured to forward to a fast local upstream (use
scripts/mock_upstream.py) — otherwise we're measuring OpenAI's latency,
not the proxy's overhead.

Usage:
    python scripts/loadtest_traffic_proxy.py \
        --proxy-url http://localhost:8000 \
        --duration 5 --workers 1,4,16,64

Workload defaults match Tier 1 and the realistic traffic mix:
  - 100 synthetic agents, each identified by a distinct X-API-Key
  - ~1:10 chat:embedding ratio
  - Closed-loop: each worker loops as fast as the proxy allows

The X-API-Key header is what the proxy's _agent_id() hashes — different
keys produce different agent_ids, exercising the per-agent meter rows
the same way real traffic would.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time
from dataclasses import dataclass, field

import httpx

N_AGENTS_DEFAULT = 100
P_CHAT_DEFAULT = 1.0 / 11.0
DEFAULT_PHASES = (1, 4, 16, 64, 128)


@dataclass
class PhaseStats:
    workers: int
    n_ops: int = 0
    wall_s: float = 0.0
    samples: list[float] = field(default_factory=list)
    errors: int = 0
    first_error: str | None = None


CHAT_BODY = {
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "ping"}],
}
EMBEDDING_BODY = {
    "model": "text-embedding-3-small",
    "input": "ping",
}


def _pick_kind(p_chat: float) -> str:
    return "chat" if random.random() < p_chat else "embedding"


async def _one_op(
    client: httpx.AsyncClient,
    proxy_url: str,
    agent_key: str,
    kind: str,
) -> float:
    if kind == "chat":
        path = "/v1/chat/completions"
        body = CHAT_BODY
    else:
        path = "/v1/embeddings"
        body = EMBEDDING_BODY
    headers = {
        "X-API-Key": agent_key,
        "Content-Type": "application/json",
        "User-Agent": "loadtest-traffic-proxy/1.0",
    }
    t0 = time.perf_counter()
    r = await client.post(proxy_url + path, json=body, headers=headers, timeout=30.0)
    elapsed = (time.perf_counter() - t0) * 1000.0
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} {r.text[:200]}")
    return elapsed


async def _phase(
    *,
    workers: int,
    duration_s: float,
    proxy_url: str,
    agents: list[str],
    p_chat: float,
) -> PhaseStats:
    stats = PhaseStats(workers=workers)
    stop_at = time.time() + duration_s

    # httpx's connection pool defaults are tight (5 keepalive, 10 max);
    # bump them so the client itself isn't the bottleneck.
    limits = httpx.Limits(
        max_keepalive_connections=workers, max_connections=workers * 2
    )

    async with httpx.AsyncClient(limits=limits) as client:

        async def worker():
            local_samples: list[float] = []
            local_errors = 0
            local_first: str | None = None
            while time.time() < stop_at:
                agent = random.choice(agents)
                kind = _pick_kind(p_chat)
                try:
                    local_samples.append(await _one_op(client, proxy_url, agent, kind))
                except Exception as exc:
                    local_errors += 1
                    if local_first is None:
                        local_first = repr(exc)
            return local_samples, local_errors, local_first

        t0 = time.perf_counter()
        results = await asyncio.gather(*[worker() for _ in range(workers)])
        stats.wall_s = time.perf_counter() - t0

    for samples, errors, first_err in results:
        stats.samples.extend(samples)
        stats.errors += errors
        if stats.first_error is None and first_err is not None:
            stats.first_error = first_err
    stats.n_ops = len(stats.samples)
    return stats


def _print_row(s: PhaseStats) -> None:
    if s.n_ops == 0:
        print(
            f"  workers={s.workers:>4}  ops=      0  wall={s.wall_s:>5.2f}s  "
            f"rate=     0.0/s  err={s.errors}",
            flush=True,
        )
        if s.first_error:
            print(f"    first error: {s.first_error}", flush=True)
        return

    s_sorted = sorted(s.samples)

    def pct(p: float) -> float:
        idx = max(0, min(len(s_sorted) - 1, int(round(p * (len(s_sorted) - 1)))))
        return s_sorted[idx]

    print(
        f"  workers={s.workers:>4}  "
        f"ops={s.n_ops:>7}  "
        f"wall={s.wall_s:>5.2f}s  "
        f"rate={s.n_ops / s.wall_s:>8.1f}/s  "
        f"p50={pct(0.50):>6.2f}ms  "
        f"p95={pct(0.95):>6.2f}ms  "
        f"p99={pct(0.99):>6.2f}ms  "
        f"max={s_sorted[-1]:>7.2f}ms  "
        f"err={s.errors}",
        flush=True,
    )
    if s.errors and s.first_error:
        print(f"    first error: {s.first_error}", flush=True)


async def amain() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--proxy-url",
        default="http://localhost:8000",
        help="Proxy base URL (default: http://localhost:8000)",
    )
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--warmup", type=float, default=1.0)
    parser.add_argument(
        "--workers",
        type=str,
        default=",".join(str(w) for w in DEFAULT_PHASES),
        help=f"Comma-separated worker counts (default: {DEFAULT_PHASES})",
    )
    parser.add_argument("--agents", type=int, default=N_AGENTS_DEFAULT)
    parser.add_argument(
        "--p-chat",
        type=float,
        default=P_CHAT_DEFAULT,
        help=(
            "Probability of picking a chat request (default ≈ 0.091, "
            "≈ 1:10 chat:embedding)"
        ),
    )
    parser.add_argument(
        "--chat-only",
        action="store_true",
        help=(
            "Override --p-chat and send only chat requests. Baseline scenario "
            "for measuring metering overhead vs. Phase A behavior."
        ),
    )
    args = parser.parse_args()

    phases = [int(w) for w in args.workers.split(",") if w.strip()]
    agents = [f"loadtest-key-{i:04x}" for i in range(args.agents)]
    p_chat = 1.0 if args.chat_only else args.p_chat

    print(f"proxy: {args.proxy_url}")
    print(
        f"workload: {len(agents)} agents, "
        f"p(chat)={p_chat:.3f}{' [chat-only]' if args.chat_only else ''}, "
        f"phases={phases}, duration={args.duration}s, warmup={args.warmup}s"
    )

    # Quick reachability check before the first phase.
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(args.proxy_url + "/healthz")
            print(f"proxy reachable: status={r.status_code}")
    except Exception as exc:
        print(f"!! proxy unreachable at {args.proxy_url}: {exc}")
        return 1

    print()
    print(
        "phase  |  workers   ops     wall   rate          "
        "p50      p95      p99     max       err"
    )
    print("-" * 100)

    for workers in phases:
        # Warmup at lower concurrency, ignored.
        await _phase(
            workers=max(1, workers // 2),
            duration_s=args.warmup,
            proxy_url=args.proxy_url,
            agents=agents,
            p_chat=p_chat,
        )
        stats = await _phase(
            workers=workers,
            duration_s=args.duration,
            proxy_url=args.proxy_url,
            agents=agents,
            p_chat=p_chat,
        )
        _print_row(stats)

    print()
    print(
        "tip: latency above mock-upstream baseline ≈ proxy overhead per request. "
        "Compare chat-only vs. mixed to isolate the cost of mode lookup + non-chat dispatch."
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
