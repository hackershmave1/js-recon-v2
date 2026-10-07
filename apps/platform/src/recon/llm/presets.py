"""Cost/strength presets: Cheapest · Balanced · Strongest → a model for the credential's
own provider. A preset never changes the provider (the key must travel with its provider),
so team overrides, which are model ids for the team provider, apply only to that provider."""

from __future__ import annotations

from collections.abc import Mapping

from recon.llm.provider import DEFAULT_MODELS

PRESETS: tuple[str, ...] = ("cheapest", "balanced", "strongest")

# NOTE: OpenRouter ids are catalog ids (dots); direct Anthropic ids use hyphens. "balanced"
# is each provider's DEFAULT_MODELS entry so today's default stays the middle option.
BUILTIN_PRESET_MODELS: dict[str, dict[str, str]] = {
    "anthropic": {
        "cheapest": "claude-haiku-4-5-20251001",
        "balanced": DEFAULT_MODELS["anthropic"],
        "strongest": "claude-opus-5-5",
    },
    "openrouter": {
        "cheapest": "anthropic/claude-haiku-4.5",
        "balanced": DEFAULT_MODELS["openrouter"],
        "strongest": "anthropic/claude-opus-5.5",
    },
    "gemini": {
        "cheapest": "gemini-2.5-flash-lite",
        "balanced": DEFAULT_MODELS["gemini"],
        "strongest": "gemini-2.5-pro",
    },
}


def resolve_model(
    preset: str | None,
    credential_provider: str,
    team_provider: str | None,
    overrides: Mapping[str, str],
    fallback_model: str | None,
) -> str | None:
    """The model to run. None preset keeps the credential's own model (may be None: the
    provider default). Cheapest on OpenRouter gets ``:floor`` (cheapest host, same model)."""
    if preset is None:
        return fallback_model
    model = overrides.get(preset) if credential_provider == team_provider else None
    if not model:
        model = BUILTIN_PRESET_MODELS[credential_provider][preset]
    if credential_provider == "openrouter" and preset == "cheapest" and ":" not in model:
        model += ":floor"
    return model
