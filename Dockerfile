# Build the Sourcemapper source-recovery engine from source: it publishes no
# binary release and no version tags, so it is pinned to a commit SHA (never
# @latest) and built with Go. It is a pure-Go static binary, so it drops cleanly
# into the slim runtime image below.
FROM golang:1.23-bookworm AS sourcemapper-build
RUN go install github.com/denandz/sourcemapper@442aed28d1841f32580dda91b4bea740c07bd2ad

# Single application image; the same image runs as the API, the worker, and the
# one-shot migration job (the command differs per compose service).
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first for layer caching, then the source.
COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic.ini ./
RUN pip install .

# Sourcemapper binary onto PATH (root-owned, readable by the app user).
COPY --from=sourcemapper-build /go/bin/sourcemapper /usr/local/bin/sourcemapper

# Run as a non-root user.
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

# Pre-warm Kingfisher's compiled-rule cache into the image (as the `app` user, so
# it lands in that user's home) — a fresh container's first secret scan then skips
# the full ruleset compile, helping the <4min SLA. Best-effort: never fail build.
RUN printf 'const kingfisherWarmup = 1;\n' > /tmp/warm.js \
    && (kingfisher scan /tmp/warm.js --no-validate --no-update-check >/dev/null 2>&1 || true) \
    && rm -f /tmp/warm.js

EXPOSE 8000

# Default to the API; worker/migrate override this in docker-compose.
CMD ["uvicorn", "recon.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
