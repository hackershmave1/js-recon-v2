"""OpenRouter routing must only use hosts that honour response_format."""

import asyncio
from types import SimpleNamespace

import openai
from pydantic import BaseModel

from recon.llm.provider import OpenRouterProvider


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
