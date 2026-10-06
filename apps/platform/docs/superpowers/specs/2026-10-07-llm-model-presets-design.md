# LLM model catalog + cost/strength presets: design

**Date:** 2026-10-07 · **Branch:** `feat/llm-model-presets` (stacked on `feat/team-llm-settings`)
**Status:** approved in chat. Decisions: presets + full picker (C); Settings default plus a per-run
choice; **admins edit each preset**; **anyone on the team** may pick any preset per run.

## Problem

The team Settings page (`feat/team-llm-settings`) takes the model as free text, pre-filled
with a single default per provider (`recon.llm.provider.DEFAULT_MODELS`). Users can't browse
OpenRouter's catalog, can't see what a model costs, and can't trade cost against strength,
either as a team default or for a single threat-model run.

## Goal

1. A searchable, price-sortable picker over OpenRouter's **live** model catalog.
2. Three presets, **Cheapest · Balanced · Strongest**, with sensible built-in defaults that an
   admin can override for the team.
3. The Threat Model tab's *Generate* takes an optional preset for that run only.
4. Each option shows an estimated **cost per threat model**, computed from live prices and the
   team's real token history.

Non-goals (YAGNI): catalogs for the direct Anthropic/Gemini providers (they keep free-text
models plus built-in presets), per-user preferences, spend caps or budgets, an automatic
"best model" ranking, streaming catalog updates.

## Invariant (carried over)

**A key always travels with its own provider.** A preset never changes the provider.
`(preset, credential provider)` resolves to a model **for that provider**. An admin override
applies only when the credential's provider equals the team config's provider.

## 1. Catalog: `recon/llm/catalog.py` + `GET /settings/llm/models`

- Fetch `GET https://openrouter.ai/api/v1/models` (public; no key) with `httpx`, 10 s timeout.
  This is a fixed vendor URL, not user-supplied, so it doesn't go through the target-fetch
  SSRF guard. That guard covers crawling targets.
- Keep only models whose `supported_parameters` contains `response_format`. The OpenRouter
  provider sends `response_format={"type": "json_object"}`
  (`recon/llm/provider.py`, OpenRouterProvider).
- Normalized item: `{id, name, context_length, prompt_price, completion_price}`. Prices are
  USD per token, parsed from OpenRouter's string fields with `Decimal`. Unparseable or
  negative prices drop the model.
- **Cache:** in-process, 1 h TTL (single uvicorn worker, `docker-compose.yml` api command). On
  fetch failure, serve the last good copy if there is one (`stale: true`), else return
  `{models: [], available: false}`. The UI then falls back to the free-text model field.
- Endpoint `GET /settings/llm/models` (any team member, `get_tenant_id`) returns
  `{available, stale, fetched_at, models: [...], estimate: {prompt_tokens, completion_tokens,
  basis}}`. See §2 for `estimate`.
- **Observability:** `llm.catalog.fetched` (count, ms), `llm.catalog.fetch_failed`
  (error_type, served_stale).

## 2. Cost estimate: data, not prose

`estimate` gives the token counts the UI multiplies by each model's price:
- **basis `"history"`:** the average `prompt_tokens` and `completion_tokens` over this tenant's
  completed threat models (`session_threat_model`, `status='done'`), read in `tenant_session`.
- **basis `"assumed"`:** when there is no history, a labelled assumption: 20,000 prompt and
  4,000 completion tokens. The UI says "assumed, no history yet".
- The UI shows `≈ $X.XX per threat model` = `prompt_price*prompt_tokens +
  completion_price*completion_tokens`, rounded to cents (`<$0.01` below one cent).

## 3. Presets: `recon/llm/presets.py`

- `PRESETS = ("cheapest", "balanced", "strongest")`.
- `BUILTIN_PRESET_MODELS: dict[provider, dict[preset, model_id]]` covers anthropic,
  openrouter and gemini. Haiku / Sonnet / Opus class for anthropic and openrouter; flash-lite /
  flash / pro for gemini. The exact IDs are fixed in the plan. A test asserts every provider in
  `VALID_PROVIDERS` has all three presets, and that `balanced` equals `DEFAULT_MODELS[provider]`
  (today's default stays the middle option).
- `resolve_model(preset, credential_provider, team_provider, overrides, fallback_model) -> str`:
  1. `preset is None` → `fallback_model` (the credential's own model; current behaviour).
  2. `credential_provider == team_provider` and `overrides.get(preset)` → that override.
  3. else `BUILTIN_PRESET_MODELS[credential_provider][preset]`.
  4. For `openrouter` + `cheapest`, append `:floor` unless the id already has a `:` variant.
- **Admin overrides:** new nullable JSONB `tenant_llm_config.preset_models`
  (`{"cheapest": "...", ...}`), migration **`0030_llm_preset_models`** (`ADD COLUMN IF NOT
  EXISTS`). The table is already RLS-protected, so no new policy is needed.
- `PUT /settings/llm` accepts optional `preset_models` (keys ⊆ PRESETS, non-blank strings;
  else 422). **Changing `provider` clears `preset_models`**, since they're IDs for the old
  provider, unless the same PUT supplies new ones.
- `GET /settings/llm` adds `presets: {preset: {model, source: "team"|"builtin", available}}`
  for the team provider, plus `builtin_preset_models` so the UI can show what "reset" means.
  `available` is `true`/`false` when the team provider is `openrouter` and the catalog is
  available: whether the model ID (minus any `:variant`) is in the catalog. Otherwise it's
  `null` (unknown). The UI shows a warning badge for `false`; generation is never blocked by it.

## 4. Per-run choice

- `POST /sessions/{id}/threat-model` accepts an optional JSON body `{"preset": "cheapest" |
  "balanced" | "strongest"}`. Anything else → 422. No body → today's behaviour.
- `load_credentials` is unchanged. `run_generation(tenant_id, session_id, preset=None)`
  resolves the model with `resolve_model(...)` after loading credentials, using
  `tenant_config` for `team_provider` + `overrides`.
- The chosen preset and resolved model are logged (`threat_model.generation_model` with
  preset, provider, model) and stored in the existing `provider`/`model` columns. Nothing new
  is persisted per run.
- **Permission:** any team member (decision). The cost estimate is shown on each option.

## 5. UI

- **Settings, admin view:** three preset rows, each showing name, resolved model, `team` /
  `default` badge, ≈ cost, and an **Edit** that opens the model picker; **Reset** removes the
  override. The team default model field gains **Choose from catalog…**, the same picker.
- **Picker** (`features/settings/ModelPicker.tsx`): search by id or name, sort by price or name,
  columns: name / id, context, ≈ cost per threat model. It's virtualized or capped at 200
  visible rows, and shows "catalog unavailable — type a model ID" when `available` is false.
  For direct providers (anthropic, gemini) the picker isn't offered; free text stays.
- **Settings, analyst view:** the presets render read-only.
- **Threat Model tab:** next to *Generate*, a select **Default · Cheapest · Balanced ·
  Strongest**, each with its ≈ cost when known. Sends `{preset}` (omitted for Default).

## 6. Testing

- `catalog_test.py` (host lane, no network): a stubbed `httpx` transport covers parse, filter,
  bad prices, TTL cache, stale-on-failure, unavailable-without-cache.
- `presets_test.py` (host): `resolve_model` for every rule, including `:floor` and the
  provider-mismatch fallback to builtin.
- Integration: estimate from history vs assumed; `PUT` preset validation; provider change
  clears overrides; `/settings/llm/models` shape with the catalog stubbed; trigger with
  preset → the run uses the resolved model (stub provider records the model).
- Vitest: picker search, sort and cost formatting, unavailable fallback; preset rows edit and
  reset; Generate sends the preset.

## Accepted limits

- Built-in preset IDs need occasional updates. The catalog check surfaces a retired ID as
  unavailable rather than failing silently at generation time.
- Cost is an estimate (average tokens × list price); provider-side discounts and `:floor`
  savings aren't reflected.
