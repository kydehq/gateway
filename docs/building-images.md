# Building the KYDE Gateway container images

One `Dockerfile` (in this repo root) builds **two editions**, selected by the
`EDITION` build-arg. The edition is decided by *which distributions get
installed*, not by stripping files:

| Edition | `--build-arg` | What's installed | Runtime behavior |
|---|---|---|---|
| **sandbox** | `EDITION=sandbox` | `kyde-gateway` only (public core) | Unsigned, observe-only. `kyde.signing` / `kyde.enforce` are absent. |
| **enterprise** | `EDITION=enterprise` (default) | core **+** the `kyde-enterprise` wheel | Audit signing + inline enforcement active. |

Both builds run **from this (`gateway`) repo root** — that's the Docker build
context. The enterprise edition additionally needs the proprietary
`kyde-enterprise` wheel dropped into `./wheels/` (see §3). There is **no private
index** — the enterprise wheel ships as a build artifact.

> Why multi-stage: the `builder` stage installs into a venv; the `runtime` stage
> copies **only** that venv, never the source tree or the wheel file. So a
> sandbox image carries no enterprise code in any layer, and the enterprise wheel never
> lands in a shipped layer.

---

## 1. Prerequisites

- Docker (any recent version; the default BuildKit builder is fine — no special
  flags or secrets are needed).
- To build the **enterprise** image: the `kyde-enterprise` wheel, built from the
  `gateway-enterprise` repo (§3). Keep versions in lockstep — `kyde-enterprise`
  pins a matching `kyde-gateway` (both `0.1.0` today).
- The compiled SPA frontend is a **separate** nginx image (`frontend/Dockerfile`)
  and is not part of these backend images.

---

## 2. Build the sandbox image (public core only)

Nothing extra is required — `./wheels/` stays empty (only `.gitkeep`):

```bash
cd gateway
docker build --build-arg EDITION=sandbox -t kyde-gateway:0.1.0-sandbox .
```

Verify the edition inside the image:

```bash
docker run --rm kyde-gateway:0.1.0-sandbox \
  python -c "from kyde._features import edition, HAS_SIGNING, HAS_ENFORCEMENT; \
             print(edition(), HAS_SIGNING, HAS_ENFORCEMENT)"
# -> sandbox False False
```

---

## 3. Build the enterprise (enterprise) image

### 3a. Build the enterprise wheel (once per release)

In the **`gateway-enterprise`** repo:

```bash
cd ../gateway-enterprise
python -m build --wheel          # or: uv build --wheel
# -> dist/kyde_enterprise-0.1.0-py3-none-any.whl
```

### 3b. Drop the wheel into the core build context

```bash
cp ../gateway-enterprise/dist/kyde_enterprise-*.whl ./wheels/
```

`./wheels/*.whl` is gitignored in this public repo — the proprietary wheel must
never be committed here. It exists only in your local/CI build context.

### 3c. Build

```bash
cd gateway
docker build --build-arg EDITION=enterprise -t kyde-gateway:0.1.0-enterprise .
# EDITION=enterprise is the default, so `docker build -t ... .` also produces enterprise.
```

If you ask for `EDITION=enterprise` but `./wheels/` has no `kyde_enterprise-*.whl`, the
build **fails loudly** (`ERROR: EDITION=enterprise but no kyde_enterprise-*.whl found
in ./wheels/`) rather than silently producing a sandbox image.

Verify:

```bash
docker run --rm kyde-gateway:0.1.0-enterprise \
  python -c "from kyde._features import edition, HAS_SIGNING, HAS_ENFORCEMENT; \
             print(edition(), HAS_SIGNING, HAS_ENFORCEMENT)"
# -> enterprise True True

# confirm the wheel file did not leak into the runtime image:
docker run --rm kyde-gateway:0.1.0-enterprise sh -c \
  'ls /tmp/wheels 2>/dev/null && echo LEAKED || echo "no wheel in runtime (good)"'
```

---

## 4. Running an image

Both editions expose the proxy on port 8000 and start `kyde serve` by default:

```bash
docker run --rm -p 8000:8000 kyde-gateway:0.1.0-sandbox
# health: GET http://127.0.0.1:8000/health
```

The image runs as the non-root `kyde` user (uid 1000, `$HOME=/home/kyde`). It
needs a Postgres reachable via `DATABASE_URL`; see the deploy compose repos for a
full stack.

**Enterprise edition — persist the signing key.** Signing keys live in
`/home/kyde/.agent-ledger`. Mount a durable volume there or every redeploy
silently re-keys the ledger. Generate the key once with `kyde keygen` (or
`--type tpm`). Full details: `gateway-enterprise/docs/signing-install.md`.

```bash
docker run --rm -p 8000:8000 \
  -v kyde-keys:/home/kyde/.agent-ledger \
  kyde-gateway:0.1.0-enterprise
```

---

## 5. Tagging, pushing, and release notes

- **Tag by edition + version**, e.g. `kyde-gateway:0.1.0-sandbox` /
  `:0.1.0-enterprise`. Push the sandbox image to your public registry and the enterprise
  image to your private one:
  ```bash
  docker tag kyde-gateway:0.1.0-enterprise <registry>/kyde-gateway:0.1.0-enterprise
  docker push <registry>/kyde-gateway:0.1.0-enterprise
  ```
- **Version lockstep:** rebuild the enterprise wheel whenever the core version
  bumps; `kyde-enterprise==X` pins `kyde-gateway==X`.
- **CI tip:** don't commit the wheel — have CI build it in `gateway-enterprise`,
  copy it into the `gateway` build context, build the enterprise image, then discard.

### Runtime Python version
The image runtime and the dev/test venvs are both on Python 3.14 (`Dockerfile`
`FROM python:3.14-slim`, and `ci.yml` runs the suite on 3.14) — image/CI parity,
nothing to reconcile.
