"""A test adapter for the provider seam (``provider.ADAPTERS``): install it with
``FakeProvider.install(monkeypatch, "openrouter", content={...})`` and every call for that
provider is answered here instead of a real API. It records the calls it receives."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel

from recon.llm import provider as provider_module
from recon.llm.provider import LLMProvider, LLMResponse, LLMUsage


class FakeProvider(LLMProvider):
    calls: ClassVar[list[dict[str, Any]]] = []
    content: ClassVar[dict[str, Any]] = {}
    usage: ClassVar[LLMUsage] = LLMUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120)
    error: ClassVar[BaseException | None] = None
    finish_reason: ClassVar[str | None] = "stop"

    @classmethod
    def install(
        cls,
        monkeypatch: Any,
        provider: str,
        *,
        content: dict[str, Any] | None = None,
        error: BaseException | None = None,
        usage: LLMUsage | None = None,
    ) -> type[FakeProvider]:
        """Answer ``provider``'s calls with ``content`` (or raise ``error``). Returns a fresh
        subclass whose ``calls`` list records (api_key, model, user_prompt, max_tokens)."""
        fake = type(
            "InstalledFake",
            (cls,),
            {
                "calls": [],
                "content": content or {},
                "error": error,
                "usage": usage or cls.usage,
            },
        )
        monkeypatch.setitem(provider_module.ADAPTERS, provider, fake)
        return fake

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        output_schema: type[BaseModel],
        max_tokens: int = 8192,
    ) -> LLMResponse:
        type(self).calls.append(
            {
                "api_key": self.api_key,
                "model": self.model,
                "user_prompt": user_prompt,
                "max_tokens": max_tokens,
            }
        )
        if self.error is not None:
            raise self.error
        return LLMResponse(
            content=self.content,
            usage=self.usage,
            model=self.model,
            provider="fake",
            finish_reason=self.finish_reason,
        )
