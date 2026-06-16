# JavaScript Security Extractor API

FastAPI backend for the JavaScript Security Extractor platform.

## Quick Start

### Using Docker (Recommended)

```bash
docker compose up -d
```
This starts the supported local stack: `postgres` and `api`. Celery/Redis worker services are no longer part of the active runtime.

### Manual Setup

```bash
# Create virtual environment  
uv venv
source .venv/bin/activate

# Install dependencies
uv sync

# Start services
docker compose up -d postgres
uvicorn app.main:app --reload --port 3000
```

## API Endpoints

### Health Check
```
GET /health
```

### Ingestion
```
POST /api/save-files
Body: { metadata: {}, files: [...] }
```

### Sessions
```
GET /api/sessions
# Each session row includes `analysisSummary` {completed, failed, performed}
GET /api/sessions/{session_id}/files
POST /api/sessions/{session_id}/analyze/start
GET /api/sessions/{session_id}/analyze/progress
POST /api/sessions/{session_id}/analyze/stop
GET /api/sessions/{session_id}/comprehensive-analysis
```

### Files
```
GET /api/files/{file_id}
GET /api/files/{file_id}/content
GET /api/files/{file_id}/dependencies?recursive=true
```

## Configuration

Create `.env` file:

```env
DATABASE_URL=postgresql://user:pass@localhost:5432/js_extractor
STORAGE_PATH=/var/lib/js-extractor/storage
FILE_CONTENT_TTL_DAYS=30
SOURCEMAP_CONTENT_TTL_DAYS=30
CLEANUP_MAX_DELETIONS_PER_RUN=500
SOURCEMAP_PROCESSING_TIMEOUT_SECONDS=30
SOURCEMAP_MAX_SIZE_BYTES=52428800
SOURCEMAP_MAX_RECONSTRUCTED_FILES=1000
API_KEY=your-secret-key
```

TTL values apply to stored content artifacts only (`storage/sessions/*/files` and `storage/sessions/*/maps`), not URL/metadata rows.

## Architecture

- **FastAPI**: REST API framework
- **PostgreSQL**: Metadata storage
- **Background work**: FastAPI background tasks and DB-backed job records
- **File Storage**: Local or S3
- **Migrations**: startup runs `python -m alembic upgrade head`
- **Job recovery**: startup marks orphaned queued/running/cancelling jobs terminal

## Development

```bash
# Run tests
pytest tests/

# Format code
black app/

# Lint
flake8 app/
```

Note: tests require `DATABASE_URL` to be set and a reachable database.

## Production Deployment

1. Update `docker-compose.yml` with secure passwords
2. Configure HTTPS/SSL
3. Set up monitoring (Prometheus/Grafana)
4. Configure backups
5. Run multiple API replicas behind a process manager/load balancer only after validating job ownership semantics

## Troubleshooting

### Database connection error
```bash
docker-compose logs postgres
```

### Storage issues
Check permissions on storage directory:
```bash
ls -la /var/lib/js-extractor/storage
```

## License

MIT
