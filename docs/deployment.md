# Kyde Gateway — Deployment Guide

> **Applies to Kyde Gateway `v0.3.1`** · last reviewed 2026-07-14.

This guide describes how to install and run the Kyde Gateway — the `kyde`
proxy plus its UI and DLP sidecars — on a single host, from a first
`docker compose up` to TLS, backups, and day-2 operations.

Two **editions** of the gateway exist, selected purely by which images you
run (see [§3](#3-the-two-knobs-edition-and-posture)):

- **Public / sandbox** — `ghcr.io/kydehq/gateway/{gateway,ui}`, built from
  this repository by `.github/workflows/release-docker.yml`. Hash-chained but
  unsigned ledger, observe-only DLP.
- **Enterprise** — `ghcr.io/kydehq/gateway-distribution/*`, licensed images
  with Ed25519/TPM audit signing and inline enforcement, built by the private
  `gateway-enterprise` pipeline (see [`ci.md`](./ci.md)).

Two **deployment paths** are supported:

1. **Docker Compose** — recommended for staging and production. Brings up the
   gateway, dashboard API, UI, and DLP services as a single unit.
2. **Local pip install** — for development, debugging, or embedding the proxy
   in an existing Python environment ([§8](#8-deployment-b--local-pip-install)).

For using the deployed system (roles, alerts, policies), see the
[`user manual`](./user-manual.md).

---

## 1. Architectural overview

The Kyde Gateway is an **LLM egress proxy with behavioral logging and data-loss
prevention (DLP)**. Agents, IDEs, and apps inside your network stop talking
directly to OpenAI / Anthropic / Gemini / Copilot / local LLMs; they talk to
the gateway, which:

- Authenticates the request path to a provider (auto-routed from the URL).
- Scans the prompt through two DLP engines (neural + regex) before forwarding.
- Streams the response back while recording a tamper-evident ledger entry
  (signed on the enterprise edition).
- Optionally re-validates flagged prompts through a local LLM validator.
- Exposes an admin dashboard for policy, users, alerts, and the ledger.

### Service topology

Up to seven containers, one docker network, one public entry point
(`dlp-bert` and `kyde-validator` are optional, profile-gated services):

```
                       ┌────────────────── host (single VM) ──────────────────┐
                       │                                                     │
  ┌──── users ────┐    │   ┌── kyde-ui (nginx) ───────────────────┐          │
  │ browsers      │────┼───▶ :80    admin SPA + /api/*            │          │
  │ (admins)      │    │   │                                      │          │
  └───────────────┘    │   │                                      │          │
                       │   │        ┌──────────────┐              │          │
  ┌── agents/IDEs ─┐   │   │ :4000  │              │              │          │
  │ openai/anthrop.│───┼───▶ /v1/*  │   kyde-api   │              │          │
  │ /gemini/...   │    │   │        │ (dashboard + │              │          │
  └───────────────┘    │   └───┬────┤   REST API)  │              │          │
                       │       │    └──────┬───────┘              │          │
                       │       │           │                      │          │
                       │       ▼           ▼                      │          │
                       │   ┌────────────────┐  ┌──────────────┐   │          │
                       │   │  kyde-gateway  │──▶  dlp-bert    │   │          │
                       │   │ (LLM proxy,    │  │  (neural)    │   │          │
                       │   │  4 workers)    │──▶  dlp-regex   │   │          │
                       │   └───────┬────────┘  └──────────────┘   │          │
                       │           │                               │          │
                       │           ├──▶  kyde-validator            │          │
                       │           │    (local Ollama/Gemma-3 4B)  │          │
                       │           ▼                               │          │
                       │   ┌────────────────┐                      │          │
                       │   │  postgres 16   │  ledger, users,      │          │
                       │   │  (internal)    │  DLP alerts (JSONB)  │          │
                       │   └────────┬───────┘                      │          │
                       │            │                              │          │
                       │            ▼                              │          │
                       │     named volumes:                        │          │
                       │     postgres-data, kyde-store,            │          │
                       │     dlp-models, kyde-validator-models     │          │
                       └──────────────────────────────────────────┼──────────┘
                                                                  │
                                           TLS (your nginx / ALB) │
                                                                  ▼
                                                              egress to
                                                             LLM providers
```

### What each service does

| Service | Image | Port | Role |
| --- | --- | --- | --- |
| `kyde-ui` | `${GATEWAY_REPO}/ui` | `127.0.0.1:8080` (admin), `127.0.0.1:4000` (LLM) in prod | nginx + SPA. The **only** container published to the host. Two listeners: `:80` for the admin surface, `:4000` for the LLM proxy. |
| `kyde-api` | `${GATEWAY_REPO}/gateway` | internal `:8501` | JSON API + auth flows (`/api/*`, `/login`, `/setup`). Reached via `kyde-ui :80`. |
| `kyde-gateway` | `${GATEWAY_REPO}/gateway` | internal `:8000` | LLM proxy. Auto-detects upstream from path. 4 uvicorn workers. Reached via `kyde-ui :4000`. |
| `postgres` | `postgres:16-alpine` | internal `:5432` | Behavioral ledger, users, DLP alerts. The only stateful tier that needs a backup story. |
| `dlp-regex` | `${DLP_REPO}/dlp-classifier-regex` | internal `:8000` | Regex DLP engine (Presidio / Gitleaks / OWASP CRS / Google DLP patterns). Always on. |
| `dlp-bert` | `${DLP_REPO}/dlp-classifier-bert` | internal `:8000` | Neural DLP classifier. Optional — `--profile with-bert`. |
| `kyde-validator` | `ollama/ollama:latest` | internal `:11434` | Local Gemma 3 4B via Ollama. Used by the dashboard to re-check flagged alerts; not inline in the request path. Optional — `--profile validator`. First start pulls ~3 GB of weights. |

`${GATEWAY_REPO}` is the edition switch (`gateway` = public, this repo;
`gateway-distribution` = enterprise) — see [§3](#3-the-two-knobs-edition-and-posture).
The DLP sidecar images ship from the licensed `gateway-distribution` registry
in both editions (`DLP_REPO`).

### Trust boundaries

- **Public**: whatever you front with TLS (see [§6.3](#63-put-tls-in-front)).
  In the prod posture only `kyde-ui` has host-bound ports, and both are on
  `127.0.0.1`. Nothing is reachable from the network until you put a
  reverse proxy in front.
- **Internal**: `gateway-network` (a docker bridge). All inter-service
  traffic stays on this network.
- **Egress**: `kyde-gateway` needs provider egress; `kyde-validator`
  needs `ollama.com` only for the first model pull. The other services do
  not need outbound internet access.

### Data surfaces

| Volume | Holds | Backup? |
| --- | --- | --- |
| `postgres-data` | Ledger, users, DLP alerts. | **Yes — nightly `pg_dump`.** |
| `kyde-store` | Ed25519 signing keys (`signing.key`, `signing.pub`) on the enterprise edition, plus the SMTP secret key. Shared between proxy (signs) and API (verifies). | Yes — backup once after `/setup`. |
| `dlp-models` | BERT model weights (~1 GB). | Optional — can be re-downloaded. |
| `kyde-validator-models` | Ollama/Gemma weights (~3 GB). | Optional — can be re-downloaded. |

---

## 2. Prerequisites

### 2.1 Host

| Component | Version | Notes |
| --- | --- | --- |
| Linux / macOS host | — | A container-capable OS. Windows works via WSL2. |
| Docker Engine | ≥ 24.0 | Required for the Compose path. |
| Docker Compose | v2 plugin | `docker compose` (not legacy `docker-compose`). |
| Python | ≥ 3.11 | Only needed for the pip-based install. |
| Open ports | 4000, 8080, 8081, 8501 | UI (agent proxy + admin) and direct dev ports. Adjust in `docker-compose.override.yml` if conflicting. |
| Outbound HTTPS | 443 | To reach OpenAI / Anthropic / Gemini / Copilot / GHCR. |
| TPM 2.0 device (optional) | — | Enterprise edition only, for hardware-backed signing. |

For production sizing (CPU/RAM/disk for a real user population), see
[§4](#4-sizing).

### 2.2 Accounts and registry access

- The **public images** (`ghcr.io/kydehq/gateway/*`) pull anonymously once
  published — no GitHub account needed.
- The **licensed images** (`ghcr.io/kydehq/gateway-distribution/*` — the
  enterprise gateway/UI **and the DLP sidecars in both editions**) require a
  GitHub account with read access to the `kydehq` packages, granted by Kyde.
  Log in once before pulling:

  ```bash
  docker login ghcr.io
  # username: your GitHub username
  # password: a PAT with read:packages scope
  ```

- A Hugging Face token is required **only** if you enable the neural DLP
  classifier (`--profile with-bert`); the default stack is regex-only and
  needs no HF token.

### 2.3 Network

- **Outbound HTTPS (443)** from the host to:
  - `ghcr.io` — pull images.
  - `ollama.com` — pull Gemma weights (first start only, `--profile validator`).
  - Whichever LLM providers your users will call
    (`api.openai.com`, `api.anthropic.com`, `generativelanguage.googleapis.com`,
    `api.githubcopilot.com`, …).
- **Two DNS names** recommended for production — one for the admin UI, one
  for the LLM proxy endpoint that agents will use. Example:
  - `gateway.company.com` → admin UI (`kyde-ui :80`)
  - `llm.company.com` → LLM proxy (`kyde-ui :4000`)
  You can collapse these onto a single hostname with path-based routing,
  but separate hostnames make ACLs much cleaner.

### 2.4 TLS certificates

Two server certificates (or one SAN cert covering both names). Let's Encrypt
works; so does any internal CA your org already trusts.

### 2.5 Email-notification relay (for DLP alerts)

Auditor users receive an email when a new DLP finding is detected. You
need to provide the delivery path; your admins will then configure
credentials and policy through the **Settings** page (admin UI). At
deploy time, make sure the following is in place:

- An authenticated SMTP relay reachable from the `kyde-api` container —
  typically on **587 (STARTTLS)** or **465 (implicit TLS / SMTPS)**.
  Plaintext `25` is supported for test environments only. (In the dev
  posture, `--profile dev` starts a `mailpit` mail-trap instead.)
- **Outbound egress** from the host on whichever submission port you
  pick. Add it to the outbound allow-list alongside `ghcr.io` and the
  LLM provider endpoints (see §2.3).
- A **From address** on a domain your relay is authorized to send for —
  misaligned senders will either bounce or land in spam.
- The SMTP password is stored **encrypted at rest** with an AES-GCM-256
  key at `/home/kyde/.agent-ledger/smtp_aes.key` inside the
  `kyde-store` volume. The key is generated automatically on first
  dashboard startup and **must be included in your volume backup story**
  ([§9.1](#91-nightly-postgres-backup)) alongside the Ed25519 signing keys.
  Losing the key is not destructive to the ledger or alerts, but every
  stored SMTP password becomes unrecoverable and an admin must re-enter
  it in the UI.

Credentials, trigger policy (e.g. only on first detection vs every
occurrence), and the auditor-recipient model are configured by an
administrator through the admin UI after deployment — see the
[`user manual`](./user-manual.md) for the step-by-step.

### 2.6 DLP scanners

The DLP pipeline runs as sidecars (`dlp-regex` always on, `dlp-bert`
opt-in — see §1) and is fully wired into the stack — no deploy-time
configuration is required. In production the regex pattern packs ship baked
into the `dlp-regex` image and load automatically; in the dev posture the
repo's [`dlp-patterns/`](../dlp-patterns) directory is mounted instead so
patterns can be edited locally (see [§10.3](#103-dlp-patterns)).

Alert review, allowlist management, the "Reapply allowlist" workflow,
dedup behavior, and the alert-lifecycle states are day-to-day operations
rather than deployment concerns — see the [`user manual`](./user-manual.md).

---

## 3. The two knobs: edition and posture

The Compose setup is driven by **one base file** plus small overlays. Two
independent choices decide what you run:

- **Edition — sandbox vs enterprise.** Purely an *image* choice, selected by the
  `GATEWAY_REPO` env var:
  - `gateway` → `ghcr.io/kydehq/gateway/gateway` — the public core: unsigned,
    observe-only. `signing`/`enforce` are simply not installed in the image.
  - `gateway-distribution` → the licensed image with Ed25519/TPM audit signing
    and inline enforcement.

  Nothing in the Compose YAML changes between editions — only the image
  reference. The enterprise gateway image is built and published by the private
  `gateway-enterprise` pipeline (see [`ci.md`](./ci.md)).

- **Posture — dev vs prod.** Selected by *which overlay file* you merge:
  - **dev** → `docker-compose.override.yml`, auto-merged by a bare
    `docker compose up`. Builds the image locally and publishes ports to the
    host.
  - **prod** → `docker-compose.prod.yml`, named explicitly with `-f`. Pulls
    pinned images, binds only the UI to loopback, and adds resource limits and
    log rotation.

Files at a glance:

| File | Role | When loaded |
| --- | --- | --- |
| `docker-compose.yml` | base — services, internal ports, image via `${GATEWAY_REPO}` | always |
| `docker-compose.override.yml` | dev overlay — local build + host ports | auto (bare `up`) |
| `docker-compose.prod.yml` | prod overlay — pull, loopback, limits | explicit `-f` |
| `docker-compose.regex-dev.yml` | swap in a locally-built `dlp-regex:local` | explicit `-f`, opt-in |
| `.env.sandbox` / `.env.prod` / `.env.enterprise-dev` | edition + version selection | `--env-file` |

Command matrix:

```bash
# dev, sandbox edition, built from source (the default)
docker compose up --build

# dev, enterprise edition (needs kyde-enterprise wheel in ./wheels/)
docker compose --env-file .env.enterprise-dev up --build

# prod, enterprise edition
docker compose --env-file .env.prod \
  -f docker-compose.yml -f docker-compose.prod.yml up -d

# prod posture on the public sandbox image (hardened free deployment)
docker compose --env-file .env.sandbox \
  -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Optional add-ons compose with any of the above:

| Flag | Effect |
| --- | --- |
| `--profile with-bert` | start the neural DLP classifier (`dlp-bert`); also set `DLP_BERT_ENABLED=true` |
| `--profile validator` | start the local Ollama validator LLM |
| `--profile dev` | start dev extras (`mailpit` mail-trap + `kyde-validator`) |
| `-f docker-compose.regex-dev.yml` | use a locally-built `dlp-regex:local` instead of the published image |

---

## 4. Sizing

Kyde traffic is bursty rather than uniform: the heavy load comes from
long-running IDE agents holding streaming connections for minutes, not from
short one-shot requests. As a reference point, for a population of **~250
users** we assume a **10–20 % concurrency ceiling** (≈ 30–55 active streams
at peak). The published `docker-compose.yml` is sized for this workload out
of the box; you only need to provide a host that can carry it.

### Recommended host (reference: ~250 users)

| Resource | Minimum | Recommended |
| --- | --- | --- |
| vCPU | 8 | **16** |
| RAM | 16 GB | **32 GB** |
| SSD | 100 GB | **200 GB** |
| Network | 1 Gbps symmetric | 1 Gbps symmetric |
| OS | Ubuntu 22.04 / Debian 12 / RHEL 9 | Ubuntu 24.04 LTS |

A **single VM** is sufficient. Horizontal scaling is only needed above ~1 000
active users.

Why 32 GB RAM? Container limits stacked (prod overlay):

| Service | `limits.memory` | Notes |
| --- | --- | --- |
| `postgres` | 2 GB | Grows with ledger volume. |
| `dlp-bert` | 4 GB | Model weights + inference. |
| `kyde-validator` | 4 GB | Gemma 3 4B on CPU. |
| `kyde-gateway` | 1 GB | 4 uvicorn workers. |
| `kyde-api` | 512 MB | — |
| `kyde-ui` | 128 MB | nginx. |
| `dlp-regex` | 256 MB | — |
| **Subtotal** | **~12 GB** | Leaves ~20 GB for OS, page cache, headroom. |

### Disk sizing

- **Postgres ledger**: plan ~2–5 MB per active user per month at typical
  chat volume. For 250 users over 24 months, budget **40–80 GB** of
  postgres data plus room for `pg_dump` on the same volume.
- **DLP models**: ~1 GB, stable.
- **Validator models**: ~3 GB, stable.
- **Logs**: each service caps at 10 MB × 5 rotations = 50 MB/service
  (350 MB total).

200 GB gives you a comfortable two-year runway before you need to prune.

### GPU (optional, requires a compose override)

GPUs are **not required** — the CPU host above is sized for ~250 users.

The stock `docker-compose.yml` runs everything on CPU. GPU access is
**not wired in by default**: neither `dlp-bert` nor `kyde-validator`
declare an NVIDIA device reservation, so even on a GPU host they will
run on CPU until you add one.

To enable GPU acceleration you need, on the host:

1. An NVIDIA GPU with a recent driver.
2. [`nvidia-container-toolkit`](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
   installed and `docker info | grep -i runtime` listing `nvidia`.

…and a small compose override file (so the stock compose stays untouched).
Create `docker-compose.gpu.yml` next to the prod file:

```yaml
services:
  dlp-bert:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
  kyde-validator:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

Then add `-f docker-compose.gpu.yml` to your usual `docker compose` command.

Notes and caveats:

- **Ollama / kyde-validator** auto-detects the GPU once the device is
  passed in and will offload Gemma 3 4B automatically — expect 30–60×
  speedup versus CPU.
- **dlp-bert** uses the GPU only if the image is the CUDA build of
  PyTorch. Confirm with your Kyde contact which `DLP_BERT_VERSION` tag
  is CUDA-enabled; otherwise passing the device is a no-op.
- Verify after start:
  `docker compose exec kyde-validator nvidia-smi` should list the GPU.

---

## 5. Deployment A — Docker Compose, dev posture

The dev posture builds the gateway and UI images locally from this repo and
publishes every service's port to the host. Use it for development and
evaluation; for production hosts skip to [§6](#6-deployment-a-prod--production-overlay-with-published-images).

### 5.1 Clone the repository

```bash
git clone https://github.com/kydehq/gateway.git
cd gateway
```

### 5.2 (Optional) create a `.env` file

A plain `docker compose up --build` runs the **sandbox** edition with sensible
defaults and needs no env file. Create one only to change the edition,
thresholds, or pinned versions:

```bash
# DLP scoring thresholds (0.0 = store everything flagged, 1.0 = store nothing)
DLP_BERT_THRESHOLD=0.5
DLP_REGEX_THRESHOLD=0.7

# Enable the neural classifier (requires --profile with-bert + an HF token)
DLP_BERT_ENABLED=true
HF_USER_ORG=kydehq
```

Dashboard authentication is DB-backed — no credentials live in env vars.
On first start the UI routes you to `/setup` to create the admin account;
subsequent users are added from the admin's **Users** page.

Never commit `.env` — it should be in `.gitignore`.

### 5.3 Start the stack

```bash
docker compose up -d --build
```

This builds the sandbox `kyde-gateway`/`kyde-api` images and the `kyde-ui`
image from the repo, pulls the `dlp-regex` sidecar, and starts five services:

- `kyde-gateway` — LLM proxy + `/health`, direct dev port **8081**
- `kyde-api` — admin JSON API + auth flows, direct dev port **8501**
- `kyde-ui` — nginx: admin surface on **8080**, agent LLM proxy on **4000**
- `dlp-regex` — regex DLP engine, dev port **8002**
- `postgres` — ledger + users + DLP alerts, loopback **5432**

`dlp-bert`, `kyde-validator`, and `mailpit` are off by default; start them with
`--profile with-bert`, `--profile validator`, or `--profile dev` respectively.

### 5.4 Verify health

```bash
# Proxy (direct) and agent proxy (through the UI)
curl -fsS http://localhost:8081/health
curl -fsS http://localhost:4000/health

# Admin surface (returns HTML 200)
curl -fsS -o /dev/null -w "%{http_code}\n" http://localhost:8080/

# DLP regex sidecar
curl -fsS http://localhost:8002/health

# Compose-level view
docker compose ps
```

All services should be `healthy` within ~40 seconds (if you enabled
`dlp-bert`, it is the slowest because it loads model weights).

### 5.5 Generate signing keys (enterprise edition only)

The **sandbox** edition has no signing code — its ledger is hash-chained but
unsigned, so there is no key to generate. On the **enterprise** edition the
ledger is signed with an Ed25519 key stored in the shared `kyde-store` volume.
Run `keygen` inside either container — the other picks it up automatically:

```bash
docker compose exec kyde-gateway kyde keygen --type local
```

For TPM-backed signing, see [§8.5](#85-tpm-backed-signing-enterprise-edition-optional).

### 5.6 Point an agent at the gateway

A single gateway instance handles **all supported providers simultaneously**.
The upstream is auto-detected from the request path — no `UPSTREAM`
environment variable, no headers, no per-provider configuration. Explicit
prefixes (`/openai/`, `/anthropic/`, `/claude/`, `/gemini/`, `/copilot/`)
take precedence; otherwise `/v1/messages` is routed to Anthropic and
everything else defaults to OpenAI.

Auth headers are forwarded as-is, so each request must carry the correct key
for its target upstream. In dev you can hit the gateway directly on `8081`, or
go through the UI's agent listener on `4000` (the path used in prod).

```bash
# OpenAI-style client
export OPENAI_BASE_URL=http://localhost:8081/v1
export OPENAI_API_KEY=sk-...

# Anthropic-style client
export ANTHROPIC_BASE_URL=http://localhost:8081
export ANTHROPIC_API_KEY=sk-ant-...
```

See the [reference](./reference.md#multi-provider-routing) for the full provider routing table.

### 5.7 Access the dashboard

In dev the admin surface is on host port **8080** (the UI's `:80` listener).

**First start — bootstrap the admin.** Open `http://localhost:8080/`; on an
empty DB the UI routes you to `/setup` to create the admin account. The
username is fixed as `admin`; pick an email + a password meeting the policy
(≥ 12 chars, 1 upper, 1 lower, 1 digit, 1 special). You are logged in
automatically.

**Manage other accounts.** As admin, open the **Users** page (sidebar, admin
only). You can add users, set their roles, reset their passwords, unlock
accounts, and soft-delete.

**Roles and what they see:**

| Role | Sees |
| --- | --- |
| `viewer` | All metadata: entries, agents, tokens, hashes, signatures, tool-call names and arguments, incidents, DLP alerts (scanner/score/status/finding labels). Message bodies and DLP `matched_value` / `context_snippet` are redacted with a placeholder. |
| `auditor` | Everything a viewer sees, **plus** the captured prompt/reasoning bodies (`Reasoning Context (Why)`, `Full Message History`, per-session `why_last`) and the raw DLP finding values. An auditor implicitly has viewer capability. |
| `admin` | All metadata + the Users page. Admins are **not** implicitly auditors — to inspect message bodies an admin must also hold the auditor role. Admins cannot grant themselves the auditor role (a different admin must do it, or the admin creates a dedicated auditor account). |

Enforcement is server-side: `/api/entry/{ref}` returns `content_redacted: true`
and empty `why_parsed` / `full_messages_parsed` for anyone without the auditor
role, so it cannot be bypassed from the browser. The sidebar shows the signed-in
user with their role chips; admin is highlighted red, auditor amber.

**First-login password change.** When an admin creates or resets a user, the
server generates a 16-char temp password **shown once** in the admin's modal.
Give it to the user out-of-band; on their first login they are forced to set
a new password meeting the same policy.

**Account lockout.** Three consecutive failed logins lock the account. An
admin unlocks it from the Users page — there is no time-based auto-unlock.

### 5.8 Updating

```bash
git pull
docker compose pull            # refresh pulled sidecar images
docker compose up -d --build   # rebuild local images and restart
```

Ledger data and model caches live in named volumes (`postgres-data`,
`kyde-store`, `dlp-models`) and survive `up -d --build`.

---

## 6. Deployment A-prod — Production overlay with published images

For production hosts, merge `docker-compose.prod.yml` **on top of** the base
`docker-compose.yml`. Key differences from the dev posture:

- **Pinned, published images** from GHCR. Nothing is built on the host.
- **Only the UI is published**, and only on `127.0.0.1`. The gateway and API
  are internal (`expose`) and reached through the UI's nginx listeners, so no
  admin surface and no direct proxy port are exposed to the internet.
- `restart: always` replaces `unless-stopped`.
- Resource limits, graceful stop timeouts, and JSON log rotation (10 MB × 5
  files per service) are set.
- The **edition** is chosen entirely by the env file (`GATEWAY_REPO`) — the
  same prod overlay serves the enterprise image (`.env.prod`) or the public
  sandbox image (`.env.sandbox`) with no YAML change.

### Service layout (prod)

| Service | Host port | Scope | Role |
| --- | --- | --- | --- |
| `kyde-ui` | `127.0.0.1:4000` → `:4000` | Public (via TLS) | Agent LLM proxy (`/v1/*`). |
| `kyde-ui` | `127.0.0.1:8080` → `:80` | Internal / restricted | Admin surface (SPA + `/api/*`). |
| `kyde-gateway` | (not published) | Internal | LLM proxy origin, 4 uvicorn workers. |
| `kyde-api` | (not published) | Internal | Admin API + auth. |
| `dlp-regex` | (not published) | Internal | Regex DLP engine. |
| `dlp-bert` | (not published) | Internal, `--profile with-bert` | Neural DLP classifier. |
| `kyde-validator` | (not published) | Internal, `--profile validator` | Local validator LLM. |

### 6.1 Image sources

| Service | Image | Published by |
| --- | --- | --- |
| `kyde-gateway` / `kyde-api` | `ghcr.io/kydehq/${GATEWAY_REPO}/gateway:${TAG}` | sandbox → `release-docker.yml` (this repo); enterprise → the `gateway-enterprise` pipeline. |
| `kyde-ui` | `ghcr.io/kydehq/${GATEWAY_REPO}/ui:${TAG}` | `release-docker.yml` (both editions). |
| `dlp-regex` / `dlp-bert` | `ghcr.io/kydehq/${DLP_REPO}/dlp-classifier-*:${DLP_*_VERSION}` | the DLP sidecar pipelines (licensed repo, both editions). |

### 6.2 Configure, pull, and start

```bash
cp .env.prod.example .env.prod        # enterprise edition
#   ...or .env.sandbox.example        # public core, hardened
# Edit it — set POSTGRES_PASSWORD (e.g. `openssl rand -base64 32`) and pin
# TAG / DLP versions. Dashboard credentials are not env-based; create the
# admin via /setup on first start.
```

Log in to GHCR if you deploy the enterprise edition or pull the DLP sidecars
(see [§2.2](#22-accounts-and-registry-access)), then:

```bash
docker compose --env-file .env.prod \
  -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose --env-file .env.prod \
  -f docker-compose.yml -f docker-compose.prod.yml up -d
docker compose --env-file .env.prod \
  -f docker-compose.yml -f docker-compose.prod.yml ps
```

You **must** pass both `-f` files: `docker-compose.prod.yml` carries only the
hardening deltas and relies on the base for the service definitions. Naming the
files explicitly is also what suppresses the dev `docker-compose.override.yml`
(which would otherwise re-introduce local builds and host ports).

> **Tip:** export `COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml` and
> `COMPOSE_ENV_FILE=.env.prod` in the deploy shell to drop the repeated flags.
> The remaining prod examples in this guide assume that.

First start takes a while if you enabled the optional profiles
(`kyde-validator` pulls ~3 GB of model weights). `docker compose ps` should
eventually show every started service as `healthy`.

Verify from the host:

```bash
# UI nginx liveness — should return "ok"
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:4000/healthz

# Gateway health through the UI's proxy listener
curl -fsS http://127.0.0.1:4000/health
```

Nothing is reachable from outside the host yet — `kyde-ui` is bound to
`127.0.0.1` on both ports. That is intentional; §6.3 puts TLS in front.

### 6.3 Put TLS in front

The recommended topology is a **host-level nginx** (or Traefik / Caddy /
HAProxy / cloud ALB) terminating TLS and forwarding to the two UI
listeners. The two listeners have different needs:

- **Agent proxy (`:4000`)** is public-facing and carries streaming LLM
  responses — `proxy_buffering off` is mandatory.
- **Admin surface (`:8080`)** is for operators. Put it on a separate hostname
  and gate it with mTLS, IP allow-listing, or an OIDC auth proxy. Never expose
  it on the same public hostname as the LLM gateway — keeping them on separate
  hostnames is what makes the IP allow-list on the admin side actually
  meaningful.

Example for a host-installed nginx:

```nginx
# Admin dashboard — restrict to your corp network.
server {
    listen 443 ssl http2;
    server_name gateway.company.com;
    ssl_certificate     /etc/letsencrypt/live/gateway.company.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/gateway.company.com/privkey.pem;

    allow 10.0.0.0/8;
    allow 192.168.0.0/16;
    deny  all;

    client_max_body_size 25m;

    location / {
        proxy_pass              http://127.0.0.1:8080;
        proxy_http_version      1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

# LLM proxy — what agents point at. Streaming; buffering MUST be off.
server {
    listen 443 ssl http2;
    server_name llm.company.com;
    ssl_certificate     /etc/letsencrypt/live/llm.company.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/llm.company.com/privkey.pem;

    client_max_body_size 25m;
    proxy_read_timeout   600s;
    proxy_send_timeout   600s;
    proxy_buffering      off;
    proxy_request_buffering off;

    location / {
        proxy_pass              http://127.0.0.1:4000;
        proxy_http_version      1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection        "";
    }
}
```

If you don't want to run nginx at all, reach the admin surface via SSH tunnel:

```bash
ssh -L 8080:127.0.0.1:8080 prod-host
# then open http://127.0.0.1:8080 in a local browser
```

### 6.4 Upgrading

```bash
# Update the version pins (TAG, DLP_*_VERSION) in .env.prod, then:
docker compose pull
docker compose up -d
```

Named volumes (`postgres-data`, `kyde-store`, `dlp-models`) are preserved
across upgrades — your ledger, DLP alerts, users, and signing keys survive.

---

## 7. First-run tasks

### 7.1 Create the admin account

Open `https://gateway.company.com/` in a browser (or `http://localhost:8080/`
in dev). Because the database is empty, the UI routes you to `/setup`. Pick:

- **email** — your administrator's email
- **password** — ≥ 12 chars, at least one upper, lower, digit, special

The username is fixed as `admin`. You are logged in automatically after
setup. From here:

- **Users page** — add regular users, set roles, reset passwords.
- **Alerts page** — review DLP alerts, optionally re-validate through
  `kyde-validator`.
- **Ledger page** — browse request/response entries.
- **Configuration page** — see the active upstream routing table.

### 7.2 Generate and back up the signing keys (enterprise edition only)

Generate the Ed25519 key once (first run only — see §5.5 for details, or
[§8.5](#85-tpm-backed-signing-enterprise-edition-optional) for TPM):

```bash
docker compose exec kyde-gateway kyde keygen --type local
```

Then copy the `kyde-store` volume contents off the host:

```bash
docker compose cp kyde-gateway:/home/kyde/.agent-ledger \
  ./kyde-store-backup-$(date +%F)
```

Store this somewhere only your security team can reach. If you lose it, the
old ledger entries can still be *read*, but no new signatures will verify
against the new key after a re-keygen. (The sandbox edition has no signing
keys, but the same volume holds the SMTP secret key — see §2.5.)

### 7.3 Point agents at the gateway

The gateway auto-routes based on the URL path. Point each client at
`https://llm.company.com/` with the **same API key the client would
normally use** — auth headers are passed through untouched.

```bash
# OpenAI-style clients (VS Code, Cursor, many SDKs)
export OPENAI_BASE_URL=https://llm.company.com/v1
export OPENAI_API_KEY=sk-...

# Anthropic-style clients (Claude Code, Claude SDK)
export ANTHROPIC_BASE_URL=https://llm.company.com
export ANTHROPIC_API_KEY=sk-ant-...

# Google Gemini
export GEMINI_BASE_URL=https://llm.company.com/gemini

# GitHub Copilot Business (enterprise plan detection applies)
# Follow your Copilot client's proxy configuration.
```

If you added custom upstreams in `config.yaml` (see
[§10.2](#102-upstream-routing--configyaml)), they appear as new path prefixes
automatically, e.g. `https://llm.company.com/ollama/v1/…`.

---

## 8. Deployment B — Local pip install

Use this path for development, or if you want to run the gateway without
Docker.

### 8.1 Install

```bash
git clone https://github.com/kydehq/gateway.git
cd gateway

python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

This installs the `kyde` CLI entry point (sandbox/core only — the enterprise
`signing`/`enforce` packages come from the separate `kyde-enterprise` wheel).

### 8.2 Generate signing keys (enterprise edition only)

```bash
kyde keygen --type local    # Ed25519 software key (default)
# or
kyde keygen --type tpm      # requires `pip install .[tpm]`
```

Keys land in `~/.agent-ledger/`. `keygen` refuses to overwrite existing keys
unless `--force` is passed.

### 8.3 Run the proxy

```bash
kyde serve --host 0.0.0.0 --port 8000
# Then visit http://localhost:8501/ to run the /setup flow, or bootstrap a
# first admin from the CLI:
kyde admin create-admin --username admin --email you@example.com
```

For higher throughput run multiple Uvicorn workers:

```bash
uvicorn kyde.server:proxy_app --host 0.0.0.0 --port 8000 --workers 4
```

### 8.4 Run the DLP sidecars

The gateway expects `dlp-bert` on port 8001 and `dlp-regex` on port 8002. In
this deployment mode you can still start just those two as containers:

```bash
docker run -d --name dlp-regex -p 8002:8000 \
  ghcr.io/kydehq/gateway-distribution/dlp-classifier-regex:v0.2.0
docker run -d --name dlp-bert  -p 8001:8000 \
  -e MODEL_TYPE=enhanced -e HF_USER_ORG=kydehq \
  ghcr.io/kydehq/gateway-distribution/dlp-classifier-bert:v0.2.0
```

If neither sidecar is reachable, the gateway continues to serve traffic but
DLP scanning is skipped.

### 8.5 TPM-backed signing (enterprise edition, optional)

For hardware-rooted trust:

1. Confirm the host has a TPM 2.0 device at `/dev/tpm0` and `/dev/tpmrm0`.
2. Install the extra: `pip install kyde-gateway[tpm]` (or rebuild the image with
   `tpm2-pytss` added to `pyproject.toml`).
3. In Docker, mount the TPM device into the `kyde-gateway` service:

   ```yaml
   devices:
     - /dev/tpmrm0:/dev/tpmrm0
   ```

4. Generate the key: `kyde keygen --type tpm`.
5. Verify: `kyde key` — the active key should report `ECDSA-P256` and
   `TPM Status: ✓`.

The private key never leaves the TPM; only the public key is exported.

---

## 9. Day-2 operations

The commands below assume the prod overlay with `COMPOSE_FILE` /
`COMPOSE_ENV_FILE` exported (§6.2); for dev, a bare `docker compose` works.

### 9.1 Nightly Postgres backup

The behavioral ledger, users, and DLP alerts live in the `postgres` container
(volume `postgres-data`). Back it up with `pg_dump`; the output restores
cleanly onto a fresh cluster. Create `/etc/cron.daily/kyde-pgdump` (adjust
`/opt/gateway` to your checkout path):

```bash
#!/bin/sh
set -e
BACKUP_DIR=/var/backups/kyde
DATE=$(date +%F)
mkdir -p "$BACKUP_DIR"
cd /opt/gateway
docker compose --env-file .env.prod \
    -f docker-compose.yml -f docker-compose.prod.yml \
    exec -T postgres pg_dump -U kyde -Fc kyde > "$BACKUP_DIR/kyde-$DATE.dump"
find "$BACKUP_DIR" -name 'kyde-*.dump' -mtime +30 -delete
```

Then `chmod +x` it. Restore (onto an empty Postgres):

```bash
docker compose exec -T postgres \
  pg_restore -U kyde -d kyde --clean --if-exists < kyde-YYYY-MM-DD.dump
```

Also back up the `kyde-store` volume (signing keys on enterprise, SMTP secret
key on both editions — see §7.2). Without the signing keys, no previously
signed entry can be verified even if the ledger data is intact. For
compliance-grade point-in-time recovery (WAL archiving across the day), layer
`pgBackRest` or `barman` on top of the cluster.

### 9.2 Logs and health

```bash
# Tail a service
docker compose logs -f kyde-gateway
docker compose logs -f kyde-api

# All at once
docker compose logs -f

# Recent ledger entries (either container has access to the volume)
docker compose exec kyde-gateway kyde ledger list

# Full chain integrity verification
docker compose exec kyde-gateway kyde ledger verify
```

### 9.3 Stop / start / destroy

```bash
# Stop the stack (volumes preserved)
docker compose down

# Restart after a reboot — compose remembers state thanks to `restart: always`,
# but if needed:
docker compose up -d

# ☠ DANGER — destroys the ledger, users, alerts, and model caches:
docker compose down -v
```

### 9.4 Admin recovery

If every admin account is locked out of the dashboard, shell into the API
container and create a rescue admin:

```bash
docker compose exec kyde-api \
  kyde admin create-admin --username rescue --email rescue@company.com
```

The command prints a one-time temp password; use it to sign in, reset the
primary admin, then delete `rescue` from the Users page.

---

## 10. Configuration reference

### 10.1 Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `REGISTRY` | `ghcr.io/kydehq` | Container registry root. |
| `GATEWAY_REPO` | `gateway` | **The edition switch.** `gateway` (public core, this repo) or `gateway-distribution` (enterprise). Selects the `gateway`/`ui` image path. |
| `DLP_REPO` | `gateway-distribution` | Registry path for the DLP sidecars (licensed repo in both editions). |
| `TAG` | `v0.3.1` | Gateway + UI image tag. |
| `DLP_BERT_VERSION` / `DLP_REGEX_VERSION` | `v0.2.0` | DLP sidecar image tags. |
| `EDITION` | `sandbox` | Build arg for **local** dev builds only (`sandbox` or `enterprise`); enterprise also needs `./wheels/kyde_enterprise-*.whl`. |
| `POSTGRES_PASSWORD` | `kyde-dev-only` | **Set a strong value in prod.** Password for the Postgres `kyde` user. `openssl rand -base64 32`. |
| `DATABASE_URL` | `postgresql://kyde:$POSTGRES_PASSWORD@postgres:5432/kyde` | Used by `kyde-gateway` and `kyde-api`. Override only for an external Postgres. |
| `DLP_BERT_ENABLED` | `false` | Whether the gateway calls `dlp-bert` (pair with `--profile with-bert`). |
| `DLP_BERT_THRESHOLD` / `DLP_REGEX_THRESHOLD` | `0.5` / `0.7` | Minimum score (0.0–1.0) to persist a DLP alert. |
| `HF_USER_ORG` | `kydehq` | HF org/user namespace for the DLP-BERT model. |
| `VALIDATOR_MODEL` | `gemma3:4b` | Ollama model for `kyde-validator`. |

Dashboard credentials are not env vars. Users live in the `users` table in
Postgres. The first admin is created via `/setup` on first start; thereafter
the admin manages accounts from the Users page, or via
`kyde admin create-admin` for recovery.

Ports exposed on the host differ by posture:

**Dev (`docker-compose.yml` + `docker-compose.override.yml`, published for local access):**

| Host port | Container | Service |
| --- | --- | --- |
| `8080` | `kyde-ui:80` | Admin surface (SPA + `/api/*`) |
| `4000` | `kyde-ui:4000` | Agent LLM proxy (`/v1/*`) |
| `8081` | `kyde-gateway:8000` | LLM gateway + `/health` (direct) |
| `8501` | `kyde-api:8501` | Admin API (direct) |
| `8002` | `dlp-regex:8000` | Regex DLP engine |
| `8001` | `dlp-bert:8000` | BERT DLP classifier (`--profile with-bert`) |
| `127.0.0.1:5432` | `postgres:5432` | Postgres (for the host test suite) |
| `127.0.0.1:11435` | `kyde-validator:11434` | Validator LLM (`--profile dev`/`validator`) |
| `8025` | `mailpit:8025` | Dev mail-trap UI (`--profile dev`) |

**Prod (`docker-compose.yml` + `docker-compose.prod.yml`, UI only, loopback):**

| Host port | Container | Service | Reachability |
| --- | --- | --- | --- |
| `127.0.0.1:4000` | `kyde-ui:4000` | Agent LLM proxy | Public, via reverse-proxy TLS |
| `127.0.0.1:8080` | `kyde-ui:80` | Admin surface | Internal only |
| (none) | `kyde-gateway` / `kyde-api` / `dlp-*` | — | Internal (`expose`) |

### 10.2 Upstream routing — `config.yaml`

The repo-root [`config.yaml`](../config.yaml) is mounted into both
`kyde-gateway` and `kyde-api` at `/app/config.yaml`. Entries add new upstreams
or override the built-in defaults (`openai`, `anthropic`, `gemini`,
`copilot`). Adding an entry makes the name a valid path prefix immediately —
no rebuild needed:

```yaml
upstreams:
  ollama:
    base: http://192.168.68.116:11434
    api_prefix: ""
  vllm:
    base: http://vllm.internal.company.com:8000
    api_prefix: /v1
```

After `docker compose up -d`, calls to
`https://llm.company.com/ollama/v1/chat/completions` route to the first
entry, `/vllm/v1/…` to the second. See the [reference](./reference.md#custom-and-local-upstreams) for the
full format.

### 10.3 DLP patterns

The regex engine ships with five pattern packs baked into the `dlp-regex`
image: `common_regex`, `gitleaks`, `google_dlp`, `owasp_crs`, `presidio`
(sources in [`dlp-patterns/`](../dlp-patterns)). In production they load
from the image — to add or change patterns, deploy an updated `dlp-regex`
image and bump `DLP_REGEX_VERSION` (§6.4). In the dev posture the local
`dlp-patterns/` directory is mounted instead, so pattern edits apply on
restart without a rebuild.

Allowlisting and tuning which findings become alerts is done at runtime from
the admin UI — see the [`user manual`](./user-manual.md).

---

## 11. Troubleshooting

**`dlp-bert` stuck in `starting` state.** First-run model download can take a
few minutes; check `docker compose logs dlp-bert` and validate host
memory/CPU pressure. If you see `401 Unauthorized`, the HF credentials are
missing or invalid.

**`kyde-validator` stuck in `starting` for more than 10 minutes.** Gemma
weights are ~3 GB. Check `docker compose logs kyde-validator`. It will
retry automatically on first real request if the pre-pull fails.

**Browser hits `gateway.company.com` and gets a 502.** The host nginx is
reaching `127.0.0.1:8080` but the container is not healthy yet. Run
`docker compose ps`; wait for `kyde-ui` to be `healthy`.

**Agents get `502 Bad Gateway` or truncated responses on long answers.**
Your host-level reverse proxy is buffering. Set `proxy_buffering off;`
and `proxy_read_timeout 600s;` on the `:4000` server block (see §6.3).

**Dashboard redirects to `/setup` and no admin exists.** Expected on a fresh
install — complete the form to create the admin account. If you see `/setup`
unexpectedly, the `users` table is empty; check the `postgres` volume is
mounted and you're connected to the right DB.

**Account locked after 3 failed logins.** An admin unlocks it from the Users
page. There is no time-based auto-unlock.

**Locked out of all admin accounts.** See [§9.4](#94-admin-recovery).

**Message bodies show "auditor role required".** The signed-in user does not
hold the `auditor` role. Grant it from the admin's Users page — note an
admin cannot grant themselves the auditor role; either another admin does
it, or create a dedicated auditor account.

**Port 8081 (or 8080/4000) already in use.** Edit the left-hand side of the
port mapping in `docker-compose.override.yml` (`"8081:8000"` → e.g.
`"18081:8000"`).

**DLP alerts not showing in the UI / ledger entries missing DLP fields.**
Both sidecars must be `healthy`, and the thresholds must not be set so high
that everything is suppressed (`DLP_*_THRESHOLD=1.0` stores nothing). See the
[`user manual`](./user-manual.md).

**`POSTGRES_PASSWORD must be set` at boot.** The env file was not passed.
Use `--env-file .env.prod` (or `.env.sandbox`) on every `docker compose`
invocation — or export `COMPOSE_ENV_FILE` (§6.2).

**`kyde keygen` refuses to run.** A key already exists in `~/.agent-ledger/`
(or the container volume). Re-run with `--force` only if you understand that
signatures produced under the old key will no longer verify against the new
public key. (Enterprise edition only — the sandbox edition does not sign.)

**Ledger says entries don't verify after a restore.** The signing key in
`kyde-store` was restored from a different generation than the ledger.
Restore the matching `kyde-store` backup (§7.2).

---

## 12. Enterprise support

Licensed customers can reach Kyde at `support@kyde.com`. Include:

- Output of `docker compose ps`.
- Last 200 lines of the failing service's logs.
- The `TAG`, `DLP_BERT_VERSION`, `DLP_REGEX_VERSION` pins from your env file
  (secrets **redacted**).
- Host OS, kernel, and docker engine version.

---

## Appendix — Container changes: v0.1.3 → v0.3.x

For operators upgrading from the older `v0.1.3` deployment baseline, these are
the practical container-level changes to account for:

| Area | v0.1.3 baseline | v0.3.x baseline |
| --- | --- | --- |
| Gateway image pin variable | `KYDE_VERSION` | `TAG` (was `GATEWAY_VERSION` in the old distribution compose) |
| Gateway/UI tag | `v0.1.3` | `v0.3.1` |
| BERT DLP sidecar tag | `v0.1.10` | `v0.2.0` |
| Regex DLP sidecar tag | `v0.1.5` | `v0.2.0` |
| dlp-bert startup secret | `HF_TOKEN` required | no Hugging Face token required |

What did **not** change: the production stack is still the same service set
(`postgres`, `kyde-gateway`, `kyde-api`, `kyde-ui`, `dlp-regex`, plus the
optional `dlp-bert` and `kyde-validator`) on one internal network.
