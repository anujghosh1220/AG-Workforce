"""LLM provider interface with structured-output validation."""
from __future__ import annotations

import os
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMProvider(Protocol):
    name: str
    def configured(self) -> bool: ...
    def generate_json(self, system: str, user: str, schema: type[T],
                      model: str | None = None) -> T: ...


class LLMError(Exception):
    pass


def get_llm():
    provider = os.environ.get("LLM_PROVIDER", "gemini").lower()
    raw = None
    if provider == "gemini":
        from app.integrations.llm.gemini import GeminiProvider
        p = GeminiProvider()
        if p.configured():
            raw = p
    if raw is None:
        return None
    # Wrap every provider call in metering (Phase 8.8): each real invocation
    # is recorded; heuristic fallbacks never reach this function.
    from app.services.llm_metering import MeteredLLM
    return MeteredLLM(raw)
