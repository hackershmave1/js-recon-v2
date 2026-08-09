# Architecture Decision Records

Short, durable records of the **why** behind this platform's load-bearing architectural
choices — the companion to [`../ARCHITECTURE.md`](../ARCHITECTURE.md), which describes the
**what**. Format is [MADR](https://adr.github.io/madr/), trimmed — see
[`0000-adr-template.md`](0000-adr-template.md). Each record cites the code that enforces the
decision and the requirement (`../REQUIREMENTS.md`) that drove it, rather than restating the
design — one source of truth per fact.

Most records here were **backfilled 2026-08-08** (DEBT D10) for decisions already shipped;
each carries a "Recorded retroactively" note so its file date isn't mistaken for the
decision date.

`status` values: `proposed` · `accepted` · `rejected` · `deprecated` ·
`superseded by ADR-XXXX`.

| ADR | Decision | Status |
|-----|----------|--------|
| [0001](0001-redis-streams-task-broker.md) | Redis Streams as the task broker (at-least-once delivery) | accepted |
| [0002](0002-postgres-row-level-security.md) | Tenant isolation via Postgres row-level security | accepted |
| [0003](0003-cooperative-orchestrator-level-run-pause.md) | Cooperative, orchestrator-level run pause (not OS signal-stop) | accepted |
| [0004](0004-content-addressed-blob-storage.md) | Content-addressed blob storage (S3/MinIO, sha256 key) | accepted |
| [0005](0005-ssrf-egress-guard-fail-closed.md) | SSRF egress guard, fail-closed | accepted |
| [0006](0006-static-analysis-no-active-traffic.md) | Static analysis, no automated active/exploit traffic | accepted |
| [0007](0007-single-analysis-core-v1-convergence.md) | Single analysis core (v1 convergence, no dual-core union) | accepted |
| [0008](0008-hardened-out-of-process-engine-harness.md) | One hardened harness for out-of-process engines | accepted |

## Adding an ADR

1. Copy `0000-adr-template.md` to the next `NNNN-kebab-title.md` (four-digit, zero-padded).
2. Fill it in; keep it to ~1 page; cite the enforcing code in *Confirmation* and the
   requirement (`../REQUIREMENTS.md`) in *More Information*.
3. Add a row to the table above — the structure test
   (`apps/platform/src/recon/adr_structure_test.py`) fails the build if this index and the
   files ever drift apart.
4. To reverse a past decision, add a **new** ADR and set the old one's `status` to
   `superseded by ADR-XXXX` — don't rewrite the superseded record's history.
