---
status: accepted
date: 2026-08-08
---

# 4. Content-addressed blob storage (S3/MinIO, sha256 key)

## Context and Problem Statement

Runs produce large/binary artifacts — raw JS bundles, source maps, recovered sources,
reports. These must not sit in Postgres rows (bloat, slow queries, backup weight), and
must stay tenant-isolated like everything else (REQ-D2; see `docs/REQUIREMENTS.md`).

## Considered Options

* **Content-addressed object storage** — key = `{tenant_id}/{run_id}/{kind}/{sha256}` in
  S3/MinIO; the row keeps only the key.
* **Blobs in Postgres** (`bytea` / large objects).
* **Opaque/random object keys** (UUID) with a separate hash column.

## Decision Outcome

Chosen option: **content-addressed blobs in S3/MinIO**, keyed
`{tenant_id}/{run_id}/{kind}/{sha256}` (`storage.py:41-46`). One boto3 client is
configured path-style + s3v4 so the identical code path hits MinIO locally and S3 in
production. The key embeds the tenant id, so blob isolation matches the database's RLS
boundary (ADR-0002).

### Consequences

* Good — identical bytes deduplicate for free, and the hash *is* the integrity check.
* Good — content-addressing makes ingest idempotent: a re-uploaded file lands on the same
  key, so the capture extension's whole-batch retries never duplicate a blob
  (`docs/ARCHITECTURE.md:99-100`).
* Bad — blobs are immutable by nature; "editing" an artifact means writing a new key, and
  orphaned keys need a separate garbage-collection story (not yet built).
* Neutral — a `BLOB_KINDS` allow-list constrains which `kind` segments are writable
  (`storage.py:24-38`).

### Confirmation

`storage.py` (docstring L1-9 with the key shape; `object_key` sha256 L41-46; `BLOB_KINDS`
L24-38; `put_blob`/`get_blob` L74-85; S3 client L49-61). Requirement REQ-D2. The
idempotency consequence is cross-checked by the capture-ingest tests.

## More Information

Recorded retroactively 2026-08-08 (DEBT D10). See `docs/ARCHITECTURE.md`
("Data + isolation") and ADR-0002 — the tenant-scoped key shares the RLS tenant boundary.
