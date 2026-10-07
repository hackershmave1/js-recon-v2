"""Presets resolve to a model for the credential's own provider, never another's."""

from recon.llm import presets
from recon.llm.presets import resolve_model
from recon.llm.provider import DEFAULT_MODELS, VALID_PROVIDERS


def test_every_provider_has_all_three_presets_and_balanced_is_the_default():
    assert set(presets.BUILTIN_PRESET_MODELS) == set(VALID_PROVIDERS)
    for provider, table in presets.BUILTIN_PRESET_MODELS.items():
        assert set(table) == set(presets.PRESETS)
        assert table["balanced"] == DEFAULT_MODELS[provider]


def test_openrouter_default_is_a_catalog_id():
    # OpenRouter's catalog spells versions with dots; "anthropic/claude-sonnet-4-6" isn't listed.
    assert DEFAULT_MODELS["openrouter"] == "anthropic/claude-sonnet-4.6"


def test_no_preset_keeps_the_credentials_own_model():
    assert resolve_model(None, "anthropic", "anthropic", {"cheapest": "x"}, "fb") == "fb"
    assert resolve_model(None, "openrouter", None, {}, None) is None


def test_builtin_used_without_an_override():
    assert resolve_model("strongest", "anthropic", None, {}, None) == "claude-opus-5-5"
    assert resolve_model("balanced", "gemini", "openrouter", {}, None) == "gemini-2.5-flash"


def test_team_override_applies_only_to_the_team_provider():
    overrides = {"balanced": "vendor/team-pick"}
    assert (
        resolve_model("balanced", "openrouter", "openrouter", overrides, None) == "vendor/team-pick"
    )
    # A session's own Anthropic key must never be paired with an OpenRouter model id.
    assert (
        resolve_model("balanced", "anthropic", "openrouter", overrides, None) == "claude-sonnet-4-6"
    )


def test_cheapest_on_openrouter_routes_to_the_cheapest_host():
    assert (
        resolve_model("cheapest", "openrouter", None, {}, None)
        == "anthropic/claude-haiku-4.5:floor"
    )
    # An explicit variant is kept as-is, and direct providers never get one.
    over = {"cheapest": "vendor/model:free"}
    assert resolve_model("cheapest", "openrouter", "openrouter", over, None) == "vendor/model:free"
    assert resolve_model("cheapest", "anthropic", None, {}, None) == "claude-haiku-4-5-20251001"
