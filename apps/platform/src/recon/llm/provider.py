"""LLM provider abstraction — Anthropic, OpenRouter, Gemini.

Each provider implements ``generate_structured``: takes a system prompt, user
prompt, and a Pydantic output class; returns a normalized ``LLMResponse``.
Token field names differ across SDKs; this module normalises them so callers
never deal with ``candidates_token_count`` vs ``completion_tokens``.

Usage:
    provider = build_provider("openrouter", api_key="sk-...", model="anthropic/claude-sonnet-4.6")
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
    "openrouter": "anthropic/claude-sonnet-4.6",
    "gemini": "gemini-2.5-flash",
}


@dataclass
class LLMUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    # False when the provider sent no usage: the zeros are placeholders, not a free call.
    reported: bool = True


@dataclass
class LLMResponse:
    content: dict[str, Any]
    usage: LLMUsage
    model: str
    provider: str
    finish_reason: str | None = None

    def parse(self, schema: type[T]) -> T:
        return schema.model_validate(self.content)


class LLMOutputError(ValueError):
    """The provider answered but the answer is unusable (hit max_tokens, not JSON, no tool
    call). Carries the usage so a failed run can still report what it consumed."""

    def __init__(self, message: str, usage: LLMUsage, finish_reason: str | None) -> None:
        super().__init__(message)
        self.usage = usage
        self.finish_reason = finish_reason


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
        usage = LLMUsage(
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
        )
        # The tool_use block carries the parsed input dict. It is missing when the model stopped
        # early (e.g. stop_reason "max_tokens"); a bare next() would surface as a RuntimeError.
        tool_block = next((b for b in response.content if b.type == "tool_use"), None)
        if tool_block is None:
            raise LLMOutputError(
                f"Anthropic model {self.model!r} returned no structured output "
                f"(stop_reason={response.stop_reason!r})",
                usage,
                response.stop_reason,
            )
        content: dict[str, Any] = tool_block.input
        return LLMResponse(
            content=content,
            usage=usage,
            model=self.model,
            provider="anthropic",
            finish_reason=response.stop_reason,
        )


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
            # Without this, OpenRouter may route (incl. :floor's price sort) to a host that
            # ignores response_format and returns prose, which we can't parse.
            extra_body={"provider": {"require_parameters": True}},
        )
        u = response.usage
        usage = LLMUsage(
            prompt_tokens=u.prompt_tokens if u else 0,
            completion_tokens=u.completion_tokens if u else 0,
            total_tokens=u.total_tokens if u else 0,
            reported=u is not None,
        )
        raw = (response.choices[0].message.content or "").strip()
        finish_reason = response.choices[0].finish_reason
        if not raw:
            raise LLMOutputError(
                f"OpenRouter returned empty content for model {self.model!r}; "
                "check the model slug is valid and the key has access to it",
                usage,
                finish_reason,
            )
        if finish_reason == "length":
            raise LLMOutputError(
                f"OpenRouter model {self.model!r} hit max_tokens before finishing; "
                "increase max_tokens or reduce prompt length",
                usage,
                finish_reason,
            )
        # Some models wrap JSON in ```json ... ``` despite json_object mode — strip fences.
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            content = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMOutputError(
                f"OpenRouter model {self.model!r} returned non-JSON content "
                f"(finish_reason={finish_reason!r}): {raw[:500]!r}",
                usage,
                finish_reason,
            ) from exc
        return LLMResponse(
            content=content,
            usage=usage,
            model=self.model,
            provider="openrouter",
            finish_reason=finish_reason,
        )


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
        u = response.usage_metadata
        prompt_tokens = (u.prompt_token_count or 0) if u else 0
        completion_tokens = (u.candidates_token_count or 0) if u else 0
        usage = LLMUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=(prompt_tokens + completion_tokens),
        )
        candidates = response.candidates or []
        reason = candidates[0].finish_reason if candidates else None
        finish_reason = str(getattr(reason, "name", reason)) if reason is not None else None
        try:
            content = output_schema.model_validate_json(response.text or "").model_dump()
        except ValueError as exc:  # pydantic ValidationError is a ValueError
            raise LLMOutputError(
                f"Gemini model {self.model!r} returned unusable output "
                f"(finish_reason={finish_reason!r})",
                usage,
                finish_reason,
            ) from exc
        return LLMResponse(
            content=content,
            usage=usage,
            model=self.model,
            provider="gemini",
            finish_reason=finish_reason,
        )


# The provider seam: tests swap an entry for FakeProvider (monkeypatch.setitem), so no test
# ever reaches a real LLM API.
ADAPTERS: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openrouter": OpenRouterProvider,
    "gemini": GeminiProvider,
}


def build_provider(provider: str, api_key: str, model: str | None = None) -> LLMProvider:
    """Instantiate the right LLMProvider for the given provider name."""
    adapter = ADAPTERS.get(provider)
    if adapter is None:
        raise ValueError(
            f"unknown provider: {provider!r}. Must be one of {sorted(VALID_PROVIDERS)}"
        )
    return adapter(api_key=api_key, model=model or DEFAULT_MODELS.get(provider, ""))
