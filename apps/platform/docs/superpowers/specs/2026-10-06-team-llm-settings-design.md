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
- `save_config(tenant_id, user_id, provider, model, api_key) -> dict`: upsert. An empty
  `api_key` keeps the stored key (same contract as the session endpoint). Stamps
  `configured_at` and `configured_by`, clears `tested_at`.
- `delete_config(tenant_id) -> bool`
- `test_config(tenant_id) -> dict`: one small call through the existing `_ping`. Stamps
  `tested_at` on success; returns `{"ok": False, "error": …}` on any failure.
- `load_key(tenant_id) -> tuple[str, str, str] | None`: `(provider, model, key)` for
  `load_credentials` step 2.

Serialization: `{provider, model, has_key, configured_at, configured_by, tested_at}`, where
`configured_by` is the saving admin's **email** (looked up in the same tenant session), or
`null` if the user was deleted. The key, or any fragment of it, is **never** returned.

**Observability:** structured logs `llm.team_config.saved` / `.deleted` / `.tested` with
`tenant_id`, `user_id`, `provider`, `model`, `ok`. Never the key.

## API: `recon/llm/settings_router.py` (new), mounted like `llm/router.py`

| Route | Auth | Behavior |
|---|---|---|
| `GET /settings/llm` | `get_tenant_id` (any member) | `{config: <serialized> \| null, can_edit: bool, default_models: DEFAULT_MODELS, providers: sorted(VALID_PROVIDERS)}` |
| `PUT /settings/llm` | `get_principal`, role `admin` | body `{provider, model, api_key=""}`; 422 on a bad provider or blank model; 200 with the serialized row |
| `DELETE /settings/llm` | admin | 204, or 404 if none |
| `POST /settings/llm/test` | admin | `{ok, error?, provider?, model?}` |

The **role check** is the repo's first, so it gets one small dependency, `require_admin(principal =
Depends(get_principal)) -> Principal`, in `recon/api/deps.py`, which raises 403 `"admin role
required"` for any non-admin. Writes use `principal.tenant_id`.

`can_edit` on GET: `true` only when auth is on and the caller is an admin. GET deliberately
uses `get_tenant_id`, so analysts and the extension can read it. With auth off
(`RECON_AUTH_SECRET=""`, the integration suite's mode), `get_principal` returns 401, so
writes are unavailable there and `can_edit` is `false`. Tests that exercise writes enable
auth per test (the existing `auth_router` test pattern).

## UI: `web/src/features/settings/`

- **Sidebar:** a **Settings** item (gear icon) after *Sessions*, with the same
  button-plus-`navigate()` pattern as Sessions, so it's a cross-run route.
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

**Extension:** unaffected (no label or type changes).

## Rollout

- No data migration. Existing session keys keep precedence.
- Existing dev DBs pick up `0029` with a normal `alembic upgrade head`.
- Docs: `docs/OPERATING.md` gets a short "LLM key for threat models" note covering the
  lookup order and where to set each key.
