"""Gemini LLM provider — real generateContent calls, JSON-mode output."""
from __future__ import annotations

import json
import os

import httpx
from pydantic import BaseModel, ValidationError

from .base import LLMError

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiProvider:
    name = "gemini"

    def _key(self):
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

    def configured(self) -> bool:
        return bool(self._key())

    def generate_json(self, system: str, user: str, schema: type[BaseModel],
                      model: str | None = None) -> BaseModel:
        key = self._key()
        if not key:
            raise LLMError("GEMINI_API_KEY not configured")
        model = model or os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema.model_json_schema(),
                "temperature": 0.4,
            },
        }
        try:
            r = httpx.post(URL.format(model=model), params={"key": key}, json=body, timeout=45)
        except httpx.HTTPError as exc:
            raise LLMError(f"Gemini request failed: {exc}") from exc
        if r.status_code == 429:
            raise LLMError("Gemini rate limit exceeded")
        if r.status_code >= 400:
            raise LLMError(f"Gemini error {r.status_code}: {r.text[:300]}")
        try:
            data = r.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            um = data.get("usageMetadata") or {}
            self.last_usage = {
                "prompt": um.get("promptTokenCount", 0),
                "completion": um.get("candidatesTokenCount", 0),
                "model": model,
            }
            self.last_model = model
            return schema.model_validate_json(text)
        except (KeyError, IndexError, json.JSONDecodeError, ValidationError) as exc:
            raise LLMError(f"Gemini returned invalid output: {exc}") from exc
