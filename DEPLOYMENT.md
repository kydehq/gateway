# Kyde Gateway — Deployment Guide

This document describes how to install and run the Kyde Gateway (the `kyde`
proxy plus its UI and DLP sidecars) on a single host. Two deployment paths are
supported:

1. **Docker Compose** — recommended for staging and production. Brings up the
   gateway, dashboard API, UI, and DLP services as a single unit.
2. **Local pip install** — for development, debugging, or embedding the proxy
   in an existing Python environment.

---

## 1. The two knobs: edition and posture

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
  `gateway-enterprise` pipeline (see `docs/ci.md`).

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

## 2. Prerequisites

| Component | Version | Notes |
| --- | --- | --- |
| Linux / macOS host | — | A container-capable OS. Windows works via WSL2. |
| Docker Engine | ≥ 24.0 | Required for the Compose path. |
| Docker Compose | v2 plugin | `docker compose` (not legacy `docker-compose`). |
| Python | ≥ 3.11 | Only needed for the pip-based install. |
| Open ports | 4000, 8080, 8081, 8501 | UI (agent proxy + admin) and direct dev ports. Adjust in `docker-compose.override.yml` if conflicting. |
| Outbound HTTPS | 443 | To reach OpenAI / Anthropic / Gemini / Copilot / GHCR. |
| TPM 2.0 device (optional) | — | Enterprise edition only, for hardware-backed signing. |

A Hugging Face token is required **only** if you enable the neural DLP
classifier (`--profile with-bert`); the default stack is regex-only and needs
no HF token.

---

## 3. Deployment A — Docker Compose (recommended)

### 3.1 Clone the repository

```bash
git clone https://github.com/kydehq/gateway.git
cd gateway
```

### 3.2 (Optional) create a `.env` file

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

### 3.3 Start the stack

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

### 3.4 Verify health

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

### 3.5 Generate signing keys (enterprise edition only)

The **sandbox** edition has no signing code — its ledger is hash-chained but
unsigned, so there is no key to generate. On the **enterprise** edition the ledger is
signed with an Ed25519 key stored in the shared `kyde-store` volume. Run
`keygen` inside either container — the other picks it up automatically:

```bash
docker compose exec kyde-gateway kyde keygen --type local
```

For TPM-backed signing, see §6.

### 3.6 Point an agent at the gateway

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

See the README for the full provider routing table.

### 3.7 Access the dashboard

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

### 3.8 Updating

```bash
git pull
docker compose pull            # refresh pulled sidecar images
docker compose up -d --build   # rebuild local images and restart
```

Ledger data and model caches live in named volumes (`postgres-data`,
`kyde-store`, `dlp-models`) and survive `up -d --build`.

---

## 4. Deployment A-prod — Production overlay with published images

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
  same prod overlay serves the enterprise image (`.env.prod`) or the public sandbox
  image (`.env.sandbox`) with no YAML change.

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

### 4.1 Image sources

| Service | Image | Published by |
| --- | --- | --- |
| `kyde-gateway` / `kyde-api` | `ghcr.io/kydehq/${GATEWAY_REPO}/gateway:${TAG}` | sandbox → `release-docker.yml`; enterprise → the `gateway-enterprise` pipeline. |
| `kyde-ui` | `ghcr.io/kydehq/${GATEWAY_REPO}/ui:${TAG}` | `release-docker.yml` (both editions). |
| `dlp-regex` / `dlp-bert` | `ghcr.io/kydehq/${DLP_REPO}/dlp-classifier-*:${DLP_*_VERSION}` | the DLP sidecar pipelines (licensed repo, both editions). |

### 4.2 Configure

```bash
cp .env.prod.example .env.prod        # enterprise edition
#   ...or .env.sandbox.example        # public core, hardened
# Edit it — set POSTGRES_PASSWORD and pin TAG / DLP versions. Dashboard
# credentials are not env-based; create the admin via /setup on first start.
```

### 4.3 Pull and start

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

### 4.4 Generate signing keys (enterprise edition, first run only)

```bash
docker compose --env-file .env.prod \
  -f docker-compose.yml -f docker-compose.prod.yml \
  exec kyde-gateway kyde keygen --type local
```

Keys persist in the `kyde-store` named volume and are picked up automatically
by `kyde-api` on its next request. (The sandbox edition has no signing — skip
this step.)

### 4.5 Reverse proxy (recommended)

Only the UI is published, on `127.0.0.1`, so nothing is reachable from outside
the host until a reverse proxy is placed in front. The two UI listeners have
different needs:

- **Agent proxy (`:4000`)** is public-facing and carries streaming LLM
  responses — `proxy_buffering off` is mandatory.
- **Admin surface (`:8080`)** is for operators. Put it on a separate hostname
  and gate it with mTLS, IP allow-listing, or an OIDC auth proxy. Never expose
  it on the same public hostname as the LLM gateway.

Two-`server` nginx example:

```nginx
# Public LLM gateway → UI agent listener
server {
    listen 443 ssl http2;
    server_name gateway.example.com;
    ssl_certificate     /etc/letsencrypt/live/gateway.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/gateway.example.com/privkey.pem;

    client_max_body_size 10m;
    proxy_read_timeout   300s;
    proxy_buffering      off;   # required for streaming LLM responses

    location / {
        proxy_pass         http://127.0.0.1:4000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}

# Internal admin dashboard → UI admin listener. Restrict access here.
server {
    listen 443 ssl http2;
    server_name ops.example.internal;
    ssl_certificate     /etc/letsencrypt/live/ops.example.internal/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ops.example.internal/privkey.pem;

    allow 10.0.0.0/8;
    allow 192.168.0.0/16;
    deny  all;

    location / {
        proxy_pass         http://127.0.0.1:8080;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

If you don't want to run nginx at all, reach the admin surface via SSH tunnel:

```bash
ssh -L 8080:127.0.0.1:8080 prod-host
# then open http://127.0.0.1:8080 in a local browser
```

### 4.6 Upgrading

```bash
# Update the version pins in .env.prod, then:
docker compose --env-file .env.prod \
  -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose --env-file .env.prod \
  -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Named volumes (`postgres-data`, `kyde-store`, `dlp-models`) are preserved
across upgrades.

---

## 5. Deployment B — Local pip install

Use this path for development, or if you want to run the gateway without
Docker.

### 5.1 Install

```bash
git clone https://github.com/kydehq/gateway.git
cd gateway

python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

This installs the `kyde` CLI entry point (sandbox/core only — the enterprise
`signing`/`enforce` packages come from the separate `kyde-enterprise` wheel).

### 5.2 Generate signing keys (enterprise edition only)

```bash
kyde keygen --type local    # Ed25519 software key (default)
# or
kyde keygen --type tpm      # requires `pip install .[tpm]`
```

Keys land in `~/.agent-ledger/`. `keygen` refuses to overwrite existing keys
unless `--force` is passed.

### 5.3 Run the proxy

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

### 5.4 Run the DLP sidecars

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

---

## 6. TPM-backed signing (enterprise edition, optional)

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

## 7. Configuration reference

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

---

## 8. Operational tasks

The commands below assume the prod overlay; for dev, drop
`--env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml`.

```bash
# Tail proxy logs
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f kyde-gateway

# Tail dashboard-API logs
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f kyde-api

# Recent ledger entries (either container has access to the volume)
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec kyde-gateway kyde ledger list

# Full chain integrity verification
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec kyde-gateway kyde ledger verify

# Stop everything (volumes preserved)
docker compose -f docker-compose.yml -f docker-compose.prod.yml down

# Stop and destroy ledger + model cache (DATA LOSS)
docker compose -f docker-compose.yml -f docker-compose.prod.yml down -v
```

### Postgres backups

The behavioral ledger, users, and DLP alerts live in the `postgres` container
(volume `postgres-data`). Back it up with `pg_dump`; the output restores
cleanly onto a fresh cluster.

```bash
# One-shot dump
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U kyde -Fc kyde > kyde-$(date -u +%Y%m%d).dump

# Restore (onto an empty Postgres)
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T postgres \
  pg_restore -U kyde -d kyde --clean --if-exists < kyde-YYYYMMDD.dump
```

Schedule the dump nightly via cron on the host (NOT inside the container):

```cron
15 2 * * *  cd /opt/gateway && docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T postgres pg_dump -U kyde -Fc kyde > /var/backups/gateway/kyde-$(date -u +\%Y\%m\%d).dump
```

Also back up the signing keys (`~/.agent-ledger/signing.key`, `tpm_key.pem`) on
the enterprise edition — without them, no previously signed entry can be verified,
even if the ledger data is intact. For compliance-grade point-in-time recovery
(WAL archiving across the day), layer `pgBackRest` or `barman` on top of the
cluster.

---

## 9. Troubleshooting

**`dlp-bert` stuck in `starting` state.** First-run model download can take a
few minutes. Check `docker compose logs dlp-bert`. If you see `401
Unauthorized`, the HF credentials are missing or invalid.

**Dashboard redirects to `/setup` and no admin exists.** Expected on a fresh
install — complete the form to create the admin account. If you see `/setup`
unexpectedly, the `users` table is empty; check the `postgres` volume is
mounted and you're connected to the right DB.

**Account locked after 3 failed logins.** An admin unlocks it from the Users
page. There is no time-based auto-unlock.

**Locked out of all admin accounts.** Shell into the API container and create a
rescue admin:
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec kyde-api \
  kyde admin create-admin --username rescue --email you@co
```
The command prints a one-time temp password; use it to sign in, then pick
a permanent password on first login.

**Message bodies show "auditor role required".** The signed-in user does not
hold the `auditor` role. Grant it from the admin's Users page — note an
admin cannot grant themselves the auditor role; either another admin does
it, or create a dedicated auditor account.

**Port 8081 (or 8080/4000) already in use.** Edit the left-hand side of the
port mapping in `docker-compose.override.yml` (`"8081:8000"` → e.g.
`"18081:8000"`).

**Ledger entries missing DLP fields.** Confirm the DLP sidecars are `healthy`
and that the thresholds are not set too high (`DLP_*_THRESHOLD=1.0` would
suppress every alert).

**`kyde keygen` refuses to run.** A key already exists in `~/.agent-ledger/`
(or the container volume). Re-run with `--force` only if you understand that
signatures produced under the old key will no longer verify against the new
public key. (Enterprise edition only — the sandbox edition does not sign.)
```

