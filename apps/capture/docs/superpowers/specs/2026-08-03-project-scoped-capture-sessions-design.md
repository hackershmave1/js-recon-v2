# Project-scoped capture sessions — design

- **Date:** 2026-08-03
- **Status:** Draft for review
- **Repo:** `js-security-extractor` (spans `chrome-extension/`, `api/`, `web/`)
- **Author calls confirmed by user:** binding = snapshot-on-create; membership = optional; ambition = generalized (project owns scope + all capture/analysis defaults); server-side scope enforcement = out of scope.

---

## 1. Problem & goal

Today, recon scope is defined **ad-hoc, per session**: the operator retypes root domains every time they start a capture, the value lives in one global slot on the client that the next session overwrites, and the backend stores it but never acts on it. There is no way to define an engagement once and have every capture inherit it.

**Goal:** introduce a first-class **project** (an engagement) that owns a set of default recon settings. A capture session attaches to a project and **inherits** those defaults, with a per-session **override**. Standalone (project-less) capture keeps working exactly as it does today.

Non-goal for this cut: making the server *enforce* scope. Scope stays a client-side capture gate + a stored label (see §11).

---

## 2. Current state (grounded)

Backend (`api/`):
- `Session` (`api/app/models/session.py`) is the top-level entity (UUID id). Scope lives on it as `root_domains` (JSON list of bare hosts) + `include_subdomains` (bool). **No code reads these for classification** — `api/app/session_scope.py` is pure helpers (`host_of`, `derive_root_domains`, `normalize_root_domains`, `scope_payload`); there is no `is_in_scope`.
- Scope is seeded **only on session create**: `POST /api/save-files` (`api/app/api/routes/ingestion.py:135-142`) reads `metadata.rootDomains` / `metadata.includeSubdomains`, else derives from file URLs. `POST /api/recon/jobs/start` seeds from crawl targets. `PATCH /api/sessions/{id}` (`api/app/api/routes/sessions.py:661-680`, `SessionUpdateRequest` fields `name`, `rootDomains`, `includeSubdomains`) is the only after-the-fact editor.
- **No project/workspace/engagement entity exists** — `api/app/models/__init__.py` registers `Session, File, FileAnalysis, Dependency, SourceMap, AssetNode, AssetEdge, DiscoveryMethod, AssetType, Job, FindingStatus`.

Extension client (`chrome-extension/`):
- Scope + rules are **global** `chrome.storage.local` keys, not per-session records: `domainScopes`, `useDomainScope`, `includeSubdomains`, `outOfScopeMode` (`tag|mute|exclude`), `maxAssetMb` (≤10), `denyRules[]`, `denyDefaultProfile`, plus analysis toggles `performAnalysisOnUpload`, `captureSourceMaps`.
- `isInScope(url)` (`background.js:676`) gates capture client-side (exact host OR subdomain suffix). `shouldSkipUrl` applies the denylist + `outOfScopeMode==='exclude'`.
- `newSession` (`background.js:844`) rotates the persisted `reconSessionId` (`modules/session-store.js`), clears per-session state, and **overwrites the global scope keys** with the scope typed into the popup's New Session control (`src/popup/components/HomeView.jsx` → `app.jsx:141 startNewSession`).
- `BatchUploader.scopeMetadata()` (`modules/batch-uploader.js:271`) stamps `rootDomains` / `includeSubdomains` onto each upload's `metadata`; the backend binds them on create. The other settings are enforced client-side only and never persisted.
- `authContextDomains` was removed as inert in the recent cleanup — **it is not resurrected here.**

Workspace SPA (`web/`): session-centric recon workspace (Overview / Sessions / Findings / Sources + NewReconModal / ScopeModal / ExportModal). Per-session scope edit already exists via `ScopeModal` → `api.setSessionScope` → `PATCH /api/sessions/{id}`. No project concept.

---

## 3. Core concept: project = engagement

A **project** models an engagement (a bug-bounty program, a pentest, a target org). It owns the authorized boundary (scope) and the working defaults. It is an **organizational entity, not a security/tenant boundary** — the backend stays single-user and unauthenticated.

---

## 4. The inherit / override model

**One rule, applied per field:** `null (unset) = inherit from project` · `set = replace the inherited value`. There is no union/merge of list values — an overridden list fully replaces the inherited list. The override editor **prefills with the inherited value**, so "override scope" ergonomically means "edit a copy that starts as the project's scope," and the stored/effective value is always explicit and auditable.

**Binding = snapshot-on-create (confirmed).** When a session is created, its effective config is resolved once — `resolve(project.defaults, session_overrides)` — and **stored on the session**. Editing a project afterward affects only **new** sessions; existing sessions keep the config they were born with. Rationale: a session is the auditable record of *what was captured under which rules, and when*; a record that silently rewrites itself later is unacceptable for security work. This also matches the backend's existing seed-on-create behavior.

- Rejected alternative — **live-inherit** (session holds only sparse overrides, effective config resolved from the mutable project on every read): more DRY, but project edits retroactively reinterpret past captures. Rejected.
- Fast-follow (not this cut): an explicit **"apply to existing sessions"** action on project edit, so DRY updates are opt-in and visible — never automatic.

**Provenance.** The session records `override_keys` (the dotted config paths it overrode, e.g. `["scope.rootDomains"]`). The UI renders `inherited` vs `overridden` from this — the operator can always see what is in force and where it came from.

**Standalone (no project).** No inheritance. The session's effective config comes from the client's current global settings (today's behavior). `override_keys = []`. For a uniform record, the extension still snapshots the resolved config onto the session (see §6).

---

## 5. What a project owns — the config schema

A single typed document (`defaults`), grouped into four sections. This is the whole surface of "defaults a project owns."

```
{
  "scope":     { "rootDomains": ["*.acme.com"], "includeSubdomains": true },
  "capture":   { "outOfScopeMode": "exclude", "maxAssetMb": 10 },
  "denylist":  { "rules": [ { "tag": "analytics", "pattern": "*.google-analytics.com" } ],
                 "useDefaultProfile": true },
  "analysis":  { "analyzeOnUpload": false, "captureSourceMaps": true }
}
```

The schema is the **single source of truth for the merge**: a `CONFIG_SCHEMA` list of leaf paths drives one generic `resolve(defaults, overrides)` function — no per-field branches. Adding a future default = adding a schema leaf, nothing else.

Excluded on purpose: `authContextDomains` (dead setting, removed in cleanup).

---

## 6. Data model (backend)

New table **`projects`**:

| column | type | notes |
|---|---|---|
| `id` | UUID pk | `default=uuid4` |
| `name` | String | non-null |
| `created_at` | DateTime | |
| `updated_at` | DateTime | bumped on edit |
| `defaults` | JSON | the §5 document; non-null, server_default = system defaults |

Changes to **`sessions`**:

| column | type | notes |
|---|---|---|
| `project_id` | UUID fk → `projects.id` | **nullable**; `ON DELETE SET NULL` |
| `capture_config` | JSON | snapshot of the non-scope groups (`capture`, `denylist`, `analysis`); nullable |
| `override_keys` | JSON (list[str]) | dotted paths overridden; default `[]` |

Scope stays in the existing `root_domains` / `include_subdomains` columns (reuses the plumbing `ScopeModal` and upload metadata already use); the other three groups snapshot into `capture_config`. The project's `defaults.scope` still seeds those scope columns on create. (Minor asymmetry — scope in columns, the rest in one JSON — accepted for zero-churn on existing scope code.)

**Migration** (new alembic revision): create `projects`; add the three columns to `sessions`. Existing sessions → `project_id = NULL`, `capture_config = NULL`, `override_keys = []` (they become "loose"). Zero backfill required.

Deleting a project sets its sessions' `project_id = NULL` (they keep their snapshot; nothing breaks).

---

## 7. API

Projects CRUD (new router `api/app/api/routes/projects.py`):
- `GET /api/projects` → `[{ id, name, createdAt, updatedAt, defaults }]` (defaults inline; one call is enough for the extension).
- `POST /api/projects` `{ name, defaults? }` → creates; missing `defaults` (or missing sections) fall back to system defaults.
- `GET /api/projects/{id}`.
- `PATCH /api/projects/{id}` `{ name?, defaults? }` → partial; `defaults` deep-merges into stored defaults (validated against the schema). Bumps `updated_at`. Does **not** touch existing sessions (snapshot model).
- `DELETE /api/projects/{id}` → nulls child `project_id`.

Session create seam (extend the existing lazy create — no new create endpoint): `POST /api/save-files` `metadata` gains `projectId?`, `captureConfig?` (`{capture,denylist,analysis}`), `overrideKeys?`. On **create only** (matching how scope is seeded today), the backend binds `project_id`, `capture_config`, `override_keys`; scope columns are seeded from `metadata.rootDomains/includeSubdomains` as today.

**Who resolves (settled):** the **client** resolves the effective config (it must — it gates capture locally against exactly those values), and sends the resolved snapshot. The backend **stores it as-is and does not re-resolve** against the project's current defaults. This is deliberate: the honest audit record is the config the capture *actually ran under* (the client's, possibly against a slightly stale cached project), not what the project says now. `projectId` + `override_keys` are stored for provenance/display, not to reconstruct the config server-side. (This is safe because the backend is single-user; it is not a trust boundary.)

Session override edit: extend `SessionUpdateRequest` (`PATCH /api/sessions/{id}`) to also accept `captureConfig?` and recompute `override_keys`. (Scope editing via `rootDomains`/`includeSubdomains` already exists.)

Validation: a Pydantic model mirrors the §5 schema; `outOfScopeMode ∈ {tag,mute,exclude}`, `maxAssetMb ≤ 10`, rules well-formed. A pure `resolve_effective_config(defaults, overrides)` helper (unit-tested like the `session_scope` helpers) is the single merge implementation, shared by create + validation.

---

## 8. Extension (client) flow

Popup New-Session journey (replaces the free-text "root domains" box; see the reviewed wireframe):

1. **Pick engagement** — popup fetches `GET /api/projects`, cached in `chrome.storage.local` (`projectsCache` + timestamp) so capture survives a brief backend outage; refresh on popup open. Options: pick a project · **＋ New project** · **Standalone (no project)**.
2. **＋ New project** (optional) — minimal quick-create: name + scope (root domains, include-subdomains) → `POST /api/projects`, then select it. Full defaults editing lives in the workspace.
3. **Review inherited defaults** — show the effective config with each field tagged `inherited`.
4. **Override for this session** (optional) — expand to edit any field; the editor is **prefilled from the inherited value**; touched fields flip to `overridden`.
5. **Start capture** — `startNewSession({ projectId, overrides })` → background:
   - resolves effective config = `resolve(project.defaults, overrides)`;
   - applies it to live capture gating (sets `domainScopes`/`useDomainScope`/`includeSubdomains`, `denyRules`/`denyDefaultProfile`, `outOfScopeMode`/`maxAssetMb`, analysis toggles) — same enforcement path as today;
   - rotates `reconSessionId`;
   - stamps every upload's `metadata` with `projectId`, resolved `rootDomains`/`includeSubdomains`, `captureConfig`, `overrideKeys`.

> **Implementation note (Plan B):** the **popup** performs the resolve (it already resolves for the inherited/override preview) and sends the resolved snapshot `{ projectId, scope, captureConfig, overrideKeys }`; the background service worker is the thin applicator (maps the snapshot onto the flat capture-gate keys + the uploader). This realizes §7's "the client resolves; the backend stores as-is" — read "background resolves" above as "the extension client resolves, then background applies."

Standalone: `startNewSession({ projectId: null, scope })` → today's behavior (type scope ad-hoc, global defaults), still snapshotting the resolved `captureConfig` for a uniform record.

Client modules touched: `modules/workspace-client.js` (add `listProjects`, `createProject`), `background.js` (`newSession` becomes project-aware; a `resolveEffectiveConfig` mirror of the server schema), `modules/batch-uploader.js` (`scopeMetadata` → `configMetadata`: adds `projectId`/`captureConfig`/`overrideKeys`), popup `src/popup/{app.jsx, api.js, components/HomeView.jsx}` (project picker + inherited display + override editor), `src/popup/components/SettingsView.jsx` (the global "default scope for new sessions" becomes a fallback used only by Standalone).

---

## 9. Workspace SPA (`web/`)

- New **Projects** view (+ sidebar nav): list / create / edit / delete projects; edit the full `defaults` document (scope, capture, denylist, analysis). This is the proper home for "analysis depth" (the cleanup already said analysis config belongs in the workspace). New `web/src/api.js` methods: `listProjects`, `createProject`, `updateProject`, `deleteProject`.
- **Sessions** view: show each session's project name + an `overridden` badge (from `override_keys`).
- **ScopeModal → "Session settings"**: generalize the existing per-session scope editor to edit the full session override set (scope + capture + denylist + analysis), prefilled from the session's effective config, saved via `PATCH /api/sessions/{id}`.

---

## 10. Crawler path (thin)

`POST /api/recon/jobs/start` uses the same session-create seam. Add an optional project picker to `NewReconModal` so a crawl-created session can attach to a project and inherit its scope. Keep the change minimal — no rework of the crawler's own `same_origin_only` logic in this cut.

---

## 11. Out of scope / non-goals

- **Server-side scope enforcement/classification** — the server keeps storing scope without acting on it; the extension remains the scope gate. (User did not select this.)
- **Auth / tenant isolation** — projects organize work; they are not a security boundary.
- **Live-inherit** and **mandatory membership** — both explicitly rejected.
- **"Apply to existing sessions" on project edit** — fast-follow.
- **Reassigning an existing session to a different project** — fast-follow (snapshot model makes this a re-resolve or a relabel; defer the decision).
- Per-project **findings aggregation** view — future.

---

## 12. Migration & backward compatibility

- One additive alembic revision (new table + three nullable session columns). Existing sessions become loose (`project_id = NULL`); no backfill.
- Standalone capture is byte-for-byte today's behavior, so an un-updated mental model still works.
- Old extension builds that omit the new `metadata` fields still create sessions (fields are optional; behave as standalone).

---

## 13. Testing (colocated, per repo convention)

Backend:
- `resolve_effective_config` unit tests: inherit-all, per-field override replaces, unknown keys rejected, list replace (not union), schema validation (bad `outOfScopeMode`, `maxAssetMb > 10`).
- Project CRUD + `PATCH` deep-merge + `DELETE` nulls `project_id`.
- `save-files` binds `project_id`/`capture_config`/`override_keys` on create only (append does not re-bind).
- `PATCH /api/sessions/{id}` edits override + recomputes `override_keys`.

Extension:
- `newSession` resolves + applies live gating + stamps upload metadata (project and standalone paths).
- `projectsCache` read/write + offline fallback.
- Override prefill = inherited value; standalone parity with today.

Web:
- Projects view CRUD; sessions list provenance badge; Session-settings modal round-trips override via PATCH.

---

## 14. Open questions / fast-follows

1. "Apply to existing sessions" opt-in on project edit.
2. Reassign a session's project (re-resolve vs relabel).
3. Should Standalone stop sending `captureConfig` (keep the backend record minimal) or keep it for uniformity? (Spec assumes: keep it.)
4. Per-project findings/asset aggregation view in the workspace.
