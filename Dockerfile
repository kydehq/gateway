# Python runtime for the KYDE Gateway backend.
#
# Serves two distinct FastAPI apps depending on the CMD used:
#   - `kyde serve` / `uvicorn kyde.server:proxy_app`  → LLM proxy
#   - `kyde dashboard`                                 → admin/JSON API
#
# The compiled frontend lives in a separate nginx-based image (see
# `frontend/Dockerfile`) and is not embedded here. The dashboard still
# responds to /login, /setup, /change-password and /api/* itself — only
# the SPA shell and /assets/ moved out.
#
# Two editions = which distributions get installed (no file-stripping):
#   --build-arg EDITION=sandbox    free image: installs only the public core
#                                  (kyde-gateway). kyde.signing / kyde.enforce
#                                  are never installed, so the unsigned,
#                                  observe-only gateway is all that exists.
#   --build-arg EDITION=enterprise (default) full image: core + the private
#                                  kyde-enterprise wheel (signing + enforce),
#                                  which merges into the same `kyde` namespace
#                                  package. Ship the wheel as a build artifact:
#                                  drop kyde_enterprise-*.whl into ./wheels/
#                                  before building (no private index / token).
#
# `EDITION=paid` was the old name for `enterprise`; it is now rejected with a
# hard error so a stale caller never silently produces a sandbox image.
#
# The split is multi-stage on purpose: the runtime image copies only the
# installed virtualenv, never the source tree, so enterprise code that was never
# installed cannot linger in an intermediate layer.

# ---- builder ---------------------------------------------------------------
FROM python:3.14-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

ARG EDITION=enterprise

# Enterprise edition ships kyde-enterprise as a BUILD ARTIFACT, not from a private
# index: drop kyde_enterprise-*.whl into ./wheels/ before building. The wheel is
# consumed in this builder stage only — the runtime image copies just the
# installed venv, so the wheel file never lands in a shipped layer. For sandbox
# builds ./wheels/ is simply empty (only .gitkeep).
COPY wheels/ /tmp/wheels/

# Core (kyde-gateway) always installs from this repo. Enterprise additionally
# installs the local kyde-enterprise wheel, dropping kyde.signing + kyde.enforce
# into the shared `kyde` namespace package. Sandbox installs core only —
# find_spec then reports both features absent at runtime. An enterprise build
# with no wheel present is a hard error rather than a silent sandbox image, and
# any unknown EDITION (including the retired `paid`) fails loudly.
RUN pip install --upgrade pip && pip install . && \
    if [ "$EDITION" = "paid" ]; then \
        echo "ERROR: EDITION=paid was renamed to EDITION=enterprise — update your build arg"; exit 1; \
    fi; \
    if [ "$EDITION" = "enterprise" ]; then \
        ls /tmp/wheels/kyde_enterprise-*.whl >/dev/null 2>&1 || \
            { echo "ERROR: EDITION=enterprise but no kyde_enterprise-*.whl found in ./wheels/"; exit 1; }; \
        echo "==> ENTERPRISE edition: installing kyde-enterprise wheel from ./wheels/"; \
        pip install /tmp/wheels/kyde_enterprise-*.whl; \
    elif [ "$EDITION" = "sandbox" ]; then \
        echo "==> SANDBOX edition: core only (no enterprise packages installed)"; \
    else \
        echo "ERROR: unknown EDITION=$EDITION (expected 'enterprise' or 'sandbox')"; exit 1; \
    fi

# ---- runtime ---------------------------------------------------------------
FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:$PATH"

ARG EDITION=enterprise
# Informational only — the real gating is by module presence (find_spec).
ENV KYDE_EDITION=${EDITION}

WORKDIR /app

# Only the installed virtualenv crosses over — NOT the source tree — so a
# sandbox image carries no enterprise source in any layer.
COPY --from=builder /opt/venv /opt/venv

# Bundled DLP regex patterns — the gateway is the source of truth for the
# active set and pushes it to dlp-regex on startup. Same YAMLs that get
# volume-mounted into the dlp-regex container in docker-compose.
COPY dlp-patterns ./dlp-patterns

RUN useradd --create-home --uid 1000 kyde && \
    mkdir -p /home/kyde/.agent-ledger && \
    chown -R kyde:kyde /app /home/kyde

USER kyde

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/health', timeout=5).read()" || exit 1

CMD ["kyde", "serve", "--host", "0.0.0.0", "--port", "8000"]
