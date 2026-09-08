"""LLM provider abstraction — Anthropic, OpenRouter, Gemini.

Each provider implements ``generate_structured``: takes a system prompt, user
prompt, and a Pydantic output class; returns a normalized ``LLMResponse``.
Token field names differ across SDKs; this module normalises them so callers
never deal with ``candidates_token_count`` vs ``completion_tokens``.

Usage:
    provider = build_provider("openrouter", api_key="sk-...", model="anthropic/claude-sonnet-4-5")
    response = await provider.generate_structured(system, user, MyOutputModel)
    result: MyOutputModel = response.parsed
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

VALID_PROVIDERS = frozenset({"anthropic", "openrouter", "gemini"})

# Sensible defaults per provider — callers may override.
DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    "openrouter": "anthropic/claude-sonnet-4-5",
    "gemini": "gemini-2.5-flash",
}


@dataclass
class LLMUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass
class LLMResponse:
    content: dict[str, Any]
    usage: LLMUsage
    model: str
    provider: str

    def parse(self, schema: type[T]) -> T:
        return schema.model_validate(self.content)


class LLMProvider(ABC):
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    @abstractmethod
    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        output_schema: type[BaseModel],
        max_tokens: int = 8192,
    ) -> LLMResponse: ...


class AnthropicProvider(LLMProvider):
    """Direct Claude API via the anthropic SDK."""

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        output_schema: type[BaseModel],
        max_tokens: int = 8192,
    ) -> LLMResponse:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        schema = output_schema.model_json_schema()

        # Use tool_use for structured output — the Anthropic API doesn't have a
        # native response_format parameter; tool_use is the endorsed pattern.
        tool = {
            "name": "structured_output",
            "description": "Return the analysis as structured JSON.",
            "input_schema": schema,
        }
        response = await client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[tool],
            tool_choice={"type": "tool", "name": "structured_output"},
        )
        # The tool_use block carries the parsed input dict.
        tool_block = next(b for b in response.content if b.type == "tool_use")
        content: dict[str, Any] = tool_block.input
        usage = LLMUsage(
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
        )
        return LLMResponse(content=content, usage=usage, model=self.model, provider="anthropic")


class OpenRouterProvider(LLMProvider):
    """Any model via OpenRouter — OpenAI SDK with base_url override."""

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        output_schema: type[BaseModel],
        max_tokens: int = 8192,
    ) -> LLMResponse:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=self.api_key,
            default_headers={
                "HTTP-Referer": "https://recon-platform",
                "X-OpenRouter-Title": "JS Recon Platform",
            },
        )
        # Use json_object mode rather than strict json_schema — strict mode requires
        # all $defs to be inlined, which Pydantic's generated schema doesn't do for
        # nested models, causing the model to silently drop constrained fields.
        # The system prompt carries the full structural contract instead.
        response = await client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        content = json.loads(raw)
        u = response.usage
        usage = LLMUsage(
            prompt_tokens=u.prompt_tokens if u else 0,
            completion_tokens=u.completion_tokens if u else 0,
            total_tokens=u.total_tokens if u else 0,
        )
        return LLMResponse(content=content, usage=usage, model=self.model, provider="openrouter")


class GeminiProvider(LLMProvider):
    """Google Gemini via the google-genai SDK (google-generativeai is deprecated)."""

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        output_schema: type[BaseModel],
        max_tokens: int = 8192,
    ) -> LLMResponse:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        response = await client.aio.models.generate_content(
            model=self.model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=output_schema,
                max_output_tokens=max_tokens,
            ),
        )
        content = output_schema.model_validate_json(response.text).model_dump()
        u = response.usage_metadata
        prompt_tokens = u.prompt_token_count if u else 0
        completion_tokens = u.candidates_token_count if u else 0
        usage = LLMUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=(prompt_tokens + completion_tokens),
        )
        return LLMResponse(content=content, usage=usage, model=self.model, provider="gemini")


def build_provider(provider: str, api_key: str, model: str | None = None) -> LLMProvider:
    """Instantiate the right LLMProvider for the given provider name."""
    resolved_model = model or DEFAULT_MODELS.get(provider, "")
    if provider == "anthropic":
        return AnthropicProvider(api_key=api_key, model=resolved_model)
    if provider == "openrouter":
        return OpenRouterProvider(api_key=api_key, model=resolved_model)
    if provider == "gemini":
        return GeminiProvider(api_key=api_key, model=resolved_model)
    raise ValueError(f"unknown provider: {provider!r}. Must be one of {sorted(VALID_PROVIDERS)}")
