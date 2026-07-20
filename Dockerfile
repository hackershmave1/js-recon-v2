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

# Run as a non-root user.
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8000

# Default to the API; worker/migrate override this in docker-compose.
CMD ["uvicorn", "recon.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
