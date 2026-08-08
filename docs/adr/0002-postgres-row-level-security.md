---
status: accepted
date: 2026-08-08
---

# 2. Tenant isolation via Postgres row-level security

## Context and Problem Statement

The platform is multi-tenant: many security engagements share one database, and a bug
must never let one tenant read another's runs, findings, or assets. Where should that
isolation be enforced — in application query code, or in the database itself (REQ-S1 —
"authorization enforced at the data layer, not just the API"; see `docs/REQUIREMENTS.md`)?

## Considered Options

* **Row-level security (RLS) in Postgres** — every tenant-scoped table carries a policy;
  the app connects as a non-superuser role so the policy always applies.
* **Application-level scoping** — every query adds `WHERE tenant_id = ?`.
* **Database-per-tenant** — physical isolation per tenant.

## Decision Outcome

Chosen option: **RLS in Postgres.** Every tenant-scoped table has a `tenant_isolation`
policy `USING (tenant_id::text = current_setting('app.current_tenant', true))`, RLS is
enabled/forced, and the app connects as the non-superuser `recon_app` role so the policy
is never bypassed. The **only** supported access path is `tenant_session()`, which sets
that GUC transaction-locally; `admin_session()` (privileged, off the HTTP surface) is the
deliberate bypass for bootstrap/migrations.

### Consequences

* Good — isolation is a database invariant, not a discipline every query must remember:
  "Forget it and RLS returns nothing" (`db/base.py:1-7`). A missing `WHERE` clause cannot
  leak across tenants.
* Good — blob isolation rides along because object keys embed the tenant id (ADR-0004).
* Bad — all app DB access must go through `tenant_session()` and set the GUC; a raw
  connection sees nothing. A new tenant-scoped table MUST be added to the RLS migration
  loop and the `*_TABLES` tuples or it ships unprotected.
* Neutral — this is not database-per-tenant: one shared cluster, so a noisy tenant is not
  physically isolated (acceptable at this scale).

### Confirmation

Policy creation per table in the migrations (`migrations/versions/0001_initial.py:32-57`,
including the `recon_app` role; repeated in
`0002/0004/0005/0006/0007/0008/0009`). The `*_TABLES` tuples (`db/models.py:565-596`), consumed by the RLS migration
`upgrade()`/`downgrade()` bodies (e.g. `0001_initial.py:32`). Access path + GUC: `db/base.py:35-67` (`tenant_session` /
`admin_session`); per-request tenant resolution `api/deps.py:24-34`. RLS behaviour is
covered by the DB/integration tests.

## More Information

Recorded retroactively 2026-08-08 (DEBT D10). Application-scoping and database-per-tenant
were weighed as design-time judgment (off-repo memory `slice1-foundation-choices`); the
in-repo driver is REQ-S1's "at the data layer, not just the API". See
`docs/ARCHITECTURE.md` ("Data + isolation") and ADR-0004.
