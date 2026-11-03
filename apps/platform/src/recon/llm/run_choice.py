"""Which provider, model and key a session's LLM run uses — the one place that decides.

Precedence: a key saved on the session, then the team key (Settings), then an operator-wide
env key. A key always travels with the provider it was saved for, so a preset or a saved model
never moves a key to another provider. The Threat Model tab's preset menu is built from the
same decision (``preset_menu``), so it can never advertise a model a real run won't use.

Nothing here decrypts unless ``with_key=True``: the menu must render (and name the provider)
even when the stored key is undecryptable; the run then fails with the re-save message.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from recon.db.base import tenant_session
from recon.db.models import SessionLlmConfig
from recon.llm import tenant_config
from recon.llm.crypto import decrypt_api_key
from recon.llm.presets import PRESETS, resolve_model
from recon.llm.provider import DEFAULT_MODELS
from recon.sessions.service import resolve_session_id

KeySource = Literal["session", "team", "env"]

# Operator-wide keys, tried in order when neither the session nor the team has a key. Each is
# bound to the provider it belongs to: the key and the provider must come from the same lookup,
# or an OpenRouter key ends up sent to Anthropic.
_ENV_KEYS = (("openrouter", "OPENROUTER_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY"))


@dataclass(frozen=True)
class RunChoice:
    provider: str
    model: str | None  # None => the provider's default model (build_provider)
    source: KeySource
    api_key: str | None  # only with with_key=True


@dataclass(frozen=True)
class _Base:
    provider: str
    fallback_model: str | None  # the model a preset-less run uses
    source: KeySource
    secret: str  # ciphertext for session/team keys, the plain key for env


def resolve(
    tenant_id: str, session_id: str, preset: str | None = None, *, with_key: bool = False
) -> RunChoice | None:
    """The provider/model/key a run would use, or None (no key anywhere, or unknown session).

    With ``with_key`` the key is decrypted (raises KeyDecryptError on a bad stored key)
    BEFORE the preset is applied, so a bad key reports as such even if presets would fail too.
    """
    base = _base(tenant_id, session_id)
    if base is None:
        return None
    api_key = (
        (base.secret if base.source == "env" else decrypt_api_key(base.secret))
        if with_key
        else None
    )
    model = base.fallback_model
    if preset is not None:
        team_provider, overrides = tenant_config.load_preset_context(tenant_id)
        model = resolve_model(preset, base.provider, team_provider, overrides, base.fallback_model)
    return RunChoice(provider=base.provider, model=model, source=base.source, api_key=api_key)


def preset_menu(tenant_id: str, session_id: str) -> dict[str, Any]:
    """What each preset would run for this session's credential — never key material.
    ``default_model`` is what a run with no preset uses (the model chosen in Settings or on the
    session, else the provider default), so the tab can name it instead of "Default model"."""
    base = _base(tenant_id, session_id)
    if base is None:
        return {
            "credential_provider": None,
            "default_model": None,
            "key_source": None,
            "presets": None,
        }
    team_provider, overrides = tenant_config.load_preset_context(tenant_id)
    return {
        "credential_provider": base.provider,
        "default_model": base.fallback_model or DEFAULT_MODELS[base.provider],
        "key_source": base.source,
        "presets": {
            p: resolve_model(p, base.provider, team_provider, overrides, base.fallback_model)
            for p in PRESETS
        },
    }


def _base(tenant_id: str, session_id: str) -> _Base | None:
    try:
        resolved = resolve_session_id(tenant_id, session_id)
    except ValueError:
        return None
    with tenant_session(tenant_id) as db:
        row = (
            db.query(SessionLlmConfig)
            .filter_by(session_id=uuid.UUID(resolved), tenant_id=uuid.UUID(tenant_id))
            .first()
        )
        if row is not None and row.encrypted_api_key:
            return _Base(row.provider, row.model, "session", row.encrypted_api_key)
        saved_provider, saved_model = (row.provider, row.model) if row else (None, None)
    # Team key next: it carries its own provider + model, never the session's.
    team = tenant_config.team_key_row(tenant_id)
    if team is not None:
        provider, model, encrypted = team
        return _Base(provider, model, "team", encrypted)
    for provider, env_var in _ENV_KEYS:
        if api_key := os.environ.get(env_var):
            # Model ids are provider-specific, so a saved model only carries over to its own
            # provider; otherwise build_provider falls back to that provider's default.
            return _Base(
                provider, saved_model if saved_provider == provider else None, "env", api_key
            )
    return None
