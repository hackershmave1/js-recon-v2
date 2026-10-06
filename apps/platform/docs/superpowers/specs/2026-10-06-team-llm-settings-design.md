# Team-wide LLM key settings: design

**Date:** 2026-10-06 · **Branch:** `feat/team-llm-settings` (stacked on `feat/llm-env-key-fallback`, which is stacked on #141)
**Status:** approved in chat (design option B, "admins only", storage approach 1)

## Problem

The workspace has a **Threat Model** tab, but no way to give it an LLM key. The backend has
per-session endpoints (`/sessions/{id}/llm-config`), but the only UI that calls them is the
browser extension's popup (`apps/capture/chrome-extension/src/popup/components/SettingsView.jsx`).
A workspace user who clicks *Generate* gets `no LLM API key: save one for this session, or set
OPENROUTER_API_KEY / ANTHROPIC_API_KEY on the server`, with nowhere in the UI to act on it.

The operator env key (`feat/llm-env-key-fallback`) unblocks single-team deployments, but in a
multi-tenant one every team spends the operator's key.

## Goal

An admin sets **one provider + model + key per team (tenant)** on a workspace **Settings**
page. Every session in that team uses it unless the session has its own key.

Non-goals (YAGNI): per-user keys, key history/rotation, a Gemini env var, a web UI for
per-session keys (the extension keeps that), any role-management UI.

## Credential lookup order

`recon.llm.service.load_credentials(tenant_id, session_id) -> (provider, model, api_key) | None`:

| # | Source | Returns |
|---|---|---|
| 1 | session row has a saved key | session provider, session model, session key |
| 2 | team row has a saved key | team provider, team model, team key |
| 3 | `OPENROUTER_API_KEY`, then `ANTHROPIC_API_KEY` | that env key's provider; the session's saved model only if it is for the same provider, else `None` (provider default) |
| 4 | none of the above | `None`; `run_generation` fails with the existing "no LLM API key" message |

Saving with a blank key keeps the stored key only if the provider is unchanged; switching provider requires a new key (422 otherwise).

Decrypt failures: `load_credentials` can raise (`InvalidToken` after a
`RECON_LLM_ENCRYPTION_KEY` rotation). `run_generation` calls it after setting `running`, so
today a bad key leaves the threat model stuck at `running` until the 5-minute orphan window.
A team key widens that to every session in the team. `run_generation` wraps the call:
on any exception it sets `failed` with `stored LLM key could not be decrypted; re-save it`
and logs `llm.credentials.decrypt_failed` (no key material).

The no-key error becomes `no LLM API key: save one for this session, ask an admin to set a
team key in Settings, or set OPENROUTER_API_KEY / ANTHROPIC_API_KEY on the server`. The
`no LLM API key` prefix is what the UI link matches on.

Invariant (from the env-key fix): **a key is always returned with the provider it belongs
to.** A team key never borrows the session's provider or model, and a session's provider
never borrows the team key.

## Data: `tenant_llm_config` (migration `0029_tenant_llm_config`)

Revision id is 22 chars (≤32, the `alembic_version` column limit). `down_revision =
"0028_finding_taxonomy_fields"`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `gen_random_uuid()` |
| `tenant_id` | UUID FK `tenant.id` ON DELETE CASCADE | **UNIQUE** (`uq_tenant_llm_config_tenant`), one row per team |
| `provider` | `String(32)` | CHECK `IN ('anthropic','openrouter','gemini')` (`ck_tenant_llm_config_provider`) |
| `model` | `Text` NOT NULL | |
| `encrypted_api_key` | `Text` NULL | Fernet ciphertext via the existing `_encrypt`/`_decrypt` (`RECON_LLM_ENCRYPTION_KEY`; cleartext in dev, as for session keys) |
| `configured_at` | timestamptz NOT NULL, default now() | |
| `configured_by` | UUID NULL, FK `app_user.id` ON DELETE SET NULL | audit: which admin last saved it |
| `tested_at` | timestamptz NULL | stamped by a successful `/test` |

The model `TenantLlmConfig` goes in `recon/db/models.py`, next to `SessionLlmConfig`, with a
new constant `TENANT_LLM_TABLES = ("tenant_llm_config",)`.

The migration follows the **0017 / fixed-0026 pattern**: `Base.metadata.create_all(bind)`
(a no-op on a fresh DB, where 0001 already built it from the live model), then per table:
`ENABLE` + `FORCE ROW LEVEL SECURITY`, `DROP POLICY IF EXISTS tenant_isolation`, `CREATE POLICY
tenant_isolation … USING/WITH CHECK (tenant_id::text = current_setting('app.current_tenant',
true))`, `GRANT SELECT, INSERT, UPDATE, DELETE … TO recon_app`. Downgrade: drop the policy,
then the table.

## Service: `recon/llm/tenant_config.py` (new)

A new file keeps `llm/service.py` under the ~300-line cap. Every DB access goes through
`tenant_session(tenant_id)` (flush, never a mid-block commit).

- `get_config(tenant_id) -> dict | None`: the serialized row (never the key).
- `save_config(tenant_id, user_id, provider, model, api_key) -> dict | None`: inside one
  `tenant_session`, first `SELECT role FROM app_user WHERE id = :user_id`. If the row is
  missing or the role isn't `admin`, return `None` (the router maps that to 403). This makes
  the role check **current** rather than trusting the up-to-8h token claim, and guarantees
  the `configured_by` FK target exists. The upsert itself is a single
  `INSERT … ON CONFLICT (tenant_id) DO UPDATE` (postgresql dialect `insert`), so two
  concurrent first saves can't collide. An empty `api_key` keeps the stored key (same
  contract as the session endpoint). Stamps `configured_at` and `configured_by`, clears
  `tested_at`. `delete_config` uses the same DB role check.
- `delete_config(tenant_id, user_id) -> bool | None` (None = caller isn't an admin)
- Testing a key is **split across the thread boundary**. The existing session `test_config`
  calls `asyncio.get_event_loop().run_until_complete(_ping(…))` inside `run_in_threadpool`,
  which on Python 3.11 raises `RuntimeError: There is no current event loop` in the worker
  thread (verified in the running container), so it **always** returns `ok: false`. New
  shape: the router is `async`; sync helpers `load_key(tenant_id)` and
  `mark_tested(tenant_id)` run in `run_in_threadpool`, and `await _ping(provider)` runs on
  the request's own loop. The per-session `/sessions/{id}/llm-config/test` gets the **same
  fix** in this branch (shared bug, shared `_ping`), with a test that actually reaches
  `_ping` through a stub provider.
- `load_key(tenant_id) -> tuple[str, str, str] | None`: `(provider, model, key)` for
  `load_credentials` step 2.

Serialization: `{provider, model, has_key, configured_at, configured_by, tested_at}`, where
`configured_by` is the saving admin's **email** (looked up in the same tenant session), or
`null` if the user was deleted. The key, or any fragment of it, is **never** returned.

**Observability:** structured logs `llm.team_config.saved` / `.deleted` / `.tested` with
`tenant_id`, `user_id`, `provider`, `model`, `ok`. Never the key.

## API: `recon/llm/settings_router.py` (new), mounted **once**, bare (not also under `/api`; the extension is a non-goal)

| Route | Auth | Behavior |
|---|---|---|
| `GET /settings/llm` | `get_tenant_id` (any member) | `{config: <serialized> \| null, can_edit: bool, default_models: DEFAULT_MODELS, providers: sorted(VALID_PROVIDERS)}` |
| `PUT /settings/llm` | `get_principal`, role `admin` | body `{provider, model, api_key=""}`; 422 on a bad provider or blank model; 200 with the serialized row |
| `DELETE /settings/llm` | admin | 204, or 404 if none |
| `POST /settings/llm/test` | admin | `{ok, error?, provider?, model?}` |

The **role check** is the repo's first, so it gets one small dependency, `require_admin(principal =
Depends(get_principal)) -> Principal`, in `recon/api/deps.py`, which raises 403 `"admin role
required"` for any non-admin. Writes use `principal.tenant_id`.

`can_edit` on GET: `true` only when auth is on and the token's role is `admin` (a UI hint;
the write itself re-checks the role in the DB). `get_principal` raises with no token, so
GET uses a non-raising `get_optional_principal` (modelled on `get_actor`, `deps.py`). GET
uses `get_tenant_id`, so analysts can read it. `configured_by` (an email) is included only
when the request has a valid token, so with `RECON_ALLOW_HEADER_TENANT` a token-less header
read never exposes an admin's email. With auth off
(`RECON_AUTH_SECRET=""`, the integration suite's mode), `get_principal` returns 401, so
writes are unavailable there and `can_edit` is `false`. Tests that exercise writes enable
auth per test (the existing `auth_router` test pattern).

## UI: `web/src/features/settings/`

- **Sidebar:** a **Settings** item (gear icon) after *Sessions*, with the same
  button-plus-`navigate()` pattern as Sessions, so it's a cross-run route. The shell's mode
  union `"run" | "sessions"` (`Shell.tsx`, `TopBar.tsx`, `Sidebar.tsx`) widens to include
  `"settings"`, so Sessions isn't highlighted on `/settings`. A `gear` path is added to
  `shell/icons.tsx`.
- **Dev proxy:** `web/vite.config.ts` proxies `"/settings/llm"`, **not** `"/settings"`. A bare
  prefix would send a hard refresh of the SPA route to FastAPI.
- **Route:** `/settings` → `SettingsPage`, registered in `main.tsx` next to `/sessions`.
- **`SettingsPage.tsx`**: one card, "Team LLM provider".
  - Provider `<select>` (from `providers`); model `<input>` pre-filled with
    `default_models[provider]` when no config is saved or the provider changes; API key
    `<input type="password" autocomplete="off">`, always empty on load, with a hint that
    shows "A key is saved. Leave blank to keep it." when `has_key`.
  - Buttons: **Save**, **Test** (enabled when `has_key`), **Remove** (in-app confirm
    modal, not `window.confirm`, which Chrome suppresses; see repo memory), each with an
    inline status line.
  - `can_edit === false`: the same fields rendered read-only (no key field) plus "Only
    admins can change the team key."
- **`settingsApi.ts`**: thin calls through the existing `apiClient`.
- **Threat Model error:** when the error text starts with `no LLM API key`, append a
  `<Link to="/settings">Set a team key in Settings →</Link>`.

## Testing

**Integration (pytest, marker `integration`):**
- `db/llm_threat_model_rls_test.py` pattern: RLS `relrowsecurity` and
  `relforcerowsecurity` true on `tenant_llm_config`; tenant B's unfiltered count is 0.
- `llm/tenant_config_test.py`: save/get/delete round-trip; an empty key keeps the stored
  key; the serialized output never contains the key.
- `llm/service_test.py` (extend): lookup order: session key beats team key; team key
  beats env; team key carries its **own** provider and model even when the session row has
  another provider; env is still used when no team key exists.
- `llm/settings_router_test.py`: analyst PUT → 403; admin PUT → 200; GET as analyst shows
  `can_edit: false` and no key; DELETE → 204, then 404.
- Fresh-DB migrate: covered by the integration lane (empty DB in CI).

**Web (vitest):** `SettingsPage.test.tsx`: admin sees an editable form and Save calls PUT
with the typed key; analyst sees the read-only view; the key never renders after load.
Threat-model link: shown only for the no-key error.

- `llm/router` session `/test` + `settings_router` `/test`: a stub provider is monkeypatched
  into `build_provider`; asserts `ok: true` and `tested_at` stamped. These are the tests whose
  absence let the event-loop bug ship.
- The auth-enabled client is copied locally from `api/auth_router_test.py::make_auth_client`
  (it's not a shared fixture), and users are seeded for real because of the FK.

**Extension:** unaffected (no label or type changes).

## Accepted limits

- PUT doesn't check that the model id belongs to the provider (e.g. an OpenRouter-style id
  under `anthropic`), the same as the session endpoint. **Test** surfaces it immediately.
- `can_edit` reflects the token's role, which can be up to 8h stale. It only controls which
  buttons render; the write path checks the current DB role.

## Adversarial review (2026-10-06)

Verdict **SOUND WITH FIXES**. One blocking issue (the broken event-loop `test_config`
pattern, verified) and ten non-blocking ones, all folded in above: current DB role check,
decrypt-failure handling, error text, single mount, non-raising principal for GET, shell
mode + gear icon, the dev-proxy path, local auth fixture, `ON CONFLICT` upsert, and
`configured_by` gated on a token. Verified correct: RLS on `app_user` for the email lookup,
FK + `SET NULL` under FORCE RLS (precedent `session.created_by`), the migration on fresh and
existing DBs, tenant agreement between GET and writes, role in the signed token, and no
`/settings` route collision in prod.

## Rollout

- No data migration. Existing session keys keep precedence.
- Existing dev DBs pick up `0029` with a normal `alembic upgrade head`.
- Docs: `docs/OPERATING.md` gets a short "LLM key for threat models" note covering the
  lookup order and where to set each key.
