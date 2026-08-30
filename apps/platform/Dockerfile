# Build the Sourcemapper source-recovery engine from source: it publishes no
# binary release and no version tags, so it is pinned to a commit SHA (never
# @latest) and built with Go. It is a pure-Go static binary, so it drops cleanly
# into the slim runtime image below.
FROM golang:1.23-bookworm AS sourcemapper-build
RUN go install github.com/denandz/sourcemapper@442aed28d1841f32580dda91b4bea740c07bd2ad

# Build katana (JS-asset discovery crawler). Pinned to a released tag, never
# @latest — v1.6.1 is the current latest stable release as of 2026-07-26
# (verified via github.com/projectdiscovery/katana/releases; supersedes the
# older v1.1.0 placeholder). katana v1.6.1's go.mod requires go >= 1.25.7, so
# this stage uses golang:1.25-bookworm rather than 1.23 (sourcemapper-build's
# version) — a build-stage-only bump that doesn't affect the runtime image.
# Re-verify the tag + that `-headless -system-chrome` works against the
# vendored chromium at build time (verified working 2026-08-02; see the
# `-system-chrome-path` note in src/recon/discover/katana.py).
FROM golang:1.25-bookworm AS katana-build
RUN go install github.com/projectdiscovery/katana/cmd/katana@v1.6.1

# Build the front-end SPA (Node), copied into the runtime image below.
FROM node:20-bookworm AS web-build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Pin Python deps to the committed lock (uv.lock) instead of resolving pyproject's
# >= floors fresh at build, so the image matches CI's `uv sync --frozen`. Export the
# locked, prod-only (no dev) set to a hash-pinned requirements.txt via uv; the
# runtime stage pip-installs exactly that. `uv export` is offline — it just
# serializes the lock. (DEBT D7.)
FROM python:3.11-slim-bookworm AS deps-export
COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt -o requirements.txt

# Single application image; the same image runs as the API, the worker, and the
# one-shot migration job (the command differs per compose service).
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Headless crawl needs a browser; katana drives system chromium over CDP.
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium \
    && rm -rf /var/lib/apt/lists/*

# D37-L0: the sourcemapper recovery child is memory-bounded via an exec'd `prlimit`
# (util-linux, part of the slim base). Assert it at build time so its absence can't
# silently degrade every source-map recovery into a soft "engine unavailable" skip.
RUN command -v prlimit >/dev/null || { echo "prlimit (util-linux) missing — required for the D37 engine memory bound"; exit 1; }

# Dependencies first for layer caching: install the exact locked set (exported
# above from uv.lock), then the project itself NON-editably (--no-deps, a real wheel
# build) so its packaged rule data (findings/rules/*.yml) is validated in the image
# rather than silently picked up from a src checkout. (DEBT D7.)
COPY --from=deps-export /app/requirements.txt ./
RUN pip install -r requirements.txt
COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic.ini ./
RUN pip install --no-deps .

# Front-end build output; RECON_SPA_DIST_DIR (compose) points the API at it.
COPY --from=web-build /web/dist ./web/dist

# Sourcemapper binary onto PATH (root-owned, readable by the app user).
COPY --from=sourcemapper-build /go/bin/sourcemapper /usr/local/bin/sourcemapper

# katana binary onto PATH (root-owned, readable by the app user).
COPY --from=katana-build /go/bin/katana /usr/local/bin/katana

# Run as a non-root user.
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

# Pre-warm Kingfisher's compiled-rule cache into the image (as the `app` user, so
# it lands in that user's home) — a fresh container's first secret scan then skips
# the full ruleset compile, helping the <4min SLA. Best-effort: never fail build.
RUN printf 'const kingfisherWarmup = 1;\n' > /tmp/warm.js \
    && KF_RULES="$(python -c "import importlib.util, pathlib; s = importlib.util.find_spec('recon.findings'); print(pathlib.Path(s.origin).parent / 'rules' / 'custom_rules.yml')" 2>/dev/null || true)" \
    && (kingfisher scan /tmp/warm.js --no-validate --no-update-check --rules-path "$KF_RULES" >/dev/null 2>&1 || true) \
    && rm -f /tmp/warm.js

EXPOSE 8000

# Default to the API; worker/migrate override this in docker-compose.
CMD ["uvicorn", "recon.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
