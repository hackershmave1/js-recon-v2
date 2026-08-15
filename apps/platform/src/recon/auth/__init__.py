"""User authentication: the central login (username + password -> session token).

A thin, stateless layer that finally puts real credentials in front of the tenant
boundary. The pieces:

- ``token`` — a stateless signed session token, carrying the user id + tenant id +
  role, signed with ``RECON_AUTH_SECRET``.
- ``passwords`` — bcrypt hashing/verification (never store or compare plaintext).
- ``service`` — ``authenticate()`` (cross-tenant user lookup at login time, which
  necessarily precedes tenant context) and the idempotent dev-admin ``seed_admin``.

The FastAPI wiring (``get_principal`` / the reworked ``get_tenant_id``) lives in
``recon.api.deps``; the HTTP routes live in ``recon.api.auth_router``. When
``RECON_AUTH_SECRET`` is empty, auth is DISABLED and the platform falls back to the
legacy ``X-Tenant-Id`` header stand-in (dev/test) — see ``config.Settings``.
"""
