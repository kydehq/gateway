# syntax=docker/dockerfile:1.7

# --------------------------------------------------------------------------
# Stage 1 — Build the Vite bundle.
#
# Pinned to $BUILDPLATFORM: the bundle is static files (arch-independent),
# so node/npm always run natively on the build host instead of under qemu
# emulation — npm segfaults ("Illegal instruction") when emulating arm64
# on the amd64 CI runners. Only the nginx stage below is per-arch.
# --------------------------------------------------------------------------
FROM --platform=$BUILDPLATFORM node:20-alpine AS build

WORKDIR /build

COPY package.json package-lock.json* ./
RUN npm ci || npm install

COPY . .
RUN npm run build


# --------------------------------------------------------------------------
# Stage 2 — nginx serving the bundle + reverse-proxying the backends.
#
# Routes:
#   /api/           → kyde-api:8501   (dashboard JSON API + auth)
#   /login, /setup, /logout, /change-password, /openapi.json
#                   → kyde-api:8501   (server-rendered auth flows)
#   /v1/            → kyde-gateway:8000     (LLM proxy)
#   everything else → static bundle (SPA shell + /assets/*)
# --------------------------------------------------------------------------
FROM nginx:1.27-alpine

# Drop the stock default.conf — it conflicts with ours.
RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/kyde.conf
COPY --from=build /build/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=3s --retries=3 \
    CMD wget -qO- http://127.0.0.1/healthz >/dev/null || exit 1
