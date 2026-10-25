# syntax=docker/dockerfile:1.7
# mailr: one image for the API/web UI and the worker.

# Base images are pinned (version tag + digest); see site/docs/admin/upgrades.md "Pinned images".
# The web and docs stages produce files that do not depend on the CPU architecture, so a multi-arch
# build runs them once, natively ($BUILDPLATFORM), instead of under emulation for each platform.
FROM --platform=$BUILDPLATFORM node:22.23.3-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# Brotli and gzip copies of the build, served as they are (backend core/webstatic.py).
RUN npm run build && node scripts/precompress.mjs dist

# The user guide for /help/ (backend/mailr/core/helpdocs.py, site/build_help.py): the docs site
# with the pinned versions of site/requirements-docs.lock and Mermaid pinned by checksum
# (MIT; the same version and checksum as site/build_help.py).
FROM --platform=$BUILDPLATFORM python:3.13.15-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b AS docs
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /site
COPY site/requirements-docs.lock ./
RUN pip install --require-hashes --no-deps -r requirements-docs.lock
ADD --checksum=sha256:581ed7d74bd9048d0e3a91363927d72ef22942d7722546b27f7cc29e35390eb8 \
    https://cdn.jsdelivr.net/npm/mermaid@11.17.2/dist/mermaid.min.js /vendor/mermaid.min.js
COPY site/ ./
RUN python build_help.py --mermaid /vendor/mermaid.min.js --out /help

FROM python:3.13.15-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    MAILR_DATA_DIR=/data XDG_CACHE_HOME=/data/.cache HOME=/data/home
# postgresql-client-17: pg_dump and pg_restore for the nightly database dump and the restore drill
# (core/dbdump.py); the same major version as the server (Debian 13 ships 17).
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl tini postgresql-client-17 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 1000 --home /app mailr
COPY --from=ghcr.io/astral-sh/uv:0.8.24@sha256:1d31be550ff927957472b2a491dc3de1ea9b5c2d319a9cea5b6a48021e2990a6 /uv /usr/local/bin/uv
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev
# fastText language identification (core/langid.py): lid.176.ftz, CC BY-SA 3.0 (Facebook AI
# Research; site/docs/license.md). Pinned by checksum; the build fails if the file changes.
# The directory exists first, so --chmod sets only the file's mode.
RUN mkdir -p /app/mailr/data
ADD --checksum=sha256:8f3472cfe8738a7b6099e8e999c3cbfae0dcd15696aac7d7738a8039db603e83 --chmod=644 \
    https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.ftz /app/mailr/data/lid.176.ftz
COPY --from=web /web/dist /app/mailr/static
COPY --from=docs /help /app/mailr/help
# Swagger UI for the API reference at /api/v1/docs (backend api/apidocs_routes.py; Apache-2.0),
# served from this server, pinned by checksum (the same version and checksums as that module).
# The directory exists first, so --chmod sets only the files' mode.
RUN mkdir -p /app/vendor/swagger-ui
ADD --checksum=sha256:c2e4a9ef08144839ff47c14202063ecfe4e59e70a4e7154a26bd50d880c88ba1 --chmod=644 \
    https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js /app/vendor/swagger-ui/swagger-ui-bundle.js
ADD --checksum=sha256:40170f0ee859d17f92131ba707329a88a070e4f66874d11365e9a77d232f6117 --chmod=644 \
    https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css /app/vendor/swagger-ui/swagger-ui.css
# The app user owns only its data; the code and virtualenv stay root-owned (read-only to it).
# Nothing in /data is open to other accounts (mailr serve/worker also set umask 027).
RUN mkdir -p /data/home /data/.cache /data/tmp && chown -R mailr /data && chmod 0750 /data && chmod 0700 /data/tmp
USER mailr
ENV PATH="/app/.venv/bin:$PATH"
# The version the server reports (the web's help menu, the phone's Settings, GET /me) and tags error
# reports with (core/errors.py): `--build-arg MAILR_RELEASE=<version>`. docker-publish.yml passes the
# release's version; without it the server reports backend/pyproject.toml's.
ARG MAILR_RELEASE=""
ENV MAILR_RELEASE=$MAILR_RELEASE
VOLUME ["/data"]
EXPOSE 8025
# Liveness: the web process, the database and the mail store answer (core/health.py). Readiness
# (/api/v1/health/ready: the worker, sync, exports, backups, disk) is for uptime monitors: a proxy
# such as Traefik stops routing to an unhealthy container, and one expired account password or an
# unmounted backup share must not take the web app down. The start period covers a dump and the
# migrations before the first start of a new version. The worker's healthcheck is in the compose file.
HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=3 \
  CMD curl -fsS -o /dev/null http://127.0.0.1:8025/api/v1/health/live || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["mailr", "serve"]
