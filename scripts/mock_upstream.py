#!/usr/bin/env python
"""Mock LLM upstream — returns canned responses for the endpoints the
proxy forwards. Used as the upstream target during Tier 2 load tests so
we measure the proxy's own overhead, not OpenAI's network latency.

Endpoints:
  POST /v1/chat/completions   → small chat completion
  POST /v1/embeddings         → embedding response with usage tokens
  POST /v1/moderations        → moderation pass
  GET  /v1/models             → static model list

Usage:
    python scripts/mock_upstream.py                       # listen on :9000
    python scripts/mock_upstream.py --port 9001 --delay-ms 10
    python scripts/mock_upstream.py --delay-ms 50         # simulate slow API

Run alongside the proxy with a loadtest_config.yaml that points
upstream `openai.base` at http://localhost:9000.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def make_app(delay_ms: float) -> FastAPI:
    app = FastAPI(title="loadtest-mock-upstream")

    async def _maybe_delay() -> None:
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000.0)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> JSONResponse:
        await _maybe_delay()
        try:
            body = await request.json()
        except Exception:
            body = {}
        return JSONResponse(
            {
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "created": 0,
                "model": body.get("model", "gpt-4o-mini"),
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "ok",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 1,
                    "total_tokens": 6,
                },
            }
        )

    @app.post("/v1/embeddings")
    async def embeddings(request: Request) -> JSONResponse:
        await _maybe_delay()
        try:
            body = await request.json()
        except Exception:
            body = {}
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {"object": "embedding", "embedding": [0.1, 0.2, 0.3], "index": 0}
                ],
                "model": body.get("model", "text-embedding-3-small"),
                "usage": {"prompt_tokens": 4, "total_tokens": 4},
            }
        )

    @app.post("/v1/moderations")
    async def moderations(request: Request) -> JSONResponse:
        await _maybe_delay()
        return JSONResponse(
            {
                "id": "modr-mock",
                "model": "omni-moderation-latest",
                "results": [
                    {
                        "flagged": False,
                        "categories": {},
                        "category_scores": {},
                    }
                ],
            }
        )

    @app.get("/v1/models")
    async def models() -> JSONResponse:
        await _maybe_delay()
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {"id": "gpt-4o-mini", "object": "model", "owned_by": "mock"},
                    {
                        "id": "text-embedding-3-small",
                        "object": "model",
                        "owned_by": "mock",
                    },
                ],
            }
        )

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument(
        "--delay-ms",
        type=float,
        default=0.0,
        help=(
            "Inject this many milliseconds of artificial latency into "
            "every response. Use to simulate slow upstreams."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Uvicorn worker count (default 1, raise if the mock itself is the bottleneck)",
    )
    args = parser.parse_args()

    print(
        f"[mock-upstream] listening on http://{args.host}:{args.port} "
        f"(delay={args.delay_ms}ms, workers={args.workers})",
        flush=True,
    )

    # When workers>1 uvicorn requires the app as an import string, so
    # we expose a default factory at module level for that mode.
    if args.workers > 1:
        # Stash the delay on the module so the factory can pick it up.
        sys.modules[__name__]._delay_ms = args.delay_ms  # type: ignore[attr-defined]
        uvicorn.run(
            "scripts.mock_upstream:_factory_app",
            host=args.host,
            port=args.port,
            workers=args.workers,
            log_level="warning",
            factory=True,
        )
    else:
        uvicorn.run(
            make_app(args.delay_ms),
            host=args.host,
            port=args.port,
            log_level="warning",
        )
    return 0


def _factory_app() -> FastAPI:
    """Uvicorn multi-worker entry. Reads delay from module state set by main()."""
    delay = getattr(sys.modules[__name__], "_delay_ms", 0.0)
    return make_app(delay)


if __name__ == "__main__":
    raise SystemExit(main())
