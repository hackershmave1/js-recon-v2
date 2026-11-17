"""The LLM's structured output — passed to ``calls.generate`` as the output schema."""

from __future__ import annotations

from pydantic import BaseModel


class TestStep(BaseModel):
    model_config = {"extra": "ignore"}
    action: str = ""
    tool: str = ""
    command: str = ""
    expected_if_vulnerable: str = ""
    expected_if_secure: str = ""


class ThreatItem(BaseModel):
    model_config = {"extra": "ignore"}
    title: str
    owasp_category: str = ""
    severity: str = "medium"
    description: str = ""
    affected_endpoints: list[str] = []
    test_steps: list[TestStep] = []
    citations: list[str] = []


class ThreatModelOutput(BaseModel):
    model_config = {"extra": "ignore"}
    analysis_summary: str = ""
    threats: list[ThreatItem] = []
