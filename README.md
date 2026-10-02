# Behavioral scoring for AI agents starts with seeing every action.

[![CI](https://github.com/kydehq/gateway/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kydehq/gateway/actions/workflows/ci.yml)
![Backend coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/kydehq/gateway/badges/backend-coverage.json)
![Frontend coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/kydehq/gateway/badges/frontend-coverage.json)

AI agents are checked once, on the day they go live. After that, the only one watching the agent is the agent itself. Agents drift away from their task, get hijacked by prompt injection, or get stuck in loops and burn money. Most teams find out weeks later, from a customer, a bill or an auditor.
Banks solved this for credit cards decades ago. A card is scored once when you apply, and then the bank scores how you use it, with every swipe. Kyde does the same for AI agents: observe every action, score the behavior against the agent’s own normal, and stop the agent when it deviates.
Kyde Gateway is the part that sits in the path. It is a drop-in, OpenAI-compatible proxy outside the agent. Every action is recorded into a hash-chained, signed ledger that the agent cannot reach, and out-of-scope requests can be stopped before they leave your network. Each model vendor logs only its own slice. The gateway records across all of them, in one place you own.
training data.

**Observe every action. Stop what deviates. Prove what happened.**

<img width="1028" height="649" alt="image" src="https://github.com/user-attachments/assets/a6bf5a2a-2b2b-45c3-9f67-7e52eb972311" />


**Two ways to start — same install, one switch:**

| | What it does | Setup |
|---|---|---|
| 🔍 **Observe** (start here) | Logs everything, blocks nothing. Zero risk, zero code changes. In week one you know: what your agents do, what leaves your house, what it costs. | One line: `export OPENAI_BASE_URL=http://localhost:4000/v1` — [Quickstart](#quickstart-docker-compose) |
| 🛡️ **Enforce** (when ready) | Flip DLP prevention per pattern and MCP tool allow/deny — out-of-scope requests get a 403 before they reach the upstream. | [Policy enforcement](docs/reference.md#policy-enforcement-dlp-prevention) |


## The score

The dashboard shows a live trust score per agent and for the whole fleet. It is deterministic and computed from the ledger: the same record always gives the same score, so an auditor can recompute it.
This is version 1. The five inputs (security, compliance, integrity, reliability, economics) are heuristics derived from what the ledger already stores. A learned baseline of each agent’s normal behavior is not part of the score yet. The drift detectors and the research behind them are published separately: kydehq/behavioral-drift-detection.

## What it does

```
Agent ──► your-proxy:4000/v1 ──► OpenAI/Anthropic/Gemini/Copilot/any LLM
                │
                ▼
        Behavioral Ledger (Postgres, JSONB)
        ┌─────────────────────────────────────────────┐
        │ entry_id   │ timestamp │ agent_id           │
        │ action     │ model     │ why (context)      │
        │ tool_calls │ prev_hash │ entry_hash         │
        │ signature (Ed25519)                         │
        └─────────────────────────────────────────────┘
```

Each entry is:
- **Signed** with Ed25519 (hardware-rootable via PKCS#11 / HSM)
- **Hash-chained** — tampering with any past entry breaks all subsequent hashes
- **Causally linked** — captures the reasoning context (*why*) before every tool call

One gateway instance proxies **all supported providers simultaneously** —
OpenAI, Anthropic, Gemini, Copilot, and any local LLM. The upstream is
auto-detected from the request path; auth headers pass through untouched.
Full routing table: [reference](docs/reference.md#multi-provider-routing).

## Quickstart (Docker Compose)

The stack is five containers: the LLM proxy, the admin API, the dashboard UI,
a regex DLP engine, and Postgres for the ledger. Both options below end in the
same place — pick one.

### Option A — Run the published images (recommended, zero config)

Uses the public images from GHCR; nothing is built on your host, and only
the UI is published — loopback-only (admin `127.0.0.1:8080`, agent proxy
`127.0.0.1:4000`).

```bash
git clone https://github.com/kydehq/gateway.git
cd gateway

docker compose up -d
```

For a local evaluation the built-in defaults are all you need. Before any
real deployment, switch to the hardened prod posture and set your own
Postgres password:

```bash
cp .env.starter.example .env.starter
# Edit .env.starter: set POSTGRES_PASSWORD (e.g. `openssl rand -base64 32`)

docker compose --env-file .env.starter \
  -f docker-compose.yml -f docker-compose.prod.yml up -d
```

> ⚠️ Keep the file named `.env.starter` — do **not** copy it to `.env`.
> Compose auto-loads `.env` into *every* invocation, including the dev stack,
> and `POSTGRES_PASSWORD` only takes effect when the Postgres volume is first
> created — changing it later locks the services out of an existing database.

### Option B — Build from source (development)

Builds the gateway and UI images from this repo and additionally publishes
each service's port directly to the host (gateway `8081`, admin API `8501`,
DLP regex `8002`, Postgres loopback `5432`) — handy for development.

```bash
git clone https://github.com/kydehq/gateway.git
cd gateway

docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

### Verify (both options)

```bash
curl -fsS http://localhost:4000/v1/health                           # LLM proxy
curl -fsS -o /dev/null -w "%{http_code}\n" http://localhost:8080/   # admin UI → 200
docker compose ps                                                    # everything "healthy"
```

All services should be healthy within ~40 seconds.

### Point your agent at it

One line — the gateway forwards your real API key untouched:

```bash
# OpenAI-style clients (VS Code, Cursor, most SDKs)
export OPENAI_BASE_URL=http://localhost:4000/v1

# Anthropic-style clients (Claude Code, Claude SDK)
export ANTHROPIC_BASE_URL=http://localhost:4000
```

Optionally name your agent with an `X-Agent-ID` header; without it the gateway
derives a stable pseudonymous ID from the API key hash
([details](docs/reference.md#agent-identity)).

### See it in the ledger

Open **http://localhost:8080/** — on first start the UI routes you to `/setup`
to create the admin account. Run your agent once, refresh the dashboard: the
request is there, hash-chained into the ledger, with its causal context, tool
calls, token counts, and DLP findings.

That's the whole pitch in one screen. From here: TLS, backups, upgrades, and
the optional neural-DLP / validator services are in the
[deployment guide](docs/deployment.md).

> **This is our public Starter release — we want your feedback.**
> Broke during setup? Missing a provider? Wondering whether you'd ever
> flip enforcement on? [Open an issue](../../issues/new/choose) or write
> us: **feedback@kyde.com**. We read everything.

## Editions

The quickstart above runs the **starter** edition — this repo's public images:
hash-chained but unsigned ledger, observe-only DLP. The **enterprise** edition
(`ghcr.io/kydehq/gateway-distribution/*`) adds Ed25519/TPM audit signing and
inline enforcement, on the same compose files — the edition is just an env-file
switch. See [deployment guide §3](docs/deployment.md#3-the-two-knobs-edition-and-posture)
and [enterprise support](docs/deployment.md#12-enterprise-support).

## Documentation

| Guide | Covers |
| --- | --- |
| [Deployment guide](docs/deployment.md) | Installing and operating the full stack — Docker Compose, editions, TLS, backups, upgrades |
| [Reference](docs/reference.md) | Provider routing, MCP routing, `config.yaml`, CLI, agent identity, ledger format |
| [User manual](docs/user-manual.md) | Using the dashboard — roles, DLP alerts and policies, users, settings |
| [Building images](docs/building-images.md) | Building the container images and the starter/enterprise edition split |
| [CI](docs/ci.md) | CI and release pipelines (public and private) |

## Development (pip install)

For hacking on the proxy itself, or embedding it in an existing Python
environment, you can skip the containers:

```bash
pip install -e .        # installs the `kyde` CLI
kyde keygen             # generate a signing keypair (~/.agent-ledger/)
kyde serve --port 8000  # start the proxy
```

This runs the bare proxy without the dashboard UI, DLP sidecars, or Postgres
ledger — see [deployment guide §8](docs/deployment.md#8-deployment-b--local-pip-install)
for wiring those up individually, and the [CLI reference](docs/reference.md#cli).

## What it doesn't do (yet)
- A learned drift baseline behind the score. Today’s score is a deterministic v1 heuristic. The drift detectors live in behavioral-drift-detection and are not wired into the gateway yet
- Limits derived from the score, and stopping an agent because its score dropped. Today enforcement works on DLP patterns and MCP tool rules
- Multi-agent correlation (linking entries across agents in the same task)
- Distributed ledger / external verification
- Streaming reassembly for tool call extraction (tool calls in streamed responses are captured but reconstruction is partial)
