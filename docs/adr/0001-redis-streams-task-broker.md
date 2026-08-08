---
status: accepted
date: 2026-08-08
---

# 1. Redis Streams as the task broker

## Context and Problem Statement

The API tier is a thin accept/validate/enqueue/read layer — "no route does
crawl/fetch/parse/LLM/probe work" (`apps/platform/src/recon/api/app.py:1-5`); all heavy
work runs in a separate **worker** process. The two need a durable broker with one queue
per work class, bounded retry plus a dead-letter path, at-least-once delivery, and
recovery of jobs abandoned by a crashed worker (REQ-Q1/Q2, REQ-R2/R3 —
`docs/REQUIREMENTS.md`).

## Decision Drivers

* Per-work-class queues + priority lanes (REQ-Q1).
* Bounded retry + a per-queue dead-letter queue (REQ-Q2).
* At-least-once delivery and crash-recovery of in-flight jobs (REQ-R2/R3).
* One lightweight infrastructure dependency for a self-hostable stack.

## Considered Options

* **Redis Streams** with consumer groups.
* **RabbitMQ** (named as the alternative broker in the requirements component diagram —
  see the "Recommended stack candidates" table in `docs/REQUIREMENTS.md`).
* **Celery / RQ** over Redis (the requirements name "Python / Celery" for the worker
  pool, same table).
* A **Postgres-backed queue** (an append-only table) — a no-extra-broker alternative.

## Decision Outcome

Chosen option: **Redis Streams with a per-stream `workers` consumer group**
(`apps/platform/src/recon/queue/streams.py`). Consumer groups give at-least-once delivery
(a message stays in the group's pending-entries list until `XACK`), `XAUTOCLAIM`-based
recovery of entries abandoned by a dead consumer, and a natural per-stream dead-letter
stream — all from Redis, which the compose stack already runs, with no extra broker.

### Consequences

* Good — at-least-once + crash-reclaim + DLQ are native to the primitive, not bolted on;
  one dependency; the worker loop stays small and inspectable
  (`worker/main.py:231-261`, `queue/streams.py`).
* Bad — at-least-once means the worker must be idempotent: the job claim/finish gate
  (`worker/main.py:114-119,176-181`) drops a duplicate/redelivered message for an
  already-finished job so a run can't double-advance.
* Bad — Redis Streams has no built-in delayed delivery, so backoff is implemented with an
  auxiliary per-queue ZSET (`streams.py:100-120`) rather than coming for free.
* Neutral — findings persistence layers **exactly-once** on top via a transactional
  outbox (REQ-A3, `findings/store.py:1-5`); don't conflate the two guarantees.

### Confirmation

`queue/streams.py` (docstring L1-9; `XGROUP CREATE mkstream` L37-43; `XADD` enqueue
L62-64; `XREADGROUP` L67-93; `XACK` L96-97; backoff ZSET + `promote_due` L100-120; DLQ
L123-127; `XAUTOCLAIM reclaim_stalled` L130-156). Worker loop `worker/main.py:231-261`.
Stage→queue map `runs/coordinator.py:29-37,75-85`. At-least-once + retry behaviours are
covered by `queue/*_test.py`.

## More Information

Recorded retroactively 2026-08-08 (DEBT D10). The in-repo requirements name Redis
Streams / RabbitMQ for the broker and Python / Celery for the worker pool
(`docs/REQUIREMENTS.md`, "Recommended stack candidates") as a **candidate set**, not a
recorded choose-and-reject; the head-to-head "why Redis Streams over RabbitMQ/Celery" was a
design-time judgment at the Slice-1 foundation stage (off-repo session memory
`slice1-foundation-choices`). See also `docs/ARCHITECTURE.md` ("Async spine").
