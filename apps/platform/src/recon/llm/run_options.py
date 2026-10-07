"""What each preset would run for one session: the credential's provider decides, so the
Threat Model tab never shows a model the run won't actually use."""

from __future__ import annotations

from typing import Any

from recon.llm import service as llm_service
from recon.llm import tenant_config
from recon.llm.presets import PRESETS, resolve_model


def session_preset_options(tenant_id: str, session_id: str) -> dict[str, Any]:
    provider = llm_service.peek_credential_provider(tenant_id, session_id)
    if provider is None:
        return {"credential_provider": None, "presets": None}
    team_provider, overrides = tenant_config.load_preset_context(tenant_id)
    return {
        "credential_provider": provider,
        "presets": {p: resolve_model(p, provider, team_provider, overrides, None) for p in PRESETS},
    }
