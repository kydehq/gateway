# KYDE Gateway — The Behavioral Firewall for AI Agents

[![CI](https://github.com/kydehq/gateway/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kydehq/gateway/actions/workflows/ci.yml)
![Backend coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/kydehq/gateway/badges/backend-coverage.json)
![Frontend coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/kydehq/gateway/badges/frontend-coverage.json)

Nobody hands real responsibility to an agent nobody can trust. Agents
stay stuck waiting for human approval on every step — because when
something goes wrong, nobody can prove what happened. The provider's
log lives on their infrastructure, signed with their keys: the suspect
can't write the police report.

KYDE Gateway is a drop-in, OpenAI-compatible proxy that sits outside
the agent, in the path itself. Every action is intercepted and recorded
into an Ed25519-signed, hash-chained ledger — independent of every
model provider, undeletable by any agent, including the one being
investigated. And while it records, it sees what flows upstream: your
prompts, traces, and corrections, before they become someone else's
training data.

**Prevent what must not happen. Prove what did. Own what your agents produce.**

**Two ways to start — same install, one switch:**

| | What it does | Setup |
|---|---|---|
| 🔍 **Observe** (start here) | Logs everything, blocks nothing. Zero risk, zero code changes. In week one you know: what your agents do, what leaves your house, what it costs. | One line: `export OPENAI_BASE_URL=http://localhost:8000/v1` — [Quickstart](#quickstart) |
| 🛡️ **Enforce** (when ready) | Flip DLP prevention per pattern and MCP tool allow/deny — out-of-scope requests get a 403 before they reach the upstream. | [Policy enforcement](#policy-enforcement-dlp-prevention) |

> **This is our public sandbox — we want your feedback.**
> Broke during setup? Missing a provider? Wondering whether you'd ever
> flip enforcement on? [Open an issue](../../issues/new/choose) or write
> us: **feedback@kyde.com**. We read everything.

## What it does

```
Agent ──► your-proxy:8000/v1 ──► OpenAI/Anthropic/any LLM
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

## Documentation

| Guide | Covers |
| --- | --- |
| [Deployment guide](docs/deployment.md) | Installing and operating the full stack — Docker Compose, editions, TLS, backups, upgrades |
| [User manual](docs/user-manual.md) | Using the dashboard — roles, DLP alerts and policies, users, settings |
| [Building images](docs/building-images.md) | Building the container images and the sandbox/enterprise edition split |
| [CI](docs/ci.md) | CI and release pipelines (public and private) |

## Install

```bash
pip install -e .
```

This installs the `kyde` CLI command.

To run the full stack (proxy + dashboard UI + DLP sidecars + Postgres) with
Docker Compose instead, see the [deployment guide](docs/deployment.md) —
`docker compose up --build` is all it takes for a local dev stack.

## Quickstart

```bash
# 1. Generate signing keypair (stored in ~/.agent-ledger/)
kyde keygen

# 2. Start proxy
kyde serve --port 8000

# 3. Point your agent at the proxy (one line change)
export OPENAI_BASE_URL=http://localhost:8000/v1
export OPENAI_API_KEY=sk-...   # your real key, passed through transparently

# 4. Run your agent normally — everything is logged automatically
```

## Multi-provider routing

One proxy instance handles all providers simultaneously. Routing is **path-based** — no configuration, environment variables, or headers needed. The proxy detects the upstream from the request path.

```bash
kyde serve --port 8080
```

### Routing rules

| Path pattern | Upstream | Final URL |
| --- | --- | --- |
| `/v1/chat/completions` | OpenAI (default) | `https://api.openai.com/v1/chat/completions` |
| `/v1/messages` | Anthropic (auto-detected) | `https://api.anthropic.com/v1/messages` |
| `/openai/v1/chat/completions` | OpenAI | `https://api.openai.com/v1/chat/completions` |
| `/anthropic/v1/messages` | Anthropic | `https://api.anthropic.com/v1/messages` |
| `/claude/v1/messages` | Anthropic (alias) | `https://api.anthropic.com/v1/messages` |
| `/gemini/chat/completions` | Gemini | `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions` |
| `/copilot/chat/completions` | Copilot | `https://api.githubcopilot.com/chat/completions` |

The provider prefix (`/openai/`, `/anthropic/`, etc.) can appear before or after `/v1/`:

- `/v1/openai/chat/completions` and `/openai/v1/chat/completions` both work.

For unprefixed paths, the endpoint name determines the provider:

- `messages` → Anthropic (this is Anthropic's native endpoint)
- Everything else → OpenAI (default)

Auth headers are forwarded as-is, so each request must include the correct key for its target upstream.

### Concurrency and threads

No explicit thread configuration is required for normal use.
FastAPI/Uvicorn handles simultaneous requests via async I/O, which is enough for concurrent provider traffic.

For higher throughput, run multiple workers:

```bash
uvicorn server:app --host 0.0.0.0 --port 8080 --workers 4
```

### Example: one proxy, three providers

```bash
# OpenAI — default for /v1/chat/completions
curl -sS http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hello"}]}'

# Gemini — explicit prefix
curl -sS http://localhost:8080/gemini/chat/completions \
  -H "Authorization: Bearer $GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gemini-2.0-flash","messages":[{"role":"user","content":"hello"}]}'

# Claude/Anthropic — auto-detected from /v1/messages endpoint
curl -sS http://localhost:8080/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-20250514","max_tokens":128,"messages":[{"role":"user","content":"hello"}]}'

# Or with explicit prefix
curl -sS http://localhost:8080/anthropic/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-20250514","max_tokens":128,"messages":[{"role":"user","content":"hello"}]}'
```

## MCP routing

The proxy also routes **Model Context Protocol** traffic — JSON-RPC over
Streamable HTTP — alongside the LLM upstreams. MCP routes are declared before
the catch-all proxy route so `/mcp/...` isn't swallowed as a chat-completions
path.

| Path | Methods | Behavior |
| --- | --- | --- |
| `/mcp/{server_name}` | GET, POST, DELETE | Resolve `{server_name}` against the per-tenant registry, walk the JSON-RPC envelope through DLP, forward to the upstream, and record a signed ledger row. |
| `/mcp` (aggregator) | GET, POST, DELETE | Serve the union of every backend's tools; route `tools/call` by `{server}__{tool}` namespace to the right backend. `tools/list` is served from a per-tenant in-memory catalog (5-minute TTL). |

- The agent's `Authorization` header is forwarded unchanged — the gateway is
  transparent on upstream auth and never caches or refreshes credentials.
- DLP on the MCP path is observe-only by default (findings land in
  `dlp_alerts` with `source_type='mcp'`); per-tool allow/deny policy is handled
  by `mcp_policy.py`.
- `DELETE` terminates the upstream MCP session.

## Custom and local upstreams

The upstream registry can be extended or overridden via a `config.yaml` file in
the project root. New upstream names become valid path prefixes automatically —
no code changes required.

### Config file location

| Source | Path |
| --- | --- |
| Default | `config.yaml` next to `pyproject.toml` |
| Override | Set `KYDE_CONFIG=/absolute/path/to/config.yaml` |

### Format

```yaml
upstreams:
  <name>:
    base: <url>          # required — scheme + host (+ optional port), no trailing slash
    api_prefix: <path>   # optional — prepended between base and the request path
```

### Override a built-in upstream

Redirect the `openai` upstream to a local proxy:

```yaml
upstreams:
  openai:
    base: http://my-openai-proxy.internal
    api_prefix: /v1
```

### Add a local LLM

```yaml
upstreams:
  ollama:
    base: http://localhost:11434
    api_prefix: /v1

  vllm:
    base: http://localhost:8000
    api_prefix: /v1

  lmstudio:
    base: http://localhost:1234
    api_prefix: /v1
```

Once defined, each name is a valid path prefix:

| Request path | Forwards to |
| --- | --- |
| `/ollama/v1/chat/completions` | `http://localhost:11434/v1/chat/completions` |
| `/vllm/v1/chat/completions` | `http://localhost:8000/v1/chat/completions` |
| `/lmstudio/v1/chat/completions` | `http://localhost:1234/v1/chat/completions` |

The same prefix-stripping and `/v1/` deduplication rules that apply to built-in
providers apply to custom ones too.

### Point an agent at a custom upstream

```python
import openai

client = openai.OpenAI(
    base_url="http://localhost:8000/ollama/v1",
    api_key="ollama",  # placeholder — forwarded as-is
)
response = client.chat.completions.create(
    model="llama3.2",
    messages=[{"role": "user", "content": "hello"}],
)
```

### Startup output

On startup, `load_upstreams()` prints a line for each entry loaded from the
config file so you can confirm the registry was applied:

```
  ✓ config: upstream 'ollama' → http://localhost:11434
  ✓ config: upstream 'openai' → http://my-openai-proxy.internal
```

Entries that are missing the required `base` field are skipped with a warning.
If `config.yaml` does not exist, the built-in defaults are used silently.

## CLI

```bash
kyde keygen [--type local|tpm]  # generate keys (default: local Ed25519)
kyde key                        # show all keys (local & TPM), TPM status, active key
kyde ledger list                # show recent entries
kyde ledger verify              # verify full chain integrity
kyde ledger show <id>           # detailed view of one entry
```

### Key generation

```bash
kyde keygen                       # Ed25519 (default, backward compatible)
kyde keygen --type local          # Ed25519 software key
kyde keygen --type tpm            # ECDSA P-256 hardware key (requires TPM + tpm2-pytss)
kyde keygen --type local --force  # Overwrite existing key (caution!)
```

Key protection: `keygen` refuses to overwrite existing keys unless `--force` is passed. This prevents accidental key loss.

### Key information

The `kyde key` command shows:
- **TPM Status**: Whether TPM is accessible (if not installed, shows ✗)
- **Local Software Key**: Ed25519 key status and whether it's active
- **TPM Key**: ECDSA P-256 key status and whether it's active (precedence if TPM accessible)
- **Active Public Key**: The fingerprint and PEM of the currently used key

## Agent identity

Agents are identified by the `X-Agent-ID` header:

```python
client = openai.OpenAI(
    base_url="http://localhost:8000/v1",
    default_headers={"X-Agent-ID": "my-research-agent-v1"},
)
```

Without it, the proxy derives a consistent pseudonymous ID from the API key hash.

## Architecture

```
src/kyde/
├── __init__.py        Package marker
├── proxy.py           Entry point + CLI dispatch
├── commands.py        CLI: keygen, serve, ledger inspection
├── server.py          FastAPI proxy, request interception, streaming, route order
├── config.py          Upstream registry loader (merges defaults + config.yaml)
├── ledger.py          Append-only Postgres ledger (JSONB), hash chaining
├── signing.py         Ed25519 keypair management, sign/verify
├── dashboard.py       FastAPI audit dashboard
│
│  # MCP routing
├── mcp_proxy.py       Per-server MCP JSON-RPC proxy (/mcp/{server_name})
├── mcp_aggregator.py  Bare /mcp aggregator — union tool catalog + namespaced routing
├── mcp_registry.py    Per-tenant MCP server registry
├── mcp_policy.py      Per-tool allow/deny policy enforcement
├── mcp_ledger.py      Signed ledger rows for MCP calls
│
│  # DLP
├── dlp.py             Post-hoc DLP scanner → alerts (observe)
├── dlp_prevention.py  Inline request-side blocking → 403 (enforce)
├── dlp_policies.py    DLP policies + allowlist
└── dlp_json_walk.py   Walk JSON-RPC params/results through DLP
```

(Plus supporting modules: `auth.py`, `crypto.py`, `settings.py`, `audit_log.py`,
`notifications.py`, `topology.py`, `migrations/`, and others.)

## Ledger entry fields

| Field | Description |
| --- | --- |
| `agent_id` | Who acted |
| `action_type` | `chat` or `tool_call` |
| `why` | Last N messages before the action (causal context) |
| `input_hash` | SHA-256 of full request |
| `output_hash` | SHA-256 of full response |
| `tool_calls` | Extracted tool name + args |
| `prompt_tokens` | Input/up token count from upstream |
| `completion_tokens` | Output/down token count from upstream |
| `prev_hash` | Hash of previous entry (chain) |
| `entry_hash` | Hash of this entry's signed fields |
| `signature` | Ed25519 signature over all above fields |

## Extending toward hardware roots

The signing key in `~/.agent-ledger/signing.key` can be replaced with a PKCS#11
interface to a hardware security module (HSM) or TPM. The `core/signing.py`
module is the only component that needs to change — everything else stays identical.

```python
# Future: swap software key for HSM-backed key
private_key = load_pkcs11_key(slot=0, pin=os.environ["HSM_PIN"])
```

This is the extensibility point that takes the proxy from software-rooted to
hardware-rooted trust without touching the ledger or proxy logic.

## Policy enforcement (DLP prevention)

Beyond logging, the proxy can **block** requests inline before they reach the
upstream. Inline DLP prevention runs on the request hot path and returns a
`403` when a payload matches an enforced policy:

- **Regex prevention** — a pattern match survives the allowlist, clears
  `DLP_REGEX_THRESHOLD`, and the pattern is explicitly opted into prevention
  (per-pattern, via a `dlp_prevention_patterns` row).
- **BERT prevention** — the classifier score clears `DLP_BERT_THRESHOLD` for a
  non-allowlisted label.

Design points:

- **Fail-open** — if the scanner is unreachable, the request is forwarded and a
  high-severity incident is raised rather than taking the gateway down.
- **Delta-only** — since LLM clients re-send the whole conversation each turn,
  only messages appended since the last *forwarded* entry are scanned, so a
  match in earlier history doesn't permanently block a session.
- Block responses never echo matched values — only pattern ids/names/severities.

The post-hoc scanner (`dlp.py`) still detects and alerts *after* forwarding;
inline prevention (`dlp_prevention.py`) is the enforcement counterpart.

## What it doesn't do (yet)

- Semantic anomaly detection on the behavioral stream
- Multi-agent correlation (linking entries across agents in the same task)
- Policy enforcement based on ledger state (history-aware block/allow)
- Distributed ledger / external verification
- Streaming reassembly for tool call extraction (tool calls in streamed responses
  are captured but reconstruction is partial)
