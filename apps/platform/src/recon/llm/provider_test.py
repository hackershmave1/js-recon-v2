"""OpenRouter routing must only use hosts that honour response_format."""

import asyncio
from types import SimpleNamespace

import openai
import pytest
from pydantic import BaseModel

from recon.llm.provider import AnthropicProvider, LLMOutputError, OpenRouterProvider, build_provider


class _Out(BaseModel):
    ok: bool


def test_openrouter_requires_hosts_that_support_our_parameters(monkeypatch):
    captured: dict = {}

    class _Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            message = SimpleNamespace(content='{"ok": true}')
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message, finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )

    class _Client:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=_Completions())

    monkeypatch.setattr(openai, "AsyncOpenAI", _Client)
    asyncio.run(OpenRouterProvider(api_key="k", model="m").generate_structured("s", "u", _Out, 10))
    assert captured["extra_body"] == {"provider": {"require_parameters": True}}
    assert captured["response_format"] == {"type": "json_object"}


def _openrouter_returning(monkeypatch, content: str, finish_reason: str) -> None:
    class _Completions:
        async def create(self, **_kwargs):
            message = SimpleNamespace(content=content)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
                usage=SimpleNamespace(
                    prompt_tokens=21480, completion_tokens=12000, total_tokens=33480
                ),
            )

    class _Client:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=_Completions())

    monkeypatch.setattr(openai, "AsyncOpenAI", _Client)


def test_openrouter_truncation_reports_the_tokens_it_consumed(monkeypatch):
    # A failed run must still show what it cost: the usage travels on the error.
    _openrouter_returning(monkeypatch, '{"ok": tr', "length")
    with pytest.raises(LLMOutputError) as raised:
        asyncio.run(
            OpenRouterProvider(api_key="k", model="m").generate_structured("s", "u", _Out, 10)
        )
    assert raised.value.finish_reason == "length"
    assert (raised.value.usage.prompt_tokens, raised.value.usage.completion_tokens) == (
        21480,
        12000,
    )
    assert isinstance(raised.value, ValueError), "callers that catch ValueError keep working"


def test_openrouter_success_reports_finish_reason(monkeypatch):
    _openrouter_returning(monkeypatch, '{"ok": true}', "stop")
    response = asyncio.run(
        OpenRouterProvider(api_key="k", model="m").generate_structured("s", "u", _Out, 10)
    )
    assert response.finish_reason == "stop"


def test_anthropic_without_tool_output_is_a_clear_error(monkeypatch):
    import anthropic

    class _Messages:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="I ran out of room")],
                stop_reason="max_tokens",
                usage=SimpleNamespace(input_tokens=5, output_tokens=7),
            )

    class _Client:
        def __init__(self, **_kwargs):
            self.messages = _Messages()

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _Client)
    with pytest.raises(LLMOutputError) as raised:
        asyncio.run(
            AnthropicProvider(api_key="k", model="m").generate_structured("s", "u", _Out, 10)
        )
    assert raised.value.finish_reason == "max_tokens"
    assert raised.value.usage.completion_tokens == 7


def test_build_provider_uses_the_swappable_adapter_table(monkeypatch):
    from recon.llm.fake_provider import FakeProvider

    fake = FakeProvider.install(monkeypatch, "gemini", content={"ok": True})
    adapter = build_provider("gemini", api_key="k")
    assert isinstance(adapter, fake)
    assert adapter.model == "gemini-2.5-flash", "provider default model still applies"
    with pytest.raises(ValueError):
        build_provider("nope", api_key="k")


def test_response_parse_still_validates_content():
    from recon.llm.provider import LLMResponse, LLMUsage

    response = LLMResponse(content={"ok": True}, usage=LLMUsage(1, 1, 2), model="m", provider="p")
    assert response.parse(_Out) == _Out(ok=True)
